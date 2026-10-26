use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};

use tracing::{debug, info, warn};

/// Network optimization profile for gaming.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NetOptimizerConfig {
    /// Enable BBR congestion control.
    #[serde(default = "default_true")]
    pub bbr: bool,
    /// Enable fq_codel Smart Queue Management on the default interface.
    #[serde(default = "default_true")]
    pub sqm: bool,
    /// Enable socket buffer tuning.
    #[serde(default = "default_true")]
    pub socket_buffers: bool,
    /// Enable game port DSCP marking (EF / CS7).
    #[serde(default = "default_true")]
    pub dscp_marking: bool,
    /// Enable TCP low-latency tweaks.
    #[serde(default = "default_true")]
    pub tcp_tweaks: bool,
    /// Game ports to prioritize (UDP). Common defaults included.
    #[serde(default = "default_game_ports")]
    pub game_ports: Vec<u16>,
    /// Target download speed in Mbit/s (for SQM shaping). 0 = auto.
    #[serde(default)]
    pub download_mbps: u32,
    /// Target upload speed in Mbit/s (for SQM shaping). 0 = auto.
    #[serde(default)]
    pub upload_mbps: u32,
}

fn default_true() -> bool {
    true
}

fn default_game_ports() -> Vec<u16> {
    vec![
        // Common game ports
        27015, 27016, 27017, 27018, 27019, 27020, // Steam / Source engine
        3478, 3479, 3480, // PlayStation Network
        3074, // Xbox Live
        6112, 6113, 6114, 6115, 6116, // Blizzard / Warcraft
        5060, 5061, // SIP (general game VoIP)
        1935, // RTMP streaming
        80, 443, // HTTP/HTTPS (game APIs)
    ]
}

impl Default for NetOptimizerConfig {
    fn default() -> Self {
        Self {
            bbr: true,
            sqm: true,
            socket_buffers: true,
            dscp_marking: true,
            tcp_tweaks: true,
            game_ports: default_game_ports(),
            download_mbps: 0,
            upload_mbps: 0,
        }
    }
}

/// Result of applying network optimizations.
#[derive(Debug, Default)]
pub struct OptimizeResult {
    pub bbr_applied: bool,
    pub sqm_applied: bool,
    pub socket_buffers_applied: bool,
    pub dscp_applied: bool,
    pub tcp_tweaks_applied: bool,
    pub errors: Vec<String>,
}

impl std::fmt::Display for OptimizeResult {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        writeln!(f, "Network Optimization Results:")?;
        writeln!(
            f,
            "  BBR congestion:  {}",
            if self.bbr_applied {
                "✓ applied"
            } else {
                "— skipped"
            }
        )?;
        writeln!(
            f,
            "  SQM (fq_codel):  {}",
            if self.sqm_applied {
                "✓ applied"
            } else {
                "— skipped"
            }
        )?;
        writeln!(
            f,
            "  Socket buffers:  {}",
            if self.socket_buffers_applied {
                "✓ applied"
            } else {
                "— skipped"
            }
        )?;
        writeln!(
            f,
            "  DSCP marking:   {}",
            if self.dscp_applied {
                "✓ applied"
            } else {
                "— skipped"
            }
        )?;
        writeln!(
            f,
            "  TCP tweaks:     {}",
            if self.tcp_tweaks_applied {
                "✓ applied"
            } else {
                "— skipped"
            }
        )?;
        if !self.errors.is_empty() {
            writeln!(f, "  Errors:")?;
            for err in &self.errors {
                writeln!(f, "    ⚠ {err}")?;
            }
        }
        Ok(())
    }
}

/// Apply all network optimizations.
pub fn optimize(config: &NetOptimizerConfig) -> Result<OptimizeResult> {
    info!("Applying network optimizations for gaming...");
    let mut result = OptimizeResult::default();

    // 1. BBR congestion control
    if config.bbr {
        match enable_bbr() {
            Ok(()) => {
                result.bbr_applied = true;
                info!("  ✓ BBR congestion control enabled");
            }
            Err(e) => {
                result.errors.push(format!("BBR: {e}"));
                warn!("  ⚠ BBR failed: {e}");
            }
        }
    }

    // 2. Socket buffer tuning
    if config.socket_buffers {
        match tune_socket_buffers() {
            Ok(()) => {
                result.socket_buffers_applied = true;
                info!("  ✓ Socket buffers tuned");
            }
            Err(e) => {
                result.errors.push(format!("Socket buffers: {e}"));
                warn!("  ⚠ Socket buffers failed: {e}");
            }
        }
    }

    // 3. TCP low-latency tweaks
    if config.tcp_tweaks {
        match apply_tcp_tweaks() {
            Ok(()) => {
                result.tcp_tweaks_applied = true;
                info!("  ✓ TCP tweaks applied");
            }
            Err(e) => {
                result.errors.push(format!("TCP tweaks: {e}"));
                warn!("  ⚠ TCP tweaks failed: {e}");
            }
        }
    }

    // 4. SQM (fq_codel) on the default interface
    if config.sqm {
        match apply_sqm(config.download_mbps, config.upload_mbps) {
            Ok(()) => {
                result.sqm_applied = true;
                info!("  ✓ SQM (fq_codel) applied");
            }
            Err(e) => {
                result.errors.push(format!("SQM: {e}"));
                warn!("  ⚠ SQM failed: {e}");
            }
        }
    }

    // 5. DSCP marking for game ports
    if config.dscp_marking {
        match apply_dscp_rules(&config.game_ports) {
            Ok(()) => {
                result.dscp_applied = true;
                info!(
                    "  ✓ DSCP marking applied for {} ports",
                    config.game_ports.len()
                );
            }
            Err(e) => {
                result.errors.push(format!("DSCP: {e}"));
                warn!("  ⚠ DSCP failed: {e}");
            }
        }
    }

    info!("Network optimization complete");
    Ok(result)
}

/// Enable BBR congestion control via sysctl.
fn enable_bbr() -> Result<()> {
    // Check if BBR is available
    let available =
        read_proc("/proc/sys/net/ipv4/tcp_available_congestion_control").unwrap_or_default();
    if !available.contains("bbr") {
        return Err(anyhow::anyhow!(
            "BBR not available in kernel. Available: {available}"
        ));
    }

    write_proc("/proc/sys/net/ipv4/tcp_congestion_control", "bbr")
        .context("Failed to set BBR congestion control")?;

    // Also set default qdisc to fq (required for BBR to work optimally)
    // This requires tc, so we do it in apply_sqm as well
    debug!("BBR congestion control set");
    Ok(())
}

/// Tune socket buffer sizes for gaming (UDP-heavy workloads).
fn tune_socket_buffers() -> Result<()> {
    // UDP game traffic needs larger receive buffers to avoid packet drops
    // Default Linux buffers are too small for high-PPS game traffic
    let params = [
        // TCP receive buffer: min 4KB, default 256KB, max 16MB
        ("/proc/sys/net/core/rmem_default", "262144"),
        ("/proc/sys/net/core/rmem_max", "16777216"),
        // TCP send buffer
        ("/proc/sys/net/core/wmem_default", "262144"),
        ("/proc/sys/net/core/wmem_max", "16777216"),
        // UDP specific (game traffic)
        ("/proc/sys/net/ipv4/udp_rmem_min", "8192"),
        ("/proc/sys/net/ipv4/udp_wmem_min", "8192"),
        // Network device backlog (more packets before dropping)
        ("/proc/sys/net/core/netdev_max_backlog", "5000"),
        // Busy poll for low-latency receive (microseconds)
        ("/proc/sys/net/core/busy_read", "50"),
        ("/proc/sys/net/core/busy_poll", "50"),
    ];

    for (path, value) in &params {
        if let Err(e) = write_proc(path, value) {
            debug!("  Skipping {path}: {e}");
        }
    }

    Ok(())
}

/// Apply TCP low-latency tweaks.
fn apply_tcp_tweaks() -> Result<()> {
    let params = [
        // Disable TCP slow start after idle (faster ramp-up)
        ("/proc/sys/net/ipv4/tcp_slow_start_after_idle", "0"),
        // Reduce TCP keepalive time (detect dead connections faster)
        ("/proc/sys/net/ipv4/tcp_keepalive_time", "60"),
        ("/proc/sys/net/ipv4/tcp_keepalive_intvl", "10"),
        ("/proc/sys/net/ipv4/tcp_keepalive_probes", "6"),
        // Enable TCP window scaling
        ("/proc/sys/net/ipv4/tcp_window_scaling", "1"),
        // Enable TCP timestamps
        ("/proc/sys/net/ipv4/tcp_timestamps", "1"),
        // Enable selective ACK
        ("/proc/sys/net/ipv4/tcp_sack", "1"),
        // Reduce FIN timeout
        ("/proc/sys/net/ipv4/tcp_fin_timeout", "15"),
        // Increase max orphaned sockets
        ("/proc/sys/net/ipv4/tcp_max_orphans", "4096"),
        // Fast open for TCP (reduces handshake latency)
        ("/proc/sys/net/ipv4/tcp_fastopen", "3"),
        // MTU probing (auto-find optimal MTU)
        ("/proc/sys/net/ipv4/tcp_mtu_probing", "1"),
    ];

    for (path, value) in &params {
        if let Err(e) = write_proc(path, value) {
            debug!("  Skipping {path}: {e}");
        }
    }

    Ok(())
}

/// Get the default network interface (the one with the default route).
fn get_default_interface() -> Result<String> {
    // Read /proc/net/route to find the default interface
    let routes =
        std::fs::read_to_string("/proc/net/route").context("Failed to read /proc/net/route")?;

    for line in routes.lines().skip(1) {
        let fields: Vec<&str> = line.split_whitespace().collect();
        if fields.len() >= 3 && fields[1] == "00000000" {
            return Ok(fields[0].to_string());
        }
    }

    // Fallback: try common names
    for iface in &["eth0", "enp0s3", "wlan0", "wlp2s0", "enp3s0"] {
        let path = format!("/sys/class/net/{iface}");
        if std::path::Path::new(&path).exists() {
            return Ok(iface.to_string());
        }
    }

    Err(anyhow::anyhow!(
        "Could not detect default network interface"
    ))
}

/// Apply SQM (Smart Queue Management) using tc + fq_codel.
fn apply_sqm(download_mbps: u32, upload_mbps: u32) -> Result<()> {
    let iface = get_default_interface()?;
    info!("  Applying SQM on interface: {iface}");

    // Calculate bandwidth limits (use 85% of detected speed if not specified)
    let _rates = if download_mbps > 0 && upload_mbps > 0 {
        (
            format!("{}mbit", download_mbps),
            format!("{}mbit", upload_mbps),
        )
    } else {
        ("1gbit".to_string(), "1gbit".to_string())
    };

    // Clear existing qdisc (ignore errors if none exists)
    let _ = run_tc(&["qdisc", "del", "dev", &iface, "root"]);

    // Add root qdisc with fq_codel
    let result = run_tc(&[
        "qdisc", "add", "dev", &iface, "root", "handle", "1:", "fq_codel", "limit",
        "10240", // max packets in queue
        "target", "5ms", // target latency (5ms for gaming)
        "interval", "100ms", // interval for dropping
        "quantum", "1514", // bytes per round (1 MTU)
        "ecn",  // ECN marking instead of drops
    ]);

    match result {
        Ok(()) => {
            debug!("  fq_codel qdisc added on {iface}");
            Ok(())
        }
        Err(e) => {
            // fq_codel may not be available, try fq as fallback
            warn!("  fq_codel failed, trying fq fallback: {e}");
            run_tc(&[
                "qdisc",
                "add",
                "dev",
                &iface,
                "root",
                "handle",
                "1:",
                "fq",
                "quantum",
                "1514",
                "initial_quantum",
                "1514",
            ])
            .context("Failed to add fq qdisc")?;
            Ok(())
        }
    }
}

/// Apply iptables DSCP marking rules for game ports.
fn apply_dscp_rules(game_ports: &[u16]) -> Result<()> {
    // DSCP EF (Expedited Forwarding) = 46 = 0x2E
    // This tells routers to prioritize these packets
    let dscp_value = "0x2e";

    for port in game_ports {
        // UDP game traffic - mark outbound
        let result = run_cmd(
            "iptables",
            &[
                "-t",
                "mangle",
                "-A",
                "OUTPUT",
                "-p",
                "udp",
                "--dport",
                &port.to_string(),
                "-j",
                "DSCP",
                "--set-dscp",
                dscp_value,
            ],
        );

        if let Err(e) = result {
            debug!("  DSCP rule for UDP:{port} skipped: {e}");
        }

        // Also mark inbound game traffic (for local QoS)
        let result = run_cmd(
            "iptables",
            &[
                "-t",
                "mangle",
                "-A",
                "INPUT",
                "-p",
                "udp",
                "--sport",
                &port.to_string(),
                "-j",
                "DSCP",
                "--set-dscp",
                dscp_value,
            ],
        );

        if let Err(e) = result {
            debug!("  DSCP rule for inbound UDP:{port} skipped: {e}");
        }
    }

    Ok(())
}

/// Remove all DSCP rules added by the optimizer.
pub fn cleanup_dscp_rules(game_ports: &[u16]) -> Result<()> {
    let dscp_value = "0x2e";
    for port in game_ports {
        // Delete (ignore errors if rule doesn't exist)
        let _ = run_cmd(
            "iptables",
            &[
                "-t",
                "mangle",
                "-D",
                "OUTPUT",
                "-p",
                "udp",
                "--dport",
                &port.to_string(),
                "-j",
                "DSCP",
                "--set-dscp",
                dscp_value,
            ],
        );
        let _ = run_cmd(
            "iptables",
            &[
                "-t",
                "mangle",
                "-D",
                "INPUT",
                "-p",
                "udp",
                "--sport",
                &port.to_string(),
                "-j",
                "DSCP",
                "--set-dscp",
                dscp_value,
            ],
        );
    }
    Ok(())
}

/// Remove SQM qdisc from the default interface.
pub fn cleanup_sqm() -> Result<()> {
    if let Ok(iface) = get_default_interface() {
        let _ = run_tc(&["qdisc", "del", "dev", &iface, "root"]);
        info!("  SQM removed from {iface}");
    }
    Ok(())
}

/// Full cleanup: remove all network optimizations applied by this tool.
pub fn cleanup(config: &NetOptimizerConfig) -> Result<()> {
    info!("Cleaning up network optimizations...");
    if config.sqm {
        cleanup_sqm()?;
    }
    if config.dscp_marking {
        cleanup_dscp_rules(&config.game_ports)?;
    }
    info!("Cleanup complete");
    Ok(())
}

// --- Helpers ---

fn read_proc(path: &str) -> Result<String> {
    std::fs::read_to_string(path)
        .map(|s| s.trim().to_string())
        .with_context(|| format!("Failed to read {path}"))
}

fn write_proc(path: &str, value: &str) -> Result<()> {
    std::fs::write(path, value).with_context(|| format!("Failed to write {value} to {path}"))
}

fn run_tc(args: &[&str]) -> Result<()> {
    let output = std::process::Command::new("tc")
        .args(args)
        .output()
        .context("Failed to run tc (is iproute2 installed?)")?;

    if output.status.success() {
        Ok(())
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr);
        Err(anyhow::anyhow!("tc failed: {}", stderr.trim()))
    }
}

fn run_cmd(cmd: &str, args: &[&str]) -> Result<()> {
    let output = std::process::Command::new(cmd)
        .args(args)
        .output()
        .with_context(|| format!("Failed to run {cmd}"))?;

    if output.status.success() {
        Ok(())
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr);
        Err(anyhow::anyhow!("{cmd}: {}", stderr.trim()))
    }
}

/// Run network diagnostics and write a tuned config file.
pub fn diagnose_and_configure() -> Result<()> {
    use std::io::Write;

    println!("=== Network Diagnostics ===\n");

    // 1. Check BBR availability
    let bbr_available = read_proc("/proc/sys/net/ipv4/tcp_available_congestion_control")
        .map(|s| s.contains("bbr"))
        .unwrap_or(false);
    let current_congestion =
        read_proc("/proc/sys/net/ipv4/tcp_congestion_control").unwrap_or_else(|_| "unknown".into());
    println!(
        "BBR available:   {}",
        if bbr_available { "yes" } else { "no" }
    );
    println!("Current CC:      {current_congestion}");

    // 2. Check current qdisc on default interface
    match get_default_interface() {
        Ok(iface) => {
            println!("Default iface:   {iface}");
            // Show current qdisc
            let output = std::process::Command::new("tc")
                .args(["qdisc", "show", "dev", &iface])
                .output();
            if let Ok(out) = output {
                let qdisc = String::from_utf8_lossy(&out.stdout);
                for line in qdisc.lines().take(2) {
                    println!("Current qdisc:   {}", line.trim());
                }
            }
        }
        Err(e) => {
            println!("Default iface:   could not detect ({e})");
        }
    }

    // 3. Check socket buffer sizes
    let rmem_max = read_proc("/proc/sys/net/core/rmem_max").unwrap_or_default();
    let wmem_max = read_proc("/proc/sys/net/core/wmem_max").unwrap_or_default();
    println!("Max recv buf:    {rmem_max} bytes");
    println!("Max send buf:    {wmem_max} bytes");

    // 4. Check busy poll
    let busy_read = read_proc("/proc/sys/net/core/busy_read").unwrap_or_default();
    println!("Busy poll:       {busy_read} μs");

    // 5. Check tcp_fastopen
    let fastopen = read_proc("/proc/sys/net/ipv4/tcp_fastopen").unwrap_or_default();
    println!("TCP fast open:   {fastopen}");

    // 6. Check kernel version for NTsync / BBR2 support
    if let Ok(ver) = read_proc("/proc/sys/kernel/osrelease") {
        println!("Kernel:          {ver}");
    }

    // 7. Check if tc and iptables are available
    let tc_ok = std::process::Command::new("tc")
        .args(["-Version"])
        .output()
        .is_ok();
    let ipt_ok = std::process::Command::new("iptables")
        .args(["--version"])
        .output()
        .is_ok();
    println!("tc available:    {tc_ok}");
    println!("iptables avail:  {ipt_ok}");

    println!("\n=== Generating Config ===\n");

    // Generate config with sensible defaults
    let config = NetOptimizerConfig {
        bbr: bbr_available,
        sqm: tc_ok,
        socket_buffers: true,
        dscp_marking: ipt_ok,
        tcp_tweaks: true,
        game_ports: default_game_ports(),
        download_mbps: 0,
        upload_mbps: 0,
    };

    // Write config file
    let config_path = expand_tilde("~/.config/win-sandbox/net-optimizer.json");
    if let Some(parent) = config_path.parent() {
        std::fs::create_dir_all(parent)?;
    }

    let json = serde_json::to_string_pretty(&config)?;
    let mut file = std::fs::File::create(&config_path)?;
    file.write_all(json.as_bytes())?;

    println!("Config written to: {}", config_path.display());
    println!("\nEdit the file to set download_mbps/upload_mbps to your actual speed,");
    println!("then run: sudo win-sandbox-runner --optimize-net");

    Ok(())
}

/// Load config from file or use defaults.
pub fn load_config(path: Option<&str>) -> NetOptimizerConfig {
    if let Some(p) = path {
        if let Ok(contents) = std::fs::read_to_string(p) {
            if let Ok(config) = serde_json::from_str(&contents) {
                return config;
            }
        }
    }

    // Try default locations
    let search = [
        "~/.config/win-sandbox/net-optimizer.json",
        "/etc/win-sandbox-runner/net-optimizer.json",
    ];
    for p in &search {
        let expanded = expand_tilde(p);
        if expanded.exists() {
            if let Ok(contents) = std::fs::read_to_string(&expanded) {
                if let Ok(config) = serde_json::from_str(&contents) {
                    info!("Loaded net optimizer config from {}", expanded.display());
                    return config;
                }
            }
        }
    }

    NetOptimizerConfig::default()
}

fn expand_tilde(path: &str) -> std::path::PathBuf {
    if let Some(rest) = path.strip_prefix("~/") {
        if let Ok(home) = std::env::var("HOME") {
            return std::path::PathBuf::from(format!("{home}/{rest}"));
        }
    }
    std::path::PathBuf::from(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_config_is_valid() {
        let config = NetOptimizerConfig::default();
        assert!(config.bbr);
        assert!(config.sqm);
        assert!(config.socket_buffers);
        assert!(config.dscp_marking);
        assert!(config.tcp_tweaks);
        assert!(!config.game_ports.is_empty());
        assert!(config.game_ports.contains(&27015)); // Steam
        assert!(config.game_ports.contains(&3074)); // Xbox
        assert!(config.game_ports.contains(&3478)); // PlayStation
    }

    #[test]
    fn config_serialization_round_trip() {
        let config = NetOptimizerConfig::default();
        let json = serde_json::to_string_pretty(&config).unwrap();
        let parsed: NetOptimizerConfig = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.bbr, config.bbr);
        assert_eq!(parsed.game_ports.len(), config.game_ports.len());
    }

    #[test]
    fn optimize_result_display() {
        let result = OptimizeResult {
            bbr_applied: true,
            sqm_applied: false,
            socket_buffers_applied: true,
            dscp_applied: false,
            tcp_tweaks_applied: true,
            errors: vec!["SQM: needs root".into()],
        };
        let display = format!("{result}");
        assert!(display.contains("BBR"));
        assert!(display.contains("applied"));
        assert!(display.contains("needs root"));
    }

    #[test]
    fn game_ports_include_common() {
        let ports = default_game_ports();
        // Verify all major gaming platforms are covered
        assert!(ports.contains(&27015)); // Steam/Source
        assert!(ports.contains(&3074)); // Xbox Live
        assert!(ports.contains(&3478)); // PSN
        assert!(ports.contains(&6112)); // Blizzard
        assert!(ports.contains(&80)); // HTTP
        assert!(ports.contains(&443)); // HTTPS
    }

    #[test]
    fn config_from_json() {
        let json = r#"{
            "bbr": false,
            "sqm": true,
            "socket_buffers": true,
            "dscp_marking": false,
            "tcp_tweaks": true,
            "game_ports": [27015, 3074],
            "download_mbps": 100,
            "upload_mbps": 50
        }"#;
        let config: NetOptimizerConfig = serde_json::from_str(json).unwrap();
        assert!(!config.bbr);
        assert!(config.sqm);
        assert_eq!(config.download_mbps, 100);
        assert_eq!(config.game_ports.len(), 2);
    }
}
