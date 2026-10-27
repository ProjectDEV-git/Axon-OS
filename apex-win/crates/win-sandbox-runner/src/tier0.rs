use crate::Args;
use anyhow::Result;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};
use tracing::info;

/// Axon patch: the `wine` binary of the Proton/Wine build the user picked.
///
/// `AXON_WINE_DIR` (set by `axon-apex-run` from the user's own choice in
/// Game Center) names a runner directory such as
/// `~/.local/share/Steam/compatibilitytools.d/GE-Proton9-27/files`. It is read
/// from this process's environment, never from a daemon client, and only used
/// when `bin/wine` exists, is executable and is not group/world-writable.
/// Anything else falls back to the system `wine`.
fn runner_wine(dir: Option<&str>) -> Option<PathBuf> {
    use std::os::unix::fs::PermissionsExt;
    let wine = Path::new(dir?).join("bin").join("wine");
    let meta = std::fs::metadata(&wine).ok()?;
    let mode = meta.permissions().mode();
    if !meta.is_file() || mode & 0o111 == 0 || mode & 0o022 != 0 {
        return None;
    }
    Some(wine)
}

/// Tier 0: Direct wine execution with no sandboxing.
///
/// Sets up a sanitized environment (allowlisted vars only, no secrets)
/// and exec's wine directly.
#[allow(dead_code)]
pub fn run(args: &Args) -> Result<ExitCode> {
    run_with_env(args, &std::collections::HashMap::new())
}

/// Tier 0 with additional app-specific environment variables.
///
/// For trusted apps that need custom env (e.g. DXVK_HUD, MESA overrides).
/// App env vars are layered on top of the sanitized base environment.
///
/// `app_env` must come from a rule or app profile, never from the client:
/// app keys that would subvert the sanitizer (loader hooks, PATH, prefix
/// redirection) are dropped here regardless of source.
pub fn run_with_env(
    args: &Args,
    app_env: &std::collections::HashMap<String, String>,
) -> Result<ExitCode> {
    let exe = args.exe.as_deref().unwrap();
    info!("Tier 0: Direct wine execution for {exe}");

    let config = crate::config::load_config(None);
    let user_env = crate::env_sanitize::user_env_for_args(args);
    let sandbox_env = crate::env_sanitize::build_sandbox_env(
        &config,
        user_env,
        args.resolved_wineprefix.as_deref(),
    )?;

    let wine = runner_wine(std::env::var("AXON_WINE_DIR").ok().as_deref());
    if let Some(wine) = &wine {
        info!("Using runner {}", wine.display());
    }
    let mut cmd = Command::new(wine.as_deref().unwrap_or(Path::new("wine")));
    cmd.arg(exe);
    cmd.args(&args.args);

    // Clear inherited environment and apply only sanitized vars
    cmd.env_clear();
    for (key, val) in &sandbox_env {
        cmd.env(key, val);
    }
    // Layer app-specific env on top
    for (key, val) in app_env {
        if crate::env_sanitize::is_forbidden_app_env(key) {
            tracing::warn!("Ignoring app env override of {key}");
            continue;
        }
        cmd.env(key, val);
    }

    // Switch to target UID in the child process (daemon mode)
    if let Some(uid) = args.uid {
        unsafe { crate::daemon::configure_child_uid(&mut cmd, uid) };
    }

    crate::hasher::verify_unchanged(exe, args.exe_identity.as_ref())?;
    let status = cmd.status()?;
    if status.success() {
        Ok(ExitCode::SUCCESS)
    } else {
        Ok(ExitCode::from(status.code().unwrap_or(1) as u8))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    fn make_wine(root: &Path, mode: u32) {
        std::fs::create_dir_all(root.join("bin")).unwrap();
        let wine = root.join("bin").join("wine");
        std::fs::write(&wine, "#!/bin/sh\n").unwrap();
        std::fs::set_permissions(&wine, std::fs::Permissions::from_mode(mode)).unwrap();
    }

    #[test]
    fn runner_wine_accepts_executable_binary() {
        let dir = tempfile::tempdir().unwrap();
        make_wine(dir.path(), 0o755);
        let got = runner_wine(dir.path().to_str());
        assert_eq!(got, Some(dir.path().join("bin").join("wine")));
    }

    #[test]
    fn runner_wine_rejects_unset_missing_and_unsafe() {
        assert_eq!(runner_wine(None), None);
        assert_eq!(runner_wine(Some("/nonexistent/runner")), None);
        let writable = tempfile::tempdir().unwrap();
        make_wine(writable.path(), 0o777);
        assert_eq!(runner_wine(writable.path().to_str()), None);
        let not_exec = tempfile::tempdir().unwrap();
        make_wine(not_exec.path(), 0o644);
        assert_eq!(runner_wine(not_exec.path().to_str()), None);
    }
}
