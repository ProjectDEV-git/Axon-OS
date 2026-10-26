use crate::Args;
use anyhow::{bail, Result};
use std::os::unix::process::CommandExt;
use std::process::{Command, ExitCode};
use tracing::{debug, info, warn};

/// System directories that get read-only bind-mounted into the container.
const RO_SYSTEM_DIRS: &[&str] = &[
    "/usr", "/lib", "/lib32", "/lib64", "/bin", "/sbin", "/opt", "/etc",
];

/// Tier 2: Bubblewrap container with optional networking.
#[allow(dead_code)]
pub fn run(args: &Args) -> Result<ExitCode> {
    run_with_network(args, false)
}

/// Apply bwrap namespace flags related to host networking.
///
/// `network == true` shares the host network namespace (documented Linux Tier 2
/// host-shared / TAP mode). `network == false` must leave the net namespace
/// unshared so the sandbox has no host network.
fn apply_namespace_net_flags(cmd: &mut Command, network: bool) {
    cmd.arg("--unshare-all");
    // --unshare-all already unshares the net namespace; only re-share host
    // networking when policy explicitly asks for it.
    if network {
        cmd.arg("--share-net");
    }
}

/// Tier 2: Bubblewrap container with explicit network control.
///
/// When `network` is true, starts the TAP bridge daemon and bind-mounts
/// its Unix socket + sys_netmp.dll into the container for isolated
/// Wine networking. When false, the container runs without network.
pub fn run_with_network(args: &Args, network: bool) -> Result<ExitCode> {
    let exe = args.exe.as_deref().unwrap();
    info!("Tier 2: Bubblewrap container for {exe} (network={network})");

    let config = crate::config::load_config(None);

    let nvidia = crate::nvidia::detect();
    let amd_gpu = crate::amd::detect();
    let runtime = crate::env_sanitize::session_var(args, "XDG_RUNTIME_DIR");
    let audio = crate::audio::detect_in(
        runtime.as_deref(),
        crate::env_sanitize::session_var(args, "PULSE_SERVER").as_deref(),
    );
    let display = crate::display::detect_from(
        crate::env_sanitize::session_var(args, "WAYLAND_DISPLAY"),
        crate::env_sanitize::session_var(args, "DISPLAY"),
    );

    if nvidia.is_some() {
        warn!("Nvidia GPU detected — GPU passthrough with bwrap may fail");
        warn!("Consider using --tier 1 (Landlock) for Nvidia systems");
    }

    // Resolve display mode and launch nested display server if needed
    let display_mode = crate::display::resolve_display_mode(
        args.nested_x11,
        args.xvfb,
        args.host_x11,
        args.wayland,
        2, // Tier 2
        matches!(
            display,
            crate::display::DisplayServer::Wayland { .. }
                | crate::display::DisplayServer::XWayland { .. }
        ),
    );
    crate::display::warn_display_security(&display_mode);

    // Launch Xephyr/Xvfb if needed (kept alive by DisplayHandle Drop guard).
    // They run as the launching user on the user's own display.
    let _display_handle: Option<crate::display::DisplayHandle> = match display_mode {
        crate::display::DisplayMode::NestedX11 => crate::display::launch_nested_x11(args),
        crate::display::DisplayMode::Xvfb => crate::display::launch_xvfb(args),
        _ => None,
    };

    let hostname = sandbox_hostname();

    let mut cmd = Command::new("bwrap");

    // Namespace flags
    apply_namespace_net_flags(&mut cmd, network);
    cmd.arg("--die-with-parent");
    cmd.args(["--hostname", &hostname]);

    // Root filesystem: new tmpfs
    cmd.args(["--tmpfs", "/"]);

    // System directories: read-only binds
    for dir in existing_ro_dirs() {
        cmd.args(["--ro-bind", dir, dir]);
    }

    // Proc, dev, tmp
    cmd.args(["--proc", "/proc"]);
    cmd.args(["--dev", "/dev"]);
    cmd.args(["--tmpfs", "/tmp"]);

    // Home directory: new tmpfs (isolated from host). The name comes from the
    // session (passwd-derived in daemon mode), not root's process env.
    let username = crate::env_sanitize::session_var(args, "USER")
        .filter(|u| !u.contains('/') && u != "." && u != "..")
        .unwrap_or_else(|| "user".into());
    cmd.args(["--tmpfs", "/home"]);
    cmd.args(["--tmpfs", &format!("/home/{username}")]);

    // WINEPREFIX: bind-mount if it exists (resolved per-app prefix)
    let wine_prefix = crate::env_sanitize::resolved_prefix(args, &config);
    if std::path::Path::new(&wine_prefix).exists() {
        cmd.args(["--ro-bind", &wine_prefix, &wine_prefix]);
        let drive_c = format!("{wine_prefix}/drive_c");
        if std::path::Path::new(&drive_c).exists() {
            cmd.args(["--bind", &drive_c, &drive_c]);
        }
    }

    // The executable's directory: bind RW
    if let Some(parent) = exe_parent_dir(exe) {
        cmd.args(["--bind", parent, parent]);
    }

    // XDG_RUNTIME_DIR for audio/display sockets
    if let Some(runtime) = runtime
        .as_deref()
        .filter(|r| std::path::Path::new(r).exists())
    {
        cmd.args(["--bind", runtime, runtime]);
    }

    // X11 socket for the display Wine will actually use. --tmpfs /tmp above
    // hides /tmp/.X11-unix, so without this bind every X11 launch (host or
    // Xephyr) failed to connect.
    let x_display = match (&display_mode, _display_handle.as_ref()) {
        (_, Some(h)) => Some(h.display.clone()),
        (crate::display::DisplayMode::HostX11, None) => match &display {
            crate::display::DisplayServer::X11 { display }
            | crate::display::DisplayServer::XWayland {
                x11_display: display,
                ..
            } => Some(display.clone()),
            _ => None,
        },
        _ => None,
    };
    if let Some(sock) = x_display
        .as_deref()
        .and_then(crate::display::x11_socket_path)
    {
        if std::path::Path::new(&sock).exists() {
            cmd.args(["--ro-bind", &sock, &sock]);
        }
    }
    if let Some(xauth) = crate::env_sanitize::session_var(args, "XAUTHORITY") {
        if std::path::Path::new(&xauth).is_file() {
            cmd.args(["--ro-bind", &xauth, &xauth]);
        }
    }

    // GPU passthrough
    bind_gpu_devices(&mut cmd, nvidia.is_some(), amd_gpu.is_some());

    // Gamepad/controller access (Plan §11: /dev/input isolation)
    if args.gamepad {
        bind_gamepad_devices(&mut cmd);
    }

    // Audio socket
    if let Some(ref server) = audio {
        let socket_path = match server {
            crate::audio::AudioServer::PipeWire { socket } => socket,
            crate::audio::AudioServer::PulseAudio { socket } => socket,
        };
        let socket_str = socket_path.to_string_lossy();
        if socket_path.exists() {
            cmd.args(["--bind", &socket_str, &socket_str]);
        }
    }

    // NTsync (Wine 9+ synchronization device)
    crate::net::bind_ntsync(&mut cmd);

    // Sanitized environment — clear inherited env, apply allowlisted vars only
    let user_env = crate::env_sanitize::user_env_for_args(args);
    let sandbox_env = crate::env_sanitize::build_sandbox_env(
        &config,
        user_env,
        args.resolved_wineprefix.as_deref(),
    )?;
    cmd.env_clear();
    for (key, val) in &sandbox_env {
        cmd.env(key, val);
    }

    // Apply display environment based on resolved mode
    apply_display_for_mode(&mut cmd, &display_mode, &display, _display_handle.as_ref());

    // Networking: TAP bridge or isolated
    if let Err(e) = crate::net::configure_bwrap_networking(&mut cmd, network, &config) {
        warn!("Networking setup failed: {e}");
        warn!("Continuing without bridge networking");
    }

    // The command to run inside the sandbox
    cmd.args(["--", "wine"]);
    cmd.arg(exe);
    cmd.args(&args.args);

    // Switch to target UID in the child process (daemon mode)
    if let Some(uid) = args.uid {
        unsafe { crate::daemon::configure_child_uid(&mut cmd, uid) };
    }

    crate::hasher::verify_unchanged(exe, args.exe_identity.as_ref())?;
    info!("Executing bwrap for {exe}");

    // Daemon mode (uid set): use status() so the daemon process survives.
    // CLI mode: use exec() to replace current process with bwrap.
    if args.uid.is_some() {
        let status = cmd.status()?;
        let code = status.code().unwrap_or(1);
        if code != 0 {
            bail!("bwrap exited with status: {code}");
        }
        return Ok(ExitCode::from(code as u8));
    }

    let err = cmd.exec();
    bail!("Failed to exec bwrap: {err}");
}

/// Generate a sandbox hostname from the current PID.
fn sandbox_hostname() -> String {
    format!("winrun-{:08x}", std::process::id())
}

/// Return system dirs from RO_SYSTEM_DIRS that exist on this system.
fn existing_ro_dirs() -> Vec<&'static str> {
    RO_SYSTEM_DIRS
        .iter()
        .copied()
        .filter(|d| std::path::Path::new(d).exists())
        .collect()
}

/// Return the parent directory of the exe, if non-root and non-empty.
fn exe_parent_dir(exe: &str) -> Option<&str> {
    let parent = std::path::Path::new(exe).parent()?;
    let s = parent.to_str()?;
    if s.is_empty() || s == "/" {
        None
    } else {
        Some(s)
    }
}

/// Bind-mount GPU device nodes into the container.
fn bind_gpu_devices(cmd: &mut Command, has_nvidia: bool, has_amd: bool) {
    if has_nvidia {
        if std::path::Path::new("/dev/dri").exists() {
            cmd.args(["--dev-bind", "/dev/dri", "/dev/dri"]);
        }
        for i in 0..8 {
            let dev = format!("/dev/nvidia{i}");
            if std::path::Path::new(&dev).exists() {
                cmd.args(["--dev-bind", &dev, &dev]);
            }
        }
        for dev in &["/dev/nvidiactl", "/dev/nvidia-uvm"] {
            if std::path::Path::new(dev).exists() {
                cmd.args(["--dev-bind", dev, dev]);
            }
        }
    } else if has_amd && std::path::Path::new("/dev/dri").exists() {
        cmd.args(["--dev-bind", "/dev/dri", "/dev/dri"]);
    }
}

/// Bind-mount gamepad/controller devices from /dev/input.
/// Scans for /dev/input/event* devices and bind-mounts only gamepad-related ones.
/// Falls back to binding the entire /dev/input directory if sysfs scanning fails.
fn bind_gamepad_devices(cmd: &mut Command) {
    let input_dir = std::path::Path::new("/dev/input");
    if !input_dir.exists() {
        debug!("Gamepad: /dev/input does not exist");
        return;
    }

    // Try to read /sys/class/input/*/device/name to identify gamepads
    let gamepad_keywords = [
        "gamepad",
        "controller",
        "joystick",
        "xbox",
        "playstation",
        "dualshock",
        "dualsense",
        "switch",
        "pro controller",
        "joy-con",
    ];

    let mut found_gamepads = false;

    if let Ok(entries) = std::fs::read_dir("/sys/class/input") {
        for entry in entries.flatten() {
            let name = entry.file_name();
            let name_str = name.to_string_lossy();

            // Only look at event* entries
            if !name_str.starts_with("event") {
                continue;
            }

            // Read the device name from sysfs
            let device_name_path = entry.path().join("device").join("name");
            if let Ok(device_name) = std::fs::read_to_string(&device_name_path) {
                let device_name_lower = device_name.trim().to_lowercase();
                let is_gamepad = gamepad_keywords
                    .iter()
                    .any(|kw| device_name_lower.contains(kw));

                if is_gamepad {
                    let event_path = format!("/dev/input/{name_str}");
                    if std::path::Path::new(&event_path).exists() {
                        cmd.args(["--dev-bind", &event_path, &event_path]);
                        info!(
                            "Gamepad: bind-mounted {event_path} ({})",
                            device_name.trim()
                        );
                        found_gamepads = true;
                    }
                }
            }
        }
    }

    if !found_gamepads {
        debug!("Gamepad: no gamepad devices found in /dev/input");
        debug!("Gamepad: if you have a controller, ensure it is connected before launching");
    }
}

/// Apply display environment variables based on the resolved display mode.
fn apply_display_for_mode(
    cmd: &mut Command,
    mode: &crate::display::DisplayMode,
    detected: &crate::display::DisplayServer,
    _display_handle: Option<&crate::display::DisplayHandle>,
) {
    match mode {
        crate::display::DisplayMode::NestedX11 | crate::display::DisplayMode::Xvfb => {
            // Use the display from Xephyr/Xvfb if launched
            if let Some(handle) = _display_handle {
                cmd.env("DISPLAY", &handle.display);
                info!("Sandbox DISPLAY={}", handle.display);
            } else {
                // Fallback: use detected display
                apply_display_env(cmd, detected);
            }
        }
        crate::display::DisplayMode::Wayland => {
            if let crate::display::DisplayServer::Wayland { display: wl }
            | crate::display::DisplayServer::XWayland {
                wayland_display: wl,
                ..
            } = detected
            {
                cmd.env("WAYLAND_DISPLAY", wl);
                cmd.env("WINE_WAYLAND_DRIVER", "1");
                info!("Sandbox WAYLAND_DISPLAY={}", wl);
            } else {
                warn!("--wayland specified but no Wayland display detected");
            }
        }
        crate::display::DisplayMode::HostX11 => {
            apply_display_env(cmd, detected);
        }
    }
}

/// Set DISPLAY/WAYLAND_DISPLAY env vars based on detected display server.
fn apply_display_env(cmd: &mut Command, display: &crate::display::DisplayServer) {
    match display {
        crate::display::DisplayServer::X11 { display } => {
            cmd.env("DISPLAY", display);
        }
        crate::display::DisplayServer::Wayland { display } => {
            cmd.env("WAYLAND_DISPLAY", display);
            cmd.env("WINE_WAYLAND_DRIVER", "1");
        }
        crate::display::DisplayServer::XWayland { x11_display, .. } => {
            cmd.env("DISPLAY", x11_display);
        }
        crate::display::DisplayServer::Headless => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    /// Serializes tests that mutate process-global environment variables.
    static ENV_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn hostname_format() {
        let h = sandbox_hostname();
        assert!(h.starts_with("winrun-"));
        assert_eq!(h.len(), 15);
        let hex_part = &h[7..];
        assert!(u32::from_str_radix(hex_part, 16).is_ok());
    }

    /// network=false must not share the host network namespace.
    /// network=true may share it (TAP bridge / documented host-shared mode).
    #[test]
    fn share_net_only_when_network_enabled() {
        let mut offline = Command::new("bwrap");
        apply_namespace_net_flags(&mut offline, false);
        let offline_args: Vec<String> = offline
            .get_args()
            .map(|a| a.to_string_lossy().into_owned())
            .collect();
        assert!(
            offline_args.iter().any(|a| a == "--unshare-all"),
            "must unshare namespaces"
        );
        assert!(
            !offline_args.iter().any(|a| a == "--share-net"),
            "network=false must not pass --share-net, got {offline_args:?}"
        );

        let mut online = Command::new("bwrap");
        apply_namespace_net_flags(&mut online, true);
        let online_args: Vec<String> = online
            .get_args()
            .map(|a| a.to_string_lossy().into_owned())
            .collect();
        assert!(
            online_args.iter().any(|a| a == "--share-net"),
            "network=true may pass --share-net, got {online_args:?}"
        );
    }

    /// Tier 2 must bind the dispatch-resolved per-app prefix (carried on
    /// Args), not the generic config default (~/.wine) and not whatever
    /// WINEPREFIX happens to be in the (root) daemon's process env.
    #[test]
    fn resolved_prefix_prefers_args_over_process_env() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        std::env::set_var("WINEPREFIX", "/root/.wine");
        let config = crate::config::Config {
            wine_prefix: "/home/u/.wine".to_string(),
            ..crate::config::Config::default()
        };
        let mut args = crate::dispatch::tests_support::args();
        args.uid = Some(1000);
        assert_eq!(
            crate::env_sanitize::resolved_prefix(&args, &config),
            "/home/u/.wine",
            "daemon mode must ignore process WINEPREFIX"
        );
        args.resolved_wineprefix =
            Some("/home/u/.local/share/win-sandbox/prefixes/deadbeef/prefix".into());
        assert_eq!(
            crate::env_sanitize::resolved_prefix(&args, &config),
            "/home/u/.local/share/win-sandbox/prefixes/deadbeef/prefix"
        );
        std::env::remove_var("WINEPREFIX");
    }

    #[test]
    fn ro_dirs_filter_existing() {
        let dirs = existing_ro_dirs();
        assert!(dirs.contains(&"/usr"));
        assert!(dirs.contains(&"/etc"));
        assert!(dirs.contains(&"/bin"));
        for d in &dirs {
            assert!(RO_SYSTEM_DIRS.contains(d));
        }
    }

    #[test]
    fn exe_parent_dir_basic() {
        assert_eq!(exe_parent_dir("/home/user/game.exe"), Some("/home/user"));
        assert_eq!(exe_parent_dir("/game.exe"), None);
    }

    #[test]
    fn exe_parent_dir_empty() {
        assert_eq!(exe_parent_dir("game.exe"), None);
    }

    #[test]
    fn apply_display_env_x11() {
        let display = crate::display::DisplayServer::X11 {
            display: ":0".into(),
        };
        let mut cmd = Command::new("true");
        apply_display_env(&mut cmd, &display);
    }

    #[test]
    fn apply_display_env_wayland() {
        let display = crate::display::DisplayServer::Wayland {
            display: "wayland-0".into(),
        };
        let mut cmd = Command::new("true");
        apply_display_env(&mut cmd, &display);
    }

    #[test]
    fn apply_display_env_headless() {
        let display = crate::display::DisplayServer::Headless;
        let mut cmd = Command::new("true");
        apply_display_env(&mut cmd, &display);
    }

    #[test]
    fn bind_gpu_no_gpu() {
        let mut cmd = Command::new("true");
        bind_gpu_devices(&mut cmd, false, false);
    }

    #[test]
    fn bind_gamepad_does_not_panic() {
        let mut cmd = Command::new("true");
        bind_gamepad_devices(&mut cmd);
    }
}
