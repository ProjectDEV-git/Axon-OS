# 03 — Signal Emission & Streaming Reliability

**Agent**: Debug Agent 1 (D-Bus Infrastructure)
**Date**: 2026-07-02
**Files Analyzed**: brain_service.py, voice_service.py, context_service.py, gui_agent_service.py

---

## 1. Signal Definitions & Signatures

### 1.1 BrainService Signals

| Signal | Signature | Declared at | Emitted from |
|--------|-----------|-------------|--------------|
| `TokenGenerated` | `"ss"` (transaction_id, token) | line 455-458 | `_do_generate_stream`, `_do_chat_stream` |
| `GenerationCompleted` | `"sbs"` (transaction_id, success, error_msg) | line 460-463 | `_do_generate_stream`, `_do_chat_stream` |
| `PullProgress` | `"sxxs"` (model_name, completed_bytes, total_bytes, status) | line 465-468 | `_do_pull_model` |

**Signature correctness**:
- `TokenGenerated("ss")`: Both args are strings. `_sanitize_output()` returns str. ✓
- `GenerationCompleted("sbs")`: `transaction_id` is str (uuid4), `success` is bool (D-Bus maps to dbus.Boolean which is accepted for "b"), `error_msg` is str. ✓
- `PullProgress("sxxs")`: `model_name` is str, `completed_bytes` and `total_bytes` are ints from JSON, `status` is str. The "x" signature is `INT64`. Python `int` maps to dbus.Int64. ✓

**Note on "x" in PullProgress**: D-Bus `x` is signed 64-bit integer. If Ollama returns very large values (>2^63), this could overflow. In practice, model sizes won't exceed this. ✓

### 1.2 VoiceService Signals

| Signal | Signature | Declared at | Emitted from |
|--------|-----------|-------------|--------------|
| `StateChanged` | `"s"` (state) | line 121-123 | Multiple locations |
| `TranscriptReady` | `"s"` (text) | line 125-127 | `_transcribe_and_route` via `GLib.idle_add` |

**StateChanged emission points**:
- `"error"` — `_start_recording` (line 157, 165) — main loop thread ✓
- `"listening"` — `_start_recording` (line 169) — main loop thread ✓
- `"transcribing"` — `_stop_and_process` (line 193) — main loop thread ✓
- `"idle"` — `_finish` (line 362) — main loop thread via `GLib.idle_add` ✓

All VoiceService signals are emitted on the main loop thread. ✓

### 1.3 ContextService Signals

| Signal | Signature | Declared at | Emitted from |
|--------|-----------|-------------|--------------|
| `ContextChanged` | `"s"` (context_json) | line 228-231 | Multiple locations |

**Emission points**:
- `SetActiveWindow` (line 104) — D-Bus method, main loop ✓
- `SetActiveSpace` (line 111) — D-Bus method, main loop ✓
- `_on_clipboard_data` (line 304) — GLib IO callback, main loop ✓
- `_poll_xclip` (line 326) — GLib timeout callback, main loop ✓

### 1.4 GuiAgentService Signals

| Signal | Signature | Declared at | Emitted from |
|--------|-----------|-------------|--------------|
| `ActionsDone` | `"s"` (report_json) | line 101-103 | `ExecuteAsync` worker |

**Emission**: Via `GLib.idle_add(self.ActionsDone, json.dumps(report))` (line 96). ✓

---

## 2. Thread-Safe Signal Emission

### BUG — CRITICAL: BrainService signals emitted from worker threads

All 8 signal emission sites in BrainService are called from background worker threads **without** `GLib.idle_add()`:

```python
# Pattern seen throughout brain_service.py:
# From _do_generate_stream (worker thread):
self.TokenGenerated(tx_id, token)              # line 516, 567
self.GenerationCompleted(tx_id, True, "")       # line 517, 570
self.GenerationCompleted(tx_id, False, "...")   # line 520, 573

# From _do_pull_model (worker thread):
self.PullProgress(model_name, completed, total, status)  # line 486
self.PullProgress(model_name, 0, 0, "Pull failed")       # line 489
```

**python-dbus is not thread-safe for signal emission.** The `dbus.service.Object.emit_signal()` method interacts with GLib's main loop, which owns the D-Bus connection. Calling it from another thread:
- Can corrupt the GLib message queue
- May cause `GLib-GObject-CRITICAL` warnings
- Can result in lost signals
- In worst case, causes segfaults or hangs

**Compare with correct pattern in VoiceService**:
```python
# voice_service.py:237 — correct!
GLib.idle_add(self.TranscriptReady, text)
```

**Impact**: Every streaming generation, every model pull, every chat stream emits signals unsafely. This affects the most-used feature of the Brain service.

**Fix**:
```python
# Instead of:
self.TokenGenerated(tx_id, token)
# Use:
GLib.idle_add(self.TokenGenerated, tx_id, token)
```

---

## 3. Client Disconnect Handling

### 3.1 What happens when a D-Bus client disconnects mid-stream?

**BrainService streaming**:
- Brain streams tokens via `TokenGenerated` signals in a loop
- If the client disconnects, subsequent signal emissions will fail silently (python-dbus drops signals to disconnected peers)
- The worker thread **continues processing** the entire Ollama stream
- No cancellation mechanism exists

**Sequence of events on client disconnect**:
1. Client disconnects from D-Bus
2. Brain worker thread is mid-stream, calling `TokenGenerated(tx_id, token)`
3. python-dbus tries to send signal, finds client gone, silently drops it
4. Worker thread continues until Ollama stream ends
5. `GenerationCompleted(tx_id, True, "")` is emitted (no listeners)
6. Worker thread exits

**Issue — MEDIUM**: Wasted CPU/I/O. If Ollama is generating a long response, the worker thread continues generating and sending signals into the void. There's no mechanism for the client to cancel a stream, and no mechanism for Brain to detect client disconnection.

**VoiceService**: Similar — if a client subscribes to `StateChanged`/`TranscriptReady` and disconnects, signals are dropped silently. Less impactful since voice doesn't stream continuously.

### 3.2 Transaction ID Lifecycle

BrainService uses UUID4 transaction IDs for streaming:
```python
tx_id = str(uuid.uuid4())
threading.Thread(target=self._do_generate_stream, args=(tx_id, ...)).start()
return tx_id  # returned to caller
```

**Issue — LOW**: There's no registry of active transactions. A client could theoretically:
1. Call `Generate(stream=True)` to get a tx_id
2. Never listen for signals
3. The stream runs to completion in the background with no cleanup

There's no way to cancel an in-progress stream by tx_id.

---

## 4. Backpressure Handling

### 4.1 Token Streaming (BrainService)

**No backpressure mechanism exists.**

During streaming, the worker thread reads from Ollama and immediately emits a signal for each token:
```python
for raw_line in r:
    token = chunk.get("response", "")
    if token:
        self.TokenGenerated(tx_id, _sanitize_output(token))
```

If the D-Bus bus is slow (many clients, large signals), signals queue up internally. Each `TokenGenerated` signal carries the token string, so for rapid generation (e.g., 100+ tokens/sec), this creates:
- Unbounded signal queue growth
- Potential memory pressure
- Delayed delivery (signals arrive out of order or in bursts)

**Mitigation exists implicitly**: D-Bus has internal flow control. python-dbus blocks on `emit_signal()` if the outgoing buffer is full. This provides natural backpressure but can stall the worker thread.

### 4.2 Pull Progress (BrainService)

```python
for raw_line in r:
    data = json.loads(line)
    self.PullProgress(model_name, completed, total, status)
```

Pull progress updates arrive as fast as Ollama sends them, which can be very frequent during download. No throttling or batching.

### 4.3 Context Changes (ContextService)

`ContextChanged` fires on every clipboard change and window focus change. Clipboard changes can be rapid (e.g., during bulk copy operations). The clipboard store deduplicates consecutive identical entries, which provides some natural throttling.

### 4.4 Ambient Voice (VoiceService)

The ambient loop has a 1.2-second cooldown between transcriptions, which naturally limits signal rate. ✓

---

## 5. Streaming Timeout Issues

### BUG — HIGH: No read timeout during Ollama streaming

In `_do_generate_stream` and `_do_chat_stream`:
```python
with self._http_post(f"{OLLAMA_BASE_URL}/api/generate", payload) as r:
    for raw_line in r:
        line = raw_line.decode().strip()
```

The `_http_post` sets a connection timeout (60s default), but the **streaming read** (`for raw_line in r`) has **no timeout**. If Ollama hangs mid-stream (e.g., model OOM, GPU error), the worker thread blocks forever on `r.read()`.

The thread is a daemon thread, so it dies when the process exits, but during normal operation it would be a leaked thread consuming resources indefinitely.

**Similarly**: `_do_pull_model` has the same issue — no read timeout during pull progress streaming.

---

## 6. Error Recovery in Streaming

### 6.1 Exception Handling

All streaming workers have try/except that emits `GenerationCompleted(tx_id, False, error_msg)`:
```python
try:
    # ... stream ...
    self.GenerationCompleted(tx_id, True, "")
except Exception as e:
    self.GenerationCompleted(tx_id, False, "Generation failed")
```

**Good**: Errors are always communicated via the completion signal. ✓

### 6.2 Partial Stream Recovery

**Issue — LOW**: If an error occurs mid-stream (e.g., after 50 tokens), the client receives:
1. `TokenGenerated(tx_id, "token1")`
2. `TokenGenerated(tx_id, "token2")`
3. ...
4. `GenerationCompleted(tx_id, False, "Generation failed")`

The client receives partial content with a failure flag. There's no mechanism to resume or retry from the point of failure. The client must restart the entire generation.

### 6.3 Conversation Persistence on Stream Failure

In `_do_chat_stream`:
```python
try:
    # ... stream tokens, accumulate ...
    self.store.add_message(conv_id, "assistant", accumulated)
    self.GenerationCompleted(tx_id, True, "")
except Exception as e:
    self.GenerationCompleted(tx_id, False, "Chat failed")
```

On failure, `accumulated` (partial response) is **not** saved to the conversation store. The user message was already saved (in `SendMessage`), but the assistant's partial response is lost. This is correct behavior — don't save partial/broken responses. ✓

---

## Summary of Findings

### Critical Bugs
1. **BrainService emits all signals from worker threads** without `GLib.idle_add()` — thread-unsafe, can corrupt GLib state or lose signals

### High Warnings
2. **No read timeout during Ollama streaming** — worker threads can block indefinitely if Ollama hangs mid-stream
3. **No stream cancellation mechanism** — client disconnect doesn't stop the worker from completing the full Ollama request

### Medium Warnings
4. **No backpressure on signal emission** — rapid token generation can queue unbounded signals in memory
5. **No transaction registry** — can't query or cancel in-progress streams by tx_id

### Low Warnings
6. **Pull progress signals not throttled** — could flood the bus during large model downloads
7. **No partial stream recovery** — client must restart from scratch on failure
