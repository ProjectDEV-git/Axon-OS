use serde::{Deserialize, Serialize};
use std::path::Path;
use tracing::{debug, info, warn};
use win_sandbox_common::rules_schema::RuleEntry;
use win_sandbox_common::tier::Tier;

/// Built-in app database loaded from appdb.json.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppDatabase {
    pub version: u32,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub profiles: Vec<AppProfile>,
}

/// A single app profile in the built-in database.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppProfile {
    pub name: String,
    /// Exe filenames to match (case-insensitive). E.g. ["Fusion360.exe", "FusionLauncher.exe"]
    pub match_names: Vec<String>,
    pub tier: Tier,
    #[serde(default)]
    pub trusted: bool,
    #[serde(default)]
    pub network: bool,
    #[serde(default)]
    pub gpu: bool,
    #[serde(default)]
    pub dxvk: bool,
    #[serde(default)]
    pub winetricks: Vec<String>,
    #[serde(default)]
    pub env: std::collections::HashMap<String, String>,
    #[serde(default = "default_wine_variant")]
    pub wine_variant: String,
    #[serde(default)]
    pub notes: String,
}

fn default_wine_variant() -> String {
    "system".to_string()
}

impl AppDatabase {
    /// Load the built-in app database from the given path.
    pub fn load(path: &Path) -> anyhow::Result<Self> {
        let contents = std::fs::read_to_string(path)
            .map_err(|e| anyhow::anyhow!("Failed to read appdb.json: {e}"))?;
        let db: Self = serde_json::from_str(&contents)?;
        info!("Loaded app database: {} profiles", db.profiles.len());
        Ok(db)
    }

    /// Load the embedded app database (compiled into the binary).
    ///
    /// Search order:
    /// 1. User config: ~/.config/win-sandbox/appdb.json
    /// 2. System config: /etc/win-sandbox-runner/appdb.json
    /// 3. Compiled-in fallback: include_str!("../../../config/appdb.json")
    /// 4. Dev tree: config/appdb.json (relative to CWD)
    pub fn load_embedded() -> Self {
        // Try to load from the config directory first
        let search_paths = [
            "~/.config/win-sandbox/appdb.json",
            "/etc/win-sandbox-runner/appdb.json",
        ];

        for path_str in &search_paths {
            let path = expand_tilde(path_str);
            if path.exists() {
                match Self::load(&path) {
                    Ok(db) => return db,
                    Err(e) => warn!("Failed to load {path_str}: {e}"),
                }
            }
        }

        // Compiled-in fallback: always available, even if /etc is missing
        const EMBEDDED: &str = include_str!("../../../config/appdb.json");
        match serde_json::from_str::<Self>(EMBEDDED) {
            Ok(db) => {
                info!(
                    "Loaded compiled-in app database: {} profiles",
                    db.profiles.len()
                );
                return db;
            }
            Err(e) => warn!("Failed to parse compiled-in appdb: {e}"),
        }

        // Dev tree fallback (only works when running from source directory)
        let dev_path = Path::new("config/appdb.json");
        if dev_path.exists() {
            match Self::load(dev_path) {
                Ok(db) => return db,
                Err(e) => warn!("Failed to load dev appdb: {e}"),
            }
        }

        warn!("No app database found, running with empty database");
        Self {
            version: 1,
            description: "Empty database".into(),
            profiles: vec![],
        }
    }

    /// Look up an app profile by exe filename (case-insensitive).
    /// Handles both Linux and Windows path separators.
    /// Returns the matching profile and its converted RuleEntry.
    pub fn lookup_by_name(&self, exe_path: &str) -> Option<(&AppProfile, RuleEntry)> {
        // Extract the filename, handling both / and \ as separators
        let exe_name = exe_path
            .rsplit_once('/')
            .or_else(|| exe_path.rsplit_once('\\'))
            .map(|(_, rest)| rest)
            .unwrap_or(exe_path);

        let exe_lower = exe_name.to_lowercase();

        for profile in &self.profiles {
            for match_name in &profile.match_names {
                if match_name.to_lowercase() == exe_lower {
                    debug!("App database match: '{}' -> '{}'", exe_name, profile.name);
                    return Some((profile, profile.to_rule_entry()));
                }
            }
        }

        None
    }
}

impl AppProfile {
    /// Convert this app profile to a RuleEntry (for dispatch).
    ///
    /// A profile is matched by **file name only**, which any binary can
    /// claim (`evil.exe` renamed to `Fusion360.exe`). So a name match may
    /// choose sandbox *settings* (GPU, DXVK, winetricks, network) but can
    /// never grant trust or Tier 0: `trusted` is always false and Tier 0 is
    /// raised to Tier 1. Real trust comes only from a hash the user approved
    /// (`--trust`, which writes rules.json). The profile's `trusted` field is
    /// kept as a hint the wizard can show when suggesting `--trust`.
    pub fn to_rule_entry(&self) -> RuleEntry {
        let tier = if self.tier == Tier::Tier0 {
            Tier::Tier1
        } else {
            self.tier
        };
        RuleEntry {
            hash: format!("appdb:{}", self.name.to_lowercase().replace(' ', "_")),
            name: self.name.clone(),
            tier,
            allowed_paths: vec![],
            network: self.network,
            gpu: self.gpu,
            trusted: false,
            dxvk: self.dxvk,
            winetricks: self.winetricks.clone(),
            env: self.env.clone(),
            wine_variant: self.wine_variant.clone(),
        }
    }
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

/// Auto-detect the appropriate tier for an unknown .exe based on heuristics.
/// Returns (suggested_tier, reason).
pub fn auto_detect_tier(exe_path: &str) -> (Tier, &'static str) {
    let name = Path::new(exe_path)
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("")
        .to_lowercase();

    // Installers/setup — untrusted, run in sandbox
    if name.contains("setup") || name.contains("install") || name.contains("update") {
        return (
            Tier::Tier2,
            "Installer/setup detected — running in sandbox for safety",
        );
    }

    // Known safe patterns
    let safe_patterns = [
        "notepad",
        "calc",
        "mspaint",
        "winrar",
        "7z",
        "vlc",
        "foobar",
        "putty",
        "filezilla",
        "irfanview",
    ];
    for pattern in &safe_patterns {
        if name.contains(pattern) {
            return (
                Tier::Tier1,
                "Known safe application — running with light sandboxing",
            );
        }
    }

    // Games — need network + GPU, use tier 2
    let game_patterns = [
        "game",
        "launcher",
        "client",
        "steam",
        "epic",
        "gog",
        "origin",
        "battle.net",
        "riot",
        "minecraft",
        "fortnite",
        "genshin",
    ];
    for pattern in &game_patterns {
        if name.contains(pattern) {
            return (
                Tier::Tier2,
                "Game/launcher detected — running with network and GPU",
            );
        }
    }

    // Unknown — tier 1 is a safe default (light sandbox, filesystem protection)
    (
        Tier::Tier1,
        "Unknown application — running with light sandboxing",
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn auto_detect_installer() {
        let (tier, _) = auto_detect_tier("/tmp/setup.exe");
        assert_eq!(tier, Tier::Tier2);

        let (tier, _) = auto_detect_tier("C:\\install_myapp.exe");
        assert_eq!(tier, Tier::Tier2);
    }

    #[test]
    fn auto_detect_safe_app() {
        let (tier, _) = auto_detect_tier("C:\\Program Files\\7-Zip\\7zFM.exe");
        assert_eq!(tier, Tier::Tier1);

        let (tier, _) = auto_detect_tier("notepad++.exe");
        assert_eq!(tier, Tier::Tier1);
    }

    #[test]
    fn auto_detect_game() {
        let (tier, _) = auto_detect_tier("Steam.exe");
        assert_eq!(tier, Tier::Tier2);
    }

    #[test]
    fn auto_detect_unknown() {
        let (tier, _) = auto_detect_tier("random_app.exe");
        assert_eq!(tier, Tier::Tier1);
    }

    #[test]
    fn app_profile_to_rule_entry() {
        let profile = AppProfile {
            name: "Test App".into(),
            match_names: vec!["test.exe".into()],
            tier: Tier::Tier2,
            trusted: true,
            network: true,
            gpu: true,
            dxvk: true,
            winetricks: vec!["dotnet48".into()],
            env: std::collections::HashMap::new(),
            wine_variant: "proton".into(),
            notes: "Test".into(),
        };

        let entry = profile.to_rule_entry();
        assert_eq!(entry.name, "Test App");
        assert!(!entry.trusted, "a name match must never grant trust");
        assert!(entry.dxvk);
        assert_eq!(entry.winetricks, vec!["dotnet48"]);
        assert!(entry.hash.starts_with("appdb:"));
    }

    #[test]
    fn appdb_lookup_by_name() {
        let db = AppDatabase {
            version: 1,
            description: "test".into(),
            profiles: vec![AppProfile {
                name: "Fusion 360".into(),
                match_names: vec!["Fusion360.exe".into(), "FusionLauncher.exe".into()],
                tier: Tier::Tier0,
                trusted: true,
                network: true,
                gpu: true,
                dxvk: true,
                winetricks: vec!["dotnet48".into()],
                env: std::collections::HashMap::new(),
                wine_variant: "staging".into(),
                notes: "".into(),
            }],
        };

        // Case-insensitive match
        let result = db.lookup_by_name("/home/user/.wine/drive_c/Fusion360.exe");
        assert!(result.is_some());
        let (profile, entry) = result.unwrap();
        assert_eq!(profile.name, "Fusion 360");
        assert!(profile.trusted, "profile hint is preserved");
        assert!(
            !entry.trusted,
            "renaming any exe to Fusion360.exe must not unsandbox it"
        );
        assert_eq!(
            entry.tier,
            Tier::Tier1,
            "Tier 0 profiles are raised to Tier 1"
        );

        // Case variation
        let result = db.lookup_by_name("fusion360.exe");
        assert!(result.is_some());

        // No match
        let result = db.lookup_by_name("notepad.exe");
        assert!(result.is_none());
    }
}
