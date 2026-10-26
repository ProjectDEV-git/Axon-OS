use anyhow::{Context, Result};
use std::path::Path;
use tracing::{debug, info, warn};
use win_sandbox_common::rules_schema::RulesFile;

/// Load and validate a rules file from the given path.
/// If no path is provided, searches for rules.json in standard locations,
/// then falls back to the compiled-in default.
pub fn load_rules(path: Option<&Path>) -> Result<RulesFile> {
    if let Some(p) = path {
        debug!("Loading rules from: {}", p.display());
        let data = std::fs::read_to_string(p)
            .with_context(|| format!("Failed to read rules file: {}", p.display()))?;
        let rules: RulesFile = serde_json::from_str(&data)
            .with_context(|| format!("Failed to parse rules file: {}", p.display()))?;
        validate_rules(&rules)?;
        return Ok(rules);
    }

    // Search standard locations
    let search_paths = [
        "~/.config/win-sandbox/rules.json",
        "/etc/win-sandbox-runner/rules.json",
    ];

    for path_str in &search_paths {
        let expanded = expand_tilde(path_str);
        if expanded.exists() {
            match std::fs::read_to_string(&expanded) {
                Ok(data) => match serde_json::from_str::<RulesFile>(&data) {
                    Ok(rules) => {
                        if validate_rules(&rules).is_ok() {
                            info!("Loaded rules from: {}", expanded.display());
                            return Ok(rules);
                        }
                    }
                    Err(e) => warn!("Failed to parse {path_str}: {e}"),
                },
                Err(e) => warn!("Failed to read {path_str}: {e}"),
            }
        }
    }

    // Compiled-in fallback
    const EMBEDDED: &str = include_str!("../../../config/rules.json");
    match serde_json::from_str::<RulesFile>(EMBEDDED) {
        Ok(rules) => {
            if validate_rules(&rules).is_ok() {
                info!("Using compiled-in default rules");
                return Ok(rules);
            }
        }
        Err(e) => warn!("Failed to parse compiled-in rules: {e}"),
    }

    debug!("No rules file found, using empty defaults");
    Ok(default_rules())
}

/// Expand ~ to $HOME in a path string.
fn expand_tilde(path: &str) -> std::path::PathBuf {
    if let Some(rest) = path.strip_prefix("~/") {
        if let Ok(home) = std::env::var("HOME") {
            return std::path::PathBuf::from(format!("{home}/{rest}"));
        }
    }
    std::path::PathBuf::from(path)
}

/// Return default rules (no entries, permissive defaults).
fn default_rules() -> RulesFile {
    RulesFile {
        version: 1,
        entries: Vec::new(),
        defaults: Default::default(),
    }
}

/// Validate rules file structure.
fn validate_rules(rules: &RulesFile) -> Result<()> {
    if rules.version != 1 {
        anyhow::bail!("Unsupported rules version: {}", rules.version);
    }
    for entry in &rules.entries {
        if entry.hash.len() != 64 || !entry.hash.chars().all(|c| c.is_ascii_hexdigit()) {
            anyhow::bail!(
                "Invalid hash format for entry '{}': expected 64 hex chars, got '{}'",
                entry.name,
                entry.hash
            );
        }
    }
    Ok(())
}

/// Look up a rule entry by binary hash.
pub fn lookup_by_hash<'a>(
    rules: &'a RulesFile,
    hash: &str,
) -> Option<&'a win_sandbox_common::rules_schema::RuleEntry> {
    rules.entries.iter().find(|e| e.hash == hash)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_rules_are_valid() {
        let rules = default_rules();
        validate_rules(&rules).unwrap();
    }

    #[test]
    fn invalid_version_rejected() {
        let rules = RulesFile {
            version: 99,
            entries: vec![],
            defaults: Default::default(),
        };
        assert!(validate_rules(&rules).is_err());
    }

    #[test]
    fn invalid_hash_rejected() {
        let rules = RulesFile {
            version: 1,
            entries: vec![win_sandbox_common::rules_schema::RuleEntry {
                hash: "not-a-valid-hash".into(),
                name: "test".into(),
                tier: win_sandbox_common::tier::Tier::Tier0,
                allowed_paths: vec![],
                network: false,
                gpu: false,
                trusted: false,
                dxvk: false,
                winetricks: vec![],
                env: std::collections::HashMap::new(),
                wine_variant: "system".into(),
            }],
            defaults: Default::default(),
        };
        assert!(validate_rules(&rules).is_err());
    }

    #[test]
    fn load_nonexistent_returns_embedded_or_default() {
        let rules = load_rules(None).unwrap();
        assert_eq!(rules.version, 1);
        // With embedded fallback, we get the compiled-in rules (which have entries)
        // rather than empty defaults. This is the desired behavior.
    }
}
