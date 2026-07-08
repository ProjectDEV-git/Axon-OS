# 02 — Thread Safety & Race Conditions

**Agent**: Debug Agent 1 (D-Bus Infrastructure)
**Date**: 2026-07-02
**Files Analyzed**: brain_service.py, context_service.py, voice_service.py, sandbox_manager.py, search_service.py, conversation_store.py, clipboard_store.py, service_utils.py

---

## 1. D-Bus Signal Emission from Background Threads

### BUG — CRITICAL: BrainService emits signals from worker threads

In `brain_service.py`, the following signals are called **directly from background threads** without `GLib.idle_add()`:

```python
# brain_service.py:516 — called from _do_generate_stream worker thread
self.TokenGenerated(tx_id, _sanitize_output(token))

# brain_service.py:517 — called from _do_generate_stream worker thread
self.GenerationCompleted(tx_id, True, "")

# brain_service.py:486 — called from _do_pull_model worker thread
self.PullProgress(model_name, completed, total, status)

# brain_service.py:489 — called from _do_pull_model worker thread
self.PullProgress(model_name, 0, 0, "Pull failed")

# brain_service.py:520 — called from _do_generate_stream worker thread
self.GenerationCompleted(tx_id, False, "Generation failed")

# brain_service.py:567 — called from _do_chat_stream worker thread
self.TokenGenerated(tx_id, token)

# brain_service.py:570 — called from _do_chat_stream worker thread
self.GenerationCompleted(tx_id, True, "")

# brain_service.py:573 — called from _do_chat_stream worker thread
self.GenerationCompleted(tx_id, False, "Chat failed")
```

**Impact**: D-Bus signal emission via `python-dbus` is **not thread-safe**. The GLib main loop owns the D-Bus connection. Emitting from a non-main thread can:
- Corrupt internal GLib/dbus state
- Cause sporadic `GLib.Error` or segfaults
- Result in lost signals
- Trigger race conditions in the dbus message queue

**Fix**: Wrap all signal emissions in `GLib.idle_add()`:
```python
GLib.idle_add(self.TokenGenerated, tx_id, _sanitize_output(token))
```

### Services with correct thread-safe signal emission

| Service | Signal | Thread | Correct? |
|---------|--------|--------|----------|
| VoiceService | `StateChanged` | Worker thread via `GLib.idle_add` | **YES** |
| VoiceService | `TranscriptReady` | Worker thread via `GLib.idle_add` | **YES** |
| GuiAgentService | `ActionsDone` | Worker thread via `GLib.idle_add` | **YES** |
| SandboxManager | `dbus_ok` callback | Worker thread (via async_callbacks) | **YES** |
| ContextService | `ContextChanged` | GLib main loop callbacks | **YES** |

**Only BrainService has this bug** — all other services correctly use `GLib.idle_add` or run on the main loop.

---

## 2. Lock Usage Analysis

### 2.1 BrainService._config_lock

```python
self._config_lock = threading.RLock()  # brain_service.py:94
```

**Type**: `RLock` (reentrant) — correct, since `load_config()` calls `save_config()` which also acquires the lock.

**Issue — MEDIUM**: `self.config` (the dict) is read **without** holding `_config_lock` in several D-Bus methods:
- `GetStatus()` line 200: `self.config` read unprotected
- `GetEmbeddings()` line 421: `self.config.get(...)` read unprotected
- `Generate()` line 242: `self.router.select_model()` may read config

Since `self.config` is replaced atomically (whole dict assignment in `load_config()`), CPython's GIL ensures the reference swap is atomic. **However**, this is an implementation detail, not a language guarantee. In practice, this is safe on CPython but would break on PyPy or if the GIL is ever removed.

### 2.2 VoiceService._lock

```python
self._lock = threading.Lock()  # voice_service.py:69
```

**Used for**: Protecting `_recorder` and `_busy` state transitions.

**Pattern analysis**:
- `Toggle()`: Acquires lock, checks state, schedules `GLib.idle_add`. Returns. (D-Bus thread)
- `_stop_and_process()`: Acquires lock, swaps recorder to None, releases lock. (Main loop via idle_add)
- `_finish()`: Acquires lock, sets `_busy = False`. (Main loop via idle_add)

**Issue — LOW**: The lock protects state transitions, but there's a TOCTOU gap in `Toggle()`:
```python
with self._lock:
    if self._recorder is not None:
        GLib.idle_add(self._stop_and_process)
        return False
    if self._busy:
        return False
GLib.idle_add(self._start_recording)
```
Between releasing the lock (line 96) and the idle_add callback executing, the state could change. However, since both `Toggle()` and the idle callbacks run on the GLib main loop (Toggle is a D-Bus method), this is actually safe — the idle callback won't execute until Toggle returns.

### 2.3 ConversationStore._lock

```python
self._lock = threading.Lock()  # conversation_store.py:21
```

**Type**: Regular `Lock` (non-reentrant). 

**Analysis**: No method calls another locked method, so non-reentrant is correct and slightly more efficient.

**Issue — MEDIUM**: The pattern of getting a connection, using it, and closing it under a single lock is correct but **creates a new SQLite connection for every operation**:

```python
def add_message(self, conversation_id, role, content):
    with self._lock:
        conn = self._get_connection()
        try:
            # ... work ...
            conn.commit()
        finally:
            self._close_connection(conn)  # closes every time
```

The `_get_connection()` method checks `threading.local()` for an existing connection, but `_close_connection()` closes it without clearing `_local.conn`. So the next call finds a closed connection, detects it, and creates a new one. This is **functionally correct** but wasteful.

### 2.4 SearchService._lock — UNUSED!

```python
self._lock = threading.Lock()  # search_service.py:107
```

**BUG — HIGH**: `self._lock` is defined but **never used anywhere in the class**. This means:

1. **`_stats` dict has no synchronization**: Modified from `_scan_once()` (worker thread) and read from `GetStats()` (main loop thread). Dict operations in CPython are GIL-protected, but modifying multiple fields (`self._stats.update(...)`) is not atomic — a reader could see partial updates.

2. **Database access is unsynchronized**: Both `_scan_once()` and `Query()` open independent SQLite connections, which is safe with WAL mode for reads. But `_scan_once()` writes while `Query()` reads, and without explicit locking, FTS5 or vec0 internal state could conflict.

### 2.5 ClipboardStore._lock

```python
self._lock = threading.Lock()  # clipboard_store.py:25
```

**Used correctly**: Every public method acquires the lock before database operations. Good.

**Note**: Connections are opened and closed per operation (not cached), which is safe.

---

## 3. ContextService Thread Safety

### 3.1 State Access

`ContextService` state (`active_window_title`, `active_window_app`, `active_space`, `_clipboard_history`) is accessed only from the GLib main loop thread:
- Written by D-Bus methods (`SetActiveWindow`, `SetActiveSpace`) — main loop
- Written by clipboard callbacks (`_on_clipboard_data`, `_poll_xclip`) — main loop
- Read by query methods (`GetActiveContext`, `GetContextString`) — main loop

**No threading issue here** — all access is single-threaded via GLib.

### 3.2 _load_config() Race

`_load_config()` is called from multiple main-loop callbacks and D-Bus methods. It reads/writes `self.track_clipboard`, `self.track_terminal_history`, etc. Since all callers are on the main loop, there's no race.

---

## 4. SQLite Thread Safety

### 4.1 ConversationStore

- Uses `check_same_thread=False` with WAL mode
- All access serialized by `threading.Lock`
- **Safe** ✓

### 4.2 ClipboardStore

- Uses `check_same_thread=False` with WAL mode
- All access serialized by `threading.Lock`
- **Safe** ✓

### 4.3 SearchService Database

- `open_db()` creates independent connections per call
- No serialization between `_scan_once()` (worker thread) and `Query()` (main loop)
- WAL mode handles concurrent reads
- **Minor risk**: FTS5 writes during `_scan_once()` could conflict with FTS5 reads in `Query()`. SQLite's WAL prevents corruption but may return `SQLITE_BUSY` in edge cases. The code doesn't handle this.

---

## 5. Subprocess + Thread Interactions

### 5.1 VoiceService Ambient Loop

```python
def _ambient_loop(self):
    while not self._ambient_stop.is_set():
        # arecord subprocess
        # if speech detected:
        threading.Thread(target=self._transcribe_and_route, args=(wav,), daemon=True).start()
        self._ambient_stop.wait(1.2)  # cooldown
```

**Issue — LOW**: Multiple `_transcribe_and_route` threads could be running simultaneously if speech is detected during the cooldown wait (shouldn't happen since `_ambient_stop.wait` blocks). Actually, the cooldown blocks the loop, so only one transcription thread runs at a time. **Safe.**

### 5.2 BrainService HTTP Retries

```python
def _http_post(self, url, payload, stream=False, timeout=60.0, max_retries=5):
    for attempt in range(max_retries):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except (urllib.error.URLError, OSError):
            time.sleep(backoff)  # blocks the worker thread
```

**Acceptable**: Blocking the daemon thread during retries is fine since each stream/pull runs in its own thread.

---

## Summary of Findings

### Critical Bugs
1. **BrainService emits D-Bus signals from worker threads** without `GLib.idle_add()` — can corrupt GLib state, lose signals, or crash

### High Warnings
2. **SearchService._lock is defined but never used** — `_stats` dict and database access have no synchronization
3. **BrainService reads `self.config` without holding `_config_lock`** — safe on CPython due to GIL but not guaranteed

### Medium Warnings
4. **ConversationStore closes and recreates SQLite connections on every operation** — wasteful but correct
5. **SearchService database has no write/read synchronization** — WAL mode helps but doesn't fully prevent SQLITE_BUSY

### Low Warnings
6. **VoiceService._lock TOCTOU** — theoretically unsafe but practically safe due to GLib main loop serialization
