use crate::Args;
use anyhow::{bail, Result};
use std::process::ExitCode;
use tracing::{info, warn};
use win_sandbox_common::rules_schema::RulesFile;
use win_sandbox_common::tier::Tier;

use crate::{appdb, rules, wizard};

/// Check if a path is in an untrusted location.
///
/// Match directory boundaries so similarly named siblings such as "/tmpfoo"
/// are not accidentally classified as children of "/tmp".
pub fn is_untrusted_path(path: &str) -> bool {
    let untrusted = ["/tmp", "/mnt", "/media", "/var/tmp", "/dev/shm"];
    untrusted.iter().any(|prefix| {
        match path.strip_prefix(prefix) {
            // The path IS the untrusted directory itself, or lives inside it.
            Some(rest) => rest.is_empty() || rest.starts_with('/'),
            None => false,
        }
    })
}

/// Resolve whether network access is allowed for this binary.
fn resolve_network_permission(rules: &RulesFile, hash: &str) -> bool {
    if let Some(entry) = rules::lookup_by_hash(rules, hash) {
        entry.network
    } else {
        rules.defaults.network_default
    }
}

/// Resolve which tier to run at, and whether that came from an explicit,
/// user-authored source rather than a heuristic default.
///
/// Explicit sources: the `--tier` CLI flag, or a hash the user (or `--trust`)
/// put in `rules.json` themselves. Heuristic sources: an app-database match
/// (APEX-WIN's own profile for a known app), the wizard's auto-detect
/// heuristics, the untrusted-path fallback, or the unmapped-binary fallback.
/// Only the explicit path is a promise the user actually made, which is what
/// the fail-secure Tier 3 check in `execute()` depends on.
///
/// Extracted from `execute()` so this resolution logic — the part most worth
/// getting right — is testable without standing up a Wine prefix.
fn resolve_tier(
    args: &Args,
    exe: &str,
    rules: &RulesFile,
    explicit_entry: &Option<win_sandbox_common::rules_schema::RuleEntry>,
    matched_entry: &Option<win_sandbox_common::rules_schema::RuleEntry>,
) -> Result<(Tier, bool)> {
    if let Some(ref tier_str) = args.tier {
        let t: Tier = tier_str.parse()?;
        info!("Forced tier: {t}");
        return Ok((t, true));
    }
    if let Some(ref entry) = explicit_entry {
        let tier = if entry.trusted {
            Tier::Tier0
        } else {
            entry.tier
        };
        info!("Matched rule '{}', tier: {tier}", entry.name);
        return Ok((tier, true));
    }
    if let Some(ref entry) = matched_entry {
        let tier = if entry.trusted {
            Tier::Tier0
        } else {
            entry.tier
        };
        info!(
            "Matched app-database/heuristic entry '{}', tier: {tier}",
            entry.name
        );
        return Ok((tier, false));
    }
    if is_untrusted_path(exe) {
        let t = rules.defaults.untrusted_path_tier;
        warn!("Untrusted path '{}', using tier {t}", exe);
        return Ok((t, false));
    }
    let t = rules.defaults.unmapped_tier;
    info!("No rule matched, using default tier {t}");
    Ok((t, false))
}

/// Check whether an explicit Tier 3 request can be honored as real
/// ephemeral-overlay isolation. `Err` carries the human-readable reason it
/// cannot, for both the refusal message and the `--dry-run` report.
fn check_tier3_available(
    tier3_available: bool,
    bwrap_version: &Option<String>,
) -> Result<(), String> {
    if tier3_available {
        return Ok(());
    }
    Err(match bwrap_version {
        Some(v) => format!(
            "bubblewrap {v} does not support unprivileged overlay mounts \
             (needs >= 0.10.0), and OverlayFS via mount(8) requires root"
        ),
        None => "bubblewrap not found, and OverlayFS via mount(8) requires root".to_string(),
    })
}

/// Check whether Tier 1/2/3 exist as sandboxing mechanisms *at all* on this
/// platform, independent of whether a specific one (like Tier 3's overlay)
/// is currently configured correctly. This is the platform-support gate;
/// `check_tier3_available` is the finer-grained Linux capability gate that
/// runs after this one passes.
///
/// Kept as its own function (not folded into `check_tier3_available`) so the
/// existing, already-tested Tier 3 overlay logic is untouched: that check
/// answers "is overlay isolation configured on this Linux host", this one
/// answers "does Tier N exist on this OS at all".
fn check_tier_implemented(
    tier: Tier,
    caps: &crate::capabilities::Capabilities,
) -> Result<(), String> {
    check_tier_implemented_for_os(tier, caps, cfg!(target_os = "linux"), std::env::consts::OS)
}

/// `check_tier_implemented`'s actual logic, with the "which OS is this"
/// question taken as a parameter instead of read via `cfg!`/`env::consts`
/// directly. Both branches need real, deterministic test coverage (this is
/// the fail-secure gate that stands between an explicit `--tier 1/2/3`
/// request and running unsandboxed on a platform that can't provide it) but
/// a plain `cfg!(target_os = "linux")` bakes the answer in at compile time,
/// so a unit test built on Linux could never exercise the non-Linux branch,
/// and one built on macOS could never exercise the Linux branch. Splitting
/// the OS lookup out as a parameter makes both reachable from any host.
fn check_tier_implemented_for_os(
    tier: Tier,
    caps: &crate::capabilities::Capabilities,
    is_linux: bool,
    os_name: &str,
) -> Result<(), String> {
    if tier == Tier::Tier0 {
        return Ok(()); // Tier 0 (direct Wine exec) has no OS-specific sandbox.
    }
    if is_linux {
        return Ok(()); // Tier 1/2/3 all have Linux implementations.
    }
    // Non-Linux, Tier 1/2: Seatbelt (sandbox-exec) can back these on macOS
    // — see seatbelt.rs's module doc comment for exactly what guarantee
    // that is (a real deny-by-default MAC boundary, but no mount/PID
    // namespace or resource limits, so weaker than Landlock/bubblewrap on
    // the axes Seatbelt doesn't cover). `caps.seatbelt_available` is only
    // ever `Some(_)` on macOS (see its own doc comment on
    // `Capabilities::seatbelt_available`), so checking it here is
    // equivalent to "is this macOS with sandbox-exec present" without a
    // separate is_macos parameter — one less thing that could disagree
    // with what the field itself means.
    if matches!(tier, Tier::Tier1 | Tier::Tier2) && caps.seatbelt_available == Some(true) {
        return Ok(());
    }
    // Everything else: tier1.rs/tier2.rs/tier3.rs are Linux-only modules
    // and do not exist in this build at all off Linux. Tier 3 in
    // particular has no Seatbelt equivalent at all (no unprivileged
    // ephemeral overlay mechanism on macOS), so it stays refused here
    // regardless of Seatbelt availability.
    Err(match tier {
        Tier::Tier3 => format!(
            "Tier 3 needs an ephemeral OverlayFS mount, which has no macOS equivalent \
             (no unprivileged union/overlay filesystem); OverlayFS itself is Linux-only, \
             and no bubblewrap/mount(8) exist on {os_name} either way"
        ),
        Tier::Tier2 => format!(
            "Tier 2 needs Landlock/bubblewrap or Seatbelt, none of which are available on \
             {os_name}{}",
            if caps.seatbelt_available == Some(false) {
                " (sandbox-exec was checked and is not usable on this system)"
            } else {
                ""
            }
        ),
        Tier::Tier1 => format!(
            "Tier 1 needs Landlock or Seatbelt, none of which are available on {os_name}{}",
            if caps.seatbelt_available == Some(false) {
                " (sandbox-exec was checked and is not usable on this system)"
            } else {
                ""
            }
        ),
        Tier::Tier0 => unreachable!("handled above"),
    })
}

/// Execute the appropriate tier for the given binary.
///
/// Full resolution flow:
///   1. Look up by hash in rules.json (exact match)
///   2. Look up by exe name in app database (fuzzy match)
///   3. Run first-launch wizard (auto-detect heuristics)
///   4. Ensure Wine prefix exists and install deps
///   5. If trusted, run wine directly (no sandbox)
///   6. Otherwise, resolve tier and execute in sandbox
pub fn execute(
    args: &mut Args,
    exe: &str,
    hash: &str,
    rules: &RulesFile,
    app_db: &appdb::AppDatabase,
) -> Result<ExitCode> {
    // --- Step 1: Exact hash match in rules.json ---
    // This is the only path that counts as an *explicit, user-authored*
    // decision: the user (or --trust) put this hash in rules.json themselves.
    // App-database matches and wizard heuristics are APEX-WIN's own guesses,
    // not a promise the user made — that distinction is what fail-secure
    // Tier 3 handling below depends on.
    let explicit_entry = rules::lookup_by_hash(rules, hash).cloned();
    let mut matched_entry: Option<win_sandbox_common::rules_schema::RuleEntry> =
        explicit_entry.clone();

    // --- Step 2: Name-based match in app database ---
    if matched_entry.is_none() {
        if let Some((profile, entry)) = app_db.lookup_by_name(exe) {
            info!(
                "App database match: '{}' -> '{}'",
                std::path::Path::new(exe)
                    .file_name()
                    .and_then(|n| n.to_str())
                    .unwrap_or("?"),
                profile.name
            );
            if !profile.notes.is_empty() {
                info!("  Note: {}", profile.notes);
            }
            if profile.trusted {
                info!(
                    "  '{}' is usually run unsandboxed. A file-name match cannot grant that; \
                     if this is the genuine installer/binary, approve it once with: \
                     win-sandbox-runner --exe '{exe}' --trust",
                    profile.name
                );
            }
            matched_entry = Some(entry);
        }
    }

    // --- Step 3: First-launch wizard for unknown apps ---
    if matched_entry.is_none() {
        // Dry-run must not block on an interactive prompt either: preview the
        // heuristic decision instead (same headless seam as --no-gui).
        let result = wizard::run_wizard(
            exe,
            app_db,
            args.no_gui || args.dry_run,
            hash,
            args.uid.is_some(),
        );
        info!("First launch: {}", wizard::describe_decision(&result));
        matched_entry = Some(result.entry);
    }

    // --- Step 4: Per-app prefix management ---
    //
    // Dry-run must be side-effect-free: a dry-run that created the Wine
    // prefix (or kicked off DXVK/winetricks installs, or chowned trees in
    // daemon mode) would make "show what would be done without executing"
    // a lie. So in dry-run mode we only *report* the plan read-only:
    // `PrefixManager::wine_prefix`/`check_deps` are pure lookups (path
    // construction + reading an existing state.json), unlike
    // `setup_app`, which creates directories, runs wineboot, and installs
    // components. `WINEPREFIX` is likewise not set in the environment —
    // nothing downstream runs in dry-run mode to consume it.
    if let Some(ref entry) = matched_entry {
        // Root the prefix at the *invoking user's* home. In daemon mode the
        // process runs as root, where HOME is /root or unset, so relying on
        // the ambient environment would create an unusable prefix.
        let prefix_mgr = crate::prefix::PrefixManager::for_user(&args.user_env, args.uid);
        if args.dry_run {
            info!(
                "[DRY RUN] Would use WINEPREFIX: {}",
                prefix_mgr.wine_prefix(hash).display()
            );
            let (missing_dxvk, missing_wt) =
                prefix_mgr.check_deps(hash, entry.dxvk, &entry.winetricks);
            if missing_dxvk {
                info!("[DRY RUN] Would install DXVK into the prefix");
            }
            if !missing_wt.is_empty() {
                info!(
                    "[DRY RUN] Would install winetricks components: {:?}",
                    missing_wt
                );
            }
            info!(
                "[DRY RUN] Prefix creation, wineboot, DXVK and winetricks installs are all \
                 skipped in dry-run mode"
            );
        } else {
            let wine_prefix =
                prefix_mgr.setup_app(hash, entry.dxvk, &entry.winetricks, args.uid)?;
            info!("WINEPREFIX: {}", wine_prefix.display());
            // Stash on Args (not process env) so concurrent launches cannot race.
            args.resolved_wineprefix = Some(wine_prefix.display().to_string());
        }
    }

    // --- Step 5: Trusted apps — no sandboxing ---
    // A saved trust decision sets the *default* tier (0); it does not override
    // an explicit `--tier` on this launch. resolve_tier() applies the same
    // rule to rules.json entries (trusted -> Tier0 unless --tier is present).
    // This early return keeps the common case (no --tier flag) off the tier
    // resolution path entirely. With --tier present we fall through so the
    // explicit request is validated and honored — including the fail-secure
    // Tier 3 availability check — rather than silently running unsandboxed.
    if let Some(ref entry) = matched_entry {
        if entry.trusted && args.tier.is_none() {
            info!("Trusted app '{}', no sandboxing", entry.name);

            if args.dry_run {
                info!(
                    "[DRY RUN] Would run trusted '{}' with wine directly",
                    entry.name
                );
                return Ok(ExitCode::SUCCESS);
            }

            return crate::tier0::run_with_env(args, &entry.env);
        }
    }

    // --- Step 6: Resolve tier ---
    let (tier, tier_is_explicit) = resolve_tier(args, exe, rules, &explicit_entry, &matched_entry)?;

    // One capability probe, reused by both gates below: the cross-platform
    // "does Tier N exist here" check and the Linux-specific "is Tier 3's
    // overlay actually configured" check.
    let caps = crate::capabilities::Capabilities::detect();

    // Cross-platform gate: does Tier N exist as a sandbox mechanism on this
    // OS at all? On Linux this always passes (all four tiers are
    // implemented) and falls through to the finer-grained Tier 3 overlay
    // check below. On other platforms (currently: macOS build targets with
    // no tier1/tier2/tier3 modules), only Tier 0 exists.
    //
    // Same fail-secure split as Tier 3's overlay check: an EXPLICIT request
    // for a tier this OS cannot provide refuses, so a security decision the
    // user actually made is never silently weakened. A HEURISTIC match
    // (app-database/wizard/path defaults) degrades to Tier 0 with a loud
    // warning instead — direct execution, no sandboxing at all — since that
    // was never a promise the user made, and Tier 0 is strictly less
    // isolated than any Tier 1/2/3 the heuristic intended, not a silent
    // downgrade to a similar-but-weaker tier the way Tier3->Tier2 is.
    let tier = if let Err(reason) = check_tier_implemented(tier, &caps) {
        if tier_is_explicit {
            if args.dry_run {
                info!(
                    "[DRY RUN] Tier {} was explicitly requested for {exe} but is not \
                     available on this platform: {reason}. A real run would refuse.",
                    tier.level()
                );
                return Ok(ExitCode::SUCCESS);
            }
            bail!(
                "Refusing to run '{exe}': Tier {} was explicitly requested but is not \
                 available on this platform ({reason}). Use --tier 0 to run without a \
                 sandbox, or run this on Linux for real Tier 1/2/3 isolation.",
                tier.level()
            );
        }
        warn!(
            "Tier {} was suggested by a heuristic but is not available on this platform \
             ({reason}). Falling back to Tier 0 — direct execution, no sandboxing. This \
             app is not isolated from the rest of your system.",
            tier.level()
        );
        Tier::Tier0
    } else {
        tier
    };

    // Fail secure: an explicit Tier 3 request must get real ephemeral-overlay
    // isolation or be refused, not silently served as Tier 2. Tier 2 and
    // Tier 3 have different threat models (persistent bwrap namespace vs.
    // OverlayFS changes discarded on exit); an app the user explicitly
    // pinned to Tier 3 may be relying on that guarantee.
    if tier == Tier::Tier3 && tier_is_explicit {
        if let Err(reason) = check_tier3_available(caps.tier3_available(), &caps.bwrap_version) {
            if args.dry_run {
                info!(
                    "[DRY RUN] Tier 3 was explicitly requested for {exe} but is not available: \
                     {reason}. A real run would refuse rather than silently use Tier 2."
                );
                return Ok(ExitCode::SUCCESS);
            }
            bail!(
                "Refusing to run '{exe}': Tier 3 was explicitly requested but this host cannot \
                 provide it ({reason}). Serving it as Tier 2 would silently weaken an isolation \
                 guarantee you asked for. Use --tier 2 to accept that explicitly, or install \
                 bubblewrap >= 0.10 for real Tier 3 support."
            );
        }
    }

    let network = resolve_network_permission(rules, hash);
    info!("Network access: {network}");

    if args.dry_run {
        info!("[DRY RUN] Would execute tier {tier} for {exe} (network={network})",);
        return Ok(ExitCode::SUCCESS);
    }

    // Nvidia + user namespaces: downgrade Tier 2 to Tier 1
    let tier = if tier == Tier::Tier2 && crate::nvidia::detect().is_some() {
        warn!("Nvidia GPU detected — downgrading Tier 2 to Tier 1");
        Tier::Tier1
    } else {
        tier
    };

    let app_env: std::collections::HashMap<String, String> = matched_entry
        .as_ref()
        .map(|e| e.env.clone())
        .unwrap_or_default();

    // Only rule/profile env is layered on top here. The session env
    // (args.user_env) already reaches Wine through build_sandbox_env's
    // allowlist; merging it again here bypassed that allowlist entirely
    // (any client key, LD_PRELOAD included, landed in the Tier 0 command).
    let merged_env = app_env;

    info!("Executing tier {tier} for {exe}");

    match tier {
        Tier::Tier0 => crate::tier0::run_with_env(args, &merged_env),
        #[cfg(target_os = "linux")]
        Tier::Tier1 => crate::tier1::run(args),
        #[cfg(target_os = "linux")]
        Tier::Tier2 => crate::tier2::run_with_network(args, network),
        #[cfg(target_os = "linux")]
        Tier::Tier3 => crate::tier3::run_with_network(args, network, tier_is_explicit),
        // macOS: Seatbelt backs Tier 1/2 (see seatbelt.rs). Only reachable
        // when check_tier_implemented_for_os already confirmed
        // seatbelt_available == Some(true) for this exact tier, so no
        // second capability check is needed here — same pattern as the
        // Linux arms above, which don't re-check Landlock/bwrap either.
        #[cfg(target_os = "macos")]
        Tier::Tier1 => crate::seatbelt::run(args),
        #[cfg(target_os = "macos")]
        Tier::Tier2 => crate::seatbelt::run_with_network(args, network),
        // Every platform this actually ships for is covered by an arm
        // above. Reaching here means check_tier_implemented_for_os's gate
        // has a bug (it admitted a tier this platform cannot back), not a
        // legitimate case to silently handle — hence bail! rather than a
        // silent no-op. Written as a catch-all match on `tier` (not
        // per-variant #[cfg] arms) so it also covers any future platform
        // this project targets without needing a matching new arm added
        // here first.
        #[cfg(not(any(target_os = "linux", target_os = "macos")))]
        _ => bail!(
            "internal error: reached Tier {} dispatch on a platform with no tier \
             implementation; check_tier_implemented() should have refused or downgraded \
             this before now",
            tier.level()
        ),
        // On macOS specifically, Tier 3 has no Seatbelt-backed arm above
        // (see check_tier_implemented_for_os: it is refused unconditionally
        // off Linux), so it falls through to the same internal-error bail
        // as an unsupported platform would.
        #[cfg(target_os = "macos")]
        Tier::Tier3 => bail!(
            "internal error: reached Tier 3 dispatch on macOS, which has no Seatbelt-backed \
             Tier 3 implementation; check_tier_implemented() should have refused this before \
             now"
        ),
    }
}

/// Whether this invocation is a nested re-entry from Wine inside our sandbox.
///
/// The `WIN_SANDBOX_ACTIVE` marker is forgeable by any local caller, so it
/// must never alone authorize unsandboxed host exec. Only treat the process
/// as nested when the marker is set *and* the parent process looks like Wine.
pub fn is_nested_sandbox_invocation(marker_set: bool, parent_comm: Option<&str>) -> bool {
    if !marker_set {
        return false;
    }
    match parent_comm {
        Some(comm) => {
            let comm = comm.trim();
            comm.starts_with("wine") || comm == "wine-preloader" || comm == "wineserver"
        }
        None => false,
    }
}

/// Best-effort parent process name from `/proc/<ppid>/comm` (Linux).
pub fn parent_process_comm() -> Option<String> {
    let status = std::fs::read_to_string("/proc/self/status").ok()?;
    let ppid = status
        .lines()
        .find_map(|l| l.strip_prefix("PPid:"))?
        .trim()
        .parse::<i32>()
        .ok()?;
    let comm = std::fs::read_to_string(format!("/proc/{ppid}/comm")).ok()?;
    Some(comm.trim().to_string())
}

/// Pass through directly to wine (used when recursion guard is active).
pub fn passthrough_to_wine(exe: &str, args: &[String]) -> ExitCode {
    use std::process::Command;
    let status = Command::new("wine").arg(exe).args(args).status();
    match status {
        Ok(s) => {
            if s.success() {
                ExitCode::SUCCESS
            } else {
                ExitCode::from(s.code().unwrap_or(1) as u8)
            }
        }
        Err(e) => {
            tracing::error!("Failed to exec wine: {e}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(all(test, target_os = "linux"))]
pub(crate) mod tests_support {
    /// Neutral `Args` for tests in other modules.
    pub(crate) fn args() -> crate::Args {
        super::tests::test_args(None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use win_sandbox_common::rules_schema::{RuleDefaults, RuleEntry};

    #[test]
    fn nested_invocation_requires_wine_parent() {
        assert!(!is_nested_sandbox_invocation(false, Some("wine")));
        assert!(!is_nested_sandbox_invocation(true, Some("bash")));
        assert!(!is_nested_sandbox_invocation(true, None));
        assert!(!is_nested_sandbox_invocation(
            true,
            Some("win-sandbox-runner")
        ));
        assert!(is_nested_sandbox_invocation(true, Some("wine")));
        assert!(is_nested_sandbox_invocation(true, Some("wine64")));
        assert!(is_nested_sandbox_invocation(true, Some("wine-preloader")));
    }

    fn make_entry(hash: &str, tier: Tier, network: bool, trusted: bool) -> RuleEntry {
        RuleEntry {
            hash: hash.into(),
            name: "test".into(),
            tier,
            allowed_paths: vec![],
            network,
            gpu: false,
            trusted,
            dxvk: false,
            winetricks: vec![],
            env: std::collections::HashMap::new(),
            wine_variant: "system".into(),
        }
    }

    /// Minimal `Args` for tests that only exercise tier resolution, following
    /// the same literal pattern used in tier1.rs's tests (no `Default` impl
    /// exists on `Args` because clap derives its own construction).
    pub(crate) fn test_args(tier: Option<&str>) -> Args {
        Args {
            resolved_wineprefix: None,
            exe: Some("/home/test/game.exe".into()),
            tier: tier.map(String::from),
            rules: None,
            verbose: false,
            no_gui: true,
            dry_run: false,
            gamepad: false,
            nested_x11: false,
            xvfb: false,
            host_x11: false,
            wayland: false,
            args: vec![],
            trust: false,
            optimize_net: false,
            cleanup_net: false,
            configure_net: false,
            daemon: false,
            status: false,
            reload: false,
            stop: false,
            unregister: false,
            print_seatbelt_profile: false,
            wine_prefix: None,
            user_env: std::collections::HashMap::new(),
            uid: None,
            exe_identity: None,
        }
    }

    #[test]
    fn untrusted_paths_detected() {
        assert!(is_untrusted_path("/tmp/foo.exe"));
        assert!(is_untrusted_path("/mnt/usb/game.exe"));
        assert!(is_untrusted_path("/media/cdrom/setup.exe"));
        assert!(is_untrusted_path("/var/tmp/test.exe"));
        assert!(!is_untrusted_path("/home/user/game.exe"));
        assert!(!is_untrusted_path("/opt/wine-prefix/drive_c/app.exe"));
    }

    #[test]
    fn resolve_network_from_rules() {
        let rules = RulesFile {
            version: 1,
            entries: vec![make_entry("abc123", Tier::Tier2, true, false)],
            defaults: RuleDefaults::default(),
        };

        assert!(resolve_network_permission(&rules, "abc123"));
        assert!(!resolve_network_permission(&rules, "unknown"));
    }

    #[test]
    fn trusted_flag_skips_sandbox() {
        let entry = make_entry("abc", Tier::Tier0, true, true);
        assert!(entry.trusted);

        let entry = make_entry("abc", Tier::Tier2, false, false);
        assert!(!entry.trusted);
    }

    #[test]
    fn trusted_with_custom_env() {
        let mut env = std::collections::HashMap::new();
        env.insert("DXVK_HUD".into(), "1".into());

        let entry = RuleEntry {
            hash: "abc".into(),
            name: "fusion360".into(),
            tier: Tier::Tier0,
            allowed_paths: vec![],
            network: true,
            gpu: true,
            trusted: true,
            dxvk: false,
            winetricks: vec![],
            env,
            wine_variant: "proton".into(),
        };

        assert!(entry.trusted);
        assert_eq!(entry.env.get("DXVK_HUD").unwrap(), "1");
    }

    // --- resolve_tier: explicit vs heuristic provenance ---
    //
    // This is the behavior the fail-secure Tier 3 check depends on entirely:
    // it only refuses when `tier_is_explicit` is true. Every branch of
    // resolve_tier needs its own test to pin which ones set that flag.

    #[test]
    fn resolve_tier_cli_flag_is_explicit() {
        let args = test_args(Some("3"));
        let rules = RulesFile {
            version: 1,
            entries: vec![],
            defaults: RuleDefaults::default(),
        };
        let (tier, explicit) = resolve_tier(&args, "/home/u/x.exe", &rules, &None, &None).unwrap();
        assert_eq!(tier, Tier::Tier3);
        assert!(explicit, "--tier flag must count as an explicit request");
    }

    #[test]
    fn resolve_tier_explicit_rule_is_explicit() {
        let args = test_args(None);
        let rules = RulesFile {
            version: 1,
            entries: vec![],
            defaults: RuleDefaults::default(),
        };
        let entry = make_entry("h1", Tier::Tier3, false, false);
        let (tier, explicit) = resolve_tier(
            &args,
            "/home/u/x.exe",
            &rules,
            &Some(entry.clone()),
            &Some(entry),
        )
        .unwrap();
        assert_eq!(tier, Tier::Tier3);
        assert!(
            explicit,
            "a hash the user put in rules.json is an explicit promise"
        );
    }

    #[test]
    fn resolve_tier_app_database_match_is_not_explicit() {
        // matched_entry set (app-database/wizard match) but explicit_entry is
        // None: this is APEX-WIN's own guess, not something the user typed.
        let args = test_args(None);
        let rules = RulesFile {
            version: 1,
            entries: vec![],
            defaults: RuleDefaults::default(),
        };
        let heuristic_entry = make_entry("h1", Tier::Tier3, false, false);
        let (tier, explicit) = resolve_tier(
            &args,
            "/home/u/x.exe",
            &rules,
            &None,
            &Some(heuristic_entry),
        )
        .unwrap();
        assert_eq!(tier, Tier::Tier3);
        assert!(
            !explicit,
            "an app-database/wizard match must NOT count as explicit, even at tier 3"
        );
    }

    #[test]
    fn resolve_tier_untrusted_path_fallback_is_not_explicit() {
        let args = test_args(None);
        let rules = RulesFile {
            version: 1,
            entries: vec![],
            defaults: RuleDefaults {
                unmapped_tier: Tier::Tier0,
                untrusted_path_tier: Tier::Tier3,
                network_default: false,
                gpu_default: false,
            },
        };
        let (tier, explicit) =
            resolve_tier(&args, "/tmp/unknown.exe", &rules, &None, &None).unwrap();
        assert_eq!(tier, Tier::Tier3);
        assert!(
            !explicit,
            "the untrusted-path default is a heuristic, not a promise"
        );
    }

    #[test]
    fn resolve_tier_unmapped_fallback_is_not_explicit() {
        let args = test_args(None);
        let rules = RulesFile {
            version: 1,
            entries: vec![],
            defaults: RuleDefaults {
                unmapped_tier: Tier::Tier3,
                untrusted_path_tier: Tier::Tier2,
                network_default: false,
                gpu_default: false,
            },
        };
        let (tier, explicit) =
            resolve_tier(&args, "/home/u/unknown.exe", &rules, &None, &None).unwrap();
        assert_eq!(tier, Tier::Tier3);
        assert!(
            !explicit,
            "the unmapped-binary default is a heuristic, not a promise"
        );
    }

    #[test]
    fn resolve_tier_invalid_cli_flag_errors() {
        let args = test_args(Some("not-a-tier"));
        let rules = RulesFile {
            version: 1,
            entries: vec![],
            defaults: RuleDefaults::default(),
        };
        assert!(resolve_tier(&args, "/home/u/x.exe", &rules, &None, &None).is_err());
    }

    // --- trust vs. explicit tier precedence ---
    //
    // A saved trust decision is a default, not an override: an explicit
    // `--tier` on the current launch must win over "this app is trusted".
    // resolve_tier() owns that rule so CLI, GUI and daemon launches all get
    // the same answer; these tests pin it.

    #[test]
    fn trusted_rule_resolves_to_tier0_without_cli_flag() {
        let args = test_args(None);
        let rules = RulesFile {
            version: 1,
            entries: vec![],
            defaults: RuleDefaults::default(),
        };
        let entry = make_entry("h1", Tier::Tier2, true, true);
        let (tier, explicit) = resolve_tier(
            &args,
            "/home/u/x.exe",
            &rules,
            &Some(entry.clone()),
            &Some(entry),
        )
        .unwrap();
        assert_eq!(
            tier,
            Tier::Tier0,
            "trusted app must run unsandboxed by default"
        );
        assert!(
            explicit,
            "a rules.json entry is an explicit, user-authored decision"
        );
    }

    #[test]
    fn explicit_cli_flag_overrides_trusted_rule() {
        let args = test_args(Some("2"));
        let rules = RulesFile {
            version: 1,
            entries: vec![],
            defaults: RuleDefaults::default(),
        };
        let entry = make_entry("h1", Tier::Tier0, true, true);
        let (tier, explicit) = resolve_tier(
            &args,
            "/home/u/x.exe",
            &rules,
            &Some(entry.clone()),
            &Some(entry),
        )
        .unwrap();
        assert_eq!(
            tier,
            Tier::Tier2,
            "--tier on this launch must override a saved trust decision"
        );
        assert!(explicit);
    }

    #[test]
    fn trusted_appdb_entry_resolves_to_tier0_but_not_explicit() {
        // App-database/wizard matches can carry trusted=true too; same rule,
        // but provenance stays heuristic so fail-secure Tier 3 still applies.
        let args = test_args(None);
        let rules = RulesFile {
            version: 1,
            entries: vec![],
            defaults: RuleDefaults::default(),
        };
        let entry = make_entry("h1", Tier::Tier1, true, true);
        let (tier, explicit) =
            resolve_tier(&args, "/home/u/x.exe", &rules, &None, &Some(entry)).unwrap();
        assert_eq!(tier, Tier::Tier0);
        assert!(!explicit);
    }

    // --- resolve_tier / execute: dry-run side-effect guarantees ---
    //
    // These run against a temp XDG_DATA_HOME so a regression that makes
    // execute() touch the prefix tree again fails loudly instead of silently
    // creating directories under the developer's real home.

    #[test]
    fn dry_run_does_not_create_prefix_tree() {
        let tmp = tempfile::tempdir().unwrap();
        let mut args = test_args(None);
        args.dry_run = true;
        // execute() builds the PrefixManager from user_env, so pointing
        // XDG_DATA_HOME at the tempdir is how the test observes side effects
        // without touching the real home directory.
        args.user_env
            .insert("XDG_DATA_HOME".into(), tmp.path().display().to_string());
        let rules = RulesFile {
            version: 1,
            entries: vec![],
            defaults: RuleDefaults::default(),
        };
        let app_db = appdb::AppDatabase {
            version: 1,
            description: "test".into(),
            profiles: vec![],
        };
        // Unknown exe -> wizard path -> prefix step -> tier resolution. Every
        // one of those must stay read-only under dry-run.
        let result = execute(
            &mut args,
            "/home/u/unknown-app.exe",
            "deadbeef",
            &rules,
            &app_db,
        );
        assert!(result.is_ok(), "dry-run must succeed: {result:?}");
        let prefix_root = tmp.path().join("win-sandbox").join("prefixes");
        assert!(
            !prefix_root.exists(),
            "dry-run must not create the prefix tree under XDG_DATA_HOME"
        );
    }

    #[test]
    fn untrusted_path_boundary_matches_directory_edges() {
        // The untrusted roots themselves and anything inside them.
        assert!(is_untrusted_path("/tmp"));
        assert!(is_untrusted_path("/tmp/x.exe"));
        assert!(is_untrusted_path("/mnt/usb/game.exe"));
        assert!(is_untrusted_path("/media/cdrom/setup.exe"));
        assert!(is_untrusted_path("/var/tmp/test.exe"));
        assert!(is_untrusted_path("/dev/shm/drop.exe"));
    }

    #[test]
    fn similarly_named_paths_are_not_untrusted() {
        // The old starts_with(prefix) check matched these; the boundary-aware
        // check must not. "/tmpfoo" is not "/tmp".
        assert!(!is_untrusted_path("/tmpfoo/x.exe"));
        assert!(!is_untrusted_path("/tmpX"));
        assert!(!is_untrusted_path("/mntx/game.exe"));
        assert!(!is_untrusted_path("/var/tmpfoo/x.exe"));
        assert!(!is_untrusted_path("/media2/cdrom/setup.exe"));
        // A "tmp" directory inside a trusted home is not an untrusted root.
        assert!(!is_untrusted_path("/home/user/tmp/x.exe"));
    }

    // --- check_tier3_available: the actual fail-secure gate ---

    #[test]
    fn tier3_available_when_capability_present() {
        assert!(check_tier3_available(true, &Some("0.10.0".into())).is_ok());
    }

    #[test]
    fn tier3_unavailable_reports_bwrap_version_when_known() {
        let err = check_tier3_available(false, &Some("0.9.0".into())).unwrap_err();
        assert!(
            err.contains("0.9.0"),
            "refusal reason must name the installed version so a user can act on it: {err}"
        );
        assert!(err.contains(">= 0.10.0"));
    }

    #[test]
    fn tier3_unavailable_reports_missing_bwrap() {
        let err = check_tier3_available(false, &None).unwrap_err();
        assert!(err.contains("not found"));
    }

    // --- check_tier_implemented_for_os: the cross-platform tier-existence gate ---

    fn no_caps() -> crate::capabilities::Capabilities {
        crate::capabilities::Capabilities {
            landlock_abi: None,
            bwrap_version: None,
            unprivileged_overlay: false,
            seatbelt_available: None,
        }
    }

    #[test]
    fn tier0_always_implemented_regardless_of_os() {
        let caps = no_caps();
        assert!(check_tier_implemented_for_os(Tier::Tier0, &caps, true, "linux").is_ok());
        assert!(check_tier_implemented_for_os(Tier::Tier0, &caps, false, "macos").is_ok());
    }

    #[test]
    fn tier1_2_3_all_implemented_on_linux() {
        let caps = no_caps();
        for tier in [Tier::Tier1, Tier::Tier2, Tier::Tier3] {
            assert!(
                check_tier_implemented_for_os(tier, &caps, true, "linux").is_ok(),
                "Tier {} must be implemented when is_linux=true",
                tier.level()
            );
        }
    }

    /// With no capability info at all (no Landlock, no bwrap, no Seatbelt --
    /// e.g. a platform this project has never run on), every non-Tier-0
    /// tier must refuse. This is deliberately the baseline: the tests below
    /// prove Tier 1/2 specifically become available once Seatbelt is.
    #[test]
    fn tier1_2_3_all_refused_with_no_capabilities_at_all() {
        let caps = no_caps();
        for tier in [Tier::Tier1, Tier::Tier2, Tier::Tier3] {
            let err = check_tier_implemented_for_os(tier, &caps, false, "macos").unwrap_err();
            assert!(
                err.contains("macos"),
                "refusal reason must name the actual OS so a user knows why: {err}"
            );
        }
    }

    /// Tier 3 has no Seatbelt (or any other macOS) backing at all -- unlike
    /// Tier 1/2, no capability flag can ever flip this to Ok. Named
    /// mechanisms in the message should match what the profile actually
    /// needs, not a generic "not supported" so a reader understands why.
    #[test]
    fn tier3_refusal_names_the_real_mechanisms() {
        let caps = no_caps();
        let err = check_tier_implemented_for_os(Tier::Tier3, &caps, false, "macos").unwrap_err();
        assert!(err.contains("OverlayFS"));
    }

    /// Same as above, but proves capability flags genuinely cannot unlock
    /// Tier 3 the way they do for Tier 1/2 -- Seatbelt present must not
    /// change the outcome.
    #[test]
    fn tier3_stays_refused_even_with_seatbelt_available() {
        let mut caps = no_caps();
        caps.seatbelt_available = Some(true);
        assert!(check_tier_implemented_for_os(Tier::Tier3, &caps, false, "macos").is_err());
    }

    #[test]
    fn tier2_refusal_names_the_real_mechanisms() {
        let caps = no_caps();
        let err = check_tier_implemented_for_os(Tier::Tier2, &caps, false, "macos").unwrap_err();
        assert!(err.contains("Landlock"));
        assert!(err.contains("bubblewrap"));
        assert!(err.contains("Seatbelt"));
    }

    /// The actual point of this whole change: Seatbelt genuinely unlocks
    /// Tier 1/2 on non-Linux, not just a mention in the refusal text.
    #[test]
    fn tier1_and_tier2_available_on_non_linux_when_seatbelt_present() {
        let mut caps = no_caps();
        caps.seatbelt_available = Some(true);
        for tier in [Tier::Tier1, Tier::Tier2] {
            assert!(
                check_tier_implemented_for_os(tier, &caps, false, "macos").is_ok(),
                "Tier {} must be available on macOS when Seatbelt is present",
                tier.level()
            );
        }
    }

    /// Seatbelt genuinely absent (checked and not usable, not just "not
    /// macOS") must still refuse Tier 1/2 -- and the refusal should say so,
    /// distinguishing "we checked, it's not there" from "this platform has
    /// no capability field for it at all" (the None case below).
    #[test]
    fn tier1_and_tier2_refusal_mentions_seatbelt_checked_when_confirmed_absent() {
        let mut caps = no_caps();
        caps.seatbelt_available = Some(false);
        for tier in [Tier::Tier1, Tier::Tier2] {
            let err = check_tier_implemented_for_os(tier, &caps, false, "macos").unwrap_err();
            assert!(
                err.contains("sandbox-exec was checked and is not usable"),
                "Tier {} refusal should say Seatbelt was checked and found absent: {err}",
                tier.level()
            );
        }
    }

    /// `seatbelt_available: None` means "not applicable on this platform"
    /// (see the field's own doc comment) -- distinct from `Some(false)`
    /// ("checked, not usable"). The refusal wording must not conflate them.
    #[test]
    fn tier1_and_tier2_refusal_omits_seatbelt_mention_when_not_applicable() {
        let caps = no_caps();
        for tier in [Tier::Tier1, Tier::Tier2] {
            let err = check_tier_implemented_for_os(tier, &caps, false, "windows").unwrap_err();
            assert!(!err.contains("sandbox-exec"));
        }
    }

    /// The public `check_tier_implemented` wrapper must actually forward to
    /// `cfg!(target_os = "linux")`/`std::env::consts::OS`, not some other
    /// hardcoded value -- this pins that wiring down. It can only assert
    /// against whichever OS actually runs this test, which is why the
    /// `_for_os` tests above exist to cover the branch this build isn't on.
    #[test]
    fn public_wrapper_reflects_actual_host_os() {
        let caps = no_caps();
        let result = check_tier_implemented(Tier::Tier1, &caps);
        if cfg!(target_os = "linux") {
            assert!(result.is_ok());
        } else {
            assert!(result.is_err());
        }
    }
}
