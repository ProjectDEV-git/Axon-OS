# Debug Agent 7 — GUI Applications: Executive Summary

**Date:** 2026-07-02
**Scope:** All GTK4/libadwaita desktop applications in Axon OS
**Files Analyzed:** 14 source files across 8 applications

---

## Critical Bugs (Fix Immediately)

### 1. CSS Providers Accumulated on Every Window Open (5 apps affected)
**Apps:** AIPanel, IntentBar, SandboxDialog, Welcome, Shortcuts
**Impact:** Memory leak + CSS specificity conflicts. Every time a user opens/closes these windows, a duplicate CSS provider is registered globally. Over a session, this accumulates.
**Fix:** Add class-level or module-level `_css_loaded` guard flags (already done correctly in Files, Settings, Terminal, VoiceOverlay).

### 2. No Light Theme Support in Any App
**Apps:** All 8 apps
**Impact:** The system's dark/light theme toggle is completely ignored. All Axon apps are hardcoded dark-only. The Settings app even has a "Toggle Theme" button that does nothing to the app's own appearance.
**Fix:** Define CSS custom properties at `:root` using GNOME named colors, or use `Adw.StyleManager` to detect and respond to theme changes.

---

## High-Severity Issues

### 3. Streaming Background Threads Not Cancelled on Window Close
**Apps:** AIPanel, IntentBar
**Impact:** Background threads continue running after the window is hidden/closed, firing `GLib.idle_add` callbacks on potentially destroyed widgets. Can cause GTK critical warnings and wasted resources.
**Fix:** Add cancellation flags and check them in streaming loops.

### 4. No `destroy` Handler for Cleanup
**Apps:** AIPanel, IntentBar
**Impact:** Signal connections and D-Bus subscriptions are not cleaned up. Background threads can outlive the window.
**Fix:** Follow the pattern from `welcome.py` which properly disconnects D-Bus signals on `destroy`.

### 5. Terminal Missing Ctrl+Shift+W (Close Tab) Handler
**Impact:** The shortcuts overlay documents this shortcut but it doesn't work. Only the TabBar close button works.
**Fix:** Add the keyboard handler in `axon-terminal/main.py`.

### 6. Deprecated `Gtk.Dialog` in Terminal Safety Prompt
**Impact:** Uses deprecated GTK4 API. Doesn't follow libadwaita visual style.
**Fix:** Replace with `Adw.AlertDialog`.

### 7. AI Commands Executed Without User Confirmation in Intent Bar
**Impact:** `run_command` actions from the AI are executed directly. The `open_app` path has validation, but the `run_command` path only relies on `safe_exec()`.
**Fix:** Add a confirmation dialog before executing AI-generated shell commands.

### 8. Terminal Tab Close Doesn't Explicitly Kill Child Process
**Impact:** When a tab is closed, the child shell process may continue running as an orphan until Python's GC destroys the VTE widget.
**Fix:** Send SIGHUP explicitly before removing the tab.

### 9. Inconsistent Background Colors Across All Apps
**Impact:** At least 5 different "near-black" background colors (`#09090f`, `#0a0a12`, `#0b0b12`, `#0c0c10`, `#0f0f14`) create subtle visual inconsistency when apps are side by side.
**Fix:** Define a single `--bg-primary` variable.

### 10. Inconsistent Text Colors Across All Apps
**Impact:** Three different primary text colors (`#e8e8f4`, `#e4e4e8`, `#e2e8f0`) and 8+ muted text colors.
**Fix:** Define `--text-primary`, `--text-secondary`, `--text-muted` variables.

---

## Medium-Severity Issues

| # | Issue | App(s) |
|---|-------|--------|
| 11 | Settings executor thread race on rapid clicks | Settings |
| 12 | Intent bar streaming not cancellable | Intent Bar |
| 13 | AI Panel stream state not reset on visibility toggle | AI Panel |
| 14 | CSS specificity conflict: `.ai-toggle-btn` defined in both Files and Terminal | Files, Terminal |
| 15 | Deprecated `get_style_context()` API (GTK 4.10+) | Files, Welcome |
| 16 | Inconsistent accent colors (`#8b5cf6` vs `#c4b5fd` vs `#5b21b6`) | Terminal, Sandbox |
| 17 | Inconsistent font stacks across apps | All |
| 18 | Unsupported CSS `transform` property silently ignored | Files |
| 19 | Super+Space activation depends entirely on external daemon | Intent Bar |

---

## Low/Informational Issues

| # | Issue | App(s) |
|---|-------|--------|
| 20 | VoiceOverlay timer ID not cleared after removal | VoiceOverlay |
| 21 | No multi-monitor handling in any app | All |
| 22 | Files popover created per right-click (should reuse) | Files |
| 23 | Welcome D-Bus signal handler double-hops via GLib.idle_add unnecessarily | Welcome |
| 24 | Inconsistent border-radius tokens | All |
| 25 | Inconsistent shadow usage | All |
| 26 | SpacesManager file locking (multi-process risk) | Cross-app |

---

## Recommended Architecture: Design Token System

The root cause of most CSS issues is the lack of a shared design token system. Recommend creating a shared CSS file:

```css
/* axon-design-tokens.css */
@define-color bg_primary #0f0f14;
@define-color bg_secondary #11111e;
@define-color text_primary #e8e8f4;
@define-color text_secondary #9090b8;
@define-color text_muted #50507a;
@define-color accent #8b5cf6;
@define-color accent_hover #7c3aed;
@define-color accent_active #6d28d9;
@define-color border #2a2a42;
@define-color success #10b981;
@define-color error #ef4444;
```

Each app would import this single file instead of defining its own colors. This ensures visual consistency across the entire desktop environment.

---

## Validation Performed

- Read and analyzed 14 source files across 8 applications
- Cross-referenced CSS color values across all apps for consistency
- Verified CSS provider registration patterns in each app
- Traced signal connections and lifecycle handlers
- Checked thread safety patterns for background operations
- Verified keyboard shortcut implementations against documented shortcuts
- Checked for deprecated GTK4 API usage

---

## Files Analyzed

| File | App |
|------|-----|
| `apps/axon-ai-panel/ui/panel.py` | AI Panel |
| `apps/axon-files/ui.py` | Files |
| `apps/axon-files/main.css` | Files |
| `apps/axon-settings/main.py` | Settings |
| `apps/axon-settings/main.css` | Settings |
| `apps/axon-settings/settings_executor.py` | Settings |
| `apps/axon-welcome/ui/welcome.py` | Welcome |
| `apps/axon-shortcuts/main.py` | Shortcuts |
| `apps/axon-shortcuts/main.css` | Shortcuts |
| `apps/axon-terminal/main.py` | Terminal |
| `apps/axon-terminal/terminal_widget.py` | Terminal |
| `apps/axon-terminal/main.css` | Terminal |
| `apps/axon-voice-overlay/main.py` | Voice Overlay |
| `apps/intent-bar/ui/window.py` | Intent Bar |
| `apps/intent-bar/spaces_manager.py` | Intent Bar |
| `services/axon-sandbox/sandbox_manager.py` | Sandbox |
