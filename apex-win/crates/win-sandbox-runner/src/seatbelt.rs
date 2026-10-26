//! macOS Tier 1/2: filesystem (and, for Tier 2, network) isolation via
//! Apple's Seatbelt sandbox (`sandbox-exec` + SBPL profiles).
//!
//! This is the macOS analogue of `tier1.rs` (Landlock) and `tier2.rs`
//! (bubblewrap). It is NOT equivalent to either: Seatbelt has no mount or
//! PID namespace, no resource limits, and the confined process still runs
//! as the same user with the same visible process table. What it does
//! provide, and what these two tiers rely on, is a real MAC (mandatory
//! access control) boundary enforced by the XNU kernel: a `(deny default)`
//! profile that denies every filesystem write and, for Tier 2, every
//! network operation except what is explicitly allowed. That boundary
//! cannot be bypassed by the sandboxed process itself, the same property
//! Landlock and bubblewrap provide on Linux, just with a smaller set of
//! resources actually confined.
//!
//! `sandbox-exec` is deprecated by Apple with no public replacement for
//! confining arbitrary third-party binaries (App Sandbox requires the
//! binary opt in via entitlements, which a Windows .exe running under Wine
//! cannot do). It remains the only mechanism available, and is used in
//! production by Chrome, OpenAI Codex and Gemini CLI for exactly this kind
//! of untrusted-subprocess confinement — see HANDOFF.md for the sources
//! this was verified against before relying on it here.
//!
//! ## Profile design
//!
//! Both tiers use `(deny default)` plus a minimal allowlist, mirroring
//! `tier1.rs`'s Landlock ruleset:
//!   - `file-read*`: unrestricted, matching Landlock Tier 1's intent (Wine
//!     needs to read broadly across `/usr`, `/opt`, frameworks, fonts, etc.
//!     and enumerating all of that up front is both impractical and not
//!     the property either tier is trying to enforce — the write side is).
//!   - `file-write*`: scoped to the resolved Wine prefix and a per-launch
//!     temp directory only. This is the actual isolation boundary.
//!   - `process-exec`/`process-fork`: required for `wineserver` and its
//!     subprocesses, which the parent Wine process spawns. Seatbelt
//!     profiles are inherited by children (confirmed against a real
//!     production profile before this was written), so this one rule
//!     covers the whole process tree, the same way Landlock's
//!     `restrict_self()` does.
//!   - Network: Tier 1 allows it (matching Landlock Tier 1, which also
//!     cannot fully block network — see tier1.rs's own doc comment); Tier 2
//!     denies it outright, which Seatbelt CAN do completely (unlike
//!     Landlock), making Tier 2 here strictly more capable on the network
//!     axis than Linux's own Tier 1.

use crate::Args;
use anyhow::{Context, Result};
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::process::{Command, ExitCode};
use tracing::info;

/// Build the SBPL (Sandbox Profile Language) text for a given tier.
///
/// `wine_prefix` and `tmp_dir` are embedded as `(subpath ...)` literals
/// rather than `(param ...)` placeholders resolved via `-D`: this profile is
/// generated fresh per-launch (see `run`), so there is no reuse benefit to
/// parameterizing it, and inlining the literal path is one fewer thing that
/// can silently drift between the profile text and what was actually
/// intended to be granted.
///
/// `network` only affects Tier 2 (Tier 1 always allows network, matching
/// Landlock's own inability to fully block it — see the module doc comment).
pub fn build_profile(
    wine_prefix: &str,
    tmp_dir: &str,
    exe_dir: Option<&str>,
    network: bool,
) -> String {
    let mut profile = String::from(
        "(version 1)\n\
         (deny default)\n\
         \n\
         ;; Read broadly. Wine touches /usr, /opt, frameworks, fonts, ICU\n\
         ;; data, etc. across the system; enumerating all of it defeats the\n\
         ;; purpose (Tier 1's actual boundary is the write side below), and\n\
         ;; matches tier1.rs's Landlock allowlist, which grants /usr, /lib,\n\
         ;; /opt, /etc, /bin, /sbin read-only rather than trying to enumerate\n\
         ;; every path Wine might touch.\n\
         (allow file-read*)\n\
         \n\
         ;; wineserver and its children must be spawnable; Seatbelt profiles\n\
         ;; are inherited by child processes, so this one rule covers the\n\
         ;; whole process tree the same way Landlock's restrict_self() does.\n\
         (allow process-exec)\n\
         (allow process-fork)\n\
         (allow signal (target self))\n\
         \n\
         ;; The actual isolation boundary: writes are denied everywhere\n\
         ;; except the Wine prefix (registry, drive_c, wineserver lock) and\n\
         ;; this launch's own scratch directory.\n\
         (allow file-write*\n",
    );

    profile.push_str(&format!("    (subpath \"{}\")\n", sbpl_escape(wine_prefix)));
    profile.push_str(&format!("    (subpath \"{}\")\n", sbpl_escape(tmp_dir)));
    for dev in ["/dev/null", "/dev/zero", "/dev/tty", "/dev/dtracehelper"] {
        profile.push_str(&format!("    (literal \"{dev}\")\n"));
    }
    profile.push_str(")\n");

    // The directory the .exe itself lives in is often where an installer
    // or portable app expects to write logs/config next to itself. Landlock
    // Tier 1 does not grant this as read-write (only read), so Seatbelt
    // Tier 1/2 don't either, deliberately staying no more permissive than
    // the Linux tier they mirror.
    let _ = exe_dir;

    profile.push_str(
        "\n\
         ;; A handful of read-only sysctls Wine/the dynamic linker probe at\n\
         ;; startup. Mirrors the equivalent allowlist in production Seatbelt\n\
         ;; profiles (Chrome, Gemini CLI) rather than falling back to a\n\
         ;; broad (allow sysctl-read), which would defeat deny-default for\n\
         ;; no benefit any of these tiers need.\n\
         (allow sysctl-read\n\
         \x20   (sysctl-name \"hw.ncpu\")\n\
         \x20   (sysctl-name \"hw.activecpu\")\n\
         \x20   (sysctl-name \"hw.pagesize\")\n\
         \x20   (sysctl-name \"hw.machine\")\n\
         \x20   (sysctl-name \"hw.model\")\n\
         \x20   (sysctl-name \"kern.osversion\")\n\
         \x20   (sysctl-name \"kern.osrelease\")\n\
         \x20   (sysctl-name \"kern.ostype\"))\n\
         \n\
         ;; Mach IPC that GUI apps (which Wine's Win32 windowing depends on\n\
         ;; via the host WindowServer) and dynamic linking both require.\n\
         ;; Without these, Wine cannot open a window or even start.\n\
         (allow mach-lookup\n\
         \x20   (global-name \"com.apple.windowserver.active\")\n\
         \x20   (global-name \"com.apple.CoreServices.coreservicesd\")\n\
         \x20   (global-name \"com.apple.distributed_notifications@Uv3\")\n\
         \x20   (global-name \"com.apple.system.opendirectoryd.libinfo\")\n\
         \x20   (global-name \"com.apple.system.notification_center\")\n\
         \x20   (global-name \"com.apple.fonts\"))\n",
    );

    if network {
        profile.push_str(
            "\n\
             ;; Tier 1: network is allowed, matching Landlock Tier 1's own\n\
             ;; inability to fully block network access (tier1.rs's doc\n\
             ;; comment: \"Landlock cannot block all network access\").\n\
             (allow network*)\n",
        );
    } else {
        profile.push_str(
            "\n\
             ;; Tier 2: network is denied outright. Seatbelt CAN block this\n\
             ;; completely (unlike Landlock), so Tier 2 here is strictly\n\
             ;; more capable on the network axis than Linux Tier 1, and\n\
             ;; matches Tier 2's own \"network=false\" meaning on Linux\n\
             ;; (bwrap --unshare-net without --share-net).\n",
        );
    }

    profile
}

/// Escape a path for embedding inside an SBPL string literal. SBPL strings
/// are Scheme-style double-quoted literals; the only characters that need
/// escaping for a filesystem path are `"` and `\`, but a path containing
/// either is unusual enough that refusing it outright is safer than
/// emitting a profile whose write-scope silently doesn't mean what the path
/// looked like it meant.
fn sbpl_escape(path: &str) -> String {
    if path.contains('"') || path.contains('\\') {
        // Fail loudly at generation time rather than emit a profile that
        // parses as something other than the intended path. In practice
        // Wine prefixes and tmp dirs are program-generated (hash-based),
        // so this should never trigger; it exists so a future caller
        // passing a user-influenced path fails safely instead of silently.
        panic!(
            "path {path:?} contains a character that cannot be safely embedded in an SBPL \
             string literal (\" or \\); refusing to generate a profile that might not mean \
             what it looks like"
        );
    }
    path.to_string()
}

/// Resolve the writable temp directory for this launch. Mirrors
/// `tier1.rs::build_rw_paths`'s per-process scratch directory, created
/// fresh each launch and never reused across processes.
fn tmp_dir_for_this_launch() -> String {
    let pid = std::process::id();
    let dir = format!("{}/apex-win-runtime-{pid}", std::env::temp_dir().display());
    let _ = std::fs::create_dir_all(&dir);
    dir
}

/// Tier 1: Seatbelt sandbox allowing network (matching Landlock Tier 1).
pub fn run(args: &Args) -> Result<ExitCode> {
    run_tier(args, true)
}

/// Tier 2: Seatbelt sandbox denying network (matching bwrap Tier 2 with
/// `network=false`; APEX-WIN's macOS port has no TAP-bridge equivalent, so
/// unlike Linux Tier 2 there is no `network=true` variant here yet — an
/// app that needs both isolation and network on macOS should use Tier 1).
pub fn run_with_network(args: &Args, network: bool) -> Result<ExitCode> {
    if network {
        // Tier 2 with network=true has no Seatbelt equivalent to bwrap's
        // TAP bridge yet. Rather than silently grant full network (which
        // would make "Tier 2, network=true" mean something different and
        // weaker than what the same tier+flag combination means on Linux),
        // fail loudly so this gap is visible instead of silently wrong.
        anyhow::bail!(
            "Tier 2 with networking enabled has no macOS implementation yet (no TAP-bridge \
             equivalent to Linux's Tier 2 networking). Use --tier 1 for network + isolation, \
             or set this app's network to false in rules.json for Tier 2 without network."
        );
    }
    run_tier(args, false)
}

fn run_tier(args: &Args, network: bool) -> Result<ExitCode> {
    let exe = args.exe.as_deref().unwrap();
    let tier_name = if network { "Tier 1" } else { "Tier 2" };
    info!("{tier_name}: Seatbelt sandbox for {exe} (network={network})");

    // Dispatch carries the resolved per-app prefix on Args; it no longer
    // exports WINEPREFIX to the process env, so reading that here made
    // every Tier 1/2 launch fail.
    let config = crate::config::load_config(None);
    let wine_prefix = crate::env_sanitize::resolved_prefix(args, &config);
    let tmp_dir = tmp_dir_for_this_launch();
    let exe_dir = Path::new(exe).parent().and_then(|p| p.to_str());

    let profile = build_profile(&wine_prefix, &tmp_dir, exe_dir, network);
    let profile_path = format!("{tmp_dir}/profile.sb");
    std::fs::write(&profile_path, &profile)
        .with_context(|| format!("failed to write Seatbelt profile to {profile_path}"))?;

    let user_env = crate::env_sanitize::user_env_for_args(args);
    let sandbox_env = crate::env_sanitize::build_sandbox_env(
        &config,
        user_env,
        args.resolved_wineprefix.as_deref(),
    )?;

    let mut cmd = Command::new("sandbox-exec");
    cmd.arg("-f").arg(&profile_path).arg("wine").arg(exe);
    cmd.args(&args.args);

    cmd.env_clear();
    for (key, val) in &sandbox_env {
        cmd.env(key, val);
    }
    cmd.env("WINEPREFIX", &wine_prefix);

    crate::hasher::verify_unchanged(exe, args.exe_identity.as_ref())?;

    if let Some(uid) = args.uid {
        // The macOS daemon is a per-user LaunchAgent and only serves its own
        // uid (daemon::authorize_launch_peer), so this drop is a no-op in
        // practice; it still fails the spawn if the uid ever differs and
        // cannot be assumed. Daemon mode must not exec (it would replace
        // the daemon), so spawn and wait.
        unsafe { crate::privs::drop_to_uid(&mut cmd, uid) };
        let status = cmd.status()?;
        return Ok(ExitCode::from(
            status.code().unwrap_or(1).clamp(0, 255) as u8
        ));
    }

    let err = cmd.exec();
    anyhow::bail!("Failed to exec sandbox-exec: {err}");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn profile_denies_by_default() {
        let p = build_profile("/tmp/prefix", "/tmp/scratch", None, true);
        assert!(p.starts_with("(version 1)\n(deny default)"));
    }

    #[test]
    fn profile_scopes_writes_to_prefix_and_tmp_only() {
        let p = build_profile("/Users/alice/wine-prefix", "/tmp/scratch-123", None, true);
        assert!(p.contains("(subpath \"/Users/alice/wine-prefix\")"));
        assert!(p.contains("(subpath \"/tmp/scratch-123\")"));
    }

    #[test]
    fn tier1_profile_allows_network() {
        let p = build_profile("/tmp/prefix", "/tmp/scratch", None, true);
        assert!(p.contains("(allow network*)"));
    }

    #[test]
    fn tier2_profile_denies_network() {
        let p = build_profile("/tmp/prefix", "/tmp/scratch", None, false);
        assert!(
            !p.contains("(allow network"),
            "Tier 2 profile must not contain any network allow rule: {p}"
        );
    }

    #[test]
    fn profile_allows_broad_read_matching_landlock_tier1() {
        let p = build_profile("/tmp/prefix", "/tmp/scratch", None, true);
        assert!(p.contains("(allow file-read*)"));
    }

    #[test]
    fn profile_allows_process_exec_for_wineserver() {
        let p = build_profile("/tmp/prefix", "/tmp/scratch", None, true);
        assert!(p.contains("(allow process-exec)"));
        assert!(p.contains("(allow process-fork)"));
    }

    /// The one property that would silently break Tier 1/2 confinement:
    /// forgetting to scope file-write* at all would fall through to
    /// deny-default's blanket denial (safe but non-functional) or, if
    /// someone "fixed" a Wine failure by loosening this, to
    /// `(allow file-write*)` with no subpath (a real regression). This
    /// pins the exact rule shape down so that regression is caught.
    #[test]
    fn write_rule_is_subpath_scoped_not_unconditional() {
        let p = build_profile("/tmp/prefix", "/tmp/scratch", None, true);
        assert!(
            !p.contains("(allow file-write*)\n"),
            "file-write* must never appear unscoped (no subpath/literal filter): {p}"
        );
        assert!(p.contains("(allow file-write*\n"));
    }

    #[test]
    #[should_panic(expected = "cannot be safely embedded")]
    fn embedding_a_quote_in_a_path_panics_rather_than_silently_truncating_scope() {
        build_profile("/tmp/pre\"fix", "/tmp/scratch", None, true);
    }

    #[test]
    fn devices_are_literal_not_subpath() {
        // /dev/null etc. must be exact matches, not prefixes -- a subpath
        // match on "/dev/null" would also match a hypothetical
        // "/dev/nullish-thing", granting write access to something that
        // was never intended to be writable.
        let p = build_profile("/tmp/prefix", "/tmp/scratch", None, true);
        assert!(p.contains("(literal \"/dev/null\")"));
        assert!(!p.contains("(subpath \"/dev/null\")"));
    }
}
