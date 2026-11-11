# AGENTS.md

Multi-project workspace. Each project has its own conventions — read project-specific docs before modifying code.

## Orchestration Pipeline

The primary workflow uses a multi-agent pipeline defined at `.mimocode/agent/`:

```
subagent-controller (orchestrator)
  ├── explore         — read-only codebase discovery
  ├── bug-hunter      — find bugs/vulns/quality issues (read-only)
  ├── bug-fixer       — implement fixes (writes code)
  ├── test-runner     — validate fixes, check regressions
  ├── code-reviewer   — final quality gate (read-only)
  ├── feature-architect — break vague requests into technical steps
  ├── doc-generator   — auto-write/update documentation
  ├── git-manager     — stage, commit (semantic), branch
  └── general         — research, multi-step tasks, consolidation
```

**Default QA pipeline**: `explore → bug-hunter → bug-fixer → test-runner → code-reviewer`

**Orchestration strategies**:
- **Parallel fan-out**: spawn multiple independent agents simultaneously
- **Sequential pipeline**: output of one feeds the next (bug-hunter → bug-fixer → test-runner)
- **Fan-out + gather**: parallel scan, then consolidate findings
- **Recursive delegation**: spawn sub-controllers for multi-area tasks

**Run via**: `/qa-pipeline <path>` or invoke `subagent-controller` agent directly.

## Mimocode Structure

```
.mimocode/
  agent/          — agent definitions (*.md, YAML frontmatter: description, mode: agent|subagent)
  skills/         — skill playbooks (*/SKILL.md)
  command/        — command definitions (*.md)
```

- Agent files use YAML frontmatter (`description`, `mode: agent|subagent`)
- Skills at `.mimocode/skills/<name>/SKILL.md`
- Commands at `.mimocode/command/<name>.md`

## Agent Skills

### $feature-architect
- **Purpose**: Takes vague/large feature requests, breaks them into clear technical step-by-step tasks
- **When to use**: Complex new features that shouldn't go straight to bug-fixer; need design before code
- **Output**: Structured implementation plan with file structure, logic design, and dependency order
- **Trigger**: "add feature", "build X", "implement Y", "design Z"

### $doc-generator
- **Purpose**: Auto-writes docstrings, README updates, API documentation for code changes
- **When to use**: After code changes to keep documentation in sync; on-demand doc generation
- **Output**: Updated docstrings, markdown docs, API references
- **Trigger**: "document this", "update docs", "add docstrings", "write README"

### $git-manager
- **Purpose**: Stages files, writes semantic commit messages, creates branches
- **When to use**: After code passes code-reviewer; clean version control hygiene
- **Output**: Commits with conventional format (`feat:`, `fix:`, `docs:`, etc.), proper branch names
- **Trigger**: "commit", "push", "create branch", "stage changes"

## Projects

### Bolan (PHP/MySQL) — `Documents/Bolan website/`
- Restaurant website (bolan.co.th), PHP + MySQL
- **Security-critical**: all DB output must use `safe_output()` (meta/attrs) or `safe_html_output()` (content display)
- CSRF: `csrf_field()` in forms, `verify_csrf_token()` in actions (session-based tokens in `function_admin.php`)
- Passwords: bcrypt via `password_hash()`/`verify_password()`, NOT MD5
- DB: `bolan_2022`, config at `main/config/config.json`
- `safe_html_output()` has a known regex bug with nested/self-closing tags — needs rewrite
- Exclude `test01/` and `backup/` from security fixes

### Axon-OS (Python/GTK) — `Documents/Axons-OS/Axon-OS`
- AI-native Linux distro, Ubuntu 24.04 + GNOME Shell 45+
- Services use hyphens in dir names (`axon-brain/`) but Python imports need underscores — `tests/conftest.py` aliases automatically
- **Lint**: `ruff check apps/ services/ tests/ installer/` (auto-fix: `--fix`)
- **Format**: `ruff format apps/ services/ tests/ installer/`
- **Typecheck**: `mypy apps/ services/ --ignore-missing-imports`
- **Test single file**: `pytest tests/test_services.py -v`
- **Test with coverage (matches CI)**: `pytest tests/ -v --tb=short --cov=apps --cov=services --cov-report=term-missing --cov-fail-under=40`
- **Full QA**: `bash scripts/qa.sh`
- **CI order**: ruff → mypy → pytest (coverage ≥40%) → bandit security scan
- Line length: 100 (ruff + black), Python 3.10+, Google docstrings
- `gi.require_version()` calls must precede `from gi.repository import ...` (E402 ignored)
- New D-Bus services go in `services/<name>/`, subclass `ServiceBase` from `services/service_base.py`
- Constants: `from services.constants import DBUS_NAME_BRAIN, AXON_DIR, OLLAMA_BASE_URL`
- Tests require Linux (dbus, GTK) — won't pass on Windows/macOS
- Markers: `@pytest.mark.slow`, `@pytest.mark.integration`, `@pytest.mark.unit`; timeout 30s/test
- Key paths: `services/constants.py`, `services/service_utils.py`, `services/service_base.py`, `docs/architecture.md`
- SQLite `with conn:` only does commit/rollback — NOT close. Use explicit `conn.close()` or pool
- Kernel module at `kernel/axon-winabi/` — Phase 1 & 2 complete (~3,900 lines), NOT compiled/tested yet
- Docker: `docker compose up -d`, Ollama via `host.docker.internal:11434`, each service uses `Dockerfile.service`

#### Axon-OS code map (split modules)

Big modules are split into cohesive files; the original module still re-exports every public name, so existing imports and `tests/` patch points keep working.

- **AI panel** — `apps/axon-ai-panel/ui/`. `panel.py` keeps `AIPanelWindow` (init, model dropdown, key/entry handlers, `_load_status_worker`, `_run_card_command`) and re-exports `MessageBubble`, `_apply_markup`, `open_file`, `OllamaClient`, `tool_sources`, `run_diagnostics`, `confirm_and_exec`. Siblings are imported by bare name (`panel.py` is loaded both as `ui.panel` and by file path in tests, so no relative imports).
  - `panel_compat.py` — `sys.path` bootstrap (intent-bar, `services/`, `apps/shared`); must be imported before `i18n`/`ui_logic`/`axon_gtk_utils`. `OllamaClient` + `tool_sources` with fallback stubs.
  - `panel_bubble.py` — `MessageBubble`, `_apply_markup`, `open_file`
  - `panel_conversations.py` — `ConversationsMixin`: clear/new/switch/rename/archive/delete conversations
  - `panel_memory.py` — `MemoryMixin`: memories popover
  - `panel_context.py` — `ContextMixin`: Axon status section, desktop-context row, cloud-send warning
  - `panel_stream.py` — `StreamMixin`: send, stream worker, chunk/done handling, retry/regenerate
  - `panel_actions.py` — `ActionsMixin`: error bubbles, action cards (open app / diagnostics / settings)
  - Tests patch `panel.run_diagnostics` and `panel.confirm_and_exec`, so the two methods that call them (`_load_status_worker`, `_run_card_command`) stay in `panel.py`. New files with `_()` strings go in `data/locale/POTFILES.in`, then `sh scripts/update-pot.sh` (CI checks `--check`).
- **Brain service** — `services/axon-brain/`. `brain_service.py` keeps `BrainService` (lifecycle `_setup`/`_cleanup`, config load/save, model resolution, `SetModel`, and the methods that emit via `GLib` or read module config) and re-exports the helpers and `CHAT_SYSTEM_PROMPT`. D-Bus methods/signals live in mixins; **a mixin with `@dbus.service.method/signal` must subclass `dbus.service.Object`** (dbus-python only registers them via that metaclass), plain-helper mixins need not. Interface is unchanged: 52 methods+signals.
  - `brain_sanitize.py` — `_sanitize_output/_sanitize_context/_require_http_url`; `brain_tokens.py` — `TokenBuffer`; `brain_ollama.py` — Ollama timeouts, `_is_retryable`, `_ollama_error_message`, `_same_model`; `brain_config.py` — `MODEL_TIERS`
  - `brain_http.py` — `OllamaHttpMixin` (plain): `_http_post/_http_get/_installed_models`, model-ref helpers
  - `brain_gamemode.py` — `GameModeMixin`: Suspend/Resume/AllowCpuOnly, `SuspendedChanged`, request gating
  - `brain_providers.py` — `ProvidersMixin`: status/models/pull, cloud providers, API keys, OAuth sign-in
  - `brain_conversations.py` — `ConversationsMixin`: conversations, memories, `ListTools`
  - `brain_classify.py` — `ClassifyMixin`: `ClassifyWindow*`, `ClassifyIntent`, `GetEmbeddings`
  - `brain_generation.py` — `GenerationMixin`: `Generate`/`SendMessage*`/`CancelStream`, prompt building, completions
  - Tests patch `brain_service.GLib`, `.AIRouter`, `._LOCAL_OPENER`, `.CONFIG_FILE`, `.INSTALLER_CONFIG_FILE`, `._read_installer_model`, so any method reading those stays in `brain_service.py` (`_run_stream`, `_do_agent`, `_fail_stream`, `_do_pull_model`, `_do_sign_in`, `_open_with_retry`, ...). After splitting, diff `BrainService._dbus_class_table` before/after.

### Frictionless OS (Go) — `Documents/Frictionless/`
- Under 100MB cloud server OS, Go static binaries (CGO_ENABLED=0)
- Alpine containers: busybox ash, no bash, no brace expansion
- Build scripts: `#!/bin/sh` (not `#!/bin/bash`)
- Use `make olddefconfig` not `make allyesconfig`
- Docker: `sg docker -c "..."` (docker group needs new login session)
- Output: `Documents/Frictionless/output/`

### Spoofer (Rust) — `Documents/Spoofer/spoofer/`
- System-tray MAC/hostname/IP spoofing app, Rust + eframe 0.34 + egui 0.34
- **Tray icon**: Python subprocess with `AyatanaAppIndicator3` (not `AppIndicator3`), absolute PNG path required
- Theme icon names silently fail for SNI registration — must use absolute path to real PNG
- Trigger file IPC: tray.py writes `"toggle"` to `~/.config/spoofer/toggle`, Rust polls every 200ms
- eframe 0.34: `fn ui(&mut self, ui: &mut egui::Ui, frame: &mut Frame)` replaces deprecated `fn update()`

### Creation Browser — `Documents/Creation Browser/creation-browser/`
- AI-controlled browser with multi-provider AI panel (Tauri v2 + React 18 + Vite 6 + Tailwind 3 + Zustand 5)
- Converting from Tauri desktop app to standalone web app
- AI automation: multi-turn loop (max 15 iterations) with DOM snapshot re-extraction
- Frontend: `npm run dev` (Vite), `npm run build`
- Tauri backend: `src-tauri/` (Rust, Cargo)
- Full app: `cargo tauri dev` / `cargo tauri build`

## Axon OS ISO policy — what goes in the ISO vs. what downloads later

A package goes **into the ISO** only if **at least one** of these is true **and** its license
allows redistribution:

- **A. Boot / install / connect:** needed to boot, install, or get online on supported hardware
  (kernel, firmware, network stack, installer, GPU base drivers for that edition).
- **B. Axon identity:** a core Axon OS component (Brain, Context, Search, Voice, GuiAgent, shell
  extension, Axon apps, Ollama **CPU** runtime, updater, watchdog).
- **C. Headline experience offline:** required for "gaming works out of the box" without internet
  (Steam launcher, Vulkan, 32-bit graphics libs, GameMode, MangoHud, APEX-WIN/Wine).

**Everything else downloads later** (Game Center, welcome wizard, Hardware Profiler, or the app's
own first-run updater). Never in the ISO: AI models; Ollama GPU libraries (CUDA, ROCm); Flatpak
runtimes and Flatpak apps; launchers other than Steam; emulators, streaming/chat/recording/RGB
tools; Proton-GE/Wine-GE, Wine Mono/Gecko; anything needing a EULA click-through; optional media
codecs (a welcome-wizard checkbox); extra language packs.

Every download-later item must: be listed in `data/gaming/catalog.json` with size, source and
category; install without a terminal; be removable from the same UI; have a verified source
(Ubuntu/Axon apt, Flathub, or a pinned URL + SHA-256); fail gracefully offline with a retry action.

ISO size budgets (enforced in CI): Standard target ≤ 3.5 GB, hard limit 3.9 GB; NVIDIA edition
target ≤ 4.3 GB, hard limit 4.7 GB. A PR that adds more than **50 MB** to either ISO must say why
and which rule (A, B or C) justifies it. No `curl | sh` in shipped code: use apt, Flatpak, or a
pinned URL + SHA-256. Verify external identifiers (package names, Flatpak IDs, PPAs) when you
use them and record the date in a comment.

## General Rules

- `if (!function_exists())` guards when same functions in multiple includes
- `error_log()` over `die()` for error handling
- `temp-admin/` = backup copies of admin files (same fixes needed, lower priority)
- Don't modify `.local/share/waydroid/` — Android container, not project code
