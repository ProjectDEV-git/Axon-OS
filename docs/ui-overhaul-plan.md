# Axon OS UI Overhaul — Plan and Checklist

This is the working checklist for improving the Axon OS user interface. Work through
it in phase order. Tick each box (`- [x]`) in the same commit that completes it, and
add a short note under any item you skip or change.

## Ground rules

- Read `AGENTS.md` (Axon-OS section), `docs/architecture.md` and `DEVELOPING.md` first.
- Before every push run: `ruff check apps/ services/ tests/ installer/`,
  `ruff format --check apps/ services/ tests/ installer/`,
  `mypy apps/ services/ --ignore-missing-imports`, and
  `pytest tests/ -v --tb=short --cov=apps --cov=services --cov-fail-under=40`.
- One commit per checklist item (or a small group of closely related items), with
  conventional messages (`fix(intent-bar): ...`, `feat(spaces): ...`).
- Never weaken command safety. `validate_command`, `confirm_command` and
  `confirm_and_exec` in `services/service_utils.py` must stay fail-closed, and the
  command allowlist stays in force.
- GNOME Shell code is GNOME 45+ ES modules (`import St from 'gi://St'`). The Shell
  can't be run in a container: put logic you can test in small pure functions,
  syntax-check the JS, and say clearly which parts were not tested in a real session.
- When you move logic into pure Python helpers, add unit tests for them in `tests/`.
- Don't delete features or apps. Don't touch other projects in this workspace.

## Map of the UI

| Surface | Files | Notes |
|---|---|---|
| Intent Bar (primary, Super+Space) | `shell/axon-shell/intentbar.js`, `ai-actions.js` | Shell extension |
| Intent Bar (GTK app) | `apps/intent-bar/ui/window.py`, `ollama_client.py` | Second copy; fix bugs in both |
| AI side panel | `apps/axon-ai-panel/ui/panel.py`, `context_reader.py` | GTK4 + libadwaita |
| Spaces, Space OSD | `shell/axon-shell/spaces.js` | Auto-routes new windows |
| Dock, Start Menu | `shell/axon-shell/dock.js`, `startmenu.js` | |
| Shell styling | `shell/axon-shell/stylesheet.css` | Hardcoded colours/sizes |
| Shell settings / keybindings | `shell/axon-shell/schemas/org.gnome.shell.extensions.axon-shell.gschema.xml` | Recompile `gschemas.compiled` after edits |
| GTK design tokens | `apps/shared/axon-design-tokens.css` | Only 4 of 10 apps import it |
| Apps with embedded CSS | `apps/intent-bar/ui/window.py`, `apps/axon-ai-panel/ui/panel.py`, `apps/axon-welcome/ui/welcome.py`, `apps/axon-installer/ui/wizard.py` | |
| GTK theme | `theme/axon-gtk/gtk-dark.css` | |
| Brain D-Bus service | `services/axon-brain/brain_service.py` | `GetStatus`, `ListConversations`, `GetMessages`, `ListAllModels`, ... |
| AI providers | `services/axon-brain/providers.py`, `docs/ai-providers.md` | Cloud models use `@provider/model` |
| Context service | `services/axon-context/` | Clipboard history, active window, etc. |
| Command safety | `services/service_utils.py`, `tests/test_command_safety.py` | Approval is a `zenity` dialog today |
| Diagnostics | `services/diagnostics.py`, `apps/axon-settings/diagnostics_window.py` | Reuse for the status surface |
| i18n helper | `services/i18n.py`, `tests/test_i18n.py` | Not used by any app yet |

---

## Phase 0 — Baseline

- [x] Run the full QA commands above and record which pass on the untouched branch,
      so later failures can be told apart from existing ones.
      Baseline (2026-10-26, Python 3.12, ruff 0.8.6): `ruff check` passes; `mypy` passes
      (39 files); `pytest` 630 passed, 21 skipped, coverage 48.9%; `bandit` reports no
      medium+ issues. `ruff format --check` already fails on 17 untouched files (CI does
      not run it), so only files changed by this overhaul are held to it.
- [x] Read every file in the map above once.

## Phase 1 — Bug fixes (small, high impact)

- [x] **AI panel quick chips send half a prompt.** In `panel.py` the chip handler calls
      `self._send_message(lbl + ": ")`, which immediately sends `"Summarize: "`. Change it
      to put the prefix into the entry, move the cursor to the end and focus the entry,
      the same way `apps/intent-bar/ui/window.py::_on_chip_clicked` does.
- [x] **Raw JSON shown in the Intent Bar.** When the model replies with an action
      (`{"action": ...}`), each token is shown as it arrives and the reply is parsed only at
      the end. Hold back output that starts with `{` (after whitespace or a ```` ``` ```` fence)
      until the stream ends. If it parses as an action, show a friendly line ("Opening
      Firefox…") instead of the JSON. If it doesn't, show the text as normal. Put the
      "does this look like an action" check in a pure helper with tests. Do this in both
      Intent Bars, checking how `intentbar.js` gets its response first.
      Note: the Shell bar calls `Generate` with `stream=false`, so it never showed partial
      JSON; it now shares `parseAction` (also accepts fenced JSON) via `ui-logic.js`.
- [x] **Long answers push the Intent Bar off-screen.** GTK: wrap `_response_label` in a
      `Gtk.ScrolledWindow` with `set_max_content_height(...)` and
      `set_propagate_natural_height(True)`. Shell: put the response in an `St.ScrollView`
      with a `max-height` in the stylesheet.
- [x] **UI freezes while checking if the AI is available.** `is_available()` runs on the
      main thread before every query (a D-Bus call in `apps/intent-bar`, an HTTP request with
      a 2 s timeout in the AI panel). Move it to the worker thread, or cache the result and
      refresh it in the background.
- [x] **Escape doesn't cancel.** In both Intent Bars and the AI panel, the first Escape while
      a request is running must stop it (set the cancel flag, stop the spinner, re-enable
      the entry, show "Cancelled"). Only the next Escape hides the window. Show a visible
      Stop button while a request is running.
      Note: the Shell bar uses non-streaming `Generate`, which the Brain can't cancel; the
      late reply is dropped instead. The GTK apps also call Brain `CancelStream`.
- [x] **AI panel header clutter.** The header shows both a model dropdown and a model badge
      with the same name, plus an unused `spacer`. Keep the dropdown, remove the badge or
      repurpose it as the local/cloud indicator from Phase 3, and remove the spacer.
      Note: badge removed; Phase 3 adds the local/cloud indicator.
- [x] **Emoji icon buttons.** Replace `🗑`, `⎘` and any other emoji buttons with symbolic
      icons (`user-trash-symbolic`, `edit-copy-symbolic`, ...) and give each an accessible
      label (`btn.update_property([Gtk.AccessibleProperty.LABEL], ["Copy message"])`),
      keeping the tooltips.
      Note: also fixed the terminal's `✕` dismiss button. Buttons that pair a glyph with
      text ("← Back", "⬡ AI") were left as they are.
- [x] **Errors look like normal answers.** Errors are plain text such as
      `[error] Ollama is not running...` in the normal response colour. Add an `error` style
      (error colour plus an icon) and keep the recovery button next to it (e.g. Start Ollama).
      Apply it in both Intent Bars and the AI panel.
      Note: recovery is "Start Ollama" (GTK Intent Bar), "Diagnostics" (Shell bar, opens
      Axon Settings → Diagnostics) and "Try again" / "Diagnostics" (AI panel).
- [x] **AI panel too tall for small screens.** `set_default_size(420, 860)` on an
      undecorated window doesn't fit 768 px-high displays. Size it from the monitor's
      work area (e.g. height = min(860, work-area height − 48)).
      Note: GTK4 has no portable work-area API (Wayland hides it), so this uses the monitor
      geometry minus the 48 px margin, via `fit_to_screen()`.
- [x] **Space-change announcement does nothing.** `spaces.js` calls
      `Atk.get_default_root().emit_children_changed()`, which doesn't announce anything.
      Replace it with an accessible status element whose text/name is updated to e.g.
      "Switched to Code Space. 3 windows open." Check which mechanism actually announces on
      GNOME 45+ and note in the commit message how you verified it.
      Note: not verified with Orca in a real session (no GNOME Shell in the container). The
      OSD now has the NOTIFICATION role and the announcement as its accessible name.

## Phase 2 — Quick UX wins

### Spaces
- [x] **Tell the user when a window is moved.** `_routeWindowToSpace` in `spaces.js` moves
      windows silently (only `console.log`). Show a notification ("Firefox moved to Web")
      with an **Undo** action that moves the window back.
- [x] **Per-app rules.** Add a "Remember this choice" option. Store per-app routing rules
      (app ID → Space, or "never route") in a gsettings key, and check them before asking
      the AI.
- [x] **Setting to turn routing off.** Add an `auto-route-windows` boolean (default true) to
      the gschema and check it before routing. Expose it in Axon Settings if there is a
      natural place for it.
- [x] **Ask when unsure.** If the classifier returns a confidence (or can be made to), ask
      below a threshold instead of moving: "Axon thinks this belongs in Code.
      [Move] [Keep here] [Always do this]".
      Note: new Brain method `ClassifyWindowDetailed` returns `{space, confidence, source}`
      (well-known apps 0.95, exact model answer 0.75, loose answer 0.4); the Shell asks
      below 0.6 and falls back to `ClassifyWindow` on older Brains. Rules and the switch are
      in the new Axon Settings → Preferences window.
- [x] **Fast Space OSD.** The OSD asks the AI for a summary on every switch. Show a summary
      built from the window list straight away (e.g. "3 windows · Code · Terminal · Firefox")
      in a pure helper. Optionally replace it with an AI summary after a short delay, behind
      a gsettings toggle (default off), and drop the request if the user switches again.

### Intent Bar
- [x] **Clear loading states.** Replace "Thinking…" with real stages: "Connecting to Axon
      Brain", "Searching local files", "Waiting for model", "Planning action", "Ready for
      confirmation". Show only stages the code actually reaches.
      Note: the Shell bar can't see model progress (non-streaming call), so it shows only
      Connecting / Waiting / Ready for confirmation.
- [x] **Useful empty state.** Before the user types, show 3–4 example prompts ("Open my Code
      space", "find the PDF I edited yesterday", "Set volume to 40%"), the last few
      commands from history, and the keyboard shortcuts. Clicking an example fills the entry.
      Note: examples only use what works today, so "Set volume to 40%" was left out (`pactl`
      is not allowlisted). GTK history is now saved to `~/.axon/intent_history.json`
      (mode 0600); the Shell keeps its history for the session.

### Responsive sizing
- [x] **Intent Bar width.** Replace `min-width: 680px` with a width computed from the
      monitor: clamp(480 px, 45% of monitor width, 820 px), set from JS using the monitor
      the bar opens on.
- [x] **Start Menu size.** Replace fixed `width: 620px; height: 520px` with sizes clamped to
      the monitor's work area.
- [x] **Other fixed sizes.** Check dock icon sizes, OSD sizes and GTK `set_default_size`
      calls for windows that don't fit a 1366×768 screen at 100% scale or a 1280×800 screen
      at 125% scale. Fix those that don't.
      Note: Files (1000×680), Terminal (900×600), Welcome (640×600), Installer (1060×720),
      AI Models, Diagnostics and Preferences now go through `fit_default_size()`
      (`apps/shared/axon_gtk_utils.py`). Dock (48 px bar, 32 px icons) and the Space OSD
      already fit 1024×640 logical.

## Phase 3 — Trust: privacy, context, safety, status

- [ ] **Local vs cloud indicator.** Axon supports cloud models (`@openai/...`,
      `@anthropic/...`, see `docs/ai-providers.md`). Add a pure helper that tells local
      Ollama models from cloud ones, and show "Local · llama3.2:3b" or "Cloud · OpenAI ·
      gpt-4.1" in the AI panel header and the Intent Bar. Never say "no cloud connection"
      when a cloud model is selected.
- [ ] **Context controls (backend).** Add settings for what context may be sent:
      clipboard, terminal history, open files, active app, plus a master on/off. Make
      `apps/axon-ai-panel/context_reader.py::build_context_string` (and any Shell/Brain
      equivalent) respect them. Defaults: active app on, others as they are today. Add tests.
- [ ] **Context controls (UI).** In the AI panel, above the entry, show what will be sent
      ("Context: Firefox, main.py, terminal history") with a way to remove items for this
      message and a link to the settings. Add the same toggles to Axon Settings.
- [ ] **Cloud warning.** When a cloud model is selected and context includes clipboard,
      terminal history or file contents, say so clearly before the first send in a
      conversation.
- [ ] **Command confirmation card.** Replace the `zenity` question (and the Shell's
      "Asking before running: …" text) with an Axon-styled confirmation showing: the exact
      command, what it affects where known, whether it needs admin rights, a risk level
      (pure helper with tests: e.g. read-only / changes files / needs root), and
      **Allow once** / **Cancel**. Keep it fail-closed: timeout, error or closed dialog =
      no. Keep the allowlist check before the card.
      "Always allow" is allowed only for an exact command string at the lowest risk level,
      stored in a list the user can view and clear in Settings, and never for commands
      that need root or delete files.
- [ ] **Axon status surface.** Add one place (a Shell top-bar indicator/popover, or a
      section at the top of the AI panel) showing: Brain service, Ollama, active model,
      model download progress if available, semantic index progress if available, voice
      service, and the last error with its fix. Reuse `services/diagnostics.py` and Brain
      `GetStatus` instead of new checks. Link to the full Diagnostics window.

## Phase 4 — One design system

- [ ] **Single token source.** Create one file (e.g. `theme/tokens/axon-tokens.json` or
      SCSS) holding: colours, semantic states (success/warning/error/info), spacing scale,
      corner radii, typography sizes/weights, shadows, focus ring, animation durations, with
      dark, light and high-contrast variants.
- [ ] **Generator.** GNOME Shell's St CSS has no `@define-color` or CSS variables, so the
      Shell can't import the GTK token file. Write `scripts/gen_tokens.py` that generates
      `apps/shared/axon-design-tokens.css` (GTK `@define-color`) and the Shell stylesheet
      (e.g. from a `stylesheet.css.in` template). Add a test that fails when generated
      files are out of date.
- [ ] **Move embedded CSS out of Python.** Move the CSS strings in
      `apps/intent-bar/ui/window.py`, `apps/axon-ai-panel/ui/panel.py`,
      `apps/axon-welcome/ui/welcome.py` and `apps/axon-installer/ui/wizard.py` into `.css`
      files that import the shared tokens. No hardcoded hex colours left in app code.
      (Example of drift today: Intent Bar background `#09090f` vs token `#0f0f14`.)
- [ ] **Shell stylesheet on tokens.** Replace every hardcoded colour, radius and spacing
      value in `shell/axon-shell/stylesheet.css` with generated token values.
- [ ] **Drop the "Windows glass" look.** Remove the "Windows 10/11 Fluent Modern Glass
      (Acrylic)" design-system header. Reduce heavy transparency and large shadows, use
      fewer floating rounded boxes, and follow GNOME/libadwaita spacing and hierarchy.
- [ ] **Use purple sparingly.** Reserve the accent for active, selected and AI-related
      states. Use neutral borders and the standard focus ring elsewhere.
- [ ] **Follow system appearance.** GTK apps follow `Adw.StyleManager` (light/dark) and
      high contrast. The Shell follows `org.gnome.desktop.interface color-scheme`.
- [ ] **Reduced motion.** Respect `org.gnome.desktop.interface enable-animations` in all
      Shell animations and GTK transitions.

## Phase 5 — Larger features

### Intent Bar
- [ ] **Categorised suggestions while typing.** Applications (`Shell.AppSystem` /
      `Gio.AppInfo`), Files (axon-search), Settings, Spaces, Commands, "Ask AI". Keyboard
      navigable, with an accessible list.
- [ ] **Action preview.** Before running an action show what will happen ("Open Visual
      Studio Code in Code Space", "Run: `pytest tests/ -v`", "Change volume to 40%"),
      confirmed with Enter.

### AI panel
- [ ] **Conversation list.** Sidebar or popover using Brain `ListConversations` /
      `GetMessages`, with rename, delete and archive (add Brain methods if missing, with tests).
- [ ] **Message actions.** Stop generation, regenerate, copy, insert into the focused app
      (if feasible; otherwise skip and note why).
- [ ] **Model and latency.** Show the model and response time for each answer.
- [ ] **Sources.** When local files were used as context, list their paths under the answer.
- [ ] **Action cards.** Show proposed system changes as cards using the Phase 3
      confirmation component.
- [ ] **Compact mode.** Layout works at 320 px wide.

### Spaces visibility
- [ ] Numbered indicators matching Super+1…9.
- [ ] Window count and activity marker for each Space, with distinct active / inactive /
      empty states that don't rely on colour alone.
- [ ] Quick switcher with search (keyboard first).
- [ ] Optional: small window previews and drag-and-drop between Spaces.

### Accessibility
- [ ] Every Shell popup and GTK app can be fully used with the keyboard (Tab order, arrows
      in lists, Escape closes).
- [ ] Visible focus ring everywhere (from the token file).
- [ ] Accessible names on every icon-only button and on dynamic content (responses, status).
- [ ] Announce when an AI action finishes or fails.
- [ ] Text scaling: layouts survive 1.25× and 1.5× "Large Text".
- [ ] Status shown with icon or text as well as colour.

### Translation
- [ ] Wire `services/i18n.py` into the GTK apps and mark user-facing strings with `_()`.
- [ ] Use the extension's gettext for Shell strings.
- [ ] Add a `po/` template (`axon-os.pot`) and a script or Make target to refresh it.

---

## Progress log

Add a dated line per session: what was done, what was skipped and why, what wasn't
tested in a real GNOME session.

- 2026-10-26 — Phase 0 and Phase 1 done (10 items). Pure logic went into
  `services/ui_logic.py` and its JS twin `shell/axon-shell/ui-logic.js`, with Python and
  node tests. GTK smoke tests (`tests/test_ui_*.py`) run under `xvfb-run` and skip without
  a display. Not tested in a real GNOME session: the Shell Intent Bar (scroll view, Stop
  button, error row, Diagnostics button) and the Space OSD announcement with Orca.
- 2026-10-26 — Phase 2 done. Spaces now notify on moves (Undo / Always / Never), keep
  per-app rules, can be turned off, ask when the new `ClassifyWindowDetailed` is unsure,
  and show an instant OSD summary. Intent Bars got loading stages and an empty state;
  windows fit small screens. New Axon Settings → Preferences window. Not tested in a real
  GNOME session: notifications and their actions (`notify.js`, GNOME 45 vs 46+ APIs),
  window moves/undo, the OSD AI summary timing, Shell empty state and Start Menu sizing.
