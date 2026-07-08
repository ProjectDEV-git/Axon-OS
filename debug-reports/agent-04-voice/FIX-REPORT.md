# FIX REPORT — Voice & Speech Pipeline

**Agent:** Bug Fixer (agent-04)
**Date:** 2026-07-02
**Source:** `debug-reports/agent-04-voice/SUMMARY.md`

---

## Summary

Fixed 3 bugs and 3 warnings across 2 files in `services/axon-voice/`. All changes verified with `py_compile` (syntax) and `ruff check` (lint). Both pass cleanly.

| ID | Severity | Description | File | Status |
|----|----------|-------------|------|--------|
| BUG-1 | Medium-High | Recorder subprocess orphaned on shutdown | `voice_service.py` | Fixed |
| BUG-2 | Medium | TOCTOU race condition on rapid toggle | `voice_service.py` | Fixed |
| BUG-3 | Medium | No recording timeout in AdvancedVoiceService | `advanced_voice_service.py` | Fixed |
| WARN-1 | Low | Temp file leak on recorder error | `advanced_voice_service.py` | Fixed |
| WARN-3 | Medium | Vosk model recreated every transcription call | `advanced_voice_service.py` | Fixed |
| WARN-4 | Medium | TTS has no fallback chain | `advanced_voice_service.py` | Fixed |

---

## FIX 1: Recorder subprocess orphaned on shutdown (BUG-1)

**File:** `voice_service.py`, `_shutdown()` handler (line ~450)

**Problem:** On SIGTERM/SIGINT, `loop.quit()` was called but the parecord/arecord subprocess was never killed. This left the microphone device exclusively held, blocking all future recordings.

**Change:** Added `service._recorder.kill()` + `service._recorder.wait(timeout=2)` before `loop.quit()`, wrapped in try/except for safety.

```python
def _shutdown(signum, frame):
    log.info("Received signal %d, shutting down...", signum)
    if service._recorder:
        try:
            service._recorder.kill()
            service._recorder.wait(timeout=2)
        except Exception:
            pass
    loop.quit()
```

**Impact:** Microphone is properly released on shutdown. No more orphaned recorder processes.

---

## FIX 2: TOCTOU race condition on rapid toggle (BUG-2)

**File:** `voice_service.py`, `Toggle()` method (line ~88)

**Problem:** Two rapid Super+V presses could both pass the `_recorder is None` check before GLib processed the first idle callback. This caused two `_start_recording` calls, orphaning the first recorder and leaking its temp file.

**Change:** Claim the `_busy` flag atomically inside the lock, before releasing it:

```python
def Toggle(self):
    with self._lock:
        if self._recorder is not None:
            GLib.idle_add(self._stop_and_process)
            return False
        if self._busy:
            return False
        self._busy = True  # claim immediately inside lock
    GLib.idle_add(self._start_recording)
    return True
```

**Impact:** Second rapid toggle is now blocked by `_busy=True` until the first recording starts. No orphaned processes or leaked temp files from race conditions.

---

## FIX 3: AdvancedVoiceService has no recording timeout (BUG-3)

**File:** `advanced_voice_service.py`, `StartListening()` / `_start_recording()` / `_stop_and_transcribe()`

**Problem:** Unlike VoiceService (which enforces `MAX_RECORD_SECONDS = 30`), AdvancedVoiceService had no maximum recording time. If `StopAndTranscribe()` was never called, recording continued indefinitely (~1.9MB/min disk, microphone held).

**Changes:**
1. Imported `MAX_RECORD_SECONDS` from `constants`
2. Added `_record_timeout_id` attribute to `__init__`
3. Added `GLib.timeout_add_seconds(MAX_RECORD_SECONDS, self._force_stop_and_transcribe)` in `_start_recording()`
4. Added timeout cancellation in `_stop_and_transcribe()`
5. Added `_force_stop_and_transcribe()` method that forces stop if still listening

```python
def _force_stop_and_transcribe(self):
    """Called by recording timeout — force stop if still listening."""
    if self._listening:
        log.warning(
            "Recording timeout after %d seconds, forcing stop",
            MAX_RECORD_SECONDS,
        )
        GLib.idle_add(self._stop_and_transcribe)
    return False  # do not repeat
```

**Impact:** Recordings are hard-capped at 30 seconds. Matches VoiceService behavior.

---

## FIX 4: AdvancedVoiceService temp file leak (WARN-1)

**File:** `advanced_voice_service.py`, `_start_recording()` error path (line ~257)

**Problem:** If no recorder command was available, `self._wav_path` was set but never cleaned up in the error return path.

**Change:** Added `os.unlink(self._wav_path)` and `self._wav_path = None` in the error path:

```python
if not cmd:
    try:
        os.unlink(self._wav_path)
    except OSError:
        pass
    self._wav_path = None
    self._busy = False
    self._listening = False
    self.StateChanged("error")
    return
```

**Impact:** Temp WAV files are cleaned up even when no recorder is available.

---

## FIX 5: Vosk model recreated every call (WARN-3)

**File:** `advanced_voice_service.py`, `_transcribe_vosk()` (line ~361)

**Problem:** Each Vosk transcription created a new `Model(model_path)` object (1-5 second overhead per call). VoiceService correctly cached its whisper model.

**Change:** Cache the model using `self._vosk_model` and `self._vosk_model_path` (new attribute in `__init__`). Model is only recreated when it's None or the language/model path changes:

```python
if self._vosk_model is None or self._vosk_model_path != model_path:
    self._vosk_model = Model(model_path)
    self._vosk_model_path = model_path
model = self._vosk_model
```

**Impact:** Vosk model is created once per language and reused. Eliminates 1-5 second overhead on subsequent calls.

---

## FIX 6: TTS has no fallback chain (WARN-4)

**File:** `advanced_voice_service.py`, `Speak()` method (line ~192)

**Problem:** `Speak()` only tried `spd-say`. If not installed, it returned `False` with no explanation. VoiceService has a robust 4-engine fallback.

**Change:** Replaced single-engine TTS with a multi-engine fallback chain: `piper -> espeak -> espeak-ng -> pico2wave -> spd-say`. Each engine is tried in order; if one fails, the next is attempted. Added `_cleanup_after_tts()` helper for pico2wave temp file cleanup.

**Impact:** TTS now works across a much wider range of system configurations. Consistent behavior with VoiceService.

---

## Validation

| Check | Result |
|-------|--------|
| `py_compile voice_service.py` | Pass |
| `py_compile advanced_voice_service.py` | Pass |
| `ruff check` (both files) | All checks passed |
| No new imports added beyond existing deps | Verified |
| Existing code style preserved | Verified |

## Remaining items (not in scope, from SUMMARY.md)

- WARN-2: WHISPER_DIR.mkdir() and download_root for AdvancedVoiceService
- WARN-5: No Whisper model memory guard
- Informational: Expand `_NOISE_TRANSCRIPTS`, cache `shutil.which()` calls

These were not in the fix specification and remain as future improvements.
