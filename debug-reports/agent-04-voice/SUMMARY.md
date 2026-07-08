# SUMMARY — Voice & Speech Pipeline Debug Report

**Agent:** Debug Agent 4
**Date:** 2026-07-02
**Files analyzed:** 7 source files across `services/axon-voice/`, `services/constants.py`, `services/service_utils.py`

---

## Critical Bugs (3)

### BUG-1: Recorder subprocess orphaned on service shutdown
**File:** `voice_service.py` line 450
**Severity:** Medium-High
**Impact:** On SIGTERM/SIGINT, the parecord/arecord subprocess is never killed. It holds the microphone device exclusively, preventing all future recordings and blocking other PulseAudio clients. Requires manual `kill` of the orphaned process.

```python
# CURRENT — broken
def _shutdown(signum, frame):
    loop.quit()

# FIX
def _shutdown(signum, frame):
    if service._recorder:
        try:
            service._recorder.kill()
            service._recorder.wait(timeout=2)
        except Exception:
            pass
    loop.quit()
```

### BUG-2: TOCTOU race condition on rapid toggle
**File:** `voice_service.py` line 88-97
**Severity:** Medium
**Impact:** Two rapid Super+V presses (before GLib processes the first idle callback) can both pass the `_recorder is None` check, queuing two `_start_recording` calls. The second overwrites `self._recorder` and `self._wav_path`, orphaning the first recorder subprocess and leaking the first temp file.

**Fix:** Claim the slot atomically inside the lock:
```python
def Toggle(self):
    with self._lock:
        if self._recorder is not None:
            GLib.idle_add(self._stop_and_process)
            return False
        if self._busy:
            return False
        self._busy = True  # claim immediately
    GLib.idle_add(self._start_recording)
    return True
```

### BUG-3: AdvancedVoiceService has no recording timeout
**File:** `advanced_voice_service.py` (entire recording flow)
**Severity:** Medium
**Impact:** Unlike VoiceService (which enforces `MAX_RECORD_SECONDS = 30` via GLib timer), AdvancedVoiceService has no maximum recording time. If `StopAndTranscribe()` is never called, recording continues indefinitely, consuming disk space (~1.9MB/min) and holding the microphone.

**Fix:** Add `GLib.timeout_add_seconds(MAX_RECORD_SECONDS, self._force_stop_and_transcribe)` in `_start_recording()`.

---

## Warnings (5)

### WARN-1: AdvancedVoiceService temp file leak on error
**File:** `advanced_voice_service.py` line 257-265
**Severity:** Low
**Impact:** If no recorder command is available, `self._wav_path` is set but never cleaned up.

### WARN-2: AdvancedVoiceService doesn't create WHISPER_DIR or set download_root
**File:** `advanced_voice_service.py` line 338-345
**Severity:** Medium
**Impact:** Model downloads go to HF default cache (~/.cache/huggingface/) instead of the AXON_DIR. Inconsistent with VoiceService. May cause double downloads.

### WARN-3: Vosk model recreated every call (AdvancedVoiceService)
**File:** `advanced_voice_service.py` line 361-390
**Severity:** Medium
**Impact:** Each Vosk transcription recreates the Model object (1-5 second overhead). VoiceService correctly caches its whisper model.

### WARN-4: AdvancedVoiceService TTS has no fallback chain
**File:** `advanced_voice_service.py` line 192-203
**Severity:** Medium
**Impact:** If spd-say is not installed, `Speak()` returns `False` with no notification or explanation to the user. VoiceService has a robust 4-engine fallback.

### WARN-5: No Whisper model memory guard
**File:** `voice_service.py` line 207, `advanced_voice_service.py` line 345
**Severity:** Low
**Impact:** Setting `AXON_WHISPER_MODEL=large-v3` uses ~3.5GB RAM with no warning. On a 30.6GB system this is feasible but leaves less room for other services.

---

## Informational Findings (6)

1. **File descriptors:** No leak from `tempfile.mkstemp` — both call sites properly close the fd immediately.

2. **Temp WAV cleanup:** Normal flow is correct (cleaned in `finally` block). Crash leaks are small (~960KB max) and cleaned by OS.

3. **VAD webrtcvad:** Robust with aggressiveness level 2. WAV header parsing is heuristic but functionally correct. Fixed RMS threshold (500.0) in fallback is reasonable.

4. **clean_transcript:** Well-designed but the noise set is incomplete. Missing common whisper hallucinations like "Thank you for watching", "Please subscribe". Non-English fillers are not filtered (acceptable for base.en model).

5. **parse_intent_response:** Well-defended against malformed JSON. Gracefully falls back to speech for any unparseable response. Minor UX issue: missing `app`/`command` fields result in raw JSON being spoken.

6. **GTK overlay:** Correctly manages timer lifecycle. Window is never destroyed (intentional reuse pattern). CSS provider is added once globally. No resource leaks.

---

## Recommendations (Priority Order)

### P0 — Fix immediately
1. **Fix shutdown handler** to kill recorder subprocess (BUG-1). Direct user impact, microphone device held.
2. **Fix TOCTOU race** on toggle (BUG-2). Add atomic state claim in Toggle().

### P1 — Fix soon
3. **Add recording timeout** to AdvancedVoiceService (BUG-3). Reuse `MAX_RECORD_SECONDS` pattern from VoiceService.
4. **Add WHISPER_DIR.mkdir()** and `download_root` to AdvancedVoiceService._transcribe_whisper (WARN-2).
5. **Add TTS fallback chain** to AdvancedVoiceService.Speak() (WARN-4).
6. **Cache Vosk model** in AdvancedVoiceService (WARN-3).

### P2 — Improve when convenient
7. Clean up temp file on AdvancedVoiceService record error (WARN-1).
8. Add large-model warning log (WARN-5).
9. Expand `_NOISE_TRANSCRIPTS` set with more whisper hallucinations.
10. Cache TTS engine availability check instead of calling `shutil.which()` on every TTS invocation.

---

## Files Modified (None — read-only audit)

All findings are in:
- `debug-reports/agent-04-voice/01-recording-lifecycle.md`
- `debug-reports/agent-04-voice/02-whisper-memory.md`
- `debug-reports/agent-04-voice/03-intent-tts.md`
- `debug-reports/agent-04-voice/SUMMARY.md` (this file)
