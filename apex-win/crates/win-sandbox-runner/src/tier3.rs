use crate::Args;
use anyhow::{bail, Result};
use std::process::{Command, ExitCode};
use tracing::{info, warn};

/// Tier 3: OverlayFS + RAM ephemeral sandbox.
#[allow(dead_code)]
pub fn run(args: &Args) -> Result<ExitCode> {
    run_with_network(args, false, true)
}

/// Removes the overlay on every exit path of `run_with_network` (error
/// returns, panics, normal return). The global registry additionally covers
/// process exit, and the daemon's startup sweep covers SIGKILL.
struct OverlayGuard(std::path::PathBuf);

impl Drop for OverlayGuard {
    fn drop(&mut self) {
        crate::cleanup::release(&self.0);
    }
}

/// Tier 3: OverlayFS + RAM ephemeral sandbox with explicit network control.
///
/// When `network` is true, starts the TAP bridge and sets up Wine DLL
/// environment for isolated networking through the overlay.
/// `explicit` preserves dispatch's policy provenance through runtime failures:
/// only heuristic selections may fall back to a persistent Tier 2 session.
pub fn run_with_network(args: &Args, network: bool, explicit: bool) -> Result<ExitCode> {
    let exe = args.exe.as_deref().unwrap();
    info!("Tier 3: OverlayFS ephemeral sandbox for {exe} (network={network})");

    let config = crate::config::load_config(None);
    let dirs = create_overlay_dirs()?;
    let base = std::path::PathBuf::from(&dirs.base);
    crate::cleanup::register(&base);
    let guard = OverlayGuard(base);

    // The lowerdir is the per-app prefix dispatch resolved (carried on Args,
    // not process env; see env_sanitize::resolved_prefix).
    let resolved_prefix = crate::env_sanitize::resolved_prefix(args, &config);
    let lower_dir = resolve_lower_dir(&resolved_prefix, &dirs.base)?;
    // mount(8) runs as root. In daemon mode the lowerdir comes from the
    // user's home, so resolve symlinks and require it to be the user's own
    // directory: otherwise root would overlay-mount an arbitrary tree.
    let lower_dir = if let Some(uid) = args.uid {
        use std::os::unix::fs::MetadataExt;
        let canon = std::fs::canonicalize(&lower_dir)?;
        let meta = std::fs::metadata(&canon)?;
        if !meta.is_dir() || meta.uid() != uid {
            bail!(
                "refusing Tier 3: overlay lower dir {} is not a directory owned by uid {uid}",
                canon.display()
            );
        }
        canon.to_string_lossy().into_owned()
    } else {
        lower_dir
    };
    if [lower_dir.as_str(), dirs.upper.as_str(), dirs.work.as_str()]
        .iter()
        .any(|p| p.contains([',', ':', '\\']))
    {
        bail!("overlay paths may not contain ',', ':' or '\\' (mount option injection)");
    }

    // Mount OverlayFS
    let opts = mount_opts(&lower_dir, &dirs.upper, &dirs.work);
    info!(
        "Mounting OverlayFS: lower={lower_dir}, upper={}",
        dirs.upper
    );

    let output = Command::new("mount")
        .args(["-t", "overlay", "overlay", "-o", &opts, &dirs.merged])
        .output();

    match output {
        Ok(o) if o.status.success() => {
            info!("OverlayFS mounted at {}", dirs.merged);
        }
        Ok(o) => {
            // Preflight is not proof that mount will work. Preserve the
            // requested guarantee even if setup fails after preflight passed.
            let stderr = String::from_utf8_lossy(&o.stderr);
            drop(guard);
            if allow_mount_fallback(explicit, &stderr)? {
                warn!(
                    "OverlayFS needs root (mount: {}). Falling back to Tier 2 \
                     (bubblewrap namespace isolation, no ephemeral overlay).",
                    stderr.trim()
                );
                return crate::tier2::run_with_network(args, network);
            }
            bail!(
                "OverlayFS mount failed with status: {} ({})",
                o.status,
                stderr.trim()
            );
        }
        Err(e) => {
            drop(guard);
            bail!("Failed to execute mount: {e}; refusing Tier 3 (use --tier 2 to choose persistent storage)");
        }
    }

    // Resolve display mode and launch nested display server if needed
    let session_wayland = crate::env_sanitize::session_var(args, "WAYLAND_DISPLAY");
    let display_mode = crate::display::resolve_display_mode(
        args.nested_x11,
        args.xvfb,
        args.host_x11,
        args.wayland,
        3, // Tier 3
        session_wayland.is_some(),
    );
    crate::display::warn_display_security(&display_mode);

    // Launch Xephyr/Xvfb if needed (kept alive by DisplayHandle Drop guard)
    let _display_handle: Option<crate::display::DisplayHandle> = match display_mode {
        crate::display::DisplayMode::NestedX11 => crate::display::launch_nested_x11(args),
        crate::display::DisplayMode::Xvfb => crate::display::launch_xvfb(args),
        _ => None,
    };

    // Exec wine with WINEPREFIX pointing to the merged overlay
    info!("Launching wine in OverlayFS sandbox (changes lost on exit)");

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
    // Override WINEPREFIX to point at the overlay merged dir
    cmd.env("WINEPREFIX", &dirs.merged);

    // Apply display environment based on resolved mode
    apply_display_for_mode(&mut cmd, args, &display_mode, _display_handle.as_ref());

    // Networking: TAP bridge via Wine DLL
    if network {
        if let Err(e) = crate::net::ensure_bridge_running(Some(&config.tap_bridge_socket)) {
            warn!("Failed to start TAP bridge: {e}");
            warn!("Continuing without bridge networking");
        } else {
            if let Some(dll_dir) = crate::net::find_dll_path() {
                if let Some(parent) = dll_dir.parent() {
                    cmd.env("WINEDLLPATH", parent.display().to_string());
                }
            }
            cmd.env("WINE_BRIDGE_SOCKET", &config.tap_bridge_socket);
            info!("Networking enabled via TAP bridge for OverlayFS sandbox");
        }
    }

    // Switch to target UID in the child process (daemon mode)
    if let Some(uid) = args.uid {
        unsafe { crate::daemon::configure_child_uid(&mut cmd, uid) };
    }

    crate::hasher::verify_unchanged(exe, args.exe_identity.as_ref())?;

    // Daemon mode (uid set): use status() so the daemon process survives.
    // CLI mode: fork+wait too. exec() would replace this process, so no Rust
    // code (not the guard, not atexit) would ever unmount the overlay.
    let status = cmd.status()?;
    drop(guard);
    let code = status.code().unwrap_or(1);
    if args.uid.is_some() && code != 0 {
        bail!("Wine exited with status: {code}");
    }
    Ok(ExitCode::from(code.clamp(0, 255) as u8))
}

/// Apply display environment variables based on the resolved display mode.
fn apply_display_for_mode(
    cmd: &mut Command,
    args: &Args,
    mode: &crate::display::DisplayMode,
    display_handle: Option<&crate::display::DisplayHandle>,
) {
    let session = |k: &str| crate::env_sanitize::session_var(args, k);
    match mode {
        crate::display::DisplayMode::NestedX11 | crate::display::DisplayMode::Xvfb => {
            if let Some(handle) = display_handle {
                cmd.env("DISPLAY", &handle.display);
                info!("Sandbox DISPLAY={}", handle.display);
            } else if let Some(display) = session("DISPLAY") {
                // Fallback: the user's display (not ideal but works)
                cmd.env("DISPLAY", display);
            }
        }
        crate::display::DisplayMode::Wayland => {
            if let Some(wl) = session("WAYLAND_DISPLAY") {
                cmd.env("WAYLAND_DISPLAY", &wl);
                cmd.env("WINE_WAYLAND_DRIVER", "1");
                info!("Sandbox WAYLAND_DISPLAY={wl}");
            }
        }
        crate::display::DisplayMode::HostX11 => {
            if let Some(display) = session("DISPLAY") {
                cmd.env("DISPLAY", display);
            }
        }
    }
}

/// Decide fallback without launching processes, so runtime refusal is testable.
fn allow_mount_fallback(explicit: bool, stderr: &str) -> Result<bool> {
    if explicit {
        bail!(
            "Tier 3 was explicitly requested; refusing to fall back to Tier 2 after \
             overlay mount failure: {}",
            stderr.trim()
        );
    }
    Ok(stderr.contains("must be superuser") || stderr.contains("Operation not permitted"))
}

/// Paths for an overlay instance.
#[derive(Debug, Clone)]
pub struct OverlayDirs {
    pub base: String,
    pub upper: String,
    pub work: String,
    pub merged: String,
}

/// Compute overlay directory paths for a given PID and sequence number.
pub fn overlay_dirs(pid: u32, seq: u32) -> OverlayDirs {
    let base = format!(
        "{}/{}{pid}-{seq}",
        crate::cleanup::OVERLAY_ROOT,
        crate::cleanup::OVERLAY_PREFIX
    );
    OverlayDirs {
        upper: format!("{base}/upper"),
        work: format!("{base}/work"),
        merged: format!("{base}/merged"),
        base,
    }
}

/// Create a fresh overlay directory tree for this launch.
///
/// The base is created with `mkdir` (0700) and must not already exist:
/// `/dev/shm` is world-writable, and the old fixed `win-run-<pid>` name plus
/// `create_dir_all` meant any user could pre-create it (or a symlink) and
/// have root mount over / later `remove_dir_all` a path of their choosing.
/// A per-process sequence number keeps concurrent daemon launches apart
/// (they share one pid).
fn create_overlay_dirs() -> Result<OverlayDirs> {
    use std::os::unix::fs::DirBuilderExt;
    use std::sync::atomic::{AtomicU32, Ordering};
    static SEQ: AtomicU32 = AtomicU32::new(0);
    let pid = std::process::id();
    for _ in 0..64 {
        let dirs = overlay_dirs(pid, SEQ.fetch_add(1, Ordering::Relaxed));
        match std::fs::DirBuilder::new().mode(0o700).create(&dirs.base) {
            Ok(()) => {
                for d in [&dirs.upper, &dirs.work, &dirs.merged] {
                    std::fs::DirBuilder::new().mode(0o700).create(d)?;
                }
                return Ok(dirs);
            }
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e.into()),
        }
    }
    bail!("could not create a fresh overlay directory under /dev/shm")
}

/// Build the mount options string for overlayfs.
pub fn mount_opts(lower: &str, upper: &str, work: &str) -> String {
    format!("lowerdir={lower},upperdir={upper},workdir={work}")
}

/// Resolve the lower directory: use existing wine prefix, or create a stub.
fn resolve_lower_dir(wine_prefix: &str, base_dir: &str) -> Result<String> {
    if std::path::Path::new(wine_prefix).exists() {
        Ok(wine_prefix.to_string())
    } else {
        let stub = format!("{base_dir}/lower");
        std::fs::create_dir_all(&stub)?;
        Ok(stub)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Exercise the real launch entrypoint with a failed mount, not just the
    /// decision helper. A child test process isolates PATH and WINEPREFIX from
    /// other tests; its PATH contains only our stub, so Wine cannot be launched.
    #[test]
    fn explicit_runtime_mount_failure_refuses() {
        const CHILD_ROOT: &str = "APEX_TIER3_RUNTIME_TEST_ROOT";
        if let Some(root) = std::env::var_os(CHILD_ROOT) {
            use clap::Parser;
            let root = std::path::PathBuf::from(root);
            let args = Args::parse_from([
                "win-sandbox-runner",
                "--exe",
                "/unused/test.exe",
                "--tier",
                "3",
                "--no-gui",
            ]);
            let error = run_with_network(&args, false, true).unwrap_err();
            assert!(root.join("mount-called").exists(), "stub mount must run");
            let message = error.to_string();
            assert!(message.contains("explicitly requested"), "{message}");
            assert!(message.contains("Operation not permitted"), "{message}");
            assert!(message.contains("refusing to fall back"), "{message}");
            let leftovers = std::fs::read_dir(crate::cleanup::OVERLAY_ROOT)
                .unwrap()
                .flatten()
                .filter(|e| {
                    crate::cleanup::owner_pid(&e.file_name().to_string_lossy())
                        == Some(std::process::id())
                })
                .count();
            assert_eq!(leftovers, 0, "failed mount must not leave overlay dirs");
            return;
        }

        use std::os::unix::fs::PermissionsExt;
        let root = tempfile::tempdir().unwrap();
        let bin = root.path().join("bin");
        let prefix = root.path().join("prefix");
        std::fs::create_dir(&bin).unwrap();
        std::fs::create_dir(&prefix).unwrap();
        let mount = bin.join("mount");
        std::fs::write(
            &mount,
            "#!/bin/sh\n: > \"$APEX_TIER3_RUNTIME_TEST_ROOT/mount-called\"\nprintf 'mount: Operation not permitted\\n' >&2\nexit 1\n",
        )
        .unwrap();
        std::fs::set_permissions(&mount, std::fs::Permissions::from_mode(0o700)).unwrap();
        let output = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "tier3::tests::explicit_runtime_mount_failure_refuses",
                "--nocapture",
            ])
            .env(CHILD_ROOT, root.path())
            .env("PATH", &bin)
            .env("HOME", root.path())
            .env("XDG_CONFIG_HOME", root.path())
            .env("WINEPREFIX", &prefix)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "child test failed:\n{}\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(root.path().join("mount-called").exists());
    }

    #[test]
    fn explicit_mount_failure_never_allows_fallback() {
        for message in [
            "mount: must be superuser to use mount",
            "mount: Operation not permitted",
            "mount: wrong fs type",
        ] {
            let error = allow_mount_fallback(true, message).unwrap_err();
            assert!(error.to_string().contains("explicitly requested"));
            assert!(error.to_string().contains(message));
        }
    }

    #[test]
    fn heuristic_privilege_failure_allows_fallback() {
        for message in [
            "mount: must be superuser to use mount",
            "mount: Operation not permitted",
        ] {
            assert!(allow_mount_fallback(false, message).unwrap());
        }
    }

    #[test]
    fn heuristic_unrelated_mount_failure_does_not_allow_fallback() {
        assert!(!allow_mount_fallback(false, "mount: wrong fs type").unwrap());
    }

    #[test]
    fn overlay_dirs_paths() {
        let dirs = overlay_dirs(12345, 7);
        assert_eq!(dirs.base, "/dev/shm/win-run-12345-7");
        assert_eq!(dirs.upper, "/dev/shm/win-run-12345-7/upper");
        assert_eq!(dirs.work, "/dev/shm/win-run-12345-7/work");
        assert_eq!(dirs.merged, "/dev/shm/win-run-12345-7/merged");
        assert_eq!(crate::cleanup::owner_pid("win-run-12345-7"), Some(12345));
    }

    /// Concurrent launches in one process get distinct, fresh, private
    /// directories; a pre-existing (squatted) name is skipped, never reused.
    #[test]
    fn create_overlay_dirs_is_fresh_and_private() {
        use std::os::unix::fs::PermissionsExt;
        let a = create_overlay_dirs().unwrap();
        let b = create_overlay_dirs().unwrap();
        assert_ne!(a.base, b.base);
        let mode = std::fs::metadata(&a.base).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o700);
        for d in [&a, &b] {
            crate::cleanup::cleanup_overlay_base(std::path::Path::new(&d.base));
        }
    }

    #[test]
    fn mount_opts_format() {
        let opts = mount_opts("/home/user/.wine", "/tmp/upper", "/tmp/work");
        assert_eq!(
            opts,
            "lowerdir=/home/user/.wine,upperdir=/tmp/upper,workdir=/tmp/work"
        );
    }

    #[test]
    fn resolve_lower_with_existing_prefix() {
        let result = resolve_lower_dir("/usr", "/tmp/test-overlay");
        assert!(result.is_ok());
        assert_eq!(result.unwrap(), "/usr");
    }

    #[test]
    fn resolve_lower_with_missing_prefix() {
        let result = resolve_lower_dir("/nonexistent/wine/prefix", "/tmp/win-run-test-nonexistent");
        assert!(result.is_ok());
        assert!(result.unwrap().contains("lower"));
        let _ = std::fs::remove_dir_all("/tmp/win-run-test-nonexistent");
    }
}
