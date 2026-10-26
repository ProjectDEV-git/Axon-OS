//! Detects sandbox capabilities actually available on this machine.
//!
//! Reports backend feature presence. Probes do not certify a successful sandbox
//! launch: Tier 3 still uses mount(8), and runtime setup must fail securely even
//! when bubblewrap advertises overlay support.

use std::process::Command;
use tracing::debug;

/// What this host can actually provide for each isolation tier.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Capabilities {
    /// Highest Landlock ABI enforced, if any (Tier 1 depends on this).
    /// Linux-only: always `None` on other platforms, since Landlock is a
    /// Linux LSM with no equivalent elsewhere.
    pub landlock_abi: Option<u8>,
    /// bubblewrap version string as reported by `bwrap --version`, if found.
    /// Linux-only in practice: bubblewrap is not packaged for macOS.
    pub bwrap_version: Option<String>,
    /// Whether bubblewrap both is new enough (>= 0.10, when `--overlay` was
    /// added) and advertises the `--overlay` flag in its `--help` output.
    /// This is a feature-presence probe, not proof of a working mount: the
    /// Tier 3 backend still uses `mount(8)`, so a `true` here does not mean
    /// an unprivileged overlay launch will succeed. When this is `false`,
    /// an explicit Tier 3 request refuses and a heuristic one degrades to
    /// Tier 2 — see dispatch's fail-secure handling.
    pub unprivileged_overlay: bool,
    /// macOS only: whether `sandbox-exec` (Seatbelt) is present on this
    /// host. `None` on non-macOS platforms, where the field does not apply
    /// — deliberately not defaulted to `Some(false)`, so a caller can tell
    /// "not macOS" apart from "macOS but sandbox-exec missing".
    ///
    /// `sandbox-exec` is deprecated by Apple and its profile language is
    /// undocumented for third-party use (see HANDOFF.md), so even when this
    /// is `true` it is a best-effort filesystem restriction, not a Landlock-
    /// or bubblewrap-equivalent security boundary. Tier 1/2 on macOS report
    /// this honestly rather than claiming an isolation level they cannot
    /// back up the way the Linux tiers can.
    pub seatbelt_available: Option<bool>,
}

impl Capabilities {
    /// Probe the host. Cheap enough to call once at daemon startup and once
    /// per `--status` request; nothing here talks to the network or blocks
    /// meaningfully.
    pub fn detect() -> Self {
        Self {
            landlock_abi: detect_landlock_abi_level(),
            bwrap_version: detect_bwrap_version(),
            unprivileged_overlay: detect_unprivileged_overlay(),
            seatbelt_available: detect_seatbelt_available(),
        }
    }

    /// Whether a Tier 3 request can be honored as real ephemeral-overlay
    /// isolation on this host, as opposed to silently becoming Tier 2.
    pub fn tier3_available(&self) -> bool {
        self.unprivileged_overlay
    }

    /// Whether Tier 1/2 (filesystem-restriction isolation) can be honored on
    /// this host at all. On Linux this means Landlock or bubblewrap
    /// respectively — callers should check `landlock_abi`/`bwrap_version`
    /// directly for which. On macOS it means `sandbox-exec` is present; see
    /// the caveat on [`Capabilities::seatbelt_available`] about what that
    /// isolation is actually worth.
    pub fn tier12_available(&self) -> bool {
        self.landlock_abi.is_some()
            || self.bwrap_version.is_some()
            || self.seatbelt_available == Some(true)
    }
}

/// macOS only: detect whether `sandbox-exec` is present. Always `None` on
/// other platforms — see [`Capabilities::seatbelt_available`].
#[cfg(target_os = "macos")]
fn detect_seatbelt_available() -> Option<bool> {
    Some(
        Command::new("sandbox-exec")
            .arg("-n")
            .arg("no-network")
            .arg("/usr/bin/true")
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false),
    )
}

#[cfg(not(target_os = "macos"))]
fn detect_seatbelt_available() -> Option<bool> {
    None
}

#[cfg(target_os = "linux")]
fn detect_landlock_abi_level() -> Option<u8> {
    // Delegates to tier1's probe rather than reimplementing it. An earlier
    // version of this function called `Ruleset::create()` directly, on the
    // assumption that `handle_access()` alone might not reflect the real
    // kernel. Empirically (this session, kernel 7.0) `.create()` agreed with
    // `handle_access()` for every ABI level, so the extra syscall bought
    // nothing — and a second copy of the probe is exactly the kind of
    // duplication that caused the binfmt mask bug to recur three times.
    crate::tier1::detect_landlock_abi()
        .ok()
        .map(|abi| abi as u8)
}

/// Landlock is a Linux LSM; there is nothing to probe on other platforms.
#[cfg(not(target_os = "linux"))]
fn detect_landlock_abi_level() -> Option<u8> {
    None
}

fn detect_bwrap_version() -> Option<String> {
    let output = Command::new("bwrap").arg("--version").output().ok()?;
    if !output.status.success() {
        return None;
    }
    // "bubblewrap 0.9.0\n" -> "0.9.0"
    String::from_utf8_lossy(&output.stdout)
        .split_whitespace()
        .last()
        .map(str::to_string)
}

/// Parse a bubblewrap version string and check it is >= 0.10.0, the first
/// release with unprivileged `--overlay` support.
fn version_supports_overlay(version: &str) -> bool {
    let parts: Vec<u32> = version.split('.').filter_map(|p| p.parse().ok()).collect();
    match parts.as_slice() {
        [major, minor, ..] => *major > 0 || *minor >= 10,
        _ => false,
    }
}

/// Feature presence only: --help avoids mounts or temporary files during
/// previews. It cannot establish kernel support or backend runtime usability.
fn advertises_overlay(version: &str, help: &str) -> bool {
    version_supports_overlay(version)
        && help
            .lines()
            .any(|line| line.split_whitespace().next() == Some("--overlay"))
}

fn detect_unprivileged_overlay() -> bool {
    let Some(version) = detect_bwrap_version() else {
        return false;
    };
    if !version_supports_overlay(&version) {
        debug!("bwrap {version} lacks the required overlay version (>= 0.10)");
        return false;
    }
    match Command::new("bwrap").arg("--help").output() {
        Ok(output) if output.status.success() => {
            advertises_overlay(&version, &String::from_utf8_lossy(&output.stdout))
        }
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_parsing_below_threshold() {
        assert!(!version_supports_overlay("0.9.0"));
        assert!(!version_supports_overlay("0.4.1"));
        assert!(!version_supports_overlay("0.9.99"));
    }

    #[test]
    fn version_parsing_at_or_above_threshold() {
        assert!(version_supports_overlay("0.10.0"));
        assert!(version_supports_overlay("0.11.2"));
        assert!(version_supports_overlay("1.0.0"));
        assert!(version_supports_overlay("2.3.4"));
    }

    #[test]
    fn version_parsing_handles_garbage() {
        assert!(!version_supports_overlay(""));
        assert!(!version_supports_overlay("not-a-version"));
    }

    #[test]
    fn overlay_advertisement_requires_both_version_and_flag() {
        let help_with = "Usage:\n  --overlay NEWROOT\n  --tmpfs DIR\n";
        let help_without = "Usage:\n  --tmpfs DIR\n  --ro-bind SRC DST\n";
        assert!(advertises_overlay("0.11.2", help_with));
        assert!(!advertises_overlay("0.11.2", help_without));
        // Version alone is not enough even when the flag text appears.
        assert!(!advertises_overlay("0.9.0", help_with));
        assert!(!advertises_overlay("0.9.0", help_without));
        assert!(!advertises_overlay("garbage", help_with));
    }

    /// Real probe against whatever is installed on the machine running the
    /// test. Does not assert a fixed outcome (CI's bwrap may differ): it
    /// asserts internal consistency — an advertised overlay can only be
    /// reported when the detected version is new enough.
    #[test]
    fn detect_matches_installed_bwrap() {
        let caps = Capabilities::detect();
        if caps.unprivileged_overlay {
            let v = caps
                .bwrap_version
                .as_ref()
                .expect("overlay=true requires a detected bwrap");
            assert!(
                version_supports_overlay(v),
                "overlay capability must not be reported for bwrap {v} (< 0.10)"
            );
        }
    }
}
