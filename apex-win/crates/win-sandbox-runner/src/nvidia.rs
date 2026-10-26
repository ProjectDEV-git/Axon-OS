use std::path::Path;
use tracing::{debug, info};

/// Information about an Nvidia GPU for bind-mounting into containers.
#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct NvidiaInfo {
    /// Device files to bind-mount (e.g., /dev/nvidia0, /dev/nvidiactl).
    pub devices: Vec<String>,
    /// Library paths to bind-mount (e.g., /usr/lib/x86_64-linux-gnu/libnvidia-ml.so*).
    pub libs: Vec<String>,
    /// nvidia-smi binary path.
    pub smi_path: Option<String>,
}

/// Detect Nvidia GPU presence and return info for bind-mounting.
pub fn detect() -> Option<NvidiaInfo> {
    // Check for nvidia driver
    if !Path::new("/proc/driver/nvidia/version").exists() {
        debug!("No Nvidia driver found in /proc/driver/nvidia/version");
        return None;
    }

    info!("Nvidia GPU detected");

    let mut devices = Vec::new();
    // Check for device nodes
    for i in 0..8 {
        let dev = format!("/dev/nvidia{i}");
        if Path::new(&dev).exists() {
            devices.push(dev);
        }
    }
    if Path::new("/dev/nvidiactl").exists() {
        devices.push("/dev/nvidiactl".into());
    }
    if Path::new("/dev/nvidia-uvm").exists() {
        devices.push("/dev/nvidia-uvm".into());
    }

    // Find nvidia-smi
    let smi_path = which("nvidia-smi");

    Some(NvidiaInfo {
        devices,
        libs: detect_nvidia_libs(),
        smi_path,
    })
}

/// Detect Nvidia shared library paths in standard locations.
fn detect_nvidia_libs() -> Vec<String> {
    let lib_dirs = ["/usr/lib/x86_64-linux-gnu", "/usr/lib64", "/usr/lib"];

    let mut libs = Vec::new();
    for dir in &lib_dirs {
        if let Ok(entries) = std::fs::read_dir(dir) {
            for entry in entries.flatten() {
                let name = entry.file_name();
                let name_str = name.to_string_lossy();
                if name_str.starts_with("libnvidia") || name_str.starts_with("libcuda") {
                    libs.push(entry.path().to_string_lossy().to_string());
                }
            }
        }
    }
    libs
}

/// Check if a command exists in PATH.
fn which(cmd: &str) -> Option<String> {
    if let Ok(path) = std::env::var("PATH") {
        for dir in path.split(':') {
            let full = format!("{dir}/{cmd}");
            if Path::new(&full).exists() {
                return Some(full);
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detect_returns_option() {
        // Just verify it doesn't panic; result depends on hardware
        let _info = detect();
    }

    #[test]
    fn which_finds_ls() {
        assert!(which("ls").is_some());
    }

    #[test]
    fn which_misses_nonexistent() {
        assert!(which("definitely_not_a_real_command_12345").is_none());
    }
}
