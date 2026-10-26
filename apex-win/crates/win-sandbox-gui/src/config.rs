//! GUI configuration loader for win-sandbox-gui.
//!
//! Reads the win-sandbox-runner.conf INI file to get runtime settings
//! (socket path, display mode, etc.) that the GUI needs to connect
//! to the runner process.

use std::path::{Path, PathBuf};
use tracing::debug;

/// GUI configuration loaded from win-sandbox-runner.conf.
#[derive(Debug, Clone)]
pub struct GuiConfig {
    /// Path to the Unix socket for IPC with the runner.
    pub socket_path: PathBuf,
    /// Whether to remember user decisions.
    pub remember_decisions: bool,
}

impl Default for GuiConfig {
    fn default() -> Self {
        Self {
            socket_path: PathBuf::from(default_socket_path()),
            remember_decisions: true,
        }
    }
}

/// Return the default socket path based on XDG_RUNTIME_DIR.
pub fn default_socket_path() -> String {
    let runtime = std::env::var("XDG_RUNTIME_DIR").unwrap_or_else(|_| "/tmp".to_string());
    format!("{runtime}/win-sandbox-runner.sock")
}

/// Load GUI configuration from the config file.
/// Falls back to defaults if the file doesn't exist or can't be parsed.
pub fn load_config(config_path: Option<&Path>) -> GuiConfig {
    let path =
        config_path.unwrap_or_else(|| Path::new("/etc/win-sandbox-runner/win-sandbox-runner.conf"));

    if !path.exists() {
        debug!(
            "Config file not found at {}, using defaults",
            path.display()
        );
        return GuiConfig::default();
    }

    match std::fs::read_to_string(path) {
        Ok(content) => parse_gui_config(&content),
        Err(e) => {
            debug!("Failed to read config: {e}, using defaults");
            GuiConfig::default()
        }
    }
}

/// Parse GUI-relevant settings from INI config content.
fn parse_gui_config(content: &str) -> GuiConfig {
    let mut config = GuiConfig::default();

    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') || line.starts_with('[') {
            continue;
        }

        if let Some((key, value)) = line.split_once('=') {
            let key = key.trim();
            let value = value.trim();

            match key {
                "tap_bridge_socket" => {
                    config.socket_path = PathBuf::from(value);
                }
                "remember_decisions" => {
                    config.remember_decisions = parse_bool(value);
                }
                _ => {}
            }
        }
    }

    config
}

/// Parse a boolean value from config.
fn parse_bool(s: &str) -> bool {
    matches!(s.to_lowercase().as_str(), "true" | "yes" | "1" | "on")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_config_sane() {
        let config = GuiConfig::default();
        assert!(config
            .socket_path
            .to_string_lossy()
            .contains("win-sandbox-runner.sock"));
        assert!(config.remember_decisions);
    }

    #[test]
    fn parse_custom_socket() {
        let content = "[network]\ntap_bridge_socket = /var/run/custom.sock\n";
        let config = parse_gui_config(content);
        assert_eq!(config.socket_path, PathBuf::from("/var/run/custom.sock"));
    }

    #[test]
    fn parse_bool_variants() {
        assert!(parse_bool("true"));
        assert!(parse_bool("yes"));
        assert!(parse_bool("1"));
        assert!(parse_bool("on"));
        assert!(!parse_bool("false"));
        assert!(!parse_bool("no"));
        assert!(!parse_bool("0"));
    }
}
