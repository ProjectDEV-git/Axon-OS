# 03 — Integration Test Reliability

Generated: 2026-07-02T11:30:00Z

## 1. test_innovations.py — Multi-Module Integration Tests

### Service Startup/Shutdown Sequencing
- **Not applicable**: This file tests pure functions extracted from services (audit, indexer, plan, install_engine, intent_router). No services are started or stopped.
- **Import order matters**: The file adds 5 directories to `sys.path` at module scope (lines 13-21), then imports bare module names. This runs at collection time, before any test. If pytest discovers this file after other tests have modified `sys.modules`, it could import stale modules.

### Timing Dependencies
- None — all tests are synchronous pure-function calls.

### Fixture Realism
- `TestChunkText`: Uses realistic text lengths and paragraph structures. Good.
- `TestAudit`: Uses realistic malicious script patterns (SSH theft, curl pipe, rm rf, reverse shell). Good.
- `TestPlanValidation`: Uses realistic GSettings schema names and GNOME operations. Good.
- `TestFstab`: Uses realistic BTRFS and ext4 fstab entries. Good.
- `TestVecTableReady`: Uses in-memory SQLite with proper schema. Good.

### Overall Assessment: **FAIR** — Tests are well-structured but the import pattern is fragile.

## 2. test_phase4.py — Phase 4 Integration Tests

### Service Startup/Shutdown Sequencing
- **AIRouter tests**: Instantiate `AIRouter` directly — no D-Bus needed. Good isolation.
- **Telemetry tests**: Use `tmp_path` with patched `TELEMETRY_DIR`. Clean isolation.
- **ModelMarketplace tests**: Only test catalog structure and search — no Ollama calls.
- **GlobalSearchService test**: Import-only (`test_import`). No actual method testing.
- **AdvancedVoice test**: Import-only + constants check. No actual method testing.

### Timing Dependencies
- **None** — all tests are synchronous.

### Fixture Realism
- `TestAIRouter`: Tests use realistic prompts ("Explain how Linux kernel scheduling works", "Write a Python function to parse CSV files"). Good.
- `TestTelemetry`: Uses realistic event names ("app_launch", "test_event"). Good.
- `TestModelMarketplace`: Validates catalog structure against expected keys. Good.

### Flaky Test Risks
- **None identified** — no timing dependencies, no external calls.

### Overall Assessment: **GOOD** but shallow — several modules only have import/constant tests.

## 3. test_plugin_system.py — Plugin Lifecycle Tests

### Service Startup/Shutdown Sequencing
- **ServiceBase tests** mock all D-Bus initialization. The service is never actually started on the bus.
- **Missing lifecycle tests**:
  - ❌ Plugin loading (actual `import` of the entry point module)
  - ❌ Plugin starting/stopping
  - ❌ Plugin failure recovery
  - ❌ Plugin hot-reload
  - ❌ Plugin dependency ordering at runtime (topo_sort is tested but not actual dependency resolution during startup)

### Fixture Realism
- Plugin directories are created with realistic structure (manifest.toml + entry point .py file).
- TOML manifests include realistic fields: name, bus_name, object_path, entry_point, dependencies, systemd config.
- **Gap**: Entry point files are just `# placeholder\n` — no actual ServiceBase subclass code. This means the tests verify discovery and validation but NOT that a real plugin can be loaded and executed.

### Overall Assessment: **GOOD** for discovery/validation, but **INCOMPLETE** for full lifecycle.

## 4. test_conversation_store.py — Database Integration

### Service Startup/Shutdown Sequencing
- Uses `tempfile.TemporaryDirectory()` — proper isolation.
- Tests `init`, `close_connection`, `double-close`, `new_connection_after_close`.

### Fixture Realism
- Database path includes intermediate directory creation (`test_dir/test_conversations.db`) — tests that the store creates parent directories. Realistic.
- File permissions test (`0o600`) — realistic security requirement.

### Flaky Test Risks
- **None** — SQLite operations are synchronous and deterministic.

### Overall Assessment: **GOOD** but narrow — only tests connection management, not the full CRUD that test_services_enhanced.py covers.

## 5. test_telemetry.py — Telemetry Integration

### Service Startup/Shutdown Sequencing
- All tests patch `TELEMETRY_DIR` to `tmp_path` — proper isolation.
- Singleton pattern test (`get_telemetry`) patches `_instance` to None — correct.

### Fixture Realism
- Event data shapes match production: `{"event": "app_launch", "data": {"app": "files"}}`.
- Crash data shapes: `{"service": "axon-brain", "error": "ConnectionError", "traceback": "..."}`.
- Daily aggregation test creates multiple events and verifies counts. Realistic.

### Flaky Test Risks
- **None** — all operations are file writes and JSON parsing.

### Overall Assessment: **GOOD** — well-structured with realistic data shapes.

## 6. scripts/qa.sh — QA Pipeline Analysis

### What It Does
```
1. Ruff lint (apps/, services/, tests/, installer/)
2. Python syntax check (3 files)
3. ShellCheck (install.sh)
4. JSON validation (metadata.json)
5. Pre-commit hooks
6. Pytest (all tests, 30s timeout)
```

### Issues Found

#### ISSUE 1: Incomplete Python Syntax Checking
```bash
run "Python syntax"    python3 -m py_compile services/service_base.py
run "Python syntax"    python3 -m py_compile services/plugin_registry.py
run "Python syntax"    python3 -m py_compile services/plugin_deploy.py
```
Only 3 files are syntax-checked. There are 31 Python files in `services/` alone. All apps files are also skipped.

**Fix**: Use `python3 -m py_compile` on all `.py` files, or rely on `ruff` to catch syntax errors (which it does).

#### ISSUE 2: No Coverage Reporting
The QA script runs pytest but does NOT collect coverage data. AGENTS.md mentions coverage thresholds (`--cov-fail-under=40`) but qa.sh does not use them.

**Fix**: Add `--cov=apps --cov=services --cov-report=term-missing --cov-fail-under=40` to the pytest command.

#### ISSUE 3: No Type Checking
The AGENTS.md mentions `mypy apps/ services/ --ignore-missing-imports` as part of CI, but qa.sh does NOT run mypy.

**Fix**: Add `run "Mypy typecheck" mypy apps/ services/ --ignore-missing-imports` to qa.sh.

#### ISSUE 4: No Bandit Security Scan
The AGENTS.md mentions `bandit security scan` as part of CI, but qa.sh does NOT run bandit.

**Fix**: Add `run "Bandit security" bandit -r services/ apps/ -f json` to qa.sh.

#### ISSUE 5: No C Code Checks for Kernel Module
The kernel module has ~3,900 lines of C code with no compilation check, no static analysis, and no test execution in CI.

**Fix**: Add `run "Kernel Makefile check" make -C kernel/axon-winabi/ -n` (dry-run) to verify the build would parse correctly, even if it can't compile in CI.

#### ISSUE 6: Error Suppression
```bash
if "$@" >/dev/null 2>&1; then
```
All stdout and stderr are suppressed. When a check fails, there is no diagnostic output. Developers must re-run commands manually to debug.

**Fix**: Capture output to a temp file and display it on failure, or at least show the last few lines.

#### ISSUE 7: Missing `set -e` Behavior for Individual Commands
The `run()` function swallows failures and counts them, but `set -euo pipefail` is at the top. Since `run()` captures the exit code with `if`, this is actually fine — `set -e` doesn't trigger inside `if` conditions. However, if any command outside `run()` fails, the script exits immediately without cleanup.

#### ISSUE 8: Pre-commit May Not Be Installed
```bash
run "Pre-commit hooks" pre-commit run --all-files
```
If `pre-commit` is not installed, this will fail and count as a failed check, but the error message won't be visible (due to Issue 6).

#### ISSUE 9: JSON Validation Hardcodes a Specific File
```bash
run "JSON validation"  python3 -c "import json; json.load(open('shell/axon-shell/metadata.json'))"
```
Only validates ONE JSON file. There may be other JSON files (package.json, config files, etc.) that are not checked.

#### ISSUE 10: No Git Clean Check
The QA script does not verify that the working directory is clean before running. If there are uncommitted changes, the test results may not match what would be deployed.

### Execution Order Assessment
The order is logical:
1. **Lint first** (fast feedback, catches style issues)
2. **Syntax check** (catches parse errors)
3. **ShellCheck** (catches shell issues)
4. **JSON validation** (catches config issues)
5. **Pre-commit hooks** (catches committed-file issues)
6. **Pytest last** (most expensive, catches runtime issues)

This ordering is correct — fail fast on cheap checks before running expensive tests.

### Missing from qa.sh vs AGENTS.md

| AGENTS.md CI Step | In qa.sh? | Priority |
|-------------------|-----------|----------|
| ruff check | ✅ Yes | - |
| ruff format | ❌ **Missing** | HIGH |
| mypy typecheck | ❌ **Missing** | MEDIUM |
| pytest (coverage) | ⚠️ Yes but no coverage flags | HIGH |
| bandit security scan | ❌ **Missing** | MEDIUM |
| pre-commit | ✅ Yes | - |

## 7. Test Fixture Realism Summary

| Test Area | Fixture Quality | Notes |
|-----------|----------------|-------|
| AI Router | ✅ Excellent | Realistic prompts, model names |
| Sandbox | ✅ Good | Realistic script contents (malicious and clean) |
| Conversation Store | ✅ Good | Realistic message shapes |
| Plugin System | ⚠️ Fair | Manifests are realistic, but entry points are stubs |
| Hardware Profiler | ✅ Good | Mocked but realistic GPU/RAM data |
| Settings Executor | ✅ Good | Realistic gsettings keys and values |
| Installer | ✅ Good | Realistic disk configs |
| Telemetry | ✅ Good | Realistic event/crash data |
| Search Service | ✅ Good | Realistic FTS5 schema and queries |
| Voice Service | ❌ N/A | No tests |

## 8. Flaky Test Analysis

| Test | Flaky Risk | Reason |
|------|-----------|--------|
| `test_ttl_cache_get_expired` | **LOW-MED** | Uses `time.sleep(1.1)` with 100ms margin |
| `test_rate_limiter_window_reset` | **LOW-MED** | Uses `time.sleep(1.1)` with 100ms margin |
| `test_thread_safety` | **LOW** | Thread scheduling non-deterministic, but test only checks error count |
| `test_ttl_cache_thread_safety` | **LOW** | Same as above |
| `test_services.py` (all) | **HIGH** | Depends on D-Bus session, subprocess timing, sleep(2.0) |
| All others | **NONE** | Deterministic pure functions or mocked |

## 9. Recommendations for Integration Test Reliability

### High Priority
1. **Mark `test_services.py` as `@pytest.mark.integration`** — it should not run in standard CI
2. **Add `--cov` flags to qa.sh pytest command** — match AGENTS.md specification
3. **Add `ruff format --check` to qa.sh** — formatting consistency is not checked
4. **Add mypy and bandit to qa.sh** — match AGENTS.md CI specification

### Medium Priority
5. **Create real integration test fixtures** for plugin loading (actual ServiceBase subclass in temp dir)
6. **Increase time.sleep margins** in TTL/rate-limiter tests or use mock time
7. **Add timeout markers** to tests that could hang (voice, search rescan)
8. **Fix error output suppression** in qa.sh

### Low Priority
9. **Add pytest-randomly** to detect test ordering dependencies
10. **Add coverage reporting** to qa.sh with term-missing output
11. **Add kernel module build check** (dry-run Makefile) to qa.sh
12. **Create non-English locale files** to test i18n end-to-end
