mod appdb;
mod capabilities;
mod config;
mod daemon;
mod dispatch;
mod env_sanitize;
mod hasher;
mod launch;
mod netopt;
// nvidia.rs is called unconditionally from dispatch.rs's cross-platform
// execute() (the Tier2->Tier1 downgrade heuristic) and is portable by
// construction: it only probes /proc/driver/nvidia and /dev/nvidia*, which
// simply don't exist off Linux, so detect() degrades to None there with no
// platform-specific code. Left ungated so dispatch.rs compiles everywhere.
mod nvidia;
mod prefix;
mod privs;
mod rules;
mod tier0;
mod wizard;

// The following modules exist solely to serve Tier 1/2/3 (Landlock,
// bubblewrap, OverlayFS) — display/audio/GPU bind-mount detection, the TAP
// network bridge, and OverlayFS cleanup hooks. They have no callers outside
// tier1.rs/tier2.rs/tier3.rs (verified: `grep -rn "amd::\|audio::\|display::\|net::\|cleanup::"`
// against every other module turns up nothing), so gating them here keeps
// them from becoming dead code — and a `-D warnings` clippy build failure —
// on non-Linux targets once tier1/2/3 themselves are gated out below.
#[cfg(target_os = "linux")]
mod amd;
#[cfg(target_os = "linux")]
mod audio;
#[cfg(target_os = "linux")]
mod cleanup;
#[cfg(target_os = "linux")]
mod display;
#[cfg(target_os = "linux")]
mod net;
#[cfg(target_os = "linux")]
mod tier1;
#[cfg(target_os = "linux")]
mod tier2;
#[cfg(target_os = "linux")]
mod tier3;

// macOS analogue of tier1.rs/tier2.rs: filesystem (and, for Tier 2,
// network) isolation via Seatbelt (sandbox-exec). See seatbelt.rs's module
// doc comment for what it does and does not provide relative to Landlock
// and bubblewrap.
#[cfg(target_os = "macos")]
mod seatbelt;

use anyhow::Result;
use clap::Parser;
use std::process::ExitCode;
use tracing::{debug, info, warn};

/// win-sandbox-runner: Transparent tiered sandbox for Windows executables via Wine.
#[derive(Parser, Debug)]
#[command(name = "win-sandbox-runner", version, about)]
struct Args {
    /// Path to the Windows executable (.exe).
    /// Required for running apps; not needed for --optimize-net/--cleanup-net/--configure-net.
    #[arg(short, long)]
    exe: Option<String>,

    /// Force a specific tier (0–3), overriding rules.json.
    #[arg(short, long)]
    tier: Option<String>,

    /// Path to rules.json (default: auto-discover).
    #[arg(short, long)]
    rules: Option<String>,

    /// Increase logging verbosity.
    #[arg(short, long)]
    verbose: bool,

    /// Disable GUI prompts (headless mode).
    #[arg(long)]
    no_gui: bool,

    /// Show what would be done without executing.
    #[arg(long)]
    dry_run: bool,

    /// Allow gamepad/controller access in sandbox (Tier 2/3).
    /// Bind-mounts /dev/input/event* for gamepad devices.
    #[arg(long)]
    gamepad: bool,

    /// Use nested X11 (Xephyr) for display isolation (default for Tier 2/3).
    #[arg(long)]
    nested_x11: bool,

    /// Use Xvfb virtual framebuffer (headless, no visible window).
    #[arg(long)]
    xvfb: bool,

    /// Use host X11 directly (DANGEROUS: enables keylogger attacks).
    #[arg(long)]
    host_x11: bool,

    /// Use Wayland with Wine Wayland driver.
    #[arg(long)]
    wayland: bool,

    /// Additional arguments passed to the Windows executable.
    #[arg(trailing_var_arg = true)]
    args: Vec<String>,

    /// Mark this app as trusted (no sandboxing) and save to rules.json.
    /// On next launch, the app will run without sandboxing automatically.
    #[arg(long)]
    trust: bool,

    /// Apply network optimizations for gaming (BBR, SQM, socket buffers, DSCP).
    /// Requires root for tc/iptables/sysctl changes.
    #[arg(long)]
    optimize_net: bool,

    /// Remove network optimizations previously applied by --optimize-net.
    #[arg(long)]
    cleanup_net: bool,

    /// Show network diagnostics and write a tuned config file.
    /// Detects BBR availability, current qdisc, bandwidth, and game port reachability.
    #[arg(long)]
    configure_net: bool,

    /// Start as a background daemon with binfmt_misc interception.
    /// Pre-loads app database and rules for instant .exe execution.
    #[arg(long)]
    daemon: bool,

    /// Query the running daemon status (connects via IPC socket).
    #[arg(long)]
    status: bool,

    /// Reload rules and config in the running daemon (connects via IPC socket).
    #[arg(long)]
    reload: bool,

    /// Stop the running daemon gracefully.
    #[arg(long)]
    stop: bool,

    /// Unregister the binfmt_misc handler (cleanup before uninstall).
    #[arg(long)]
    unregister: bool,

    /// macOS only: print the generated Seatbelt (.sb) profile for --tier
    /// and --wine-prefix to stdout instead of running anything. Exists so
    /// CI can assert real sandbox-exec enforcement against the exact
    /// profile text this binary generates, not a hand-copied approximation
    /// of it that could silently drift from what actually runs.
    #[arg(long)]
    print_seatbelt_profile: bool,

    /// Wine prefix to use when generating a profile with
    /// --print-seatbelt-profile. Not used outside that flag: real launches
    /// resolve the prefix through PrefixManager, same as every other tier.
    #[arg(long)]
    wine_prefix: Option<String>,

    /// User environment variables (set by daemon, not CLI).
    #[arg(skip)]
    user_env: std::collections::HashMap<String, String>,

    /// Target UID for child process (set by daemon, not CLI).
    #[arg(skip)]
    uid: Option<u32>,

    /// Per-app WINEPREFIX resolved by dispatch (set internally, not CLI).
    /// Passed explicitly so concurrent launches cannot race on process env.
    #[arg(skip)]
    resolved_wineprefix: Option<String>,

    /// Identity (dev/inode/size/mtime/ctime) of the exact file that was
    /// hashed. Tiers re-check it right before spawning Wine so a swapped file
    /// never runs under a decision made for different contents.
    #[arg(skip)]
    exe_identity: Option<hasher::FileIdentity>,
}

fn main() -> ExitCode {
    let mut args = Args::parse();

    // Never act with credentials the caller does not have. With the old
    // binfmt `C` flag (or a setuid-installed binary) a setuid .exe made this
    // process run with the file owner's euid under the caller's argv/env.
    if unsafe { libc::geteuid() != libc::getuid() || libc::getegid() != libc::getgid() } {
        eprintln!(
            "win-sandbox-runner: refusing to run with elevated effective credentials \
             (setuid/setgid invocation). Re-register binfmt with scripts/register-binfmt.sh."
        );
        return ExitCode::FAILURE;
    }

    // Initialize logging.
    let filter = if args.verbose {
        tracing_subscriber::EnvFilter::new("debug")
    } else {
        tracing_subscriber::EnvFilter::new("info")
    };
    tracing_subscriber::fmt().with_env_filter(filter).init();

    // Recursion guard: only skip re-entering tier logic when we are nested
    // inside Wine that this sandbox started. The WIN_SANDBOX_ACTIVE env var
    // alone is forgeable and must not authorize unsandboxed host exec.
    let marker_set = std::env::var("WIN_SANDBOX_ACTIVE").is_ok();
    if dispatch::is_nested_sandbox_invocation(
        marker_set,
        dispatch::parent_process_comm().as_deref(),
    ) {
        info!("Recursion guard active, passing through to wine");
        let exe = match &args.exe {
            Some(e) => e.as_str(),
            None => {
                tracing::error!("Recursion guard active but no --exe provided");
                return ExitCode::FAILURE;
            }
        };
        return dispatch::passthrough_to_wine(exe, &args.args);
    }

    // Binfmt_misc detection: kernel passes .exe path as positional arg (not --exe).
    // If the daemon is running, hand it the open file over the launch socket
    // and wait for the result. Otherwise handle it directly.
    if args.exe.is_none() && !args.args.is_empty() {
        let first = args.args[0].clone();
        // binfmt_misc matches on the MZ magic, not the extension, so any
        // name can arrive here (.EXE, .scr, no extension at all).
        if is_pe_file(&first) {
            let sock = daemon::launch_socket_path();
            if sock.exists() {
                info!("binfmt: routing {first} through the daemon");
                return match route_to_daemon(&sock, &first, &args.args[1..]) {
                    Ok(code) => ExitCode::from(code.clamp(0, 255) as u8),
                    Err(e) => {
                        tracing::error!("Daemon launch failed: {e:#}");
                        ExitCode::FAILURE
                    }
                };
            }
            info!("binfmt: handling {first} directly (no daemon)");
            args.exe = Some(first);
            args.args.remove(0);
        }
    }

    match run(&mut args) {
        Ok(code) => code,
        Err(e) => {
            tracing::error!("Fatal: {e:#}");
            ExitCode::FAILURE
        }
    }
}

/// True if `path` is a regular file starting with the `MZ` magic.
fn is_pe_file(path: &str) -> bool {
    use std::io::Read;
    let mut magic = [0u8; 2];
    std::fs::File::open(path)
        .and_then(|mut f| f.read_exact(&mut magic))
        .map(|_| &magic == b"MZ")
        .unwrap_or(false)
}

/// Open `exe` with our own credentials and hand the fd to the daemon.
fn route_to_daemon(sock: &std::path::Path, exe: &str, rest: &[String]) -> Result<i32> {
    let abs = std::fs::canonicalize(exe)?;
    let file = std::fs::File::open(&abs)?;
    let env = launch::FORWARDED_ENV
        .iter()
        .filter_map(|k| std::env::var(k).ok().map(|v| (k.to_string(), v)))
        .collect();
    let msg = launch::LaunchMessage {
        exe: abs.to_string_lossy().into_owned(),
        args: rest.to_vec(),
        env,
    };
    launch::client_launch(sock, &file, &msg)
}

fn run(args: &mut Args) -> Result<ExitCode> {
    // --daemon: start background daemon
    if args.daemon {
        return daemon::run_daemon().map(|_| ExitCode::SUCCESS);
    }

    // --status: query daemon
    if args.status {
        match daemon::query_status() {
            Ok(status) => {
                println!("{status}");
                return Ok(ExitCode::SUCCESS);
            }
            Err(e) => {
                eprintln!("Daemon not running: {e}");
                return Ok(ExitCode::FAILURE);
            }
        }
    }

    // --reload: reload daemon rules
    if args.reload {
        match daemon::send_command("reload") {
            Ok(resp) => {
                println!("{resp}");
                return Ok(ExitCode::SUCCESS);
            }
            Err(e) => {
                eprintln!("Daemon not running: {e}");
                return Ok(ExitCode::FAILURE);
            }
        }
    }

    // --stop: stop daemon
    if args.stop {
        match daemon::send_command("quit") {
            Ok(resp) => {
                println!("{resp}");
                return Ok(ExitCode::SUCCESS);
            }
            Err(e) => {
                eprintln!("Daemon not running: {e}");
                return Ok(ExitCode::FAILURE);
            }
        }
    }

    // --unregister: remove binfmt handler
    if args.unregister {
        daemon::unregister_binfmt()?;
        return Ok(ExitCode::SUCCESS);
    }

    // --print-seatbelt-profile: macOS only. See the flag's own doc comment.
    if args.print_seatbelt_profile {
        #[cfg(target_os = "macos")]
        {
            let prefix = args
                .wine_prefix
                .as_deref()
                .unwrap_or("/tmp/apex-win-test-prefix");
            let tmp = std::env::temp_dir();
            let tmp_str = tmp.to_string_lossy();
            let network = match args.tier.as_deref() {
                Some("1") => true,
                Some("2") => false,
                other => {
                    tracing::error!(
                        "--print-seatbelt-profile requires --tier 1 or --tier 2, got {other:?}"
                    );
                    return Ok(ExitCode::FAILURE);
                }
            };
            println!(
                "{}",
                seatbelt::build_profile(prefix, &tmp_str, None, network)
            );
            return Ok(ExitCode::SUCCESS);
        }
        #[cfg(not(target_os = "macos"))]
        {
            tracing::error!("--print-seatbelt-profile is macOS only");
            return Ok(ExitCode::FAILURE);
        }
    }

    // --cleanup-net: remove network optimizations and exit
    if args.cleanup_net {
        let config = netopt::load_config(None);
        netopt::cleanup(&config)?;
        return Ok(ExitCode::SUCCESS);
    }

    // --configure-net: network diagnostics and config
    if args.configure_net {
        netopt::diagnose_and_configure()?;
        return Ok(ExitCode::SUCCESS);
    }

    // --optimize-net: apply network optimizations and exit
    if args.optimize_net {
        let config = netopt::load_config(None);
        let result = netopt::optimize(&config)?;
        println!("{result}");
        return Ok(ExitCode::SUCCESS);
    }

    // From here on, --exe is required
    let exe = match &args.exe {
        Some(e) => e.clone(),
        None => {
            tracing::error!("--exe is required for running applications");
            return Ok(ExitCode::FAILURE);
        }
    };

    // Check Wine version before proceeding
    if let Err(e) = check_wine_version() {
        warn!("Wine version check failed: {e}");
    }

    // Hash the binary and remember exactly which file was hashed.
    let (hash, identity) = hasher::hash_path_with_identity(&exe)?;
    args.exe_identity = Some(identity);
    info!("Binary hash: {hash}");

    // Load rules
    let rules_path = config::find_rules_path(args.rules.as_deref());
    let rules = rules::load_rules(rules_path.as_deref())?;

    // Load the built-in app database
    let app_db = appdb::AppDatabase::load_embedded();

    // --trust flag: save this app as trusted in rules.json
    //
    // Dry-run must not persist anything: --trust writes rules.json, which is
    // a state change a preview is not allowed to make. Report it instead.
    if args.trust {
        if args.dry_run {
            info!(
                "[DRY RUN] Would mark '{exe}' as trusted in rules.json (no sandboxing on \
                 future launches)"
            );
        } else {
            save_trusted_rule(&exe, &hash)?;
        }
    }

    // Auto-apply network optimization for game profiles
    //
    // netopt::optimize mutates host sysctls/qdiscs (BBR, SQM), which is a real
    // system-wide side effect a dry-run must not perform.
    if let Some((profile, _)) = app_db.lookup_by_name(&exe) {
        if profile.network && profile.gpu {
            if args.dry_run {
                info!("[DRY RUN] Would auto-apply game network optimizations (BBR/SQM)");
            } else {
                // This looks like a game — auto-optimize network
                let net_config = netopt::load_config(None);
                match netopt::optimize(&net_config) {
                    Ok(result) => {
                        if result.bbr_applied || result.sqm_applied {
                            info!("Network optimized for gaming");
                            println!("{result}");
                        }
                    }
                    Err(e) => {
                        debug!("Auto network optimization skipped: {e}");
                    }
                }
            }
        }
    }

    // Dispatch to the appropriate tier
    dispatch::execute(args, &exe, &hash, &rules, &app_db)
}

/// Check Wine version and warn if < 9.0.
/// Wine 9.0+ adds NTsync, Wayland driver, and important fixes.
fn check_wine_version() -> Result<()> {
    use std::process::Command;
    let output = Command::new("wine").arg("--version").output()?;
    let version_str = String::from_utf8_lossy(&output.stdout);
    let version_str = version_str.trim();
    info!("Wine version: {version_str}");

    // Parse "wine-X.Y.Z" or "wine-X.Y"
    if let Some(ver) = version_str.strip_prefix("wine-") {
        let major: u32 = ver
            .split('.')
            .next()
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        if major < 9 {
            warn!("Wine {ver} detected — Wine 9.0+ recommended");
            warn!("Older Wine may lack NTsync, Wayland driver, and important fixes");
        }
    }

    Ok(())
}

/// Save a trusted rule for the given exe into rules.json.
/// If the hash already exists, updates it. Otherwise, adds a new entry.
fn save_trusted_rule(exe_path: &str, hash: &str) -> Result<()> {
    use win_sandbox_common::rules_schema::{RuleEntry, RulesFile};

    let name = std::path::Path::new(exe_path)
        .file_stem()
        .and_then(|n| n.to_str())
        .unwrap_or("unknown")
        .to_string();

    // Never fall back to /tmp: a world-writable location for the file that
    // decides which binaries run unsandboxed would let any local user edit it.
    let rules_path = match config::find_rules_path(None) {
        Some(p) => p,
        None => {
            let home = std::env::var("HOME")
                .ok()
                .filter(|h| h.starts_with('/'))
                .or_else(|| {
                    privs::UserIds::lookup(unsafe { libc::getuid() })
                        .ok()
                        .map(|ids| ids.home.display().to_string())
                })
                .ok_or_else(|| {
                    anyhow::anyhow!("cannot determine a home directory for rules.json")
                })?;
            std::path::PathBuf::from(format!("{home}/.config/win-sandbox/rules.json"))
        }
    };

    // Load existing rules or create defaults
    let mut rules = rules::load_rules(Some(&rules_path)).unwrap_or(RulesFile {
        version: 1,
        entries: vec![],
        defaults: Default::default(),
    });

    // Check if entry already exists
    let existing = rules.entries.iter().position(|e| e.hash == hash);
    let entry = RuleEntry {
        hash: hash.to_string(),
        name: name.clone(),
        tier: win_sandbox_common::tier::Tier::Tier0,
        allowed_paths: vec![],
        network: true,
        gpu: true,
        trusted: true,
        dxvk: false,
        winetricks: vec![],
        env: std::collections::HashMap::new(),
        wine_variant: "system".into(),
    };

    if let Some(pos) = existing {
        rules.entries[pos] = entry;
        info!("Updated trusted rule for '{name}' in rules.json");
    } else {
        rules.entries.push(entry);
        info!("Saved trusted rule for '{name}' to rules.json");
    }

    // Ensure parent directory exists
    if let Some(parent) = rules_path.parent() {
        std::fs::create_dir_all(parent)?;
    }

    let json = serde_json::to_string_pretty(&rules)?;
    // Atomic replace: a crash mid-write must not leave a truncated rules file
    // (which load_rules would reject, silently dropping every saved decision).
    let tmp = rules_path.with_extension("json.tmp");
    std::fs::write(&tmp, json)?;
    std::fs::rename(&tmp, &rules_path)?;
    info!("Rules saved to {}", rules_path.display());

    Ok(())
}
