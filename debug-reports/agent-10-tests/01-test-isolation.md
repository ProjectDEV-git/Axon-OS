# 01 — Test Isolation & Mocking Quality

Generated: 2026-07-02T11:30:00Z

## 1. conftest.py Namespace Aliasing Pattern

**Assessment: GOOD with caveats**

The `conftest.py` uses `types.ModuleType` to register underscore aliases for hyphenated service directories. This is a correct and clever approach to solve the hyphen/underscore import mismatch.

**Issues found:**
- **No `__init__.py` files in service directories**: The namespace package pattern (`_ensure_namespace`) only works if the `services` parent also lacks `__init__.py`. The code handles this by explicitly registering `services` as a namespace. This is fragile because if someone adds an `__init__.py` to `services/`, it would shadow the namespace and break all imports.
- **Missing aliases**: `conftest.py` only registers 6 service packages (axon_brain, axon_context, axon_search, axon_voice, axon_gui_agent, axon_sandbox). Tests that import from modules outside these (e.g., `test_innovations.py` which imports `indexer`, `audit`, `plan`, `install_engine`, `intent_router`, `search_service`) use their own `sys.path.insert()` at module level, which pollutes the path for all other tests.
- **No cleanup of sys.modules**: The conftest never removes registered aliases, which is fine for test isolation within a session but could leak between test sessions if pytest is run multiple times in the same process.

## 2. test_services.py — D-Bus Integration Test

**CRITICAL: Requires a running D-Bus session bus**

`tests/test_services.py` is the most problematic test file:

- `setUpClass()` spawns real D-Bus daemons (`brain_service.py`, `context_service.py`) as subprocesses
- Connects to the **real D-Bus session bus** (`dbus.SessionBus()`)
- Uses `time.sleep(2.0)` to wait for service registration
- `tearDownClass()` calls `terminate()` + `wait()` but **does not kill the process group**
- If the subprocesses fail to start (no D-Bus session, missing dependencies), `setUpClass` will throw, and `tearDownClass` **may not run** (Python unittest limitation — teardown on class setup failure)

**Risks:**
- **Zombie processes**: If `terminate()` fails to kill the daemon (it might have spawned children), zombies will persist
- **Leftover D-Bus names**: The brain/context services will claim D-Bus names. If the test is interrupted, those names remain claimed, causing subsequent test runs to fail with `NameExistsException`
- **Non-portable**: This test ONLY works on a Linux system with a running D-Bus session bus, Ollama configured, etc. It cannot run in CI without special setup
- **File system side effects**: The brain and context services likely create config dirs/files under `~/.config/axon/` during startup

**Recommendation:** Mark this file with `@pytest.mark.integration` or `@pytest.mark.slow` so CI can skip it.

## 3. test_brain_service.py — Pure Unit Tests

**Assessment: GOOD**

- Tests import from `services.axon_brain.brain_service` (uses conftest alias)
- Tests only exercise static/class methods (`_sanitize_output`, `_sanitize_context`, `_validate_model_name`, `_validate_prompt`, `SendMessage` signature inspection)
- **No side effects**: No files created, no D-Bus required, no processes spawned
- Uses no fixtures — fully isolated

## 4. test_services_enhanced.py — Mocked D-Bus Tests

**Assessment: GOOD with minor issues**

- Uses `unittest.TestCase` with `setUpClass`/`tearDownClass`
- Thread safety test for `ConversationStore` is good — uses `tmp_path` fixtures
- **Potential flaky test**: `test_ttl_cache_get_expired` and `test_rate_limiter_window_reset` both call `time.sleep()`. On slow CI machines, timing can drift. The sleep values (1.1s, 1.1s) provide only 100ms margin.
- **Thread safety tests**: The `test_thread_safety` for ConversationStore and the TTLCache/RateLimiter thread safety tests are good practices. However, they use `threading.Thread` directly without checking for race conditions in assertions — if an error occurs in a thread, the assertion might pass before the error propagates.

## 5. test_sandbox.py and test_sandbox_manager_extended.py

**Assessment: GOOD**

- Both files properly mock `dbus.service.BusName`, `dbus.service.Object.__init__`, and `gi.repository.GLib`
- SandboxManager is instantiated via `__new__()` bypassing `__init__`, which is a valid pattern for testing without D-Bus
- `test_sandbox.py` tests fail-closed behavior (missing file, directory, read error, general exception) — all return "deny"
- `test_sandbox_manager_extended.py` tests static fallback analysis with mocked Brain service
- **No file system side effects** due to heavy mocking
- **One concern**: Tests patch `pathlib.Path.exists` globally via `patch("pathlib.Path.exists", ...)`. This affects ALL Path instances, not just the one under test. Could cause subtle test interaction bugs if other code runs between the patch context manager entry and exit.

## 6. test_conversation_store.py — Direct Module Loading

**Assessment: GOOD but fragile import**

- Uses `importlib.util.spec_from_file_location()` to load `conversation_store.py` directly, bypassing the conftest namespace alias
- Creates `tempfile.TemporaryDirectory()` in `setUp()` and cleans up in `tearDown()` — proper isolation
- Tests directory creation, file permissions, connection cleanup, double-close safety
- **Fragility**: The direct import creates a module named `conversation_store` in `sys.modules`. If `test_services_enhanced.py` runs first and registers `services.axon_brain.conversation_store` via conftest, then `test_conversation_store.py` registers a separate `conversation_store` module — these are different objects. This is unlikely to cause problems but is architecturally messy.

## 7. test_plugin_system.py and test_plugin_registry.py

**Assessment: EXCELLENT**

- Both files use `tmp_path` fixtures for plugin directories — full isolation
- No real D-Bus needed — all D-Bus interactions are mocked
- `TestServiceBase` properly patches `dbus.service.BusName`, `dbus.service.Object.__init__`, `dbus.mainloop.glib.DBusGMainLoop`, and `dbus.SessionBus`
- Plugin discovery tests create temporary plugin directories with manifests and entry points
- Tests cover: valid manifests, missing keys, core bus name rejection, missing entry points, dependency ordering, deploy artifact generation
- **No file system side effects** outside of tmp_path

## 8. test_innovations.py — Multi-Module Import Pattern

**Assessment: CONCERNING**

- Manually adds 5 directories to `sys.path` at module load time: `services/axon-search`, `services/axon-voice`, `services/axon-sandbox`, `services/axon-gui-agent`, `apps/axon-installer`
- Imports modules by bare name (`import audit`, `import indexer`, `import plan`, `from install_engine import fstab_lines`, `from intent_router import clean_transcript, parse_intent_response`)
- **Pollutes `sys.path` for all subsequent tests** — any test that runs after this could inadvertently import the wrong module if names collide
- **Mocks dbus/gi conditionally** (lines 234-246): only mocks if not already imported, which is correct to avoid poisoning D-Bus for other tests
- However, `search_service` import at line 248 will permanently bind to whatever dbus mock was set up

## 9. Temporary File and Database Cleanup

| Test File | Cleanup Method | Assessment |
|-----------|---------------|------------|
| test_brain_service.py | None needed (pure functions) | ✅ Clean |
| test_services_enhanced.py | Uses `tmp_path` fixture | ✅ Clean |
| test_sandbox*.py | Uses MagicMock, no real files | ✅ Clean |
| test_conversation_store.py | `TemporaryDirectory` in setUp/tearDown | ✅ Clean |
| test_search_service.py | Uses `tmp_path` fixture | ✅ Clean |
| test_innovations.py | Uses `tmp_path` for file tests | ✅ Clean |
| test_telemetry.py | Patches `TELEMETRY_DIR` to `tmp_path` | ✅ Clean |
| test_plugin_system.py | Uses `tmp_path` | ✅ Clean |
| **test_services.py** | **Spawns real D-Bus processes** | ⚠️ **Risk of zombie processes** |

## 10. External Dependency Mocking Summary

| Dependency | Mocked Properly? | Files |
|-----------|-------------------|-------|
| D-Bus (dbus) | ✅ Yes | sandbox, plugin_system, gui_agent_validation, search_service |
| D-Bus (dbus) | ❌ No (requires real bus) | test_services.py |
| Ollama | ✅ Yes (never called in unit tests) | All pure tests |
| GLib/GTK (gi) | ✅ Yes in sandbox tests | sandbox, sandbox_extended |
| systemd | N/A (not tested) | N/A |
| whisper/voice | ✅ Import-only tests | test_phase4 (constants only) |
| Subprocess (nvidia-smi, lspci) | ✅ Yes | test_hardware_profiler*.py |

## Summary

**Critical Issues:**
1. `test_services.py` requires a live D-Bus session bus and spawns real daemon processes — it is NOT isolated and cannot run in standard CI
2. `test_innovations.py` and `test_hardware_profiler.py` modify `sys.path` at module scope, polluting the import path for all subsequent tests

**Warnings:**
1. `time.sleep()` in TTL/rate-limiter tests creates flaky potential on slow CI
2. `pathlib.Path.exists` global mock in sandbox tests could cause subtle test interaction issues
3. `test_conversation_store.py` and `test_services_enhanced.py` both test `ConversationStore` but load it differently (direct import vs conftest alias), creating duplicate module objects

**Well-Designed Aspects:**
1. conftest.py namespace alias pattern is elegant and correct
2. Plugin system tests use `tmp_path` consistently with excellent isolation
3. Sandbox tests properly mock D-Bus and GLib for fail-closed behavior testing
4. ConversationStore tests verify connection cleanup (FD leak fix)
