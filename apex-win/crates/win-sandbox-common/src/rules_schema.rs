use crate::tier::Tier;
use serde::{Deserialize, Serialize};

/// Top-level rules file structure.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RulesFile {
    pub version: u32,
    #[serde(default)]
    pub entries: Vec<RuleEntry>,
    #[serde(default)]
    pub defaults: RuleDefaults,
}

/// A single rule entry keyed by binary SHA-256 hash.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuleEntry {
    /// SHA-256 hex string (64 chars).
    pub hash: String,
    /// Human-readable name for this binary.
    pub name: String,
    /// Sandbox tier to apply.
    pub tier: Tier,
    /// Filesystem paths the binary may access (beyond defaults).
    #[serde(default)]
    pub allowed_paths: Vec<String>,
    /// Whether network access is permitted.
    #[serde(default)]
    pub network: bool,
    /// Whether GPU passthrough is permitted.
    #[serde(default)]
    pub gpu: bool,
    /// Skip all sandboxing for this app — full access to filesystem, network,
    /// GPU, and host resources. Use for trusted apps that need unrestricted
    /// access (CAD software, game launchers, etc.).
    #[serde(default)]
    pub trusted: bool,
    /// Install DXVK (DirectX-to-Vulkan translation layer) for this app.
    #[serde(default)]
    pub dxvk: bool,
    /// Winetricks components to install before first run.
    /// Examples: ["dotnet48", "vcrun2019", "d3dx9", "corefonts"]
    #[serde(default)]
    pub winetricks: Vec<String>,
    /// Additional environment variables for this app.
    /// Example: {"DXVK_HUD": "1", "MESA_GL_VERSION_OVERRIDE": "4.5"}
    #[serde(default)]
    pub env: std::collections::HashMap<String, String>,
    /// Recommended Wine variant: "system" (default), "proton", "staging".
    /// Only used by the GUI to suggest the right Wine version.
    #[serde(default = "default_wine_variant")]
    pub wine_variant: String,
}

fn default_wine_variant() -> String {
    "system".to_string()
}

/// Default policy for unmapped binaries.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuleDefaults {
    /// Tier for binaries whose hash is not in entries.
    pub unmapped_tier: Tier,
    /// Tier for binaries launched from untrusted paths (/tmp, /mnt, etc.).
    pub untrusted_path_tier: Tier,
    /// Default network permission for unmapped binaries.
    #[serde(default)]
    pub network_default: bool,
    /// Default GPU permission for unmapped binaries.
    #[serde(default)]
    pub gpu_default: bool,
}

impl Default for RuleDefaults {
    fn default() -> Self {
        Self {
            unmapped_tier: Tier::Tier0,
            untrusted_path_tier: Tier::Tier2,
            network_default: false,
            gpu_default: false,
        }
    }
}
