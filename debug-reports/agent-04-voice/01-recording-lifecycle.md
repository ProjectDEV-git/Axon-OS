# 01 — Audio Recording Lifecycle & Resource Cleanup

**Agent:** Debug Agent 4 (Voice & Speech Pipeline)
**Date:** 2026-07-02
**Files analyzed:**
- `services/axon-voice/voice_service.py` (primary)
- `services/axon-voice/advanced_voice_service.py` (secondary)
- `services/constants.py` (MAX_RECORD_SECONDS = 30)

---

## 1. Recorder Subprocess Termination on Exit Paths

### Normal stop (`_stop_and_process`, line 176)
**Status: GOOD**
- Sends `SIGINT` to the recorder subprocess.
- Waits up to 3 seconds with `rec.wait(timeout=3)`.
- Falls back to `rec.kill()` on `TimeoutExpired`.
- The `_recorder` reference is set to `None` under the lock before signal delivery, preventing double-stop.

### Service shutdown (`_shutdown`, line 450)
**Status: BUG — Recorder orphaned on SIGTERM/SIGINT**
```python
def _shutdown(signum, frame):
    log.info("Received signal %d, shutting down...", signum)
    loop.quit()
```
The shutdown handler only quits the GLib main loop. If a recording is in progress, `self._recorder` (parecord/arecord subprocess) is **never terminated**. It will be orphaned and continue running until it exits on its own (which for `parecord` is never — it records indefinitely).

**Impact:** Medium. Orphaned parecord holds the microphone device, preventing future recordings and other PulseAudio clients from accessing it. On ALSA, it holds the device exclusively.

**Fix:** The `_shutdown` handler must terminate the recorder:
```python
def _shutdown(signum, frame):
    log.info("Received signal %d, shutting down...", signum)
    if service._recorder:
        service._recorder.kill()
    loop.quit()
```

### Error paths in `_start_recording`
**Status: OK**
- If `_recorder_command()` returns `None` (no recorder available), no subprocess is started. The error state is set correctly.
- If `subprocess.Popen()` raises `OSError`, the error is reported and no subprocess is started.
- Neither case leaves a dangling WAV file (the mkstemp fd is already closed and the file will be cleaned by the OS).

---

## 2. Temp WAV File Cleanup

### Normal flow
**Status: GOOD**
- `_start_recording()` creates temp file via `tempfile.mkstemp`, immediately closes the fd (line 149-150).
- `_transcribe_and_route()` cleans up in a `finally` block (lines 225-228): `os.unlink(wav_path)`.
- If the unlink fails (e.g., file already deleted), the `OSError` is silently caught.

### Crash/restart scenario
**Status: ACCEPTABLE — Minor leak**
- If the process crashes between recording and transcription, the temp WAV file remains in `/tmp/axon-voice-*.wav`.
- These are small (30s × 16kHz × 2 bytes = ~960KB max) and will be cleaned by the OS tmpwatch/reboot.
- **No accumulation risk** because each recording creates a uniquely-named file.

### Ambient loop temp files
**Status: GOOD**
- Speech-detected chunks: handed to `_transcribe_and_route` which cleans up in `finally`.
- Non-speech chunks: cleaned up immediately in `else` branch (line 432-434).
- Error during VAD: cleaned up in `except` branch (line 435-440).
- `arecord` failure: cleaned up in the `CalledProcessError` handler (line 418-421).

### AdvancedVoiceService temp files
**Status: BUG — Temp file leak on record failure**
In `advanced_voice_service.py` `_start_recording()` (line 257-276):
```python
def _start_recording(self):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        self._wav_path = tmp.name
    cmd = self._recorder_command(self._wav_path)
    if not cmd:
        self._busy = False
        self._listening = False
        self.StateChanged("error")
        return  # ← self._wav_path NOT cleaned up!
```
If no recorder command is available, the temp WAV file at `self._wav_path` is never deleted.

---

## 3. MAX_RECORD_SECONDS Enforcement

### VoiceService
**Status: GOOD with caveat**
- `GLib.timeout_add_seconds(MAX_RECORD_SECONDS, self._stop_and_process)` enforces a 30-second hard cap (line 171-173).
- The timer is properly removed when recording stops (line 178-179).
- If `parecord` hangs after SIGINT, the 3-second wait + kill ensures termination.

**Caveat:** If the GLib main loop is blocked (e.g., a long-running `_transcribe_and_route` via `GLib.idle_add`), the timeout callback could be delayed. However, transcription runs in a separate thread, so the main loop should remain responsive.

### AdvancedVoiceService
**Status: BUG — No recording timeout**
AdvancedVoiceService has **no maximum recording time**. There is no `GLib.timeout_add_seconds` equivalent. If the caller never invokes `StopAndTranscribe()`, the recording runs indefinitely. The only limit is disk space (WAV at 16kHz mono fills ~1.9MB/minute).

---

## 4. File Descriptor Leak from `tempfile.mkstemp`

**Status: GOOD — No leak**
Both call sites properly close the fd immediately:
```python
fd, wav_path = tempfile.mkstemp(prefix="axon-voice-", suffix=".wav")
os.close(fd)  # ← closed right away
```
This pattern is correct. The temp file is created (for uniqueness/atomicity) and then used by name only.

---

## 5. Race Condition on Rapid Toggle (TOCTOU)

**Status: BUG — Race condition on rapid toggle**

The `Toggle()` method (line 88-97):
```python
def Toggle(self):
    with self._lock:
        if self._recorder is not None:
            GLib.idle_add(self._stop_and_process)
            return False
        if self._busy:
            return False
    GLib.idle_add(self._start_recording)  # ← outside the lock!
    return True
```

**Race scenario:**
1. **Toggle call 1:** Lock acquired, `_recorder is None` → releases lock → queues `_start_recording` via `GLib.idle_add` → returns `True`.
2. **Toggle call 2** (before GLib executes idle callback): Lock acquired, `_recorder` **still** `None` (idle callback hasn't run yet), `_busy` is `False` → releases lock → queues **another** `_start_recording` → returns `True`.

**Result:** Two `_start_recording` calls are queued. The second one:
- Creates a new temp file and overwrites `self._wav_path`.
- Starts a new recorder subprocess and overwrites `self._recorder`.
- The **first recorder subprocess is orphaned** (never stopped or cleaned up).
- The **first temp file is leaked** (never deleted).

**Severity:** Medium. This requires very fast double-press (sub-millisecond window before GLib processes the idle callback), but it IS possible with keyboard repeat or accessibility tools.

**Fix:** Move the state check + idle_add under the lock, or set a flag atomically:
```python
def Toggle(self):
    with self._lock:
        if self._recorder is not None:
            GLib.idle_add(self._stop_and_process)
            return False
        if self._busy:
            return False
        self._busy = True  # ← claim the slot immediately
    GLib.idle_add(self._start_recording)
    return True
```
Then in `_start_recording`, set `_busy = False` on error paths.

---

## 6. Signal Handling Edge Case

**Status: MINOR ISSUE — Duplicate import**
Line 445: `import signal` is imported again inside `if __name__ == "__main__"`, even though `signal` is already imported at the top of the file (line 18). Harmless but unnecessary.

---

## Summary Table

| Check | Status | Severity |
|---|---|---|
| Recorder terminated on normal stop | ✅ Good | — |
| Recorder terminated on shutdown | ❌ Bug | **Medium** |
| Temp WAV cleanup (normal flow) | ✅ Good | — |
| Temp WAV cleanup (crash) | ✅ Acceptable | Low |
| Temp WAV cleanup (ambient) | ✅ Good | — |
| Temp WAV cleanup (AdvancedVoice error) | ❌ Bug | Low |
| MAX_RECORD_SECONDS enforced (VoiceService) | ✅ Good | — |
| MAX_RECORD_SECONDS enforced (AdvancedVoice) | ❌ Bug | Medium |
| File descriptor leak from mkstemp | ✅ Good | — |
| Race condition on rapid toggle | ❌ Bug | **Medium** |
