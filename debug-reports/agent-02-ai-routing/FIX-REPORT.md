# AI Routing Fix Report

**Agent:** Agent 02 — AI Routing & Context Transport
**Date:** 2026-07-02
**Status:** All 7 fixes applied and verified

---

## Files Modified

| File | Fixes Applied |
|------|--------------|
| `apps/axon-ai-panel/ui/panel.py` | FIX 1a |
| `apps/intent-bar/ollama_client.py` | FIX 1b |
| `services/axon-brain/ai_router.py` | FIX 2, 3, 4, 5, 6, 7 |
| `tests/test_phase4.py` | Test updated for FIX 4 |

---

## Fix Details

### FIX 1: Context is never passed to Brain Service (CRITICAL) — DONE

**Root cause:** `_stream_response()` received `ctx` as a parameter but never forwarded it. `OllamaClient.send_message_stream()` hardcoded `""` in the D-Bus call.

**Changes:**
- **panel.py:675** — Added `ctx=ctx` to `send_message_stream()` call
- **ollama_client.py:166** — Added `ctx: str = ""` parameter to `send_message_stream()`
- **ollama_client.py:176** — Changed `brain.SendMessage(conversation_id, message, "", model, True)` to pass `ctx` instead of `""`

**Impact:** Context-aware routing is now functional end-to-end: `ContextReader` → `panel._stream_response()` → `OllamaClient.send_message_stream()` → `Brain.SendMessage()` → `AIRouter.select_model()`.

---

### FIX 2: classify_task() ignores context parameter (CRITICAL) — DONE

**Root cause:** `classify_task(prompt, context="")` accepted `context` but never read it in the method body.

**Changes in ai_router.py:**
- Added context-based override in the speed pattern check: when context contains code editor keywords (`code`, `vim`, `neovim`, `vscode`, `terminal`), speed tasks are upgraded to DEEP
- Added context-based override in the length fallback (`< 15 chars`): same code editor keywords upgrade to DEEP
- Added context-based adjustment for GENERAL fallback: document/browser keywords keep GENERAL classification

**Impact:** A prompt like "run ls" typed while VSCode is focused will now route to the DEEP model instead of SPEED.

---

### FIX 3: Code pattern word count gate too strict (WARNING) — DONE

**Root cause:** `len(text.split()) > 5` prevented short code requests like "fix the bug" (4 words) from routing to DEEP.

**Change:** Lowered threshold from `> 5` to `> 3` in `classify_task()`.

**Impact:** 4-word code requests now correctly route to DEEP model.

---

### FIX 4: Single embedding keywords cause false routing (WARNING) — DONE

**Root cause:** Bare words like "find" or "search" (1 word) immediately triggered embedding routing via set intersection with `_EMBEDDING_KEYWORDS`.

**Change:** Added early guard: `if len(text.split()) < 3: pass` — prompts with fewer than 3 words skip the embedding keyword check entirely.

**Impact:** Single words ("search", "find") and 2-word prompts ("search files") no longer false-route to embedding. Requires 3+ words for embedding keyword matching.

**Note:** Updated `tests/test_phase4.py` assertion for `"search files"` from `"embedding"` to `"speed"` to match the new guard.

---

### FIX 5: Duplicate "debug" in _CODE_PATTERNS (WARNING) — DONE

**Root cause:** Copy-paste error in regex: `(fix|debug|debug|patch|...)`.

**Change:** Removed duplicate `debug` from the pattern on line 58.

---

### FIX 6: Embedding model not configurable (WARNING) — DONE

**Root cause:** `self._embed_model = "nomic-embed-text"` was hardcoded, ignoring user config.

**Change:** Changed to `self._config.get("embedding_model", "nomic-embed-text")` — respects `embedding_model` key in config with fallback.

---

### FIX 7: Inconsistent deep thresholds (WARNING) — DONE

**Root cause:** `classify_task()` used `len(text) > 200` for DEEP, while `get_model_for_chat()` used `message_length > 500`. Same prompt could route differently depending on code path.

**Change:** Unified `get_model_for_chat()` threshold from `> 500` to `> 200` to match `classify_task()`.

---

## Test Results

```
tests/test_ai_router.py:  28 passed
tests/test_phase4.py:     17 passed, 1 failed (pre-existing, unrelated)
  FAILED: TestAdvancedVoice.test_import — NameError in axon-voice (out of scope)
```

**Zero regressions from applied fixes.**

---

## Not Fixed (Out of Scope per Constraints)

These issues from the debug report were not in the assigned fix scope:

- **C3:** GetEmbeddings falls back to wrong model (in `brain_service.py`, not assigned)
- **W3:** No model existence validation before inference
- **W4:** hardware_profiler failure crashes Brain service
- **W7:** Config typo causes full re-profile

---

## Verification

1. All 28 `test_ai_router.py` tests pass
2. All 17 `test_phase4.py` router tests pass
3. Manual review of edited code confirms correctness
4. No modifications outside `apps/axon-ai-panel/`, `apps/intent-bar/`, `services/axon-brain/ai_router.py`, and the test file update
