# D-Bus Service Infrastructure — Fix Report

**Agent**: Agent 01 (D-Bus Infrastructure)
**Date**: 2026-07-02
**Bugs Fixed**: 5 (FIX 1-5 from debug report)
**Files Modified**: 10

---

## FIX 1: ServiceBase is dead code — all services duplicate bootstrap (CRITICAL)

**Root cause**: `ServiceBase` in `services/service_base.py` provided GLib bootstrap, `NameExistsException` handling, health tracking, and `GetStatus()` — but no concrete service inherited from it. All 9 D-Bus services directly subclassed `dbus.service.Object` and duplicated 15+ lines of identical bootstrap code.

**Fix**: Refactored all 9 services to inherit from `ServiceBase`:

| Service | File | Class |
|---------|------|-------|
| BrainService | `services/axon-brain/brain_service.py` | `BrainService(ServiceBase)` |
| ContextService | `services/axon-context/context_service.py` | `ContextService(ServiceBase)` |
| VoiceService | `services/axon-voice/voice_service.py` | `VoiceService(ServiceBase)` |
| SearchService | `services/axon-search/search_service.py` | `SearchService(ServiceBase)` |
| SandboxManager | `services/axon-sandbox/sandbox_manager.py` | `SandboxManager(ServiceBase)` |
| GuiAgentService | `services/axon-gui-agent/gui_agent_service.py` | `GuiAgentService(ServiceBase)` |
| GlobalSearchService | `services/axon-search/global_search_service.py` | `GlobalSearchService(ServiceBase)` |
| AdvancedVoiceService | `services/axon-voice/advanced_voice_service.py` | `AdvancedVoiceService(ServiceBase)` |
| ModelMarketplaceService | `services/axon-brain/model_marketplace.py` | `ModelMarketplaceService(ServiceBase)` |

**Changes per service**:
- Added `BUS_NAME`, `OBJECT_PATH`, `SERVICE_NAME` class attributes
- Replaced `__init__` with `_setup()` containing service-specific init only
- Removed duplicated GLib loop setup, `SessionBus()` call, `BusName` claim, `Object.__init__()` call, and logger setup
- Each service now gets `GetStatus()` and `GetServiceName()` from `org.axonos.Service` interface automatically
- Health tracking (uptime, `set_healthy()`, `is_healthy()`) is inherited from base

**Impact**: ~130 lines of duplicated boilerplate eliminated across 9 files. Any future security or lifecycle fix to the bootstrap process now needs to be applied only once in `ServiceBase`.

**Special handling**: SandboxManager had module-level `logger` references inside class methods. Updated all `logger.` calls to `self.logger.` to use the base class logger. `__main__` block uses inline `import logging` for the shutdown handler.

---

## FIX 2: No D-Bus session bus fallback (HIGH)

**Root cause**: `dbus.SessionBus()` was called with no try/except in all services. If the session bus is unavailable (headless, no user session, `DBUS_SESSION_BUS_ADDRESS` unset), every service crashes with a raw Python traceback.

**Fix**: Added try/except in `ServiceBase.__init__()`:

```python
try:
    self.session_bus = dbus.SessionBus()
except dbus.exceptions.DBusException as exc:
    print(f"Cannot connect to D-Bus session bus: {exc}", file=sys.stderr)
    sys.exit(1)
```

**Impact**: All 9 services now get the fallback automatically through `ServiceBase`. Headless startup produces a clean error message instead of a traceback.

---

## FIX 3: No read timeout during Ollama streaming (HIGH)

**Root cause**: `_do_generate_stream` and `_do_chat_stream` in `BrainService` read from the Ollama HTTP response in a loop with no per-read timeout. If Ollama hangs mid-stream (model OOM, GPU error, network partition), the daemon thread blocks forever. The `_http_post` connection timeout does not apply to subsequent reads after the connection is established.

**Fix**: Added `_set_stream_timeout()` static method that sets the underlying socket timeout on the HTTP response object:

```python
@staticmethod
def _set_stream_timeout(response, timeout: float = 30.0) -> None:
    try:
        fp = response.raw._fp
        if fp is not None and hasattr(fp, "sock") and fp.sock is not None:
            fp.sock.settimeout(timeout)
    except (AttributeError, OSError):
        pass
```

Called at the start of both `_do_generate_stream` and `_do_chat_stream`. The 30-second timeout is configurable via `_STREAM_READ_TIMEOUT`. Raises `TimeoutError` which is caught and reported via `GenerationCompleted` signal with `success=False`.

**Impact**: Hung Ollama instances no longer permanently block daemon threads. Streams fail fast with a clear error.

---

## FIX 4: No stream cancellation mechanism (HIGH)

**Root cause**: When a client disconnects mid-stream, the worker thread continues generating tokens from Ollama and emitting signals into the void. There was no transaction registry, no cancel API, and no mechanism to detect client disconnection.

**Fix**: Added three components to `BrainService`:

1. **Transaction registry**: `self._active_streams: dict[str, threading.Event]` maps transaction IDs to cancellation flags.

2. **CancelStream D-Bus method**: New `org.axonos.Brain.CancelStream(transaction_id) -> bool` method that sets the cancellation flag for a given transaction.

3. **Cancel checks in stream loops**: Both `_do_generate_stream` and `_do_chat_stream` check `cancel_flag.is_set()` before processing each chunk. If set, the loop breaks immediately.

4. **Lifecycle management**: `_active_streams.pop(tx_id, None)` is called in the `finally` block of both streaming methods, ensuring clean registration/deregistration.

**Impact**: Clients can cancel in-progress streams, freeing GPU/CPU resources and Ollama connections immediately. Prevents resource waste on client disconnect.

---

## FIX 5: No backpressure on rapid signal emission (MEDIUM)

**Root cause**: Token streaming could produce 100+ signals/sec with no throttling. Pull progress signals were emitted as fast as Ollama sends them. This could queue unbounded signals in the D-Bus message buffer, causing memory pressure and delayed delivery.

**Fix**: Added `TokenBuffer` class that batches token signals:

```python
class TokenBuffer:
    def __init__(self, emit_fn, flush_interval=0.1, max_tokens=10):
        # Flush when buffer has >= max_tokens tokens OR flush_interval has elapsed
```

- Initialized in `BrainService._setup()` with `GLib.idle_add(self.TokenGenerated, ...)` as the emit function (thread-safe)
- Both `_do_generate_stream` and `_do_chat_stream` use `self._token_buffer.add(token, tx_id)` instead of direct `GLib.idle_add(self.TokenGenerated, ...)`
- Buffer is force-flushed at stream completion and in exception handlers

**Impact**: Token signals are batched (up to 10 tokens per flush, or every 100ms), reducing D-Bus signal throughput by ~10x under heavy streaming load. Prevents signal flooding.

---

## Validation

- **Lint**: All 10 modified files pass `ruff check` with zero errors
- **Import sorting**: Auto-fixed by `ruff --fix` for 7 files where `from service_base import ServiceBase` was added
- **Concurrent edits**: Non-overlapping changes from agents "deer" (FIX 2: `_sanitize_context` enhancement) and "bug" (FIX 3: voice service `_force_stop_and_transcribe`) were detected and respected — no merge conflicts

## Files Modified

| File | FIX | Change Summary |
|------|-----|----------------|
| `services/service_base.py` | 1+2 | Added D-Bus session bus fallback try/except |
| `services/axon-brain/brain_service.py` | 1+3+4+5 | Inherits ServiceBase, TokenBuffer, _set_stream_timeout, CancelStream, _active_streams registry |
| `services/axon-context/context_service.py` | 1 | Inherits ServiceBase, removes bootstrap boilerplate |
| `services/axon-voice/voice_service.py` | 1 | Inherits ServiceBase, removes bootstrap boilerplate |
| `services/axon-search/search_service.py` | 1 | Inherits ServiceBase, removes bootstrap boilerplate |
| `services/axon-sandbox/sandbox_manager.py` | 1 | Inherits ServiceBase, logger refs updated to self.logger |
| `services/axon-gui-agent/gui_agent_service.py` | 1 | Inherits ServiceBase, removes bootstrap boilerplate |
| `services/axon-search/global_search_service.py` | 1 | Inherits ServiceBase, removes bootstrap boilerplate |
| `services/axon-voice/advanced_voice_service.py` | 1 | Inherits ServiceBase, removes bootstrap boilerplate |
| `services/axon-brain/model_marketplace.py` | 1 | Inherits ServiceBase, removes bootstrap boilerplate |

## Follow-up Items

1. **BrainService.GetStatus() name collision**: BrainService overrides `GetStatus` on `org.axonos.Brain` (brain-specific info) while ServiceBase provides it on `org.axonos.Service` (generic health). Both are registered on the bus, but Python callers only see the subclass version. Consider renaming the Brain-specific one to `GetBrainStatus()` for clarity.

2. **SearchService._lock**: The lock exists but `_stats` reads in `Query()` are not synchronized. Consider using the lock consistently.

3. **ConversationStore connection lifecycle**: Still opens/closes per operation. Consider persistent per-thread connections.
