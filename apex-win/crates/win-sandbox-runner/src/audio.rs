use std::path::PathBuf;
use tracing::{debug, info};

/// Detected audio server type.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AudioServer {
    /// PipeWire (modern, preferred).
    PipeWire { socket: PathBuf },
    /// PulseAudio (legacy, widely supported).
    PulseAudio { socket: PathBuf },
}

/// Detect the running audio server from the current process environment.
/// Only correct for direct CLI use; tiers call [`detect_in`] with the
/// session values so daemon launches look in the user's runtime dir.
#[allow(dead_code)]
pub fn detect() -> Option<AudioServer> {
    detect_in(
        std::env::var("XDG_RUNTIME_DIR").ok().as_deref(),
        std::env::var("PULSE_SERVER").ok().as_deref(),
    )
}

/// Detect the audio server for a given session.
///
/// Priority: PipeWire → PulseAudio (native) → `pulse_server` → None.
pub fn detect_in(runtime_dir: Option<&str>, pulse_server: Option<&str>) -> Option<AudioServer> {
    if let Some(runtime) = runtime_dir {
        // PipeWire
        let pipewire_socket = format!("{runtime}/pipewire-0");
        if PathBuf::from(&pipewire_socket).exists() {
            info!("Audio: PipeWire detected at {pipewire_socket}");
            return Some(AudioServer::PipeWire {
                socket: PathBuf::from(pipewire_socket),
            });
        }

        // PulseAudio (native socket)
        let pulse_socket = format!("{runtime}/pulse/native");
        if PathBuf::from(&pulse_socket).exists() {
            info!("Audio: PulseAudio detected at {pulse_socket}");
            return Some(AudioServer::PulseAudio {
                socket: PathBuf::from(pulse_socket),
            });
        }
    }

    // PulseAudio via $PULSE_SERVER (could be TCP)
    if let Some(pulse_server) = pulse_server {
        debug!("Audio: PULSE_SERVER={pulse_server}");
        // If it's a Unix socket path
        if pulse_server.starts_with('/') && PathBuf::from(&pulse_server).exists() {
            return Some(AudioServer::PulseAudio {
                socket: PathBuf::from(pulse_server),
            });
        }
        // TCP connections are handled by env var, no socket to bind-mount
    }

    debug!("Audio: No audio server detected");
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detect_does_not_panic() {
        let _server = detect();
    }

    #[test]
    fn detect_in_finds_pipewire_in_given_runtime_dir() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("pipewire-0"), b"").unwrap();
        let found = detect_in(dir.path().to_str(), None);
        assert!(matches!(found, Some(AudioServer::PipeWire { .. })));
        assert!(detect_in(None, None).is_none());
    }
}
