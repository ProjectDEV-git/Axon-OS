# 03 — Intent Routing & Speech Output

**Agent:** Debug Agent 4 (Voice & Speech Pipeline)
**Date:** 2026-07-02
**Files analyzed:**
- `services/axon-voice/intent_router.py`
- `services/axon-voice/vad_helper.py`
- `services/axon-voice/overlay.py`
- `services/axon-voice/voice_service.py` (TTS, overlay, intent routing)
- `services/axon-voice/advanced_voice_service.py` (TTS)
- `services/service_utils.py` (safe_exec allowlist)

---

## 1. `parse_intent_response` Robustness

### Happy path
**Status: GOOD**
- JSON with `{"action": "open_app", "app": "firefox"}` → `("open_app", "firefox")`
- JSON with `{"action": "run_command", "command": "ls"}` → `("run_command", "ls")`
- Plain text → `("say", raw)` — spoken back to the user.

### Malformed JSON
**Status: GOOD**
- Non-JSON string (doesn't start with `{`) → `("say", raw)`. No crash.
- Truncated JSON → `json.JSONDecodeError` caught → `("say", raw)`. Graceful fallback.
- JSON that isn't a dict (e.g., `[1,2,3]`, `"hello"`, `42`) → `isinstance(data, dict)` is `False` → `("say", raw)`. Graceful.

### Edge cases
**Status: MINOR ISSUES**

1. **Missing `app`/`command` fields:** If the LLM returns `{"action": "open_app"}` without `data.get("app")`, the truthiness check `data.get("app")` returns `None` → falsy → falls through to `("say", raw)`. The user hears the raw JSON. Not great UX but not a crash.

2. **Extra fields:** `{"action": "open_app", "app": "firefox", "confidence": 0.9}` → works correctly, extra fields ignored.

3. **Action is wrong type:** `{"action": 123, "app": "firefox"}` → `action == "open_app"` is `False` (int vs str) → falls through to `("say", raw)`. Safe.

4. **`run_command` with empty string:** `{"action": "run_command", "command": ""}` → `data.get("command")` returns `""` → falsy → falls to `("say", raw)`. Safe (empty string command is rejected).

**Overall:** The function is well-designed for defensive parsing. No crash paths found.

---

## 2. `clean_transcript` Analysis

### Implementation
```python
def clean_transcript(text: str) -> str:
    cleaned = " ".join(text.split()).strip()
    if cleaned.lower() in _NOISE_TRANSCRIPTS:
        return ""
    return cleaned
```

### Normalization
**Status: GOOD**
- Collapses all whitespace (multiple spaces, tabs, newlines) into single spaces.
- Strips leading/trailing whitespace.

### Noise filtering
**Status: ADEQUATE with limitations**
- The `_NOISE_TRANSCRIPTS` set contains 10 common whisper hallucination patterns.
- Matching is case-insensitive (`.lower()` applied).

**Limitations:**
1. **Partial matches not handled:** "Thank you." matches exactly, but "Thank you" (no period) does NOT. Whisper can emit either form depending on context.
2. **Hallucination variants missing:** Common whisper hallucinations not in the set:
   - "Thank you for watching"
   - "Thanks for watching"
   - "Bye"
   - "Please subscribe"
   - "Thanks for watching this video"
   - "So" (common at start of empty recordings)
3. **Non-English handling:** The noise set is English-only. For multilingual usage (advanced_voice_service supports 10 languages), filler words in other languages are not filtered. This is acceptable given the default model is `base.en`.

### Over-stripping risk
**Status: LOW RISK**
- The function only removes whitespace and checks against a specific noise set. It does NOT strip punctuation, capitalization, or meaningful content.
- A genuine short utterance like "Hi" or "Yes" would NOT be filtered (not in the noise set).
- Risk: A user saying just "you" (single word) would be filtered as noise. This is unlikely but possible.

---

## 3. VAD (Voice Activity Detection) — `is_speech_wav`

### webrtcvad path
**Status: GOOD with caveats**
- Uses aggressiveness level 2 (moderate). This is a reasonable default.
- Processes 30ms frames (standard for webrtcvad).
- Returns `True` on the first speech frame detected — efficient.

**Caveats:**
1. **WAV header parsing is fragile:** The heuristic `raw.find(b"data")` could match the string "data" within the audio payload itself. If this happens, the PCM offset is wrong, and the first few bytes of audio are misinterpreted. In practice, this rarely causes a false negative because speech is usually present throughout the chunk.
2. **No validation of sample rate:** The function assumes 16kHz but doesn't verify the WAV header's sample rate. If a different sample rate is passed, webrtcvad will still run but may produce inaccurate results.
3. **Entire file loaded into memory:** `raw = p.read_bytes()` loads the entire WAV. For 1-second chunks (~32KB), this is fine. For longer files, it could be wasteful but is unlikely to be problematic in this codebase.

### RMS fallback path
**Status: ADEQUATE**
- Threshold: `rms > 500.0` (on 16-bit PCM scale of 0-32768).
- 500.0 corresponds to about 1.5% of full scale. This is a reasonable threshold for typical microphone gain.
- Uses only the first second of audio: `pcm[: sample_rate * 2]`.

**Limitations:**
1. **Fixed threshold:** No adaptation to different microphone gains. A quiet mic or high-gain mic could shift the operating range.
2. **Short clips:** Clips shorter than 1 second have fewer samples, making RMS noisier.
3. **Environmental noise:** A loud fan or AC unit could exceed the threshold, causing false positives. webrtcvad is much better at distinguishing speech from noise.

### Edge cases
1. **Empty WAV (header only):** PCM extraction yields empty bytes → `_rms_from_pcm(b"")` → `0.0` → `False`. Correct.
2. **Very short clip (<30ms):** webrtcvad loop has zero iterations → returns `False`. Speech in very short clips is not detected. Acceptable trade-off.
3. **Corrupt WAV:** `read_bytes()` succeeds but the RIFF parser finds garbage → falls through to treating the entire file as PCM → likely works correctly because the actual audio data is still there (just at a wrong offset, but webrtcvad/RMS still sees the energy).

---

## 4. TTS Fallback Chain

### VoiceService `_speak()` (line 270-333)
**Status: GOOD — Robust fallback chain**

Engine priority: `[configured engine] → piper → espeak → pico2wave → spd-say`

| Engine | Text limit | Temp file | Cleanup |
|---|---|---|---|
| piper | 1000 chars | No (stdout pipe) | N/A |
| espeak | 1000 chars | No (stdout pipe) | N/A |
| pico2wave | 1000 chars | Yes (.wav) | Thread-based cleanup with 30s timeout |
| spd-say | 500 chars | No (built-in playback) | N/A |

**If none are available (line 330-333):**
```python
log.warning("No TTS engine available (tried: %s)", ", ".join(candidates))
self._notify("Axon Voice", "No text-to-speech engine found. Install piper, espeak, or spd-say.")
```
The user gets a desktop notification explaining the issue. **Acceptable degradation** — voice output is missing but the response is still visible as a notification and shown in the overlay.

### AdvancedVoiceService `Speak()` (line 192-203)
**Status: ISSUE — No fallback, silent failure**
```python
def Speak(self, text):
    if not text or not shutil.which("spd-say"):
        return False
```
Only uses `spd-say`. If it's not installed, returns `False` with no notification. The caller may not know TTS failed.

**Fix:** Either use the same fallback chain as VoiceService, or at least log a warning.

### Duplicate TTS engine checking
**Status: MINOR ISSUE — `shutil.which()` called repeatedly**
In VoiceService `_speak()`, `shutil.which(eng)` is called for each candidate on every TTS invocation. This spawns a `which` process each time. For a frequently-used voice service, this adds unnecessary overhead.

**Recommendation:** Cache the available TTS engine at startup:
```python
def _find_tts_engine(self):
    for eng in ["piper", "espeak", "pico2wave", "spd-say"]:
        if shutil.which(eng):
            return eng
    return None
```

---

## 5. Overlay Rendering — GTK Resource Cleanup

### VoiceOverlay (`overlay.py`)
**Status: ACCEPTABLE**

**Resource lifecycle:**
1. **Build (line 58-103):** Creates a `Gtk.Window`, CSS provider, box layout, drawing area, label. Stored as instance attributes.
2. **Show (line 35-42):** Presents the window, starts the animation timer (`GLib.timeout_add(33, ...)`).
3. **Hide (line 49-54):** Stops the timer (`GLib.source_remove`), sets window invisible (`set_visible(False)`).
4. **Re-show:** Reuses the existing window. No re-creation needed.

**What's NOT cleaned up:**
- The `Gtk.Window` is never destroyed (`win.destroy()` never called). It remains in memory as an invisible window.
- The CSS provider (`Gtk.StyleContext.add_provider_for_display`) is added globally and never removed.
- The `_build()` method is called only once (guarded by `self._window is None` check), so there's no CSS provider accumulation.

**Impact:** Low. The window object persists but is invisible and consumes minimal resources. For a long-running daemon, this is standard practice.

**What COULD go wrong:**
- If the display server (Wayland/X11) disconnects and reconnects, the window handle becomes invalid. The overlay would fail silently (caught by the `except Exception` in `_show_overlay()`).
- If `Gtk.init_check()` fails at first call but succeeds later (e.g., display server starts late), the overlay would never initialize. The `self._overlay = None` reset in `_show_overlay()` allows retry on next show, which handles this.

### Animation timer cleanup
**Status: GOOD**
- Timer ID is tracked (`self._timer_id`).
- `hide()` removes the timer before hiding the window.
- `show()` only starts a timer if one isn't already running (`if not self._timer_id`).
- No timer leak possible.

### Thread safety
**Status: LOW RISK**
- `_on_tick()` runs on the GLib main loop thread.
- `set_status()` can be called from any thread via `GLib.idle_add()`.
- GTK is not thread-safe, but all GTK calls happen on the main thread (via `GLib.idle_add` in the voice service, and the timer callback is already on the main thread). The only cross-thread call is `self._area.queue_draw()` in `set_status()`, which is technically unsafe but in practice works because `queue_draw()` is a lightweight operation that GTK handles gracefully.

---

## 6. `safe_exec` for `run_command` Intent

**Status: DESIGN LIMITATION — Very restrictive allowlist**

When the intent router returns `("run_command", cmd)`, the command is executed via `safe_exec()` (from `service_utils.py`).

The allowlist (line 18-62) includes only ~40 basic system utilities:
`ls, cat, grep, find, echo, date, whoami, hostname, uname, df, du, free, uptime, ps, top, htop, pwd, wc, head, tail, sort, uniq, diff, file, stat, readlink, realpath, basename, dirname, nmcli, bluetoothctl, pactl, paplay, xdg-open, gtk-launch, gio, notify-send, zenity`

**Notably missing:**
- `firefox`, `chromium`, `google-chrome` (web browsers)
- `code` (VS Code)
- `nautilus`, `thunar`, `dolphin` (file managers)
- `kitty`, `alacritty`, `wezterm` (terminal emulators)
- `spotify`, `discord`, `slack` (popular apps)
- `python3`, `node` (intentionally excluded for security)

**Impact:** Voice commands like "open Firefox" or "open VS Code" would be classified as `open_app` (via the Brain), which uses `gtk-launch` (in the allowlist). But commands like "run Spotify" might come back as `run_command` if the Brain can't find a `.desktop` file, and would be **silently blocked**.

**Note:** The `open_app` path uses `gtk-launch` (in allowlist) directly, so desktop app launching works. The `run_command` path is really only for shell commands, and the restriction is intentional. This is an acceptable security trade-off.

---

## 7. D-Bus Brain Classification Timeout

**Status: MINOR ISSUE**
```python
return str(brain.ClassifyIntent(text, timeout=45))
```
The 45-second timeout for the Brain classification is quite generous. If Ollama is slow or the Brain service is overloaded, the user could wait up to 45 seconds before getting a response. During this time, the overlay shows "Transcribing on-device..." (stale status) and the service is in `_busy` state, blocking further voice interactions.

**Recommendation:** Reduce to 15-20 seconds, or provide progress feedback to the overlay.

---

## Summary Table

| Check | Status | Severity |
|---|---|---|
| parse_intent_response: malformed JSON | ✅ Good | — |
| parse_intent_response: missing fields | ⚠️ Minor UX | Low |
| clean_transcript: normalization | ✅ Good | — |
| clean_transcript: noise filtering | ⚠️ Incomplete | Low |
| clean_transcript: non-English | ⚠️ Limited | Low |
| VAD webrtcvad path | ✅ Good | — |
| VAD WAV header parsing | ⚠️ Fragile | Low |
| VAD RMS threshold | ⚠️ Fixed | Low |
| TTS fallback chain (VoiceService) | ✅ Good | — |
| TTS fallback (AdvancedVoice) | ❌ Bug | **Medium** |
| TTS no-engine notification | ✅ Good (VS) / ❌ Silent (AVS) | Medium |
| Overlay GTK cleanup | ✅ Acceptable | — |
| Overlay thread safety | ⚠️ Low risk | Low |
| safe_exec allowlist | ✅ Intentional | Low |
| Brain classification timeout | ⚠️ Generous | Low |
