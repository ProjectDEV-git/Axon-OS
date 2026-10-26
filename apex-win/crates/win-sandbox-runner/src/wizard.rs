use std::io::Write;
use tracing::info;
use win_sandbox_common::rules_schema::RuleEntry;
use win_sandbox_common::tier::Tier;

use crate::appdb;

/// First-launch wizard result.
pub struct WizardResult {
    pub entry: RuleEntry,
    pub source: WizardSource,
}

/// Where the wizard got its decision from.
#[derive(Debug, Clone, PartialEq, Eq)]
#[allow(dead_code)]
pub enum WizardSource {
    /// Auto-detected from the app database by exe name.
    AppDatabase,
    /// Auto-detected by heuristics (installer, known safe, etc.).
    AutoDetect,
    /// User chose interactively.
    UserChoice,
    /// User chose to always trust this app.
    UserTrusted,
}

/// Run the first-launch wizard for an unknown .exe.
///
/// In headless mode (no_gui=true), auto-detects tier from the app database
/// and heuristics. In interactive mode (not headless, run from a real
/// terminal, not dispatched through the daemon), prompts the user.
///
/// `from_daemon` must be `args.uid.is_some()` at the call site -- see
/// `run_wizard_with`'s doc comment for why that specific signal, not
/// `isatty` alone, is what makes blocking on a prompt here safe.
pub fn run_wizard(
    exe_path: &str,
    app_db: &appdb::AppDatabase,
    no_gui: bool,
    hash: &str,
    from_daemon: bool,
) -> WizardResult {
    run_wizard_with(exe_path, app_db, no_gui, hash, from_daemon, &mut RealPrompt)
}

/// `run_wizard`'s actual logic, with the daemon-mode flag and the prompt
/// mechanism taken as parameters instead of read directly (`libc::isatty`,
/// stdin/stdout). This is the same seam pattern used by
/// `dispatch::check_tier_implemented_for_os`: the interactive-prompt branch
/// cannot be exercised by a plain unit test at all if it reads process-
/// global state directly, so both "prompt appears and is honored" and
/// "prompt is skipped" need to be reachable from a deterministic test.
///
/// `from_daemon` is `args.uid.is_some()` at the call site: every request
/// dispatched through the daemon's FIFO carries a UID to switch to (see
/// `daemon.rs`'s `handle_launch`), while direct CLI invocations never do.
/// This is a more reliable "is this actually an interactive terminal
/// session" signal than `isatty(STDIN_FILENO)` alone: a daemon thread has
/// no controlling terminal to prompt on even if stdin happens to pass an
/// isatty check (e.g. if the daemon inherited a real console fd from
/// whatever spawned it), and blocking a background daemon thread waiting
/// for input that will never come would hang every launch behind it.
fn run_wizard_with(
    exe_path: &str,
    app_db: &appdb::AppDatabase,
    no_gui: bool,
    hash: &str,
    from_daemon: bool,
    prompt: &mut dyn Prompt,
) -> WizardResult {
    // 1. Try app database match first
    if let Some((profile, entry)) = app_db.lookup_by_name(exe_path) {
        info!(
            "First launch: matched '{}' in app database -> '{}'",
            exe_path, profile.name
        );
        if !profile.notes.is_empty() {
            info!("Note: {}", profile.notes);
        }
        return WizardResult {
            entry,
            source: WizardSource::AppDatabase,
        };
    }

    // 2. Auto-detect from heuristics
    let (suggested_tier, reason) = appdb::auto_detect_tier(exe_path);
    info!("First launch: {reason}");

    // 3. Interactive confirmation, when it is actually safe to block on one.
    //
    // win-sandbox-gui exists as a separate crate but nothing in this binary
    // ever talks to its IPC socket (verified: no call site references it
    // anywhere in win-sandbox-runner), so it was never reachable from here
    // on any platform, not just macOS. A blocking TTY prompt is the
    // version of "ask the user" that is actually wired up, on Linux and
    // macOS alike, rather than adding a second disconnected GUI path.
    //
    // Whether stdin is actually a usable terminal is decided inside
    // `prompt.confirm()` (via `RealPrompt`'s own `is_interactive()` check),
    // not here: this lets a fake `Prompt` in tests always be exercised
    // regardless of whether `cargo test`'s own stdin happens to be a TTY,
    // while `RealPrompt` still correctly declines to block when it isn't.
    if !no_gui && !from_daemon {
        if let Some(choice) = prompt.confirm(exe_path, suggested_tier) {
            return WizardResult {
                entry: make_auto_entry(exe_path, hash, choice),
                source: WizardSource::UserChoice,
            };
        }
        // Not interactive (RealPrompt's own isatty check failed), or EOF /
        // unreadable input: fall through to auto-detect below rather than
        // hang or error out.
        info!("No interactive response, using auto-detected tier {suggested_tier}");
    }

    WizardResult {
        entry: make_auto_entry(exe_path, hash, suggested_tier),
        source: WizardSource::AutoDetect,
    }
}

/// Abstraction over "ask the user which tier to use", so `run_wizard_with`
/// can be tested with a scripted answer instead of a real terminal.
trait Prompt {
    /// Returns the chosen tier, or `None` if no answer could be obtained
    /// (not an interactive terminal, EOF, unreadable stdin).
    fn confirm(&mut self, exe_path: &str, suggested_tier: Tier) -> Option<Tier>;
}

/// The real terminal prompt used outside tests. Portable across Linux and
/// macOS: both read stdin/write stderr the same way, so there is no
/// platform-specific implementation here the way there is for
/// `seatbelt.rs` — a working TTY prompt makes the earlier "macOS needs its
/// own osascript dialog" framing moot, since the underlying gap (no
/// interactive path existed at all) applied identically to Linux.
struct RealPrompt;

impl Prompt for RealPrompt {
    fn confirm(&mut self, exe_path: &str, suggested_tier: Tier) -> Option<Tier> {
        if !is_interactive() {
            return None;
        }

        let name = std::path::Path::new(exe_path)
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or(exe_path);
        eprint!(
            "\nAPEX-WIN: first launch of '{name}'\n\
             Suggested isolation tier: {suggested_tier} ({})\n\
             Press Enter to accept, or type a tier number (0-3), or 'q' to quit: ",
            tier_description(suggested_tier)
        );
        let _ = std::io::stderr().flush();

        let mut line = String::new();
        match std::io::stdin().read_line(&mut line) {
            Ok(0) => None, // EOF
            Ok(_) => {
                let trimmed = line.trim();
                if trimmed.is_empty() {
                    Some(suggested_tier)
                } else if trimmed.eq_ignore_ascii_case("q") {
                    // Deliberately still returns a tier (the suggestion)
                    // rather than aborting the launch entirely: "quit" here
                    // means "stop asking me, just use your suggestion",
                    // which matches what pressing Enter does. A real abort
                    // is out of scope for this prompt -- run_wizard has no
                    // "don't run at all" outcome to return into.
                    Some(suggested_tier)
                } else {
                    Tier::from_str_level(trimmed).ok().or(Some(suggested_tier))
                }
            }
            Err(_) => None,
        }
    }
}

/// Check if stdin is a TTY (interactive terminal). Only called by
/// `RealPrompt`, never by `run_wizard_with` directly -- see the comment at
/// that call site for why.
fn is_interactive() -> bool {
    unsafe { libc::isatty(libc::STDIN_FILENO) != 0 }
}

fn tier_description(tier: Tier) -> &'static str {
    match tier {
        Tier::Tier0 => "no sandbox",
        Tier::Tier1 => "filesystem isolation",
        Tier::Tier2 => "filesystem + namespace isolation",
        Tier::Tier3 => "full ephemeral isolation",
    }
}

/// Create a RuleEntry for an auto-detected app.
fn make_auto_entry(exe_path: &str, hash: &str, tier: Tier) -> RuleEntry {
    let name = std::path::Path::new(exe_path)
        .file_stem()
        .and_then(|n| n.to_str())
        .unwrap_or("unknown")
        .to_string();

    let network = tier >= Tier::Tier2;
    let gpu = true; // Most Windows apps need GPU

    RuleEntry {
        hash: hash.to_string(),
        name,
        tier,
        allowed_paths: vec![],
        network,
        gpu,
        trusted: false,
        dxvk: false,
        winetricks: vec![],
        env: std::collections::HashMap::new(),
        wine_variant: "system".into(),
    }
}

/// Show what the wizard decided (for logging/UI).
pub fn describe_decision(result: &WizardResult) -> String {
    let entry = &result.entry;
    let source = match result.source {
        WizardSource::AppDatabase => "app database",
        WizardSource::AutoDetect => "auto-detected",
        WizardSource::UserChoice => "user selected",
        WizardSource::UserTrusted => "user trusted",
    };

    let mut parts = vec![
        format!("App: {}", entry.name),
        format!("Source: {source}"),
        format!("Tier: {}", entry.tier),
    ];

    if entry.trusted {
        parts.push("Trusted: yes (no sandbox)".into());
    }
    if entry.network {
        parts.push("Network: enabled".into());
    }
    if entry.gpu {
        parts.push("GPU: enabled".into());
    }
    if entry.dxvk {
        parts.push("DXVK: will install".into());
    }
    if !entry.winetricks.is_empty() {
        parts.push(format!("Winetricks: {}", entry.winetricks.join(", ")));
    }

    parts.join(" | ")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wizard_finds_app_in_database() {
        let db = appdb::AppDatabase {
            version: 1,
            description: "test".into(),
            profiles: vec![appdb::AppProfile {
                name: "Fusion 360".into(),
                match_names: vec!["Fusion360.exe".into()],
                tier: Tier::Tier0,
                trusted: true,
                network: true,
                gpu: true,
                dxvk: true,
                winetricks: vec!["dotnet48".into()],
                env: std::collections::HashMap::new(),
                wine_variant: "staging".into(),
                notes: "CAD app".into(),
            }],
        };

        let result = run_wizard("C:\\Fusion360.exe", &db, true, "abc123", false);
        assert_eq!(result.source, WizardSource::AppDatabase);
        assert!(
            !result.entry.trusted,
            "an app-database name match must not grant trust"
        );
    }

    #[test]
    fn wizard_falls_back_to_auto_detect() {
        let db = appdb::AppDatabase {
            version: 1,
            description: "test".into(),
            profiles: vec![],
        };

        // Installer detected
        let result = run_wizard("setup_myapp.exe", &db, true, "def456", false);
        assert_eq!(result.source, WizardSource::AutoDetect);
        assert_eq!(result.entry.tier, Tier::Tier2);

        // Unknown app
        let result = run_wizard("random.exe", &db, true, "ghi789", false);
        assert_eq!(result.source, WizardSource::AutoDetect);
        assert_eq!(result.entry.tier, Tier::Tier1);
    }

    // --- run_wizard_with: the interactive-prompt path, driven via a fake
    // Prompt so it's testable without a real terminal. `run_wizard`'s
    // public no-args-for-daemon/interactivity behavior is only reachable
    // this way; see the doc comment on run_wizard_with for why.

    struct FakePrompt {
        answer: Option<Tier>,
        called: bool,
    }

    impl Prompt for FakePrompt {
        fn confirm(&mut self, _exe_path: &str, _suggested_tier: Tier) -> Option<Tier> {
            self.called = true;
            self.answer
        }
    }

    fn empty_db() -> appdb::AppDatabase {
        appdb::AppDatabase {
            version: 1,
            description: "test".into(),
            profiles: vec![],
        }
    }

    #[test]
    fn interactive_prompt_is_asked_and_honored() {
        let mut prompt = FakePrompt {
            answer: Some(Tier::Tier3),
            called: false,
        };
        let result = run_wizard_with(
            "random.exe",
            &empty_db(),
            /* no_gui */ false,
            "hash",
            /* from_daemon */ false,
            &mut prompt,
        );
        assert!(prompt.called, "an interactive, non-daemon launch must ask");
        assert_eq!(result.source, WizardSource::UserChoice);
        assert_eq!(result.entry.tier, Tier::Tier3);
    }

    #[test]
    fn no_gui_skips_the_prompt_entirely() {
        let mut prompt = FakePrompt {
            answer: Some(Tier::Tier3),
            called: false,
        };
        let result = run_wizard_with("random.exe", &empty_db(), true, "hash", false, &mut prompt);
        assert!(
            !prompt.called,
            "--no-gui must never trigger a blocking prompt"
        );
        assert_eq!(result.source, WizardSource::AutoDetect);
    }

    /// The specific bug this seam exists to prevent: a request dispatched
    /// through the daemon's FIFO must never block waiting on stdin the
    /// daemon thread has no real access to, even if `no_gui` happens to be
    /// false and even if isatty() would (incorrectly, for this context)
    /// report true.
    #[test]
    fn daemon_dispatched_request_never_prompts_even_with_no_gui_false() {
        let mut prompt = FakePrompt {
            answer: Some(Tier::Tier3),
            called: false,
        };
        let result = run_wizard_with(
            "random.exe",
            &empty_db(),
            /* no_gui */ false,
            "hash",
            /* from_daemon */ true,
            &mut prompt,
        );
        assert!(
            !prompt.called,
            "daemon-dispatched requests must never block on a prompt"
        );
        assert_eq!(result.source, WizardSource::AutoDetect);
    }

    /// EOF (prompt returns None) must fall back to the heuristic suggestion
    /// rather than hang or propagate an error -- run_wizard has no "abort
    /// the launch" outcome to return into.
    #[test]
    fn prompt_returning_none_falls_back_to_auto_detect() {
        let mut prompt = FakePrompt {
            answer: None,
            called: false,
        };
        let result = run_wizard_with("random.exe", &empty_db(), false, "hash", false, &mut prompt);
        assert!(prompt.called);
        assert_eq!(result.source, WizardSource::AutoDetect);
        assert_eq!(result.entry.tier, Tier::Tier1);
    }

    /// An app database match short-circuits before the prompt is ever
    /// reached -- a known app's profile is authoritative, matching the
    /// existing (pre-prompt) behavior exactly.
    #[test]
    fn app_database_match_takes_priority_over_prompting() {
        let db = appdb::AppDatabase {
            version: 1,
            description: "test".into(),
            profiles: vec![appdb::AppProfile {
                name: "Fusion 360".into(),
                match_names: vec!["Fusion360.exe".into()],
                tier: Tier::Tier0,
                trusted: true,
                network: true,
                gpu: true,
                dxvk: true,
                winetricks: vec![],
                env: std::collections::HashMap::new(),
                wine_variant: "system".into(),
                notes: String::new(),
            }],
        };
        let mut prompt = FakePrompt {
            answer: Some(Tier::Tier3),
            called: false,
        };
        let result = run_wizard_with("C:\\Fusion360.exe", &db, false, "hash", false, &mut prompt);
        assert!(!prompt.called);
        assert_eq!(result.source, WizardSource::AppDatabase);
    }

    #[test]
    fn tier_description_covers_every_tier() {
        // Each arm reachable, and none panics via an unmatched variant --
        // this would fail to compile if a Tier variant were added without
        // updating tier_description's match.
        for tier in [Tier::Tier0, Tier::Tier1, Tier::Tier2, Tier::Tier3] {
            assert!(!tier_description(tier).is_empty());
        }
    }

    #[test]
    fn describe_decision_shows_info() {
        let result = WizardResult {
            entry: RuleEntry {
                hash: "abc".into(),
                name: "Test".into(),
                tier: Tier::Tier2,
                allowed_paths: vec![],
                network: true,
                gpu: true,
                trusted: false,
                dxvk: true,
                winetricks: vec!["vcrun2019".into()],
                env: std::collections::HashMap::new(),
                wine_variant: "system".into(),
            },
            source: WizardSource::AppDatabase,
        };

        let desc = describe_decision(&result);
        assert!(desc.contains("Test"));
        assert!(desc.contains("app database"));
        assert!(desc.contains("DXVK"));
        assert!(desc.contains("vcrun2019"));
    }
}
