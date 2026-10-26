use crate::Args;
use anyhow::{bail, Result};
use landlock::{
    path_beneath_rules, Access, AccessFs, CompatLevel, Compatible, LandlockStatus, Ruleset,
    RulesetAttr, RulesetCreatedAttr, RulesetStatus, ABI,
};
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::process::{Command, ExitCode};
use tracing::{info, warn};

/// Tier 1: Landlock LSM sandbox.
///
/// Applies Landlock rules to restrict filesystem access.
/// Read-only: /usr, /lib, /lib64, /opt, /etc, /bin, /sbin, wine prefix.
/// Read-write: binary dir, /tmp/win-runtime-$$.
///
/// NOTE: Landlock cannot block all network access — it can only GRANT specific
/// ports. For network isolation, use Tier 2 (bwrap with --unshare-net) or Tier 3.
/// Network restriction is NOT part of Tier 1.
pub fn run(args: &Args) -> Result<ExitCode> {
    let exe = args.exe.as_deref().unwrap();
    info!("Tier 1: Landlock sandbox for {exe}");

    let config = crate::config::load_config(None);
    let abi = detect_landlock_abi()?;

    // The prefix dispatch::execute resolved for this app. config.wine_prefix is
    // only the generic default and would grant the wrong directory.
    let resolved_prefix = crate::env_sanitize::resolved_prefix(args, &config);

    // Build read-only paths: system dirs + wine prefix
    let ro_paths = build_ro_paths(&resolved_prefix, args);
    let runtime = crate::env_sanitize::session_var(args, "XDG_RUNTIME_DIR");
    let rw_paths = build_rw_paths(&resolved_prefix, runtime.as_deref(), args.uid)?;

    // The ruleset below is applied to *this thread* and inherited by Wine.
    // In the daemon that thread is a per-launch worker, so the daemon itself
    // is not restricted. Verify the file before we lose the ability to stat it.
    crate::hasher::verify_unchanged(exe, args.exe_identity.as_ref())?;

    // Create ruleset with filesystem access handling.
    // Landlock cannot deny all TCP — it only grants specific ports. So we only
    // handle filesystem access here. Network isolation requires bwrap/seccomp.
    let status = Ruleset::default()
        .handle_access(AccessFs::from_all(abi))?
        .create()?
        // Read-only filesystem rules (system dirs, wine prefix, device nodes)
        .add_rules(path_beneath_rules(&ro_paths, AccessFs::from_read(abi)))?
        // Read-write filesystem rules (tmp dir, XDG runtime, /dev/null etc.)
        .add_rules(path_beneath_rules(&rw_paths, AccessFs::from_all(abi)))?
        .set_compatibility(CompatLevel::BestEffort)
        .restrict_self()?;

    if status.ruleset == RulesetStatus::NotEnforced {
        bail!("Landlock ruleset could not be enforced at all");
    }

    match status.landlock {
        LandlockStatus::NotEnabled => {
            warn!("Landlock is disabled in kernel config (CONFIG_LSM)");
        }
        LandlockStatus::NotImplemented => {
            warn!("Landlock is not built into this kernel");
        }
        LandlockStatus::Available { effective_abi, .. } => {
            info!("Landlock enforced with ABI v{}", effective_abi as u32);
        }
    }

    // Now exec wine — Landlock rules are inherited by child processes
    info!("Launching wine in Landlock sandbox");
    let user_env = crate::env_sanitize::user_env_for_args(args);
    let sandbox_env = crate::env_sanitize::build_sandbox_env(
        &config,
        user_env,
        args.resolved_wineprefix.as_deref(),
    )?;
    let mut cmd = Command::new("wine");
    cmd.arg(exe).args(&args.args);
    cmd.env_clear();
    for (key, val) in &sandbox_env {
        cmd.env(key, val);
    }
    // Switch to target UID in the child process (daemon mode)
    if let Some(uid) = args.uid {
        unsafe { crate::daemon::configure_child_uid(&mut cmd, uid) };
    }

    // Daemon mode (uid set): use status() so the daemon process survives.
    // CLI mode: use exec() to replace current process with wine.
    if args.uid.is_some() {
        let status = cmd.status()?;
        let code = status.code().unwrap_or(1);
        if code != 0 {
            bail!("Wine exited with status: {code}");
        }
        // Landlock was applied via restrict_self() above — status() forks a
        // child that inherits the ruleset, so the sandbox is effective.
        return Ok(ExitCode::from(code as u8));
    }

    let err = cmd.exec();
    bail!("Failed to exec wine: {err}");
}

/// Detect the highest Landlock ABI version the kernel supports.
///
/// `Ruleset::default()` already probes the running kernel (via
/// `LandlockStatus::current()`), so `handle_access()` failing here reflects a
/// real unsupported ABI, not a guess — verified empirically against
/// `.create()` returning the same result for every ABI level on this kernel.
/// `capabilities::Capabilities::detect()` calls this rather than
/// re-implementing the probe, so there is one place that can be wrong.
pub fn detect_landlock_abi() -> Result<ABI> {
    for abi in [ABI::V4, ABI::V3, ABI::V2, ABI::V1] {
        let result = Ruleset::default().handle_access(AccessFs::from_all(abi));
        if result.is_ok() {
            info!("Detected Landlock ABI v{}", abi as u32);
            return Ok(abi);
        }
    }
    bail!("Landlock is not supported on this kernel (requires 5.13+)")
}

/// Build the list of read-only filesystem paths.
fn build_ro_paths(wine_prefix: &str, args: &Args) -> Vec<String> {
    let mut paths = vec![
        "/usr".to_string(),
        "/lib".to_string(),
        "/lib64".to_string(),
        "/bin".to_string(),
        "/sbin".to_string(),
        "/opt".to_string(),
        "/etc".to_string(),
        "/dev/urandom".to_string(),
        "/dev/null".to_string(),
        "/dev/zero".to_string(),
        wine_prefix.to_string(),
    ];

    if let Some(parent) = std::path::Path::new(args.exe.as_deref().unwrap()).parent() {
        let parent_str = parent.to_string_lossy().to_string();
        if !paths.contains(&parent_str) && !parent_str.is_empty() && parent_str != "/" {
            paths.push(parent_str);
        }
    }

    paths
}

/// Build the read-write allowlist for the Landlock sandbox.
///
/// `wine_prefix` must be the *resolved* per-app prefix and `runtime_dir` the
/// session's (validated) `XDG_RUNTIME_DIR`.
///
/// Both matter because the daemon runs as a systemd service with a near-empty
/// environment. Reading XDG_RUNTIME_DIR from `std::env` there returns nothing,
/// so /run/user/<uid> was silently omitted from the allowlist and Landlock
/// denied wineserver its socket directory:
///     wine: unable to create wineserver tmpdir
///
/// The scratch dir is created fresh with `create_dir` (never reusing an
/// existing path): a predictable `/tmp/win-runtime-<pid>` that another user
/// pre-created or symlinked would otherwise be granted read-write.
fn build_rw_paths(
    wine_prefix: &str,
    runtime_dir: Option<&str>,
    uid: Option<u32>,
) -> Result<Vec<String>> {
    let tmp_dir = make_private_tmp(uid)?;

    let mut paths = vec![
        "/dev/null".to_string(),
        "/dev/full".to_string(),
        "/dev/zero".to_string(),
        tmp_dir,
    ];

    // Wine must write its prefix (registry, drive_c, wineserver lock).
    if !wine_prefix.is_empty() && Path::new(wine_prefix).exists() {
        paths.push(wine_prefix.to_string());
    }

    if let Some(runtime) = runtime_dir {
        if Path::new(runtime).exists() {
            paths.push(runtime.to_string());
        }
    }

    Ok(paths)
}

/// Create a fresh, private (0700) scratch dir under /tmp.
///
/// Runs under the caller's filesystem identity (the daemon enters the user's
/// fsuid for the whole launch), so the directory belongs to the user.
fn make_private_tmp(_uid: Option<u32>) -> Result<String> {
    use std::os::unix::fs::DirBuilderExt;
    let pid = std::process::id();
    for n in 0..1000u32 {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.subsec_nanos())
            .unwrap_or(0);
        let dir = format!(
            "/tmp/win-runtime-{pid}-{:08x}",
            nanos ^ n.wrapping_mul(2_654_435_761)
        );
        match std::fs::DirBuilder::new().mode(0o700).create(&dir) {
            Ok(()) => return Ok(dir),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e.into()),
        }
    }
    bail!("could not create a private scratch directory in /tmp")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ro_paths_include_system() {
        let args = Args {
            exe: Some("/home/test/game.exe".into()),
            tier: None,
            rules: None,
            verbose: false,
            no_gui: false,
            dry_run: false,
            gamepad: false,
            nested_x11: false,
            xvfb: false,
            host_x11: false,
            wayland: false,
            args: vec![],
            trust: false,
            optimize_net: false,
            cleanup_net: false,
            configure_net: false,
            daemon: false,
            status: false,
            reload: false,
            stop: false,
            unregister: false,
            print_seatbelt_profile: false,
            wine_prefix: None,
            user_env: std::collections::HashMap::new(),
            resolved_wineprefix: None,
            uid: None,
            exe_identity: None,
        };
        let paths = build_ro_paths("/home/test/.wine", &args);
        assert!(paths.contains(&"/usr".to_string()));
        assert!(paths.contains(&"/lib".to_string()));
        assert!(paths.contains(&"/home/test/.wine".to_string()));
        assert!(paths.contains(&"/home/test".to_string()));
    }

    #[test]
    fn ro_paths_exclude_root() {
        let args = Args {
            exe: Some("/game.exe".into()),
            tier: None,
            rules: None,
            verbose: false,
            no_gui: false,
            dry_run: false,
            gamepad: false,
            nested_x11: false,
            xvfb: false,
            host_x11: false,
            wayland: false,
            args: vec![],
            trust: false,
            optimize_net: false,
            cleanup_net: false,
            configure_net: false,
            daemon: false,
            status: false,
            reload: false,
            stop: false,
            unregister: false,
            print_seatbelt_profile: false,
            wine_prefix: None,
            user_env: std::collections::HashMap::new(),
            resolved_wineprefix: None,
            uid: None,
            exe_identity: None,
        };
        let paths = build_ro_paths("/tmp/.wine", &args);
        // "/" must NOT be in ro_paths (would grant read to entire root)
        assert!(!paths.contains(&"/".to_string()));
    }

    fn rw(prefix: &str, runtime: Option<&str>) -> Vec<String> {
        let paths = build_rw_paths(prefix, runtime, None).unwrap();
        for p in &paths {
            if p.starts_with("/tmp/win-runtime-") {
                let _ = std::fs::remove_dir(p);
            }
        }
        paths
    }

    #[test]
    fn rw_paths_include_fresh_private_tmp() {
        use std::os::unix::fs::PermissionsExt;
        let paths = build_rw_paths("", None, None).unwrap();
        let tmp = paths
            .iter()
            .find(|p| p.starts_with("/tmp/win-runtime-"))
            .expect("scratch dir");
        let mode = std::fs::metadata(tmp).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o700, "scratch dir must be private");
        let second = build_rw_paths("", None, None).unwrap();
        assert!(!second.contains(tmp), "each launch gets a new dir");
        for p in paths.iter().chain(second.iter()) {
            if p.starts_with("/tmp/win-runtime-") {
                let _ = std::fs::remove_dir(p);
            }
        }
    }

    /// The daemon runs as a systemd service with a near-empty environment, so
    /// XDG_RUNTIME_DIR must come from the (validated) session env. Without it,
    /// /run/user/<uid> is missing from the allowlist and Landlock denies
    /// wineserver its socket dir ("unable to create wineserver tmpdir").
    #[test]
    fn rw_paths_use_session_runtime_dir() {
        let paths = rw("", Some("/tmp"));
        assert!(
            paths.contains(&"/tmp".to_string()),
            "session XDG_RUNTIME_DIR must be granted RW, got {paths:?}"
        );
    }

    /// Wine writes its registry, drive_c and wineserver lock inside the prefix,
    /// so the resolved per-app prefix must be writable, not just readable.
    #[test]
    fn rw_paths_include_wine_prefix() {
        let tmp = tempfile::TempDir::new().unwrap();
        let prefix = tmp.path().to_str().unwrap();
        let paths = rw(prefix, None);
        assert!(
            paths.contains(&prefix.to_string()),
            "wine prefix must be RW, got {paths:?}"
        );
    }

    /// Non-existent paths must not be added: Landlock rejects rules for paths
    /// it cannot open, which would fail the whole ruleset.
    #[test]
    fn rw_paths_skip_missing_dirs() {
        let paths = rw("/nonexistent-prefix-xyz", Some("/nonexistent-runtime-xyz"));
        assert!(!paths.iter().any(|p| p.contains("nonexistent")));
    }
}
