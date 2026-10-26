use std::path::Path;
use tracing::{debug, info};

/// Information about AMD GPU for bind-mounting into containers.
#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct AmdInfo {
    /// DRI render node device files (e.g., /dev/dri/renderD128).
    pub render_nodes: Vec<String>,
    /// DRM card device files (e.g., /dev/dri/card0) — usually NOT bind-mounted (privileged).
    pub card_nodes: Vec<String>,
}

/// Detect AMD GPU via DRI render nodes.
/// Returns render nodes suitable for bind-mounting (non-privileged).
pub fn detect() -> Option<AmdInfo> {
    let dri_dir = Path::new("/dev/dri");
    if !dri_dir.exists() {
        debug!("No /dev/dri directory found");
        return None;
    }

    let mut render_nodes = Vec::new();
    let mut card_nodes = Vec::new();

    for entry in std::fs::read_dir(dri_dir).ok()? {
        let entry = entry.ok()?;
        let name = entry.file_name().to_string_lossy().to_string();
        let path = format!("/dev/dri/{name}");

        if name.starts_with("renderD") {
            render_nodes.push(path);
        } else if name.starts_with("card") {
            card_nodes.push(path);
        }
    }

    if render_nodes.is_empty() {
        debug!("No DRI render nodes found");
        return None;
    }

    info!(
        "AMD/Intel GPU detected: {} render nodes",
        render_nodes.len()
    );

    Some(AmdInfo {
        render_nodes,
        card_nodes,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detect_returns_option() {
        // Result depends on hardware; just verify no panic
        let _info = detect();
    }
}
