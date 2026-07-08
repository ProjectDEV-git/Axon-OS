# 02 — Whisper Model Loading & Memory Management

**Agent:** Debug Agent 4 (Voice & Speech Pipeline)
**Date:** 2026-07-02
**Files analyzed:**
- `services/axon-voice/voice_service.py` (lines 200-213)
- `services/axon-voice/advanced_voice_service.py` (lines 338-407)
- `services/constants.py` (WHISPER_DIR definition)

---

## 1. WhisperModel Lazy Loading

### VoiceService (`_load_whisper`, line 201-213)
**Status: GOOD**
```python
def _load_whisper(self):
    if self._whisper is not None:
        return self._whisper
    from faster_whisper import WhisperModel  # lazy import
    WHISPER_DIR.mkdir(parents=True, exist_ok=True)
    self._whisper = WhisperModel(
        WHISPER_MODEL,
        device="cpu",
        compute_type="int8",
        download_root=str(WHISPER_DIR),
    )
    return self._whisper
```
- Lazy import of `faster_whisper` avoids heavy import at module load time.
- Model is loaded only on first transcription. Subsequent calls reuse the cached instance.
- `WHISPER_DIR.mkdir(parents=True, exist_ok=True)` ensures the directory exists before download.

### AdvancedVoiceService (`_transcribe_whisper`, line 338-359)
**Status: GOOD with note**
```python
if self._whisper_model is None:
    model_size = os.environ.get("AXON_WHISPER_MODEL", "base.en")
    self._whisper_model = WhisperModel(model_size, device="cpu", compute_type="int8")
```
- Also lazy-loaded. Falls back to `_transcribe_cli()` on `ImportError` or any other exception.
- **Note:** Does NOT pass `download_root=str(WHISPER_DIR)`. The model will be downloaded to faster-whisper's default location (`~/.cache/huggingface/hub/`), not to the AXON_DIR. This is inconsistent with VoiceService and may cause the same model to be downloaded twice if both services are used.

---

## 2. Load Failure Handling

### VoiceService
**Status: ACCEPTABLE**
- If `_load_whisper()` raises (e.g., model download fails, disk full, incompatible CPU), the exception propagates to `_transcribe_and_route()`:
  ```python
  try:
      model = self._load_whisper()
      segments, _info = model.transcribe(wav_path, beam_size=1)
      text = clean_transcript(" ".join(s.text for s in segments))
  except Exception as exc:
      error = f"{exc}"
  ```
- The error is reported to the user via notification: `"Transcription failed: {error}"`.
- The temp WAV file is still cleaned up (in the `finally` block).
- **However:** The `_whisper` attribute remains `None`, so every subsequent transcription attempt will retry loading. This is actually desirable — transient failures (e.g., network issue during download) will be retried.

### AdvancedVoiceService
**Status: GOOD**
- Falls back through multiple engines: whisper → CLI fallback → returns empty string.
- Better degradation than VoiceService.

---

## 3. Memory Limits for Whisper Model

**Status: WARNING — No explicit memory limit**

| Model | Size | RAM Usage (int8) |
|---|---|---|
| `tiny.en` | 75 MB | ~100 MB |
| `base.en` (default) | 150 MB | ~200 MB |
| `small.en` | 500 MB | ~600 MB |
| `medium` | 1.5 GB | ~1.8 GB |
| `large-v3` | 3 GB | ~3.5 GB |

The default `base.en` model is reasonable at ~200MB RAM. However:
- Users can set `AXON_WHISPER_MODEL=large-v3` which would use ~3.5GB RAM.
- The system has 30.6GB total RAM, so even the large model is feasible but leaves less room for other services.
- **No runtime memory check** before loading — if the system is under memory pressure, the model load could trigger OOM killer.

**Recommendation:** Add a sanity check or at least a warning log when the selected model is large:
```python
if WHISPER_MODEL in ("large", "large-v1", "large-v2", "large-v3"):
    log.warning("Whisper model '%s' uses ~3.5GB RAM. Consider 'base.en' for lower memory.", WHISPER_MODEL)
```

---

## 4. Long Recordings & Memory

**Status: LOW RISK**
- MAX_RECORD_SECONDS caps recording at 30 seconds → ~960KB WAV at 16kHz mono.
- The transcription generator (`model.transcribe()`) processes segments lazily, but the code materializes all text with `" ".join(s.text for s in segments)`.
- For 30 seconds of audio, the transcript is at most ~200 words / ~1KB. No memory concern.
- **If MAX_RECORD_SECONDS were increased** (e.g., to 300s), the transcript would still be small, but the audio file would be ~9.6MB and transcription CPU time would increase.

---

## 5. WHISPER_DIR Creation and Writability

### VoiceService
**Status: GOOD**
```python
WHISPER_DIR.mkdir(parents=True, exist_ok=True)
```
Called in `_load_whisper()` before model loading. Properly creates the full directory tree.

### AdvancedVoiceService
**Status: BUG — Missing directory creation**
`_transcribe_whisper()` does NOT call `WHISPER_DIR.mkdir(...)`. It passes no `download_root` to WhisperModel, so the model goes to faster-whisper's default cache. This means:
1. The model may not be in the expected AXON_DIR location.
2. If the user manually set up a model in WHISPER_DIR, AdvancedVoiceService won't find it (it uses the default HF cache instead).
3. The Vosk model path `WHISPER_DIR / "vosk" / self._language` is never explicitly created.

---

## 6. Model Cleanup / Unloading

**Status: WARNING — No explicit unloading**
- The `_whisper` attribute holds a reference to the `WhisperModel` for the lifetime of the `VoiceService`/`AdvancedVoiceService` object.
- There is no `unload_model()`, `release_model()`, or `__del__` method.
- The model memory is only freed when the process exits.
- **Impact:** Acceptable for a long-running daemon. The model is loaded once and reused.
- **Missing capability:** No way to reload the model (e.g., if the user switches models at runtime via env var). The current model persists until restart.

**Recommendation:** Consider adding a `ReloadModel` D-Bus method:
```python
@dbus.service.method("org.axonos.Voice", in_signature="s", out_signature="b")
def ReloadModel(self, model_name):
    """Unload current model and load a new one."""
    with self._lock:
        self._whisper = None
    # Next transcription will load the new model
    os.environ["AXON_WHISPER_MODEL"] = model_name
    return True
```

---

## 7. Thread Safety of Model Loading

**Status: LOW RISK**
- `_load_whisper()` is called from `_transcribe_and_route()`, which runs in a daemon thread (line 194).
- There is a theoretical race if two transcriptions are triggered simultaneously (possible via rapid toggle or ambient loop). Both threads could see `_whisper is None` and both attempt to load the model.
- **Impact:** Low. The second load would overwrite `_whisper`, and Python's GIL prevents true data corruption. The worst case is loading the model twice and wasting memory temporarily.
- `self._busy` flag prevents overlapping transcriptions in VoiceService (line 94-95), but the ambient loop (line 425) spawns transcription threads independently.

---

## 8. AdvancedVoiceService — Additional Observations

### Vosk Model Loading (line 361-390)
**Status: ISSUE — Model recreated every call**
```python
def _transcribe_vosk(self, file_path: str) -> str:
    ...
    model = Model(model_path)  # ← new model instance every time!
```
Unlike whisper (which caches `self._whisper_model`), the Vosk model is created fresh on every transcription call. Vosk model loading can take 1-5 seconds. This means every Vosk transcription incurs the full model loading cost.

**Fix:** Cache the Vosk model like whisper:
```python
if self._vosk_model is None:
    self._vosk_model = Model(model_path)
rec = KaldiRecognizer(self._vosk_model, wf.getframerate())
```

### CLI Transcription Fallback (line 392-407)
**Status: ISSUE — Temp file not cleaned up**
```python
def _transcribe_cli(self, file_path: str) -> str:
    result = subprocess.run(
        ["whisper", file_path, "--language", self._language, "--output_format", "txt"],
        ...
    )
    txt_file = Path(file_path).with_suffix(".txt")
    if txt_file.exists():
        return txt_file.read_text().strip()
    return result.stdout.strip()
```
The CLI `whisper` command creates a `.txt` file alongside the input file. This file is **never cleaned up**. After multiple fallback transcriptions, stale `.txt` files accumulate.

---

## Summary Table

| Check | Status | Severity |
|---|---|---|
| Lazy loading (VoiceService) | ✅ Good | — |
| Lazy loading (AdvancedVoice) | ✅ Good | — |
| Load failure handling (VoiceService) | ✅ Acceptable | — |
| Load failure handling (AdvancedVoice) | ✅ Good | — |
| Memory limit for model | ⚠️ Warning | Low |
| Long recording memory | ✅ Low risk | — |
| WHISPER_DIR creation (VoiceService) | ✅ Good | — |
| WHISPER_DIR creation (AdvancedVoice) | ❌ Bug | **Medium** |
| Model unloading/cleanup | ⚠️ Warning | Low |
| Thread safety of loading | ⚠️ Low risk | Low |
| Vosk model caching | ❌ Bug | **Medium** |
| CLI fallback .txt cleanup | ❌ Bug | Low |
