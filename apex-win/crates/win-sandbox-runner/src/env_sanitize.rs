use crate::config::Config;
use anyhow::Result;
use std::collections::HashMap;
use tracing::debug;

/// Environment variables that are safe to forward into the sandbox.
const ALLOWED_ENV_VARS: &[&str] = &[
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_SESSION_TYPE",
    "HOME",
    "TERM",
    "USER",
    "LOGNAME",
    "WINEDEBUG",
    "WINEDLLOVERRIDES",
    "WINEPREFIX",
    "XDG_RUNTIME_DIR",
    "PULSE_SERVER",
    "PIPEWIRE_RUNTIME_DIR",
];

/// Environment variables that are explicitly stripped (security-sensitive).
const STRIPPED_ENV_VARS: &[&str] = &[
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "DBUS_SESSION_BUS_ADDRESS",
    "GPG_AGENT_INFO",
    "SSH_AUTH_SOCK",
    "SSH_AGENT_PID",
    "http_proxy",
    "https_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "no_proxy",
    "NO_PROXY",
];

/// Decide whether to treat this launch as daemon mode.
///
/// Daemon mode is `args.uid.is_some()`, **not** map emptiness: an empty
/// `user_env` must still be exclusive so root's process env cannot leak.
pub fn user_env_for_args(args: &crate::Args) -> Option<&HashMap<String, String>> {
    if args.uid.is_some() {
        Some(&args.user_env)
    } else if args.user_env.is_empty() {
        None
    } else {
        Some(&args.user_env)
    }
}

/// Build a sanitized environment for the sandboxed process.
///
/// Only allows explicitly whitelisted env vars through. Strips secrets,
/// proxy configs, and other sensitive data. Randomizes HOSTNAME.
///
/// When `user_env` is provided (daemon mode), the allowlist is taken
/// exclusively from that map — never from the daemon's process environment
/// (which is root under systemd). `WINEPREFIX` is never accepted from
/// `user_env`; pass the dispatch-resolved per-app prefix as
/// `resolved_wineprefix` instead of going through process env.
pub fn build_sandbox_env(
    config: &Config,
    user_env: Option<&HashMap<String, String>>,
    resolved_wineprefix: Option<&str>,
) -> Result<HashMap<String, String>> {
    let mut env = HashMap::new();

    match user_env {
        // Daemon mode: only what the invoking user sent. Do not seed from
        // the daemon process env (root's HOME/USER/DISPLAY would leak).
        // An empty map stays exclusive (no process-env fallback).
        Some(user) => {
            for &var in ALLOWED_ENV_VARS {
                if var == "WINEPREFIX" {
                    continue;
                }
                if let Some(val) = user.get(var) {
                    debug!("User env: {var}");
                    env.insert(var.to_string(), val.clone());
                }
            }
        }
        // Direct CLI: allowlisted vars from the current process.
        None => {
            for &var in ALLOWED_ENV_VARS {
                if let Ok(val) = std::env::var(var) {
                    debug!("Forwarding env: {var}");
                    env.insert(var.to_string(), val);
                }
            }
        }
    }

    // WINEPREFIX resolution order (most specific wins):
    //   1. resolved_wineprefix (dispatch-resolved per-app prefix)
    //   2. process env (direct CLI / legacy)
    //   3. config default (~/.wine)
    // Never take WINEPREFIX from user_env — that would let a FIFO writer
    // redirect Wine into an arbitrary prefix.
    if let Some(prefix) = resolved_wineprefix {
        env.insert("WINEPREFIX".to_string(), prefix.to_string());
    } else if !env.contains_key("WINEPREFIX") {
        if let Ok(val) = std::env::var("WINEPREFIX") {
            env.insert("WINEPREFIX".to_string(), val);
        } else {
            env.insert("WINEPREFIX".to_string(), config.wine_prefix.clone());
        }
    }

    // Set sanitized PATH
    env.insert("PATH".to_string(), build_sanitized_path());

    // Randomize HOSTNAME to prevent fingerprinting
    env.insert("HOSTNAME".to_string(), random_hostname());

    // Set recursion guard
    env.insert("WIN_SANDBOX_ACTIVE".to_string(), "1".to_string());

    // Verify stripped vars are not present
    for &var in STRIPPED_ENV_VARS {
        if env.contains_key(var) {
            env.remove(var);
            debug!("Stripped sensitive env: {var}");
        }
    }

    Ok(env)
}

/// Build a sanitized PATH that only keeps exact standard system directories.
fn build_sanitized_path() -> String {
    const ALLOWED: &[&str] = &["/usr/bin", "/bin", "/usr/sbin", "/sbin"];
    let default = "/usr/bin:/bin:/usr/sbin:/sbin".to_string();
    if let Ok(current) = std::env::var("PATH") {
        // Keep only whole-directory matches (not string prefixes like /usr/binevil).
        let safe: Vec<&str> = current.split(':').filter(|p| ALLOWED.contains(p)).collect();
        if safe.is_empty() {
            default
        } else {
            safe.join(":")
        }
    } else {
        default
    }
}

/// Generate a random 12-hex-digit hostname.
fn random_hostname() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    format!("winrun-{:012x}", nanos & 0xfffffffffff)
}

/// Session variable for this launch: the (validated) user env in daemon
/// mode, the process env for direct CLI use.
///
/// This is the single accessor tiers, display, and audio detection must use.
/// Reading `std::env::var` directly is wrong under the daemon: that is root's
/// systemd environment (no DISPLAY, no XDG_RUNTIME_DIR), which is how display
/// and audio silently went missing for every daemon launch.
#[cfg(target_os = "linux")]
pub fn session_var(args: &crate::Args, key: &str) -> Option<String> {
    let v = match user_env_for_args(args) {
        Some(env) => env.get(key).cloned(),
        None => std::env::var(key).ok(),
    };
    v.filter(|s| !s.is_empty())
}

/// The per-app prefix dispatch resolved, else the config default.
///
/// Tiers previously read `WINEPREFIX` from the process env, which dispatch
/// stopped setting (it stashes the prefix on `Args` so concurrent daemon
/// launches cannot race). They therefore granted/bound `~/.wine` of root or
/// of the CLI user instead of the app's own prefix.
pub fn resolved_prefix(args: &crate::Args, config: &Config) -> String {
    if let Some(p) = args.resolved_wineprefix.as_deref() {
        return p.to_string();
    }
    if args.uid.is_none() {
        if let Ok(p) = std::env::var("WINEPREFIX") {
            return p;
        }
    }
    config.wine_prefix.clone()
}

/// Validate client-forwarded session variables against the peer's real
/// identity. Anything that fails is dropped (the launch continues without
/// it) rather than trusted.
///
/// * `XDG_RUNTIME_DIR` must be exactly `/run/user/<uid>`, owned by the uid.
/// * `WAYLAND_DISPLAY` must be a plain socket name (no `/`, no `..`), so the
///   compositor socket resolves inside that runtime dir.
/// * `XAUTHORITY` must be an absolute file owned by the uid, checked with the
///   user's filesystem rights (root must not become a read oracle for others'
///   cookies).
/// * `DISPLAY` must look like `[host]:N[.S]`.
/// * `HOME`/`USER`/`LOGNAME` are always taken from passwd, never the client.
pub fn validate_daemon_env(
    raw: &HashMap<String, String>,
    ids: &crate::privs::UserIds,
) -> HashMap<String, String> {
    use std::os::unix::fs::MetadataExt;
    let mut out = HashMap::new();
    let clean = |v: &str| !v.is_empty() && v.len() <= 4096 && !v.contains(['\0', '\n']);

    let owned_by_user = |p: &str| -> bool {
        let _g = crate::privs::FsIdGuard::enter(ids.uid, ids.gid);
        std::fs::metadata(p)
            .map(|m| m.uid() == ids.uid)
            .unwrap_or(false)
    };

    let runtime = format!("/run/user/{}", ids.uid);
    if raw.get("XDG_RUNTIME_DIR").map(String::as_str) == Some(runtime.as_str())
        && owned_by_user(&runtime)
    {
        out.insert("XDG_RUNTIME_DIR".into(), runtime.clone());
    }
    if let Some(v) = raw.get("WAYLAND_DISPLAY") {
        let ok = clean(v) && !v.contains('/') && v != "." && v != ".." && v.len() <= 108;
        if ok && out.contains_key("XDG_RUNTIME_DIR") {
            out.insert("WAYLAND_DISPLAY".into(), v.clone());
        }
    }
    if let Some(v) = raw.get("DISPLAY") {
        if clean(v) && is_valid_x_display(v) {
            out.insert("DISPLAY".into(), v.clone());
        }
    }
    if let Some(v) = raw.get("XAUTHORITY") {
        if clean(v) && v.starts_with('/') && !v.contains("/../") && owned_by_user(v) {
            out.insert("XAUTHORITY".into(), v.clone());
        }
    }
    if let Some(v) = raw.get("XDG_SESSION_TYPE") {
        if matches!(v.as_str(), "x11" | "wayland" | "tty" | "mir") {
            out.insert("XDG_SESSION_TYPE".into(), v.clone());
        }
    }
    for key in ["TERM", "WINEDEBUG"] {
        if let Some(v) = raw.get(key) {
            if clean(v) && v.len() <= 256 && v.bytes().all(|b| b.is_ascii_graphic()) {
                out.insert(key.into(), v.clone());
            }
        }
    }
    out.insert("HOME".into(), ids.home.display().to_string());
    out.insert("USER".into(), ids.name.clone());
    out.insert("LOGNAME".into(), ids.name.clone());
    out
}

/// `[host]:display[.screen]` with numeric display/screen.
fn is_valid_x_display(v: &str) -> bool {
    let Some((host, rest)) = v.rsplit_once(':') else {
        return false;
    };
    if host.contains('/') && !host.starts_with("unix") {
        return false;
    }
    let (d, s) = rest.split_once('.').unwrap_or((rest, "0"));
    !d.is_empty() && d.bytes().all(|b| b.is_ascii_digit()) && s.bytes().all(|b| b.is_ascii_digit())
}

/// App-profile env keys that must never override the sanitized environment.
///
/// Rules and profiles legitimately set `DXVK_HUD`, `MESA_*`, `WINEDLLOVERRIDES`
/// and similar. They must not be able to inject code into the loader, swap
/// the binary search path, or re-point the prefix / identity / recursion
/// guard.
pub fn is_forbidden_app_env(key: &str) -> bool {
    key.starts_with("LD_")
        || key.starts_with("DYLD_")
        || matches!(
            key,
            "PATH"
                | "WINEPREFIX"
                | "WINESERVER"
                | "WINELOADER"
                | "WINEDLLPATH"
                | "WIN_SANDBOX_ACTIVE"
                | "HOME"
                | "USER"
                | "LOGNAME"
                | "XDG_RUNTIME_DIR"
                | "XAUTHORITY"
                | "SHELL"
                | "IFS"
                | "BASH_ENV"
                | "ENV"
        )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    fn me() -> crate::privs::UserIds {
        crate::privs::UserIds::lookup(unsafe { libc::getuid() }).unwrap()
    }

    #[test]
    fn validate_env_takes_identity_from_passwd_not_client() {
        let ids = me();
        let mut raw = HashMap::new();
        raw.insert("HOME".into(), "/tmp/evil".into());
        raw.insert("USER".into(), "root".into());
        let env = validate_daemon_env(&raw, &ids);
        assert_eq!(env["HOME"], ids.home.display().to_string());
        assert_eq!(env["USER"], ids.name);
    }

    #[test]
    fn validate_env_rejects_foreign_runtime_dir_and_bad_wayland() {
        let ids = me();
        let mut raw = HashMap::new();
        raw.insert("XDG_RUNTIME_DIR".into(), "/run/user/0".into());
        raw.insert("WAYLAND_DISPLAY".into(), "../0/wayland-0".into());
        raw.insert("DISPLAY".into(), ":0; rm -rf /".into());
        raw.insert("XAUTHORITY".into(), "/etc/shadow".into());
        raw.insert("XDG_SESSION_TYPE".into(), "evil".into());
        let env = validate_daemon_env(&raw, &ids);
        assert!(!env.contains_key("XDG_RUNTIME_DIR"));
        assert!(!env.contains_key("WAYLAND_DISPLAY"));
        assert!(!env.contains_key("DISPLAY"));
        assert!(!env.contains_key("XAUTHORITY"), "not owned by the user");
        assert!(!env.contains_key("XDG_SESSION_TYPE"));
    }

    #[test]
    fn validate_env_accepts_real_session_values() {
        let ids = me();
        let dir = tempfile::tempdir().unwrap();
        let xauth = dir.path().join("Xauthority");
        std::fs::write(&xauth, b"cookie").unwrap();
        let mut raw = HashMap::new();
        raw.insert("DISPLAY".into(), ":1".into());
        raw.insert("XAUTHORITY".into(), xauth.display().to_string());
        raw.insert("XDG_SESSION_TYPE".into(), "x11".into());
        let runtime = format!("/run/user/{}", ids.uid);
        raw.insert("XDG_RUNTIME_DIR".into(), runtime.clone());
        raw.insert("WAYLAND_DISPLAY".into(), "wayland-0".into());
        let env = validate_daemon_env(&raw, &ids);
        assert_eq!(env["DISPLAY"], ":1");
        assert_eq!(env["XAUTHORITY"], xauth.display().to_string());
        if std::path::Path::new(&runtime).exists() {
            assert_eq!(env["XDG_RUNTIME_DIR"], runtime);
            assert_eq!(env["WAYLAND_DISPLAY"], "wayland-0");
        }
    }

    #[test]
    fn x_display_syntax() {
        for ok in [":0", ":10.0", "localhost:1", "unix:0"] {
            assert!(is_valid_x_display(ok), "{ok}");
        }
        for bad in ["", "0", ":", ":a", ":0.x", "/tmp/x:0"] {
            assert!(!is_valid_x_display(bad), "{bad}");
        }
    }

    #[test]
    fn forbidden_app_env_keys() {
        for k in [
            "LD_PRELOAD",
            "LD_LIBRARY_PATH",
            "PATH",
            "WINEPREFIX",
            "WINELOADER",
        ] {
            assert!(is_forbidden_app_env(k), "{k}");
        }
        for k in ["DXVK_HUD", "MESA_GL_VERSION_OVERRIDE", "WINEDLLOVERRIDES"] {
            assert!(!is_forbidden_app_env(k), "{k}");
        }
    }

    /// Serializes tests that mutate process-global environment variables.
    /// Rust runs tests in parallel threads that share one environment, so
    /// without this two env-mutating tests can observe each other's writes.
    static ENV_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn sanitized_path_contains_system() {
        let path = build_sanitized_path();
        assert!(path.contains("/usr/bin"));
    }

    /// PATH allowlist must match whole path components, not string prefixes
    /// (`/usr/binevil` and `/binbackdoor` must not survive sanitization).
    #[test]
    fn sanitized_path_rejects_prefix_false_matches() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let saved = std::env::var("PATH").ok();
        std::env::set_var("PATH", "/usr/binevil:/binbackdoor:/usr/bin:/bin:/tmp/evil");
        let path = build_sanitized_path();
        assert!(path.contains("/usr/bin"));
        assert!(path.split(':').any(|p| p == "/bin"));
        assert!(!path.contains("/usr/binevil"));
        assert!(!path.contains("/binbackdoor"));
        assert!(!path.contains("/tmp/evil"));
        match saved {
            Some(v) => std::env::set_var("PATH", v),
            None => std::env::remove_var("PATH"),
        }
    }

    #[test]
    fn random_hostname_format() {
        let host = random_hostname();
        assert!(host.starts_with("winrun-"));
        assert_eq!(host.len(), 19); // "winrun-" + 12 hex chars
    }

    #[test]
    fn build_sandbox_env_has_wineprefix() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let config = Config::default();
        let env = build_sandbox_env(&config, None, None).unwrap();
        assert!(env.contains_key("WINEPREFIX"));
        assert!(env.contains_key("WIN_SANDBOX_ACTIVE"));
        assert!(env.contains_key("HOSTNAME"));
    }

    #[test]
    fn build_sandbox_env_strips_secrets() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        // Set a secret env var
        std::env::set_var("AWS_SECRET_ACCESS_KEY", "supersecret");
        let config = Config::default();
        let env = build_sandbox_env(&config, None, None).unwrap();
        assert!(!env.contains_key("AWS_SECRET_ACCESS_KEY"));
        std::env::remove_var("AWS_SECRET_ACCESS_KEY");
    }

    #[test]
    fn build_sandbox_env_merges_user_env() {
        let config = Config::default();
        let mut user_env = HashMap::new();
        user_env.insert("DISPLAY".to_string(), ":99".to_string());
        user_env.insert("WAYLAND_DISPLAY".to_string(), "wayland-1".to_string());
        let env = build_sandbox_env(&config, Some(&user_env), None).unwrap();
        assert_eq!(env.get("DISPLAY").unwrap(), ":99");
        assert_eq!(env.get("WAYLAND_DISPLAY").unwrap(), "wayland-1");
    }

    #[test]
    fn build_sandbox_env_user_env_only_allowed() {
        let config = Config::default();
        let mut user_env = HashMap::new();
        user_env.insert("DISPLAY".to_string(), ":0".to_string());
        user_env.insert("SECRET_VAR".to_string(), "should_not_pass".to_string());
        let env = build_sandbox_env(&config, Some(&user_env), None).unwrap();
        assert_eq!(env.get("DISPLAY").unwrap(), ":0");
        assert!(!env.contains_key("SECRET_VAR"));
    }

    /// X11 sessions need XAUTHORITY or Wine cannot open the display.
    /// GDM stores the cookie at /run/user/<uid>/gdm/Xauthority with no
    /// ~/.Xauthority fallback, so dropping this var breaks every launch.
    #[test]
    fn build_sandbox_env_forwards_xauthority() {
        let config = Config::default();
        let mut user_env = HashMap::new();
        user_env.insert(
            "XAUTHORITY".to_string(),
            "/run/user/1000/gdm/Xauthority".to_string(),
        );
        user_env.insert("XDG_SESSION_TYPE".to_string(), "x11".to_string());
        let env = build_sandbox_env(&config, Some(&user_env), None).unwrap();
        assert_eq!(
            env.get("XAUTHORITY").unwrap(),
            "/run/user/1000/gdm/Xauthority",
            "XAUTHORITY must reach Wine or X11 auth fails"
        );
        assert_eq!(env.get("XDG_SESSION_TYPE").unwrap(), "x11");
    }

    /// dispatch::execute resolves a per-app WINEPREFIX and exports it before
    /// calling into a tier. The sanitizer must not overwrite it with the
    /// config default, or every app collapses into one shared prefix.
    #[test]
    fn build_sandbox_env_preserves_per_app_wineprefix() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let per_app = "/home/u/.local/share/win-sandbox/prefixes/deadbeef/prefix";
        std::env::set_var("WINEPREFIX", per_app);

        let config = Config {
            wine_prefix: "/home/u/.wine".to_string(),
            ..Config::default()
        };

        let env = build_sandbox_env(&config, None, Some(per_app)).unwrap();
        assert_eq!(
            env.get("WINEPREFIX").unwrap(),
            per_app,
            "per-app prefix must survive sanitization"
        );

        std::env::remove_var("WINEPREFIX");
    }

    /// With nothing pre-set, the config default is still applied.
    #[test]
    fn build_sandbox_env_falls_back_to_config_wineprefix() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        std::env::remove_var("WINEPREFIX");
        let config = Config {
            wine_prefix: "/home/u/.wine".to_string(),
            ..Config::default()
        };
        let env = build_sandbox_env(&config, None, None).unwrap();
        assert_eq!(env.get("WINEPREFIX").unwrap(), "/home/u/.wine");
    }

    /// Daemon mode must not seed the sandbox from root's process env.
    /// Only allowlisted keys present in user_env may appear.
    #[test]
    fn build_sandbox_env_user_env_does_not_leak_process_env() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        std::env::set_var("HOME", "/root");
        std::env::set_var("USER", "root");
        std::env::remove_var("WINEPREFIX");

        let config = Config::default();
        let mut user_env = HashMap::new();
        user_env.insert("DISPLAY".to_string(), ":0".to_string());

        let env = build_sandbox_env(&config, Some(&user_env), None).unwrap();
        assert_eq!(env.get("DISPLAY").unwrap(), ":0");
        assert_ne!(
            env.get("HOME").map(String::as_str),
            Some("/root"),
            "process HOME must not leak when user_env is provided"
        );
        assert!(
            !env.contains_key("USER") || env.get("USER").map(String::as_str) != Some("root"),
            "process USER must not leak when user_env is provided"
        );

        std::env::remove_var("HOME");
        std::env::remove_var("USER");
    }

    /// A client-supplied WINEPREFIX must never clobber the per-app prefix
    /// dispatch already resolved into the process environment.
    #[test]
    fn build_sandbox_env_ignores_user_env_wineprefix() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let per_app = "/home/u/.local/share/win-sandbox/prefixes/deadbeef/prefix";
        std::env::set_var("WINEPREFIX", per_app);

        let config = Config {
            wine_prefix: "/home/u/.wine".to_string(),
            ..Config::default()
        };
        let mut user_env = HashMap::new();
        user_env.insert("WINEPREFIX".to_string(), "/tmp/evil-prefix".to_string());

        let env = build_sandbox_env(&config, Some(&user_env), Some(per_app)).unwrap();
        assert_eq!(
            env.get("WINEPREFIX").unwrap(),
            per_app,
            "user_env WINEPREFIX must not clobber the resolved per-app prefix"
        );

        std::env::remove_var("WINEPREFIX");
    }

    /// Empty user_env in daemon mode (uid set) must not fall back to process
    /// env — root's HOME/USER would leak into the sandbox.
    #[test]
    fn empty_user_env_stays_exclusive_in_daemon_mode() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        std::env::set_var("HOME", "/root");
        std::env::set_var("USER", "root");
        std::env::remove_var("WINEPREFIX");

        let config = Config::default();
        let empty = HashMap::new();
        let env = build_sandbox_env(&config, Some(&empty), None).unwrap();
        assert!(
            env.get("HOME").map(String::as_str) != Some("/root"),
            "empty user_env must not seed HOME from process env"
        );
        assert!(env.get("USER").map(String::as_str) != Some("root"));

        std::env::remove_var("HOME");
        std::env::remove_var("USER");
    }
}
