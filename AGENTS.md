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

## General Rules

- `if (!function_exists())` guards when same functions in multiple includes
- `error_log()` over `die()` for error handling
- `temp-admin/` = backup copies of admin files (same fixes needed, lower priority)
- Don't modify `.local/share/waydroid/` — Android container, not project code
