use anyhow::{bail, Context, Result};
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::sync::mpsc;
use tracing::{debug, error, info, warn};
use win_sandbox_common::message::IpcMessage;

/// IPC transport for communication between CLI runner and GUI.
#[derive(Debug, Clone)]
pub enum IpcTransport {
    /// D-Bus (primary, standard on Linux desktops).
    DBus,
    /// Unix socket fallback (for headless systems).
    UnixSocket { path: String },
}

/// Default D-Bus well-known name.
pub const DBUS_NAME: &str = "org.wine.SandboxRunner";

/// Default D-Bus object path.
pub const DBUS_PATH: &str = "/org/wine/SandboxRunner";

/// Default Unix socket path.
pub const DEFAULT_SOCKET_PATH: &str = "/var/run/win-sandbox-gui.sock";

/// A received IPC request with a sender for the response.
pub struct IpcRequest {
    pub message: IpcMessage,
    pub respond_tx: mpsc::Sender<IpcMessage>,
}

/// Start listening for IPC messages on Unix socket.
/// Returns a receiver that yields IpcRequest objects.
///
/// Each incoming connection is handled in a background thread.
/// The request is sent to the GUI main loop via the returned channel.
pub fn start_unix_listener(socket_path: &str) -> Result<mpsc::Receiver<IpcRequest>> {
    // Remove stale socket
    let _ = std::fs::remove_file(socket_path);

    let listener = UnixListener::bind(socket_path)
        .with_context(|| format!("Failed to bind Unix socket at {socket_path}"))?;

    info!("IPC listening on Unix socket: {socket_path}");

    // Set socket permissions so the wine user can connect
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(socket_path, std::fs::Permissions::from_mode(0o666));
    }

    let (tx, rx) = mpsc::channel();

    std::thread::Builder::new()
        .name("ipc-unix-listener".into())
        .spawn(move || {
            for stream in listener.incoming() {
                match stream {
                    Ok(stream) => {
                        let tx = tx.clone();
                        std::thread::Builder::new()
                            .name("ipc-unix-conn".into())
                            .spawn(move || {
                                if let Err(e) = handle_connection(stream, &tx) {
                                    debug!("IPC connection error: {e}");
                                }
                            })
                            .ok();
                    }
                    Err(e) => {
                        error!("Unix socket accept error: {e}");
                    }
                }
            }
        })
        .context("Failed to spawn IPC listener thread")?;

    Ok(rx)
}

/// Handle a single Unix socket connection.
fn handle_connection(stream: UnixStream, tx: &mpsc::Sender<IpcRequest>) -> Result<()> {
    let reader_stream = stream.try_clone()?;
    let mut reader = BufReader::new(reader_stream);
    let mut writer = stream;

    // Read one line of JSON (the request)
    let mut line = String::new();
    reader.read_line(&mut line)?;
    let line = line.trim();
    if line.is_empty() {
        bail!("empty request");
    }

    let request: IpcMessage =
        serde_json::from_str(line).with_context(|| format!("Invalid IPC JSON: {line}"))?;

    debug!("IPC received: {request:?}");

    // Create a one-shot response channel
    let (resp_tx, resp_rx) = mpsc::channel();

    let ipc_request = IpcRequest {
        message: request,
        respond_tx: resp_tx,
    };

    // Send to GUI main loop
    tx.send(ipc_request)
        .context("Failed to send IPC request to GUI")?;

    // Wait for the GUI to respond (blocks this connection thread)
    match resp_rx.recv_timeout(std::time::Duration::from_secs(300)) {
        Ok(response) => {
            let json = serde_json::to_string(&response)?;
            writeln!(writer, "{json}")?;
            writer.flush()?;
            debug!("IPC response sent");
        }
        Err(_) => {
            warn!("IPC response timeout (5 min) — sending default");
            let default = IpcMessage::ConfirmResponse {
                tier: 2,
                remember: false,
            };
            let json = serde_json::to_string(&default)?;
            writeln!(writer, "{json}")?;
            writer.flush()?;
        }
    }

    Ok(())
}

/// Send an IPC message to the GUI and wait for a response (CLI runner side).
pub fn send_request(socket_path: &str, msg: &IpcMessage) -> Result<IpcMessage> {
    let mut stream = UnixStream::connect(socket_path)
        .with_context(|| format!("Cannot connect to GUI socket at {socket_path}"))?;

    // Send request as JSON line
    let json = serde_json::to_string(msg)?;
    writeln!(stream, "{json}")?;
    stream.flush()?;

    // Read response
    let mut reader = BufReader::new(stream.try_clone()?);
    let mut line = String::new();
    reader.read_line(&mut line)?;
    let line = line.trim();

    let response: IpcMessage =
        serde_json::from_str(line).with_context(|| format!("Invalid IPC response: {line}"))?;

    Ok(response)
}

/// Start listening for IPC messages via D-Bus (primary transport).
/// Returns a receiver that yields IpcRequest objects.
///
/// This is a placeholder — full D-Bus integration requires the
/// zbus object server pattern which is more complex.
/// For now, Unix socket is the working transport.
pub fn start_dbus_listener() -> Result<mpsc::Receiver<IpcRequest>> {
    // D-Bus object server integration with zbus requires implementing the
    // org.wine.SandboxRunner Interface with method calls for ConfirmRequest
    // and TrustRequest. The Unix socket transport works as the primary
    // transport for now. D-Bus can be added when there's demand for desktop
    // integration (e.g., GNOME notification bubbles for sandbox requests).
    warn!("D-Bus IPC not yet implemented, using Unix socket fallback");
    start_unix_listener(DEFAULT_SOCKET_PATH)
}

/// Resolve which IPC transport to use.
/// Currently always returns UnixSocket; D-Bus will be used when
/// the zbus object server is implemented.
pub fn resolve_transport() -> IpcTransport {
    // D-Bus session bus availability doesn't matter until we implement
    // the zbus Interface. For now, Unix socket is the working transport.
    if std::env::var("DBUS_SESSION_BUS_ADDRESS").is_ok() {
        debug!("D-Bus available — will be used when zbus integration is complete");
    }

    IpcTransport::UnixSocket {
        path: DEFAULT_SOCKET_PATH.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ipc_message_json_round_trip() {
        let msg = IpcMessage::ConfirmRequest {
            hash: "abc123".into(),
            name: "test.exe".into(),
            path: "/tmp/test.exe".into(),
        };
        let json = serde_json::to_string(&msg).unwrap();
        let parsed: IpcMessage = serde_json::from_str(&json).unwrap();
        match parsed {
            IpcMessage::ConfirmRequest { hash, name, path } => {
                assert_eq!(hash, "abc123");
                assert_eq!(name, "test.exe");
                assert_eq!(path, "/tmp/test.exe");
            }
            _ => panic!("wrong variant"),
        }
    }

    #[test]
    fn trust_request_round_trip() {
        let msg = IpcMessage::TrustRequest {
            hash: "def456".into(),
            name: "game.exe".into(),
            path: "/home/user/game.exe".into(),
            suggested_tier: 2,
        };
        let json = serde_json::to_string(&msg).unwrap();
        let parsed: IpcMessage = serde_json::from_str(&json).unwrap();
        match parsed {
            IpcMessage::TrustRequest {
                hash,
                suggested_tier,
                ..
            } => {
                assert_eq!(hash, "def456");
                assert_eq!(suggested_tier, 2);
            }
            _ => panic!("wrong variant"),
        }
    }

    #[test]
    fn default_socket_path_correct() {
        assert_eq!(DEFAULT_SOCKET_PATH, "/var/run/win-sandbox-gui.sock");
    }
}
