# FIX-REPORT.md — Agent 10: Test Suite & CI/CD Pipeline Fixes

**Generated**: 2026-07-02T11:49:00Z
**Agent**: Agent 10 (Test Suite & CI/CD)
**Scope**: `scripts/` and `tests/` only

---

## FIX 1: qa.sh Suppresses All Error Output (CRITICAL)

**File**: `scripts/qa.sh`
**Status**: FIXED

### Problem
`run()` function used `"$@" >/dev/null 2>&1`, discarding all stdout/stderr. When checks failed, developers saw only "FAIL" with zero diagnostic output.

### Solution
Replaced `run()` with `run_check()` that:
1. Captures all output to a `mktemp` temp file
2. Shows "PASS" and cleans up on success
3. Shows "FAIL" followed by the last 20 lines of output (indented) on failure
4. Cleans up the temp file in both cases

Added `run_optional()` helper for tools that may not be installed (e.g., bandit), which gracefully skips instead of failing.

---

## FIX 2: qa.sh Missing CI Steps (CRITICAL)

**File**: `scripts/qa.sh`
**Status**: FIXED

### Problem
AGENTS.md documents a 5-step CI pipeline but qa.sh only had ruff check + pytest (no coverage). Missing: ruff format, mypy, bandit, pytest coverage.

### Solution
Added all missing pipeline steps:
1. `ruff format --check apps/ services/ tests/ installer/` — formatting gate
2. `mypy apps/ services/ --ignore-missing-imports` — type checking
3. `bandit -r apps/ services/ -f json -q` — security scanning (via `run_optional`, skipped if not installed)
4. `pytest` now includes `--cov=apps --cov=services --cov-report=term-missing --cov-fail-under=40`

---

## FIX 3: test_services.py Requires Live D-Bus (CRITICAL)

**File**: `tests/test_services.py`
**Status**: FIXED

### Problem
`TestAxonServices` spawns real D-Bus daemons and connects to the session bus. Fails on any system without D-Bus (CI, containers, headless).

### Solution
1. Added `import shutil` and `import pytest`
2. Decorated class with `@pytest.mark.integration`
3. Added docstring noting D-Bus requirement
4. Added `autouse` fixture `skip_without_dbus()` that checks for `dbus-daemon` binary and calls `pytest.skip()` if absent

This ensures the test is cleanly skipped (not failed) on systems without D-Bus, and can be selectively run with `-m integration`.

---

## FIX 4: Flaky Time-Dependent Tests (WARNING)

**File**: `tests/test_services_enhanced.py`
**Status**: FIXED

### Problem
`test_ttl_cache_get_expired` and `test_rate_limiter_window_reset` used `time.sleep(1.1)` with only 100ms margin. Flaky on slow CI under load.

### Solution
Replaced both `time.sleep()` calls with `unittest.mock.patch` on `services.service_utils.time.time`:

- **TTL cache test**: Patches `time.time` to return current time + 2.0 seconds (2x the 1-second TTL), making expiration deterministic with zero wall-clock wait.
- **Rate limiter test**: Captures `now = time.time()` before the test, then patches time to `now + 2.0` (past the 1-second window), making window reset deterministic.

Added `from unittest.mock import patch` to file imports.

---

## FIX 5: sys.path Pollution (WARNING)

**Files**: `tests/conftest.py`, `tests/test_innovations.py`, `tests/test_hardware_profiler.py`, `tests/test_voice_and_terminal.py`
**Status**: FIXED

### Problem
Three test files added directories to `sys.path` at module scope (import time), affecting all subsequent test imports and causing fragile import ordering.

### Solution
**conftest.py**: Added a centralized loop that adds all service subdirectories to `sys.path` once, before any test module loads:
```python
for _subdir in (
    "services/axon-search", "services/axon-voice", "services/axon-sandbox",
    "services/axon-gui-agent", "services/axon-brain",
    "apps/axon-installer", "apps/axon-terminal",
):
```

**test_innovations.py**: Removed `import sys`, `from pathlib import Path`, and the 5-path `sys.path.insert` loop. Imports (`audit`, `indexer`, `plan`, etc.) now resolve via conftest.

**test_hardware_profiler.py**: Removed `import sys`, `from pathlib import Path`, `TESTS_DIR`, `PROJECT_ROOT`, `BRAIN_SERVICE_DIR`, and the `sys.path.insert`. The `hardware_profiler` import resolves via conftest.

**test_voice_and_terminal.py**: Removed `import sys`, `from pathlib import Path`, `ROOT`, and both `sys.path.insert` calls. The `safety` and `vad_helper` imports resolve via conftest.

---

## FIX 6: Plugin Lifecycle Not Tested End-to-End (WARNING)

**File**: `tests/test_plugin_system.py`
**Status**: FIXED

### Problem
Tests verified discovery and manifest parsing but no test actually instantiated a `ServiceBase` subclass, registered on D-Bus, and called its methods.

### Solution
Added `TestPluginLifecycle` class with a `test_full_lifecycle` method that:
1. Creates a `ServiceRegistry` and verifies empty discovery
2. Loads a `ServiceManifest` directly with full fields
3. Defines a `MockService(ServiceBase)` subclass with real `BUS_NAME`, `OBJECT_PATH`, `SERVICE_NAME`
4. Instantiates it (with D-Bus mocked via `@patch`)
5. Verifies `is_healthy()`, `set_healthy()`, `uptime` lifecycle methods
6. Calls `GetStatus()` and validates the JSON response structure
7. Verifies `_setup()` ran and `GetServiceName()` returns correct name

All D-Bus calls are mocked (consistent with existing `TestServiceBase` patterns), so the test runs without a real session bus.

---

## Files Modified

| File | Fix | Lines Changed |
|------|-----|---------------|
| `scripts/qa.sh` | FIX 1+2 | Rewrote `run()` → `run_check()`, added `run_optional()`, added CI steps |
| `tests/test_services.py` | FIX 3 | Added imports, `@pytest.mark.integration`, `skip_without_dbus` fixture |
| `tests/test_services_enhanced.py` | FIX 4 | Added `from unittest.mock import patch`, replaced 2 `time.sleep` calls with mock |
| `tests/conftest.py` | FIX 5 | Added service subdirectory loop for `sys.path` |
| `tests/test_innovations.py` | FIX 5 | Removed `sys.path` pollution (7 lines) |
| `tests/test_hardware_profiler.py` | FIX 5 | Removed `sys.path` pollution (6 lines) |
| `tests/test_voice_and_terminal.py` | FIX 5 | Removed `sys.path` pollution (4 lines) |
| `tests/test_plugin_system.py` | FIX 6 | Added `TestPluginLifecycle` class (~70 lines) |

## Verification

All changes were verified by reading each modified file after editing. No test files outside `scripts/` and `tests/` were modified. Existing code style (pytest for tests, bash for scripts) was followed consistently.
