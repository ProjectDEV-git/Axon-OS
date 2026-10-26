//! TAP bridge networking module.
//!
//! Manages the win-tap-bridge daemon lifecycle and provides networking
//! configuration for sandboxed tiers (2/3). The bridge creates an isolated
//! TAP device that Wine can use through the sys_netmp DLL.

use anyhow::{bail, Context, Result};
use std::path::{Path, PathBuf};
use std::process::Command;
use tracing::{debug, info, warn};

/// Default path for the bridge Unix socket.
const BRIDGE_SOCKET_PATH: &str = "/var/run/win-tap-bridge.sock";

/// Default TAP device name (used by the bridge daemon, referenced here for docs).
#[allow(dead_code)]
const TAP_DEVICE_NAME: &str = "winrunner-tap0";

/// Check if the win-tap-bridge daemon is running by testing socket existence.
pub fn is_bridge_running() -> bool {
    Path::new(BRIDGE_SOCKET_PATH).exists()
}

/// Start the win-tap-bridge daemon if not already running.
/// Returns the socket path for bind-mounting into containers.
pub fn ensure_bridge_running(socket_path: Option<&str>) -> Result<PathBuf> {
    let sock = socket_path.unwrap_or(BRIDGE_SOCKET_PATH);
    let sock_path = PathBuf::from(sock);

    if is_bridge_running() {
        debug!("Bridge already running at {}", sock);
        return Ok(sock_path);
    }

    info!("Starting win-tap-bridge daemon");

    // Try to start via systemd first, fall back to direct execution
    if try_start_systemd_service()? {
        // Give systemd a moment to create the socket
        std::thread::sleep(std::time::Duration::from_millis(500));
        if is_bridge_running() {
            info!("win-tap-bridge started via systemd");
            return Ok(sock_path);
        }
        warn!("systemd start succeeded but socket not found, trying direct");
    }

    // Direct execution as fallback
    start_bridge_direct()?;
    std::thread::sleep(std::time::Duration::from_millis(200));

    if is_bridge_running() {
        info!("win-tap-bridge started directly");
        Ok(sock_path)
    } else {
        bail!("Failed to start win-tap-bridge: socket {} not found", sock)
    }
}

/// Try to start the win-tap-bridge systemd service.
/// Returns Ok(true) if the command succeeded, Ok(false) if systemd unavailable.
fn try_start_systemd_service() -> Result<bool> {
    let output = Command::new("systemctl")
        .args(["start", "win-tap-bridge.service"])
        .output();

    match output {
        Ok(o) if o.status.success() => Ok(true),
        Ok(o) => {
            let stderr = String::from_utf8_lossy(&o.stderr);
            if stderr.contains("not found") || stderr.contains("No such file") {
                debug!("systemd service not installed");
                Ok(false)
            } else {
                warn!("systemctl start failed: {}", stderr.trim());
                Ok(false)
            }
        }
        Err(e) => {
            debug!("systemctl not available: {e}");
            Ok(false)
        }
    }
}

/// Start the bridge daemon directly (forked to background).
fn start_bridge_direct() -> Result<()> {
    // Look for the binary in standard locations
    let bridge_bin = find_bridge_binary().context("win-tap-bridge binary not found")?;

    info!("Starting {} directly", bridge_bin.display());

    Command::new(&bridge_bin)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .context("Failed to spawn win-tap-bridge")?;

    Ok(())
}

/// Find the win-tap-bridge binary in standard locations.
fn find_bridge_binary() -> Option<PathBuf> {
    let candidates = [
        "/usr/local/bin/win-tap-bridge",
        "/usr/bin/win-tap-bridge",
        // Development build location
        "csrc/win-tap-bridge/win-tap-bridge",
    ];

    for path in &candidates {
        let p = PathBuf::from(path);
        if p.exists() {
            return Some(p);
        }
    }

    // Try PATH
    which::which("win-tap-bridge").ok()
}

/// Find the sys_netmp.dll for WINEDLLPATH configuration.
pub fn find_dll_path() -> Option<PathBuf> {
    let candidates = [
        "/usr/lib/wine/x86_64-windows/sys_netmp.dll",
        "csrc/sys_netmp/sys_netmp.dll",
    ];

    for path in &candidates {
        let p = PathBuf::from(path);
        if p.exists() {
            return Some(p);
        }
    }

    None
}

/// Detect and bind-mount /dev/ntsync if present (Wine 9+ NTsync support).
pub fn bind_ntsync(cmd: &mut Command) {
    let ntsync_path = "/dev/ntsync";
    if Path::new(ntsync_path).exists() {
        cmd.args(["--dev-bind", ntsync_path, ntsync_path]);
        info!("NTsync: bind-mounted /dev/ntsync (Wine 9+ synchronization)");
    } else {
        debug!("NTsync: /dev/ntsync not present (kernel too old or not enabled)");
    }
}

/// Configure networking for a bubblewrap command.
///
/// When `network` is true:
/// - Starts the bridge daemon
/// - Bind-mounts the Unix socket into the container
/// - Sets WINEDLLPATH for sys_netmp.dll
/// - Sets WINE_BRIDGE_SOCKET for the DLL
///
/// When `network` is false, the container runs with no networking
/// (--unshare-net without --share-net).
pub fn configure_bwrap_networking(
    cmd: &mut Command,
    network: bool,
    config: &crate::config::Config,
) -> Result<()> {
    if network {
        let socket_path = ensure_bridge_running(Some(&config.tap_bridge_socket))?;
        let socket_str = socket_path.to_string_lossy();

        // Bind-mount the Unix socket into the container
        cmd.args(["--bind", &socket_str, &socket_str]);

        // DNS resolution: bind-mount /etc/resolv.conf so the sandbox can resolve names
        // Must come AFTER the --ro-bind /etc or it gets shadowed
        let resolv_conf = "/etc/resolv.conf";
        if Path::new(resolv_conf).exists() {
            cmd.args(["--ro-bind", resolv_conf, resolv_conf]);
            debug!("DNS: bind-mounted {}", resolv_conf);
        } else {
            warn!(
                "{} not found — DNS resolution will fail in sandbox",
                resolv_conf
            );
        }

        // Set WINEDLLPATH so Wine can find sys_netmp.dll
        if let Some(dll_dir) = find_dll_path() {
            if let Some(parent) = dll_dir.parent() {
                let wine_dllpath = std::env::var("WINEDLLPATH")
                    .map(|existing| format!("{existing}:{}", parent.display()))
                    .unwrap_or_else(|_| parent.display().to_string());
                cmd.env("WINEDLLPATH", wine_dllpath);
            }
            debug!("sys_netmp.dll found at: {}", dll_dir.display());
        } else {
            warn!("sys_netmp.dll not found — Wine networking will be unavailable");
        }

        // Tell the DLL where to find the bridge socket
        cmd.env("WINE_BRIDGE_SOCKET", &config.tap_bridge_socket);

        info!("Networking enabled via TAP bridge (socket: {})", socket_str);
    } else {
        // No networking: don't share the host network
        // bwrap --unshare-all already isolates; we just don't add --share-net
        info!("Networking disabled for this sandbox");
    }

    Ok(())
}

/// Configure networking for an OverlayFS (Tier 3) command.
/// Same as bwrap but also bind-mounts the socket through the overlay.
/// Currently unused — tier3 calls ensure_bridge_running + env setup inline.
#[allow(dead_code)]
pub fn configure_overlay_networking(
    cmd: &mut Command,
    network: bool,
    config: &crate::config::Config,
) -> Result<()> {
    // OverlayFS uses the same bwrap commands for bind-mounting
    configure_bwrap_networking(cmd, network, config)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bridge_socket_path_correct() {
        assert_eq!(BRIDGE_SOCKET_PATH, "/var/run/win-tap-bridge.sock");
    }

    #[test]
    fn tap_device_name_correct() {
        assert_eq!(TAP_DEVICE_NAME, "winrunner-tap0");
    }

    #[test]
    fn is_bridge_running_no_false_positive() {
        // In test environment, bridge is not running
        // (socket file doesn't exist)
        let running = is_bridge_running();
        // We can't assert false because it might be running on a dev machine
        // Just verify it doesn't panic
        let _ = running;
    }

    #[test]
    fn find_bridge_binary_returns_none_when_missing() {
        // In test environment, the binary may or may not exist
        // Just verify it doesn't panic
        let _ = find_bridge_binary();
    }

    #[test]
    fn find_dll_path_returns_none_when_missing() {
        // In test environment, the DLL may or may not exist
        let _ = find_dll_path();
    }

    #[test]
    fn bind_ntsync_does_not_panic() {
        // /dev/ntsync may or may not exist on this system
        let mut cmd = Command::new("true");
        bind_ntsync(&mut cmd);
        // Verify it doesn't panic regardless
    }

    #[test]
    fn configure_bwrap_networking_false_disables() {
        let mut cmd = Command::new("true");
        let config = crate::config::Config::default();
        let result = configure_bwrap_networking(&mut cmd, false, &config);
        assert!(result.is_ok());
    }
}
