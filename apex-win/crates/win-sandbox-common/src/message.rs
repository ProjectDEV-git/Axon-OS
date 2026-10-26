use serde::{Deserialize, Serialize};

/// Messages exchanged between the CLI runner and the GUI over D-Bus or Unix socket.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", content = "payload")]
pub enum IpcMessage {
    /// CLI asks GUI to show a confirmation dialog for an unmapped binary.
    ConfirmRequest {
        /// SHA-256 hash of the binary.
        hash: String,
        /// Display name (basename of the exe).
        name: String,
        /// Full path to the executable.
        path: String,
    },
    /// GUI responds with the user's choice.
    ConfirmResponse {
        /// Chosen tier (0–3).
        tier: u8,
        /// Whether to remember this choice in rules.json.
        remember: bool,
    },
    /// CLI asks GUI to show a trust-level selector for an untrusted path.
    TrustRequest {
        hash: String,
        name: String,
        path: String,
        suggested_tier: u8,
    },
    /// GUI responds with trust settings.
    TrustResponse {
        tier: u8,
        network: bool,
        gpu: bool,
        remember: bool,
    },
    /// Ping / health check.
    Ping,
    /// Pong response.
    Pong,
    /// Runner asks GUI to show a setup progress dialog.
    SetupProgress {
        /// App name being set up.
        name: String,
        /// Current step description (e.g. "Installing DXVK", "Installing dotnet48").
        step: String,
        /// Progress 0.0–1.0 (-1 = indeterminate).
        progress: f64,
    },
    /// Runner tells GUI that setup is complete.
    SetupComplete {
        /// App name that was set up.
        name: String,
        /// Whether setup succeeded.
        success: bool,
        /// Summary message.
        summary: String,
    },
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn confirm_request_round_trip() {
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
    fn ping_pong_round_trip() {
        let msg = IpcMessage::Ping;
        let json = serde_json::to_string(&msg).unwrap();
        let parsed: IpcMessage = serde_json::from_str(&json).unwrap();
        assert!(matches!(parsed, IpcMessage::Ping));
    }
}
