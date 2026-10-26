use tracing::{debug, info, warn};

/// Detected display server configuration.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DisplayServer {
    /// Wayland compositor (native).
    Wayland { display: String },
    /// X11 server.
    X11 { display: String },
    /// Both Wayland and X11 (XWayland) — prefer X11 for Wine compatibility.
    XWayland {
        wayland_display: String,
        x11_display: String,
    },
    /// No display available (headless).
    Headless,
}

/// Display mode configuration for the sandbox.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DisplayMode {
    /// Use host X11 directly (DANGEROUS — keylogger vector).
    HostX11,
    /// Launch nested X11 via Xephyr (default for Tier 2/3).
    NestedX11,
    /// Virtual framebuffer, no visible window.
    Xvfb,
    /// Wayland native (experimental in Wine).
    Wayland,
}

/// Handle for a background display server process (Xephyr/Xvfb).
pub struct DisplayHandle {
    /// The DISPLAY value to set for the sandbox (e.g. ":42").
    pub display: String,
    /// Child process (dropped = killed).
    _child: Option<std::process::Child>,
}

impl Drop for DisplayHandle {
    fn drop(&mut self) {
        if let Some(ref mut child) = self._child {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

/// Detect the display server from the current process environment.
/// Direct-CLI only; tiers use [`detect_from`] with session values.
#[allow(dead_code)]
pub fn detect() -> DisplayServer {
    detect_from(
        std::env::var("WAYLAND_DISPLAY").ok(),
        std::env::var("DISPLAY").ok(),
    )
}

/// Classify a session's display from its WAYLAND_DISPLAY / DISPLAY values.
pub fn detect_from(wayland: Option<String>, x11: Option<String>) -> DisplayServer {
    match (wayland, x11) {
        (Some(wl), Some(x)) => {
            info!("Display: XWayland detected (WAYLAND_DISPLAY={wl}, DISPLAY={x})");
            DisplayServer::XWayland {
                wayland_display: wl,
                x11_display: x,
            }
        }
        (Some(wl), None) => {
            info!("Display: Wayland detected (WAYLAND_DISPLAY={wl})");
            DisplayServer::Wayland { display: wl }
        }
        (None, Some(x)) => {
            info!("Display: X11 detected (DISPLAY={x})");
            DisplayServer::X11 { display: x }
        }
        (None, None) => {
            debug!("Display: Headless (no WAYLAND_DISPLAY or DISPLAY)");
            DisplayServer::Headless
        }
    }
}

/// Resolve display mode from CLI flags and current environment.
/// Priority: explicit flag > config default > auto-detect.
///
/// `session_has_wayland` must describe the *launching user's* session. It
/// was read from the process env, which under the daemon is root's (never
/// Wayland), so daemon Tier 0/1 launches always picked host X11.
pub fn resolve_display_mode(
    nested_x11: bool,
    xvfb: bool,
    host_x11: bool,
    wayland: bool,
    tier: u8,
    session_has_wayland: bool,
) -> DisplayMode {
    // Explicit flags take priority
    if host_x11 {
        return DisplayMode::HostX11;
    }
    if xvfb {
        return DisplayMode::Xvfb;
    }
    if wayland {
        return DisplayMode::Wayland;
    }
    if nested_x11 {
        return DisplayMode::NestedX11;
    }

    // Auto: Tier 2/3 default to nested X11, Tier 0/1 use host display
    if tier >= 2 {
        DisplayMode::NestedX11
    } else if session_has_wayland {
        DisplayMode::Wayland
    } else {
        DisplayMode::HostX11
    }
}

/// Log security warnings for the chosen display mode.
pub fn warn_display_security(mode: &DisplayMode) {
    match mode {
        DisplayMode::HostX11 => {
            warn!("SECURITY: Using host X11 directly. Any process with X11 access can keylog.");
            warn!("Consider using --nested-x11 or --wayland for isolation.");
        }
        DisplayMode::NestedX11 => {
            if !has_xephyr() {
                warn!("Xephyr not found. Install xserver-xephyr for nested X11.");
                warn!("Falling back to Xvfb (headless).");
            }
        }
        DisplayMode::Xvfb => {
            info!("Using Xvfb (virtual framebuffer) — no visible window.");
        }
        DisplayMode::Wayland => {
            warn!("Using Wayland native — Wine Wayland support is EXPERIMENTAL.");
            warn!("Many Windows apps may not render correctly or may crash.");
            warn!("If you encounter issues, try --nested-x11 or --host-x11.");
        }
    }
}

/// Environment for a helper display server (Xephyr/Xvfb): it runs as the
/// launching user, against that user's display. Under the daemon this used
/// to be a root process with an empty environment, so Xephyr had no DISPLAY
/// to open its window on (and a root X server is its own risk).
fn display_server_cmd(bin: &str, args: &crate::Args) -> std::process::Command {
    let mut cmd = std::process::Command::new(bin);
    cmd.env_clear();
    cmd.env("PATH", "/usr/bin:/bin");
    for key in [
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
        "XDG_RUNTIME_DIR",
        "HOME",
    ] {
        if let Some(v) = crate::env_sanitize::session_var(args, key) {
            cmd.env(key, v);
        }
    }
    if let Some(uid) = args.uid {
        unsafe { crate::privs::drop_to_uid(&mut cmd, uid) };
    }
    cmd.stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
    cmd
}

/// Launch a nested X11 server (Xephyr) on a free display.
/// Returns a DisplayHandle with the display string and child process.
pub fn launch_nested_x11(args: &crate::Args) -> Option<DisplayHandle> {
    if !has_xephyr() {
        warn!("Xephyr not found — falling back to Xvfb");
        return launch_xvfb(args);
    }

    let display_num = find_free_display();
    let disp = format!(":{display_num}");

    info!("Launching Xephyr on {}", disp);

    let child = display_server_cmd("Xephyr", args)
        .args([
            &disp,
            "-resizeable",
            "-title",
            "win-sandbox-runner",
            "-nolisten",
            "tcp",
        ])
        .spawn()
        .ok()?;

    // Give Xephyr a moment to start
    std::thread::sleep(std::time::Duration::from_millis(200));

    info!("Xephyr started on {} (pid={})", disp, child.id());

    Some(DisplayHandle {
        display: disp,
        _child: Some(child),
    })
}

/// Launch Xvfb virtual framebuffer on a free display.
///
/// No `-ac`: that disabled X access control, letting any local process
/// connect to (and inject input into / screen-scrape) the sandbox display.
pub fn launch_xvfb(args: &crate::Args) -> Option<DisplayHandle> {
    if which("Xvfb").is_none() {
        warn!("Xvfb not found — install xvfb for headless mode");
        return None;
    }

    let display_num = find_free_display();
    let disp = format!(":{display_num}");

    info!("Launching Xvfb on {}", disp);

    let child = display_server_cmd("Xvfb", args)
        .args([&disp, "-screen", "0", "1920x1080x24", "-nolisten", "tcp"])
        .spawn()
        .ok()?;

    std::thread::sleep(std::time::Duration::from_millis(100));

    info!("Xvfb started on {} (pid={})", disp, child.id());

    Some(DisplayHandle {
        display: disp,
        _child: Some(child),
    })
}

/// Check if Xephyr is available for nested X11.
pub fn has_xephyr() -> bool {
    which("Xephyr").is_some()
}

/// Path of the local X11 socket for a `DISPLAY` value (`:N` / `unix:N`),
/// or None for remote/TCP displays.
pub fn x11_socket_path(display: &str) -> Option<String> {
    let (host, rest) = display.rsplit_once(':')?;
    if !(host.is_empty() || host == "unix") {
        return None;
    }
    let num = rest.split('.').next()?;
    if num.is_empty() || !num.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    Some(format!("/tmp/.X11-unix/X{num}"))
}

/// Find the next free X display number (no lock file and no socket).
fn find_free_display() -> u32 {
    for n in 10..200 {
        let lock = format!("/tmp/.X{n}-lock");
        let sock = format!("/tmp/.X11-unix/X{n}");
        if !std::path::Path::new(&lock).exists() && !std::path::Path::new(&sock).exists() {
            return n;
        }
    }
    // Fallback: use PID as display number (unlikely collision)
    std::process::id() % 100 + 200
}

/// Find a command in PATH.
fn which(cmd: &str) -> Option<String> {
    if let Ok(path) = std::env::var("PATH") {
        for dir in path.split(':') {
            let full = format!("{dir}/{cmd}");
            if std::path::Path::new(&full).exists() {
                return Some(full);
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detect_does_not_panic() {
        let _display = detect();
    }

    #[test]
    fn host_x11_warns() {
        warn_display_security(&DisplayMode::HostX11);
    }

    #[test]
    fn resolve_tier2_defaults_to_nested() {
        // Tier 2+ with no explicit flags should default to NestedX11
        let mode = resolve_display_mode(false, false, false, false, 2, true);
        assert_eq!(mode, DisplayMode::NestedX11);
    }

    #[test]
    fn resolve_tier3_defaults_to_nested() {
        let mode = resolve_display_mode(false, false, false, false, 3, false);
        assert_eq!(mode, DisplayMode::NestedX11);
    }

    /// Tier 0/1 follow the *session*, not the daemon's own environment.
    #[test]
    fn resolve_low_tier_uses_session_display() {
        assert_eq!(
            resolve_display_mode(false, false, false, false, 1, true),
            DisplayMode::Wayland
        );
        assert_eq!(
            resolve_display_mode(false, false, false, false, 1, false),
            DisplayMode::HostX11
        );
    }

    #[test]
    fn x11_socket_paths() {
        assert_eq!(x11_socket_path(":0").as_deref(), Some("/tmp/.X11-unix/X0"));
        assert_eq!(
            x11_socket_path("unix:12.0").as_deref(),
            Some("/tmp/.X11-unix/X12")
        );
        assert_eq!(x11_socket_path("host:0"), None);
        assert_eq!(x11_socket_path(":../x"), None);
    }

    #[test]
    fn detect_from_classifies() {
        assert_eq!(detect_from(None, None), DisplayServer::Headless);
        assert!(matches!(
            detect_from(Some("wayland-0".into()), Some(":0".into())),
            DisplayServer::XWayland { .. }
        ));
    }

    #[test]
    fn resolve_explicit_flags_override() {
        assert_eq!(
            resolve_display_mode(false, true, false, false, 2, false),
            DisplayMode::Xvfb
        );
        assert_eq!(
            resolve_display_mode(false, false, true, false, 2, false),
            DisplayMode::HostX11
        );
        assert_eq!(
            resolve_display_mode(false, false, false, true, 2, false),
            DisplayMode::Wayland
        );
        assert_eq!(
            resolve_display_mode(true, false, false, false, 0, false),
            DisplayMode::NestedX11
        );
    }

    #[test]
    fn find_free_display_returns_number() {
        let n = find_free_display();
        assert!(n > 0);
    }
}
