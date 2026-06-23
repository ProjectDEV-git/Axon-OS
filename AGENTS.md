# AGENTS.md — Axon OS

## Multi-Agent Structure

This repo supports parallel sub-agents for different concerns:
- **coding**: Write/modify Python, GJS, shell code in `apps/`, `services/`, `shell/`
- **analysis**: Read-only investigation of architecture, dependencies, patterns
- **testing**: Run pytest, lint, typecheck; verify changes don't break CI

Agents should declare their role upfront and scope work accordingly.

## Exact Commands

```bash
# Lint (must pass before commit)
ruff check apps/ services/ tests/ installer/

# Auto-fix lint
ruff check --fix apps/ services/ tests/ installer/

# Format
ruff format apps/ services/ tests/ installer/

# Type check
mypy apps/ services/ --ignore-missing-imports

# Tests (single file)
pytest tests/test_services.py -v

# Tests (with coverage, matches CI)
pytest tests/ -v --tb=short --cov=apps --cov=services --cov-report=term-missing --cov-fail-under=40

# Full QA pipeline (runs everything CI does)
bash scripts/qa.sh

# Pre-commit hooks
pre-commit run --all-files
```

**CI order**: ruff → mypy → pytest (coverage ≥40%) → bandit security scan

## Import Aliasing (Critical)

Service dirs use hyphens (`services/axon-brain/`), but Python imports need underscores.
`tests/conftest.py` registers aliases automatically. In test code, import as:
```python
from services.axon_brain.brain_service import BrainService  # maps to services/axon-brain/
```
Never `from services.axon-brain...` — that's a syntax error.

## Service Development

New D-Bus services go in `services/<name>/`. Use `ServiceBase` from `services/service_base.py`.
Plugin services need a `manifest.toml` with `[service]` and `[systemd]` sections.
Deploy plugins: `python3 services/plugin_deploy.py --install <dir>`

Constants: `from services.constants import DBUS_NAME_BRAIN, AXON_DIR, OLLAMA_BASE_URL`

## Style

- Line length: 100 (ruff + black)
- Python target: 3.10+
- Docstrings: Google convention (ruff D rules)
- `gi.require_version()` calls must precede `from gi.repository import ...` (E402 ignored)
- Long lines tolerated in UI/GTK string-heavy code (E501 ignored)

## Testing Quirks

- Tests require Linux system bindings (dbus, GTK) — won't pass on Windows/macOS
- Coverage threshold: 40% (enforced in CI)
- Markers: `@pytest.mark.slow`, `@pytest.mark.integration`, `@pytest.mark.unit`
- Timeout: 30s per test (via `--timeout=30`)

## Key Paths

- `axon_logger.py` — centralized logging with JSON formatter (`configure_app_logger`)
- `services/constants.py` — all D-Bus names, paths, limits in one place
- `services/service_utils.py` — TTLCache, RateLimiter, cached/rate_limited decorators
- `services/service_base.py` — D-Bus service base class
- `docs/architecture.md` — full architecture doc
- `docs/building.md` — ISO build instructions

## Service Pattern

Subclass `ServiceBase`, define class attrs `BUS_NAME`, `OBJECT_PATH`, `SERVICE_NAME`, implement `_setup()`:
```python
class MyService(ServiceBase):
    BUS_NAME = "org.axonos.MyService"
    OBJECT_PATH = "/org/axonos/MyService"
    SERVICE_NAME = "my-service"

    def _setup(self):
        # service-specific init after D-Bus registration
        pass

if __name__ == "__main__":
    MyService.main()
```

## Shell Extension (GJS)

`shell/axon-shell/` is a GNOME Shell extension written in GJS (GNOME JavaScript).
Key files: `extension.js` (entry), `spaces.js`, `intentbar.js`, `dock.js`, `dbus-helpers.js`.
Metadata in `metadata.json`. Schema in `schemas/`.

## Docker

Containerized services: `docker compose up -d`
Services connect to Ollama via `host.docker.internal:11434`.
Each service uses `Dockerfile.service` and shares `AXON_DIR` volume.
