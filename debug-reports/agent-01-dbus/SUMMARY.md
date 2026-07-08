# D-Bus Service Layer — Debug Summary

**Agent**: Debug Agent 1 (D-Bus Infrastructure)
**Date**: 2026-07-02
**Files Analyzed**: All 7 D-Bus services + supporting modules

---

## Top 5 Critical Bugs

### 1. BrainService emits D-Bus signals from worker threads (Thread Safety)
**File**: `services/axon-brain/brain_service.py` (lines 486, 489, 516, 517, 520, 567, 570, 573)
**Impact**: All streaming signals (`TokenGenerated`, `GenerationCompleted`, `PullProgress`) are emitted directly from background threads without `GLib.idle_add()`. D-Bus signal emission is not thread-safe. This can corrupt GLib internals, lose signals, cause sporadic crashes, or trigger `GLib-GObject-CRITICAL` warnings.
**Fix**: Wrap every signal emission in `GLib.idle_add(self.SignalName, arg1, arg2, ...)`.
**Comparison**: VoiceService, GuiAgentService, and ContextService all do this correctly.

### 2. ServiceBase is dead code — all services duplicate bootstrap (Architecture)
**File**: `services/service_base.py`
**Impact**: `ServiceBase` provides GLib bootstrap, `NameExistsException` handling, health tracking, and `GetStatus()`. **No service inherits from it.** All 6 concrete services (`BrainService`, `ContextService`, `VoiceService`, `SandboxManager`, `SearchService`, `GuiAgentService`) directly subclass `dbus.service.Object` and duplicate 15+ lines of identical bootstrap code. Any security or lifecycle fix must be applied 6 times. The `org.axonos.Service` interface is never exposed on the bus.
**Fix**: Refactor all services to inherit from `ServiceBase`. Move service-specific init to `_setup()`.

### 3. No D-Bus session bus fallback (Error Handling)
**Files**: All 7 service files
**Impact**: `dbus.SessionBus()` is called with no try/except. If the session bus is unavailable (headless, no user session, `DBUS_SESSION_BUS_ADDRESS` unset), every service crashes with a raw Python traceback.
**Fix**: Wrap in try/except, log a clear error message, exit with non-zero code.

### 4. No read timeout during Ollama streaming (Reliability)
**Files**: `services/axon-brain/brain_service.py` (lines 508, 557)
**Impact**: `_do_generate_stream` and `_do_chat_stream` read from the Ollama HTTP response in a loop with no per-read timeout. If Ollama hangs mid-stream (model OOM, GPU error, network partition), the daemon thread blocks forever. The `_http_post` connection timeout doesn't apply to subsequent reads.
**Fix**: Add a `timeout` parameter to the streaming read, or use `signal.alarm`/threading timer to abort stuck streams.

### 5. No stream cancellation mechanism (Reliability)
**Files**: `services/axon-brain/brain_service.py`
**Impact**: When a client disconnects mid-stream, the worker thread continues generating tokens from Ollama and emitting signals into the void. There is no transaction registry, no cancel API, and no mechanism to detect client disconnection. This wastes CPU/GPU and keeps the Ollama connection occupied.
**Fix**: Implement a transaction registry with cancel support. Use `WeakValueDictionary` or explicit registration/deregistration.

---

## Top 5 Warnings

### 1. SearchService._lock is defined but never used (Thread Safety)
**File**: `services/axon-search/search_service.py` (line 107)
**Impact**: The `_stats` dict is modified from a worker thread and read from the main loop with no synchronization. Database writes in `_scan_once()` are not synchronized with reads in `Query()`. While CPython's GIL makes dict operations atomic, `_stats.update(...)` is not atomic across multiple fields.
**Fix**: Use `self._lock` to protect `_stats` access, or use `threading.Event` + atomic swap.

### 2. BrainService reads `self.config` without holding `_config_lock` (Thread Safety)
**File**: `services/axon-brain/brain_service.py` (lines 200, 421)
**Impact**: `GetStatus()` and `GetEmbeddings()` read `self.config` while `load_config()` may be modifying it from another thread. Safe on CPython due to GIL, but not guaranteed by the language spec.
**Fix**: Either always read config under the lock, or use `threading.Event` to signal config readiness.

### 3. Inconsistent GetStatus() implementation (Architecture)
**Impact**: Only BrainService implements `GetStatus()`, and it's on the `org.axonos.Brain` interface (not `org.axonos.Service`). Five services have no status endpoint at all. There's no uniform way to check if a service is alive.
**Fix**: Have all services inherit `ServiceBase.GetStatus()` which provides standard health/uptime JSON.

### 4. No backpressure on rapid signal emission (Reliability)
**Files**: `services/axon-brain/brain_service.py`
**Impact**: Token streaming can produce 100+ signals/sec with no throttling. Pull progress signals are emitted as fast as Ollama sends them. This can queue unbounded signals in the D-Bus message buffer, causing memory pressure and delayed delivery.
**Fix**: Batch signals (e.g., emit TokenGenerated every N tokens or every M ms), or use a token buffer with configurable flush interval.

### 5. ConversationStore wastes connections by closing after every operation (Performance)
**File**: `services/axon-brain/conversation_store.py`
**Impact**: Every database operation opens a new SQLite connection, performs the operation, and closes it. The `_get_connection()` method checks `threading.local()` but connections are always closed, so the cache is useless. Each `sqlite3.connect()` call takes ~0.1ms, which adds up under load.
**Fix**: Either keep connections open (per-thread, via `threading.local()`) and close on cleanup, or remove the `threading.local()` caching since it provides no benefit.

---

## Recommendations

### Priority 1 — Immediate Fixes
1. **Wrap BrainService signal emissions in `GLib.idle_add()`** — This is a one-line fix per call site (~8 locations) and eliminates the most dangerous thread-safety bug.
2. **Add read timeouts to Ollama streaming** — Prevents zombie threads from hung Ollama instances.
3. **Wrap `dbus.SessionBus()` in try/except** — Prevents ugly crashes when D-Bus is unavailable.

### Priority 2 — Short-Term Refactoring
4. **Make all services inherit from `ServiceBase`** — Eliminates 6x code duplication and provides uniform `GetStatus()`.
5. **Use `self._lock` in SearchService** — Protect `_stats` dict and database access.
6. **Implement stream cancellation** — Add a transaction registry with cancel support for BrainService streaming.

### Priority 3 — Medium-Term Improvements
7. **Add streaming backpressure** — Batch token signals or use a ring buffer.
8. **Add per-read timeouts for Ollama HTTP streaming** — Use `urllib3` or `httpx` with explicit read timeouts instead of `urllib.request`.
9. **Normalize `GetStatus()` across all services** — Ensure every service exposes health/uptime on `org.axonos.Service`.
10. **Fix ConversationStore connection lifecycle** — Either cache per-thread properly or remove the useless `threading.local()`.
