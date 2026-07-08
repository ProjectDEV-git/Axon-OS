# SUMMARY — Debug Agent 10: Test Suite & CI/CD Analysis

Generated: 2026-07-02T11:33:00Z
Project: Axon OS (`/home/hxshin/projects/Axon-OS`)

---

## Critical Bugs (4)

### C1: test_services.py Requires Live D-Bus Session Bus
**File**: `tests/test_services.py` lines 21-41
**Impact**: This test file spawns real D-Bus daemon subprocesses and connects to the session bus. It will FAIL on any system without a running D-Bus session (CI servers, containers, headless systems). If the daemons fail to start, zombie processes and claimed D-Bus names will persist.
**Fix**: Mark with `@pytest.mark.integration` and `@pytest.mark.skipif` for environments without D-Bus.

### C2: 2,875+ Lines of Security-Critical Code Have Zero Tests
**Files**: `audit_v2.py` (507 lines), `voice_service.py` (459 lines), `shield.py` (218 lines), `context_service.py` (486 lines), `clipboard_store.py` (180 lines), `file_indexer.py` (196 lines)
**Impact**: The sandbox audit v2 module (507 lines) is the primary security boundary for script execution. A bug here could allow malicious scripts to pass inspection. The shield module is similarly security-critical. Neither has any test coverage.
**Fix**: Write tests for `audit_v2.py` and `shield.py` immediately — they are the highest-risk untested modules.

### C3: qa.sh Missing AGENTS.md CI Steps (Coverage, Typecheck, Security Scan)
**File**: `scripts/qa.sh`
**Impact**: The AGENTS.md documents a 5-step CI pipeline (ruff, mypy, pytest with coverage, bandit, pre-commit) but qa.sh only implements 2 of those 5 (ruff + pytest without coverage). No type checking, no security scanning, and no coverage enforcement.
**Fix**: Add mypy, bandit, coverage flags, and ruff format check to qa.sh.

### C4: qa.sh Suppresses All Error Output
**File**: `scripts/qa.sh` line 21
**Impact**: `if "$@" >/dev/null 2>&1; then` discards all stdout and stderr. When a check fails, developers get only "FAIL" with no diagnostic information. This dramatically increases debugging time.
**Fix**: Capture output to a temp file; show last 20 lines on failure.

---

## Warnings (6)

### W1: sys.path Pollution at Module Scope
**Files**: `test_innovations.py` (5 paths), `test_hardware_profiler.py` (1 path), `test_voice_and_terminal.py` (2 paths)
**Impact**: These files add directories to `sys.path` at import time, affecting all subsequent test imports. Can cause wrong module resolution if test ordering changes.
**Fix**: Move path manipulation into fixtures or conftest.py.

### W2: Flaky Time-Dependent Tests
**Files**: `test_services_enhanced.py` (lines 208, 243)
**Impact**: `test_ttl_cache_get_expired` and `test_rate_limiter_window_reset` use `time.sleep(1.1)` with only 100ms margin. On slow CI machines or under load, these can fail intermittently.
**Fix**: Increase margins to 2x the sleep period, or use `unittest.mock.patch` on `time.monotonic` / `time.time` to simulate time passage.

### W3: No Non-English Locale Files Exist
**Files**: Only `data/locale/en_US.po` exists
**Impact**: The i18n infrastructure is in place but has zero actual translations. The `.po` file only maps English strings to themselves. The i18n system has never been used with a non-English locale.
**Fix**: Create at least one non-English locale (e.g., `th_TH.po` for Thai, given the project is based in Thailand) and add tests for locale switching.

### W4: Plugin Lifecycle Not Tested End-to-End
**File**: `test_plugin_system.py`
**Impact**: Tests verify discovery and validation but entry point files are stubs (`# placeholder`). No test actually loads a ServiceBase subclass, registers it on D-Bus, and calls its methods.
**Fix**: Create a test fixture with a real minimal ServiceBase subclass.

### W5: GlobalSearchService and AdvancedVoiceService Only Import-Tested
**Files**: `test_phase4.py` lines 186-208
**Impact**: `TestGlobalSearch` and `TestAdvancedVoice` only verify that the modules import successfully and have expected constants. No methods are tested.
**Fix**: Write actual method-level tests with mocked dependencies.

### W6: Kernel Module (~3,900 LOC) Has No Automated Tests
**Files**: `kernel/axon-winabi/` — 14 C source files
**Impact**: The kernel module cannot be compiled or tested in CI. The only test files are manual C programs. No static analysis (cppcheck, sparse) is run.
**Fix**: At minimum, add a dry-run Makefile check and cppcheck scan to qa.sh.

---

## Recommendations (Prioritized)

### Priority 1 — Security (Do First)
1. Write tests for `audit_v2.py` — this is the security-critical script audit module with 507 lines and zero tests
2. Write tests for `shield.py` — sandbox protection boundary, 218 lines, zero tests
3. Write tests for `context_service.py` — clipboard handling has security implications (password leakage)

### Priority 2 — CI Pipeline Completeness
4. Fix qa.sh to include all AGENTS.md CI steps: mypy, bandit, ruff format, pytest with coverage
5. Fix qa.sh error output suppression — show diagnostic info on failure
6. Add coverage reporting with `--cov-fail-under=40` threshold

### Priority 3 — Test Quality
7. Mark `test_services.py` as integration test (skip in standard CI)
8. Fix sys.path pollution in test_innovations.py and test_hardware_profiler.py
9. Fix timing-sensitive tests (TTL cache, rate limiter) to use mock time
10. Add tests for `voice_service.py` (459 lines, no tests)

### Priority 4 — Coverage Expansion
11. Add method-level tests for `global_search_service.py` and `advanced_voice_service.py`
12. Add tests for `clipboard_store.py` and `file_indexer.py`
13. Add error-path tests for `brain_service.py` (Ollama failures, timeout)
14. Add i18n completeness tests (check all translatable strings are in .po file)

### Priority 5 — Infrastructure
15. Add kernel module static analysis (cppcheck) to qa.sh
16. Add pytest-randomly to detect test ordering dependencies
17. Create non-English locale files and test locale switching
18. Add pre-commit check guard (skip if not installed instead of failing)

---

## Test Suite Health Score

| Dimension | Score | Notes |
|-----------|-------|-------|
| Unit test isolation | 8/10 | conftest.py alias pattern is excellent; 1 integration test file breaks isolation |
| Mocking quality | 7/10 | D-Bus/GLib mocked well in most files; test_services.py is the exception |
| Coverage breadth | 5/10 | 42% of service files have zero tests; apps mostly untested |
| Coverage depth | 4/10 | Most tested files only cover happy path; error paths and edge cases sparse |
| CI pipeline | 4/10 | qa.sh is missing type checking, security scanning, coverage enforcement |
| Fixture realism | 8/10 | Good realistic data shapes where fixtures exist |
| Flakiness risk | 7/10 | Most tests deterministic; 2-3 timing-dependent tests; 1 D-Bus-dependent file |
| Overall | **5.4/10** | Solid foundation but significant gaps in coverage and CI completeness |
