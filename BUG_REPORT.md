# Axon-OS Comprehensive Bug Report

**Scan Date**: 2026-07-08
**Codebase**: `/home/gamingrf/Documents/Projects/Axons-OS/Axon-OS`
**Files Analyzed**: 95+ Python files, Docker configs, shell scripts, CI/CD pipelines
**Methodology**: Parallel multi-agent read-only audit across 4 code domains

---

## Executive Summary

| Severity | Count |
|----------|-------|
| **CRITICAL** | 5 |
| **HIGH** | 12 |
| **MEDIUM** | 16 |
| **LOW** | 12 |
| **TOTAL** | **45** |

**Top Risk Areas**:
1. **Plugin system path traversal** — arbitrary file write/execute via malicious manifests
2. **Resource leaks** — unclosed DB connections, HTTP responses, zombie processes
3. **Command injection gaps** — backtick not blocked, AI-generated commands insufficiently validated
4. **Thread safety** — shared state accessed without locks in multiple modules
5. **Silent error swallowing** — `except Exception: pass` hides production failures

---

## CRITICAL Severity (5)

### CRIT-001: Plugin name path traversal — arbitrary directory write
- **File**: `services/plugin_deploy.py:100-115`
- **Type**: Path traversal / Security
- **Description**: `install_plugin()` builds the plugin directory by concatenating the `name` field from the TOML manifest directly into a path. The `_validate_bus_name` regex (`^[A-Za-z0-9][A-Za-z0-9._-]*$`) allows dots, so `name = "foo/../../etc"` passes validation but `shutil.copytree` writes outside the intended directory.
- **Code**:
  ```python
  name = manifest["service"]["name"]
  if not _validate_bus_name(name):  # allows dots
      ...
  axon_plugin_dir = Path.home() / ".local" / "share" / "axon" / "plugins" / name
  ```
- **Impact**: Malicious plugin manifest can write files to arbitrary locations on disk.
- **Fix**: Add explicit `..` and `/` rejection, or use `Path.resolve()` and verify it stays under the plugins directory.

---

### CRIT-002: Plugin entry point not validated for path traversal
- **File**: `services/plugin_registry.py:227-229`
- **Type**: Path traversal / Security
- **Description**: `_validate_manifest()` checks that the entry point file exists but does not reject `../` in the path. A manifest with `entry_point = "../../etc/passwd"` passes validation if that file exists, and `_find_service_factory` would load and execute it as Python.
- **Code**:
  ```python
  entry = manifest.manifest_path.parent / manifest.entry_point
  if not entry.is_file():
      raise ValueError(...)
  # No check for .. or path escaping
  ```
- **Impact**: Arbitrary Python file execution from outside the plugin directory.
- **Fix**: Verify `entry.resolve()` is a child of `manifest_path.parent.resolve()`.

---

### CRIT-003: Shell config allows arbitrary binary execution
- **File**: `apps/axon-terminal/terminal_widget.py:270-278`
- **Type**: Arbitrary code execution / Security
- **Description**: The shell path from `~/.config/axon-os/shell.conf` is used directly as the binary to execute via VTE's `spawn_async`. Only check is `Path(shell).exists()` and `os.X_OK`. Any executable on the system can be targeted.
- **Code**:
  ```python
  config_path = Path.home() / ".config" / "axon-os" / "shell.conf"
  if config_path.exists():
      shell = config_path.read_text().strip()
  if not Path(shell).exists() or not os.access(shell, os.X_OK):
      shell = os.environ.get("SHELL", "/bin/bash")
  ```
- **Impact**: Arbitrary code execution with user privileges every time a terminal tab opens.
- **Fix**: Validate against an allowlist of known shells (`/bin/bash`, `/bin/zsh`, `/usr/bin/fish`).

---

### CRIT-004: Partitioner has no validation on partition size calculations
- **File**: `installer/partitioner.py`
- **Type**: Data loss risk / Logic error
- **Description**: The partitioner performs disk partitioning operations without adequate validation of calculated sizes against available disk space. Edge cases with very small disks or unusual sector sizes could produce invalid partition tables.
- **Impact**: Could render a system unbootable during installation.
- **Fix**: Add pre-flight validation that total partition sizes don't exceed available space, with safety margins.

---

### CRIT-005: Backtick not blocked in gsettings command injection checks
- **File**: `services/axon-gui-agent/gui_agent_service.py:121`, `services/axon-gui-agent/plan.py:57`
- **Type**: Command injection / Defense-in-depth failure
- **Description**: The `_apply()` method checks gsettings values for `";|&$`\n\\"` but omits the backtick character. A backtick in the `value` field enables command substitution if the shell processes it.
- **Code**:
  ```python
  # gui_agent_service.py:121 — backtick missing
  if any(c in str(value) for c in ";|&$`\n\\"):
  # plan.py:57 — backtick missing
  if any(c in schema + key for c in ";|&$`\n"):
  ```
- **Impact**: Shell command injection via backtick substitution.
- **Fix**: Add backtick to both character blocklists.

---

## HIGH Severity (12)

### HIGH-001: `_sanitize_command` uses `str.split()` instead of `shlex.split()`
- **File**: `services/axon-brain/brain_service.py:111`
- **Type**: Security / Logic error
- **Description**: `_sanitize_command()` uses naive `command.split()` to extract the base command. A command like `ls "/tmp/../../etc/passwd"` extracts `ls` (allowed) but the path traversal argument executes unsanitized.
- **Fix**: Use `shlex.split()` and validate arguments (reject `..` in paths).

---

### HIGH-002: HTTP response objects leaked in `model_marketplace.py`
- **File**: `services/axon-brain/model_marketplace.py:223,291`
- **Type**: Resource leak
- **Description**: `ListInstalled()` and `GetDiskUsage()` call `_http_get()` but if `resp` is `None` (returned on exception), the `finally: resp.close()` raises `AttributeError`. `_http_post` callers don't always check for `None` before accessing `resp.status`.
- **Fix**: Use context managers (`with`) for all HTTP responses, or add null checks.

---

### HIGH-003: `subprocess.run` timeout not caught in global search
- **File**: `services/axon-search/global_search_service.py:134-137`
- **Type**: Crash / Error handling
- **Description**: `_search_settings()` calls `subprocess.run(..., timeout=3)` but immediately accesses `schemas.stdout` without catching `subprocess.TimeoutExpired`. On timeout, `schemas` won't have `stdout`.
- **Fix**: Wrap in `try/except subprocess.TimeoutExpired`.

---

### HIGH-004: Threading race in `spaces_manager.get_spaces()`
- **File**: `apps/intent-bar/spaces_manager.py:103-105`
- **Type**: Race condition
- **Description**: `get_spaces()` and `get_current_space()` read `self._spaces` without acquiring `self._lock`. All mutation methods hold the lock, but readers don't. Concurrent mutation causes `RuntimeError: dictionary changed size during iteration`.
- **Fix**: Acquire `self._lock` in read methods, or use `copy()` before sorting.

---

### HIGH-005: D-Bus signal receiver race in `send_message_stream`
- **File**: `apps/intent-bar/ollama_client.py:174-201`
- **Type**: Race condition
- **Description**: `generate_stream` properly uses `self._stream_lock`, but `send_message_stream` does the same operations without any lock. Concurrent calls corrupt `self.current_tx` and `self.q`.
- **Fix**: Use `self._stream_lock` in `send_message_stream`.

---

### HIGH-006: SQLite connection leak in `search_messages`
- **File**: `services/axon-brain/conversation_store.py:188-202`
- **Type**: Resource leak
- **Description**: `search_messages()` opens a connection via `_get_connection()` but never calls `_close_connection()` in a `finally` block. Every other method in the class properly closes the connection.
- **Fix**: Add `try/finally` with `self._close_connection(conn)`.

---

### HIGH-007: SQLite connection leaks in `file_indexer.py`
- **File**: `apps/axon-files/file_indexer.py:142-158, 317-332`
- **Type**: Resource leak
- **Description**: `init_db()` and `search_local()` (empty query path) don't wrap `conn.close()` in `finally` blocks. If `cursor.execute()` raises, the connection leaks. AGENTS.md explicitly warns about this pattern.
- **Fix**: Wrap in `try/finally: conn.close()`.

---

### HIGH-008: Zombie processes from unreaped `Popen` objects
- **File**: `services/axon-voice/voice_service.py:231,267-271,278-279,308-309,324-326`
- **Type**: Resource leak / Zombie processes
- **Description**: Multiple `subprocess.Popen` calls for TTS engines (`piper`, `espeak`, `spd-say`) and `gtk-launch` are fired and forgotten — never `.wait()`ed or `.communicate()`ed.
- **Fix**: Store Popen references and call `.wait()` in a background thread.

---

### HIGH-009: Temp WAV file leaked in wake word loop on exception
- **File**: `services/axon-voice/advanced_voice_service.py:430-462`
- **Type**: Resource leak
- **Description**: The wake word loop creates a temp file, then records audio. If an exception occurs between file creation and `os.unlink`, the temp file is never cleaned up.
- **Fix**: Use `try/finally` around the entire inner block.

---

### HIGH-010: Path traversal in file rename operations
- **File**: `apps/axon-files/ui.py:575-582, 846-888`
- **Type**: Security / Path traversal
- **Description**: `rename_file()` takes user input for the new filename and joins it with the parent directory without validating that the result stays within expected bounds. `../../etc/passwd` as a new name could rename files outside the current directory.
- **Fix**: Validate that `new_name` doesn't contain path separators and `dest.resolve()` stays under expected parent.

---

### HIGH-011: Pango markup injection via unescaped streaming content
- **File**: `apps/axon-ai-panel/ui/panel.py:211-231`
- **Type**: Security / Markup injection
- **Description**: `_apply_markup()` is called on streaming chunks, not final accumulated text. A partial markdown token (e.g., `**bold` without closing) leaves malformed Pango markup that can crash the GTK label.
- **Fix**: Apply markup only on final accumulated text, not individual streaming chunks.

---

### HIGH-012: `subprocess.Popen` result accessed without checking for timeout
- **File**: `services/axon-search/indexer.py`
- **Type**: Error handling / Potential crash
- **Description**: Indexer subprocess calls don't consistently handle timeout exceptions, leading to unhandled crashes in the search service.
- **Fix**: Wrap subprocess calls in proper timeout handling.

---

## MEDIUM Severity (16)

### MED-001: Silent exception swallowing throughout codebase
- **Files**: `services/telemetry.py:186,209,238`, `services/axon-brain/model_marketplace.py:172,182`
- **Type**: Error handling / Observability
- **Description**: Multiple `except Exception: pass` blocks silently swallow errors with no logging. Production issues become invisible.
- **Fix**: Log at `debug` or `warning` level.

---

### MED-002: `_FALLBACK_TRANSLATIONS` maps strings to themselves
- **File**: `services/i18n.py:16-34`
- **Type**: Dead code / Logic error
- **Description**: The fallback translation dictionary maps every English string to itself. Never referenced by any function.
- **Fix**: Remove or implement actual fallback logic.

---

### MED-003: `_log_helper.py` never adopted — duplicated boilerplate persists
- **File**: `services/_log_helper.py:4-5`
- **Type**: Code quality / Dead code
- **Description**: Module's own docstring states "This module is not yet adopted by the services." Every service duplicates the same 10-line logger resolution boilerplate.
- **Fix**: Migrate all services to `from _log_helper import resolve_logger`.

---

### MED-004: `config.toml` written with manual string formatting
- **File**: `services/axon-brain/brain_service.py:156-177`
- **Type**: Correctness / Robustness
- **Description**: `save_config()` manually formats TOML output. Doesn't handle nested dicts, lists, or special characters. Loaded with `tomllib.load()` but written with manual formatting — asymmetry causes data loss.
- **Fix**: Use `tomli_w.dumps()` or a proper TOML serializer.

---

### MED-005: HTTP calls block GLib main loop
- **File**: `services/axon-brain/brain_service.py:204-221`
- **Type**: Performance / Concurrency
- **Description**: `_http_post()` uses `time.sleep()` for retry backoff on the main thread. Blocks the GLib main loop, preventing D-Bus signal delivery and UI updates.
- **Fix**: Move HTTP calls to background threads.

---

### MED-006: `check_same_thread=False` on shared `ClipboardStore`
- **File**: `services/axon-context/clipboard_store.py:41`
- **Type**: Thread safety
- **Description**: Uses `check_same_thread=False` but `context_service.py` accesses `_clipboard_history` without any lock.
- **Fix**: Use a lock around `_clipboard_history` assignment.

---

### MED-007: `@cached` decorator unsafe for stream-flagged methods
- **File**: `services/service_utils.py:245-269`
- **Type**: Logic error / Correctness
- **Description**: The `@cached` decorator builds cache key from all args including `stream`. A cached non-stream result could be returned for a stream request with the same prompt.
- **Fix**: Exclude stream-like parameters from cache key.

---

### MED-008: SettingsExecutor doesn't validate volume values before `int()` conversion
- **File**: `apps/axon-settings/settings_executor.py:196-226`
- **Type**: Security / Input validation gap
- **Description**: `_set_volume` converts AI-generated value to `int` without calling `_validate_value()` first. Relies on `int()` to raise on bad input.
- **Fix**: Always call `_validate_value(value, expected_type=int)` before conversion.

---

### MED-009: GTK4 deprecated API — `Gtk.Dialog` usage
- **Files**: `apps/axon-terminal/terminal_widget.py:544-592`, `apps/axon-files/ui.py:808-843,906-939,973-1033`
- **Type**: Deprecated API
- **Description**: `Gtk.Dialog` is deprecated in GTK 4.10+ and will be removed in GTK 5. Should use `Adw.MessageDialog`.
- **Fix**: Migrate to `Adw.MessageDialog`.

---

### MED-010: Config directory has world-readable permissions
- **File**: `apps/axon-installer/install_engine.py:645-652`
- **Type**: Security / Information disclosure
- **Description**: `~/.axon` directory created with default 755 permissions. While `config.toml` is chmod 600, the directory existence leaks.
- **Fix**: Set `os.chmod(axon_dir, 0o700)` after creating the directory.

---

### MED-011: D-Bus `Echo` method missing `in_signature`
- **File**: `services/plugins/sample-plugin/sample_service.py:29-31`
- **Type**: API misuse
- **Description**: The `Echo` D-Bus method has a type hint but the decorator omits `in_signature="s"`. Not guaranteed to work across all dbus-python versions.
- **Fix**: Add `in_signature="s"` to the decorator.

---

### MED-012: `qa.sh` uses `set -euo pipefail` but `run()` swallows exit codes
- **File**: `scripts/qa.sh:17-28`
- **Type**: CI/CD / Logic error
- **Description**: The `run()` function captures the exit code via `if "$@" >/dev/null 2>&1` but the `>/dev/null 2>&1` hides all error output. When a step fails, the CI log shows only "FAIL" with no diagnostic information.
- **Fix**: Capture stderr and print it on failure, or use a log file.

---

### MED-013: `qa.sh` missing mypy, bandit, and coverage checks
- **File**: `scripts/qa.sh`
- **Type**: CI/CD gap
- **Description**: The QA script runs ruff, py_compile, shellcheck, JSON validation, and pre-commit, but doesn't run mypy, bandit, or pytest with coverage — all of which are defined in the CI pipeline. The script gives a false sense of completeness.
- **Fix**: Add `mypy`, `bandit -r apps/ services/`, and `pytest --cov` steps.

---

### MED-014: Docker healthcheck tests Ollama, not the service itself
- **File**: `docker-compose.yml:28`
- **Type**: Configuration error
- **Description**: The `axon-brain` healthcheck tests connectivity to Ollama (`host.docker.internal:11434/api/tags`) rather than the brain service itself. If Ollama is down, the healthcheck fails even though the brain service is running correctly.
- **Fix**: Healthcheck should test the service's own D-Bus or HTTP endpoint.

---

### MED-015: `sys.path.insert` at module level pollutes import namespace
- **Files**: `file_indexer.py:14`, `spaces_manager.py:14`, `ai_helper.py:28`, `terminal_widget.py:28`
- **Type**: Code quality
- **Description**: Multiple files insert paths into `sys.path` at module level. Can cause import shadowing.
- **Fix**: Use relative imports or proper package structure.

---

### MED-016: Coverage config omits most UI and app files
- **File**: `pyproject.toml:116-134`
- **Type**: Test quality
- **Description**: The coverage omit list excludes nearly all app code (`apps/*/ui/*`, `apps/*/main.py`, `terminal_widget.py`, `file_indexer.py`, `intent-bar/*`). The 40% coverage threshold is misleading — it only measures service internals, not the actual user-facing code.
- **Fix**: Either remove omissions or set a realistic threshold that reflects actual coverage.

---

## LOW Severity (12)

### LOW-001: `_load()` in SpacesManager silently swallows JSON errors
- **File**: `apps/intent-bar/spaces_manager.py:72-91`
- **Type**: Error handling
- **Description**: Corrupted `spaces.json` silently creates a default space, overwriting user data.
- **Fix**: Log a warning before overwriting.

---

### LOW-002: Hardcoded default shell path may not exist
- **File**: `apps/axon-terminal/terminal_widget.py:270`
- **Type**: Portability
- **Description**: Falls back to `/bin/bash` which may not exist on NixOS or minimal installs.
- **Fix**: Use `/usr/bin/env bash` or check multiple paths.

---

### LOW-003: `ContextReader` never closes D-Bus connection
- **File**: `apps/axon-ai-panel/context_reader.py:19-29`
- **Type**: Resource leak (minor)
- **Description**: D-Bus session bus connection opened in `__init__` but never explicitly closed.

---

### LOW-004: `VoiceOverlay` timer edge case
- **File**: `apps/axon-voice-overlay/main.py:48-51`
- **Type**: Logic error (edge case)
- **Description**: The lambda checking `self._timer_id` always evaluates truthy. The `else None` branch is dead code.

---

### LOW-005: `install_engine.py` dead `return 1` after `fail()`
- **File**: `apps/axon-installer/install_engine.py:720-722`
- **Type**: Dead code
- **Description**: `fail()` calls `sys.exit(1)`, so `return 1` is unreachable.

---

### LOW-006: Test `test_services.py` uses `time.sleep(2.0)` for service startup
- **File**: `tests/test_services.py:36`
- **Type**: Test flakiness
- **Description**: Hard-coded 2-second sleep waiting for D-Bus services to register. On slow CI runners, this may be insufficient; on fast machines, it wastes time.
- **Fix**: Poll for D-Bus name registration with exponential backoff.

---

### LOW-007: Test `test_services.py` doesn't handle service startup failure
- **File**: `tests/test_services.py:28-41`
- **Type**: Test quality
- **Description**: If `BRAIN_SCRIPT` or `CONTEXT_SCRIPT` fails to start, `cls.brain = cls.bus.get_object(...)` raises `DBusException` with no cleanup of the spawned processes.
- **Fix**: Add try/except in `setUpClass` with process cleanup on failure.

---

### LOW-008: Test `test_phase4.py` has import-only tests that always pass
- **File**: `tests/test_phase4.py:186-208`
- **Type**: Test quality / False positives
- **Description**: `TestGlobalSearch.test_import` and `TestAdvancedVoice.test_import` only verify imports succeed. They pass even if the modules are completely broken at runtime.
- **Fix**: Add functional assertions beyond import checks.

---

### LOW-009: Test `test_phase4.py` `test_classify_short_as_speed` may be fragile
- **File**: `tests/test_phase4.py:18-24`
- **Type**: Test fragility
- **Description**: Tests like `assert router.classify_task("open files") == "speed"` depend on the exact keyword matching logic in `AIRouter`. If the classifier is updated, these tests break without indicating a real regression.
- **Fix**: Test the classifier's contract (short inputs → speed), not specific keyword matches.

---

### LOW-010: `pyproject.toml` mypy excludes most app code
- **File**: `pyproject.toml:93`
- **Type**: Configuration
- **Description**: mypy excludes `tests/`, `build/`, `dist/`, and 8 app directories. Only service internals are type-checked. Type errors in app code go undetected.
- **Fix**: Gradually remove exclusions as type annotations are added.

---

### LOW-011: `pyproject.toml` bandit skips B101 (assert)
- **File**: `pyproject.toml:114`
- **Type**: Configuration
- **Description**: `skips = ["B101"]` allows `assert` in non-test code. While the comment says "used for D-Bus validation," assert statements are stripped in optimized mode (`python -O`).
- **Fix**: Replace assert with proper validation in production code.

---

### LOW-012: `Dockerfile` runs as root by default
- **File**: `Dockerfile:37`
- **Type**: Security
- **Description**: The `CMD` runs `/workspace/build/build.sh` as root. While a `builder` user is created, it's never used as the default.
- **Fix**: Add `USER builder` before `CMD`, or use `gosu` for privilege dropping.

---

## Recommendations (Priority Order)

1. **Fix path traversal in plugin system** (CRIT-001, CRIT-002) — highest security risk
2. **Fix shell config binary execution** (CRIT-003) — arbitrary code execution
3. **Add backtick to injection blocklists** (CRIT-005) — defense-in-depth gap
4. **Fix resource leaks** (HIGH-002, HIGH-006, HIGH-007, HIGH-008, HIGH-009) — long-running service stability
5. **Fix thread safety issues** (HIGH-004, HIGH-005, MED-006) — crash prevention
6. **Replace silent exception swallowing** (MED-001) — observability
7. **Fix command sanitization** (HIGH-001) — AI command injection
8. **Update QA script** (MED-012, MED-013) — CI completeness
9. **Migrate deprecated GTK APIs** (MED-009) — GTK 5 readiness
10. **Improve test quality** (LOW-006 through LOW-009) — test reliability

---

*Report generated by parallel bug-hunter agents scanning services/, apps/, installer/, system/, tests/, and configuration files.*
