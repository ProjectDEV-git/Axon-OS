use crate::Args;
use anyhow::Result;
use std::process::{Command, ExitCode};
use tracing::info;

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

    let mut cmd = Command::new("wine");
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
