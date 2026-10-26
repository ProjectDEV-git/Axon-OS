use std::path::PathBuf;
use tracing::debug;

/// Paths where rules.json is searched, in priority order.
const RULES_SEARCH_PATHS: &[&str] = &[
    "~/.config/win-sandbox/rules.json",
    "/etc/win-sandbox-runner/rules.json",
];

/// Paths where the config file is searched.
const CONFIG_SEARCH_PATHS: &[&str] = &[
    "~/.config/win-sandbox/win-sandbox-runner.conf",
    "/etc/win-sandbox-runner.conf",
];

/// Runtime configuration.
#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct Config {
    pub wine_prefix: String,
    pub rules_path: Option<PathBuf>,
    pub gui_enabled: bool,
    pub default_tier: u8,
    pub display_mode: DisplayMode,
    pub log_level: String,
    pub tap_bridge_socket: String,
    pub tap_device: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
#[allow(dead_code)]
pub enum DisplayMode {
    HostX11,
    NestedX11,
    Xvfb,
    Wayland,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            wine_prefix: default_wine_prefix(),
            rules_path: None,
            gui_enabled: true,
            default_tier: 0,
            display_mode: DisplayMode::NestedX11,
            log_level: "info".into(),
            tap_bridge_socket: "/var/run/win-tap-bridge.sock".into(),
            tap_device: "winrunner-tap0".into(),
        }
    }
}

/// Return the default WINEPREFIX (~/.wine).
fn default_wine_prefix() -> String {
    dirs_or_fallback("HOME", ".wine")
}

/// Build a path from $HOME + suffix, or fallback.
fn dirs_or_fallback(home_env: &str, suffix: &str) -> String {
    if let Ok(home) = std::env::var(home_env) {
        format!("{home}/{suffix}")
    } else {
        format!("/tmp/{suffix}")
    }
}

/// Find the rules.json path: explicit override > user config > system config.
pub fn find_rules_path(override_path: Option<&str>) -> Option<PathBuf> {
    if let Some(p) = override_path {
        return Some(PathBuf::from(p));
    }
    for path in RULES_SEARCH_PATHS {
        let expanded = expand_tilde(path);
        if expanded.exists() {
            debug!("Found rules at: {}", expanded.display());
            return Some(expanded);
        }
    }
    None
}

/// Find and load the INI config file.
pub fn load_config(override_path: Option<&str>) -> Config {
    let mut config = Config::default();

    // Try to load from file
    let config_path = if let Some(p) = override_path {
        Some(PathBuf::from(p))
    } else {
        CONFIG_SEARCH_PATHS.iter().find_map(|p| {
            let expanded = expand_tilde(p);
            if expanded.exists() {
                Some(expanded)
            } else {
                None
            }
        })
    };

    if let Some(path) = config_path {
        debug!("Loading config from: {}", path.display());
        if let Ok(contents) = std::fs::read_to_string(&path) {
            parse_ini_config(&contents, &mut config);
        }
    }

    // Environment variable overrides
    if let Ok(prefix) = std::env::var("WINEPREFIX") {
        config.wine_prefix = prefix;
    }

    config
}

/// Expand ~ to $HOME in a path string.
fn expand_tilde(path: &str) -> PathBuf {
    if let Some(rest) = path.strip_prefix("~/") {
        if let Ok(home) = std::env::var("HOME") {
            return PathBuf::from(format!("{home}/{rest}"));
        }
    }
    PathBuf::from(path)
}

/// Parse a simple INI-format config file into a Config struct.
///
/// Supports `[section]` headers and `key = value` pairs.
/// Comments start with `#` or `;`.
fn parse_ini_config(contents: &str, config: &mut Config) {
    let mut _section = String::new();

    for line in contents.lines() {
        let line = line.trim();

        // Skip empty lines and comments
        if line.is_empty() || line.starts_with('#') || line.starts_with(';') {
            continue;
        }

        // Section header
        if line.starts_with('[') && line.ends_with(']') {
            _section = line[1..line.len() - 1].to_string();
            continue;
        }

        // Key = value
        if let Some((key, value)) = line.split_once('=') {
            let key = key.trim();
            let value = value.trim();

            match key {
                "prefix" => config.wine_prefix = value.to_string(),
                "rules_path" => config.rules_path = Some(PathBuf::from(value)),
                "gui_enabled" => config.gui_enabled = parse_bool(value),
                "default_tier" => {
                    if let Ok(t) = value.parse() {
                        config.default_tier = t;
                    }
                }
                "display_mode" => {
                    config.display_mode = match value {
                        "host-x11" => DisplayMode::HostX11,
                        "nested-x11" => DisplayMode::NestedX11,
                        "xvfb" => DisplayMode::Xvfb,
                        "wayland" => DisplayMode::Wayland,
                        _ => DisplayMode::NestedX11,
                    };
                }
                "level" if _section == "logging" => {
                    config.log_level = value.to_string();
                }
                "tap_bridge_socket" => {
                    config.tap_bridge_socket = value.to_string();
                }
                "tap_device" => config.tap_device = value.to_string(),
                _ => {
                    debug!("Unknown config key: [{_section}] {key}");
                }
            }
        }
    }
}

/// Parse a boolean string ("true", "1", "yes", "on" vs "false", "0", "no", "off").
fn parse_bool(s: &str) -> bool {
    matches!(s.to_lowercase().as_str(), "true" | "1" | "yes" | "on")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_config_sane() {
        let config = Config::default();
        assert!(config.gui_enabled);
        assert_eq!(config.default_tier, 0);
        assert_eq!(config.display_mode, DisplayMode::NestedX11);
    }

    #[test]
    fn expand_tilde_works() {
        // Only test if HOME is set
        if std::env::var("HOME").is_ok() {
            let expanded = expand_tilde("~/test/path");
            assert!(expanded.to_str().unwrap().ends_with("/test/path"));
            assert!(!expanded.to_str().unwrap().starts_with("~"));
        }
    }

    #[test]
    fn find_rules_no_override() {
        // Should return None if no files exist (common in test env)
        let result = find_rules_path(None);
        // We just check it doesn't panic
        let _ = result;
    }

    #[test]
    fn parse_ini_full_config() {
        let ini = "\
[wine]
prefix = /home/test/.wine-test

[sandbox]
rules_path = /etc/test/rules.json
gui_enabled = false
default_tier = 2
display_mode = wayland

[logging]
level = debug

[network]
tap_bridge_socket = /var/run/test.sock
tap_device = test-tap0
";
        let mut config = Config::default();
        parse_ini_config(ini, &mut config);
        assert_eq!(config.wine_prefix, "/home/test/.wine-test");
        assert_eq!(
            config.rules_path.unwrap().to_str().unwrap(),
            "/etc/test/rules.json"
        );
        assert!(!config.gui_enabled);
        assert_eq!(config.default_tier, 2);
        assert_eq!(config.display_mode, DisplayMode::Wayland);
        assert_eq!(config.log_level, "debug");
        assert_eq!(config.tap_bridge_socket, "/var/run/test.sock");
        assert_eq!(config.tap_device, "test-tap0");
    }

    #[test]
    fn parse_ini_comments_and_blanks() {
        let ini = "\
# This is a comment
; This is also a comment

[wine]
# prefix = not_this
prefix = /actual/path
";
        let mut config = Config::default();
        parse_ini_config(ini, &mut config);
        assert_eq!(config.wine_prefix, "/actual/path");
    }

    #[test]
    fn parse_bool_variants() {
        assert!(parse_bool("true"));
        assert!(parse_bool("1"));
        assert!(parse_bool("yes"));
        assert!(parse_bool("on"));
        assert!(!parse_bool("false"));
        assert!(!parse_bool("0"));
        assert!(!parse_bool("no"));
        assert!(!parse_bool("off"));
    }
}
