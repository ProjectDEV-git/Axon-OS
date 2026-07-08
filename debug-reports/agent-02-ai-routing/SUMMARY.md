# AI Routing Debug Summary

**Debug Agent:** Agent 02 - AI Routing & Model Selection
**Project:** Axon OS (`/home/hxshin/projects/Axon-OS`)
**Date:** 2026-07-02
**Files analyzed:**
- `services/axon-brain/ai_router.py` (211 lines)
- `services/axon-brain/brain_service.py` (593 lines)
- `apps/axon-ai-panel/context_reader.py` (102 lines)
- `apps/axon-ai-panel/ui/panel.py` (700+ lines)
- `apps/intent-bar/ollama_client.py` (230 lines)
- `services/constants.py` (51 lines)
- `tests/test_ai_router.py` (124 lines)

---

## Critical Bugs (Fix Immediately)

### C1: Context Is Never Passed to Brain Service
**File:** `apps/axon-ai-panel/ui/panel.py:666-669`
**File:** `apps/intent-bar/ollama_client.py:176`

The AI panel builds context via `ContextReader.build_context_string()` but it is **never forwarded** to the Brain service. `_stream_response()` receives `ctx` as a parameter but never passes it to `send_message_stream()`. The `OllamaClient` then hardcodes context as `""` in the D-Bus call. **Context-aware routing is completely non-functional at the transport level.**

**Fix:** Add `ctx` parameter to `send_message_stream()` and pass it through to `brain.SendMessage()`.

### C2: classify_task() Ignores Context Parameter
**File:** `services/axon-brain/ai_router.py:131-165`

The `context` parameter in `classify_task(prompt, context="")` is **accepted but never read** in the method body. The entire classification logic operates only on `prompt`. This is unimplemented functionality masquerading as a working feature.

**Fix:** Implement context-based routing adjustments (e.g., boost code classification when a code editor is detected).

### C3: GetEmbeddings Falls Back to Wrong Model
**File:** `services/axon-brain/brain_service.py:421`

When no model is specified, `GetEmbeddings()` falls back to `speed_model` (a language model like `llama3.2:3b`) instead of an embedding model. Passing a language model to `/api/embed` will fail or produce nonsensical results.

**Fix:** Change `self.config.get("speed_model", "nomic-embed-text")` to `"nomic-embed-text"` or add `embedding_model` to config.

---

## Warnings (Fix Before Release)

### W1: Code Pattern Word Count Gate Too Strict
**File:** `services/axon-brain/ai_router.py:145`

Code patterns require `> 5 words` to trigger DEEP routing. Short code requests like "fix the bug" (4 words) or "refactor my API module" (4 words) bypass code detection and route to SPEED via the length fallback. This causes incorrect model selection for common code tasks.

**Fix:** Lower threshold to `> 3 words` or remove the word count gate.

### W2: Single Embedding Keywords Cause False Embedding Routing
**File:** `services/axon-brain/ai_router.py:139-141`

Bare words like "find" or "search" (1 word, < 10 word limit) immediately route to the EMBEDDING model. Users typing "search" expect a web search, not a vector embedding operation.

**Fix:** Require embedding keywords to appear with related context words (e.g., "search for", "find similar", "embed this").

### W3: No Model Existence Validation
**Files:** `services/axon-brain/ai_router.py`, `services/axon-brain/brain_service.py`

The router returns model names (e.g., `qwen2.5:7b`) without checking if they're actually pulled in Ollama. Failed inference produces a generic error with no actionable guidance for the user.

**Fix:** Add pre-flight check against `GetStatus()` active_models list, or auto-suggest pulling the model.

### W4: hardware_profiler Failure Crashes Brain Service
**File:** `services/axon-brain/brain_service.py:131`

`load_config()` calls `hardware_profiler.profile_hardware()` without try/except. If profiling fails (e.g., unsupported hardware, missing libraries), the entire Brain service crashes on startup.

**Fix:** Wrap in try/except with hardcoded default model fallback.

### W5: Duplicate "debug" in _CODE_PATTERNS
**File:** `services/axon-brain/ai_router.py:58`

`(fix|debug|debug|patch|...)` -- "debug" appears twice. Harmless but indicates copy-paste error.

### W6: Embedding Model Not Configurable
**File:** `services/axon-brain/ai_router.py:99`

`self._embed_model = "nomic-embed-text"` is hardcoded and cannot be overridden via config. Users with different embedding models (e.g., `mxbai-embed-text`) have no way to configure this.

### W7: Config Typo Causes Full Re-profile
**File:** `services/axon-brain/brain_service.py:123-136`

If config has a typo (e.g., `deep_modle` instead of `deep_model`), `load_config()` treats it as a missing key and re-profiles hardware, overwriting all correct values without warning.

### W8: Inconsistent Deep Thresholds
**Files:** `ai_router.py:161` vs `brain_service.py (get_model_for_chat)`

`classify_task()` uses `len(text) > 200` for DEEP, while `get_model_for_chat()` uses `message_length > 500`. Different code paths may route the same prompt to different models.

---

## Recommendations (Priority Order)

### Immediate (P0)
1. **Fix context transport:** Wire `ctx_string` through `send_message_stream()` -> `brain.SendMessage()`
2. **Fix GetEmbeddings fallback:** Use `nomic-embed-text`, not `speed_model`
3. **Add try/except around hardware_profiler** in `load_config()`

### Short-term (P1)
4. **Lower code gate** from `> 5` to `> 3` words in `classify_task()`
5. **Implement context-aware routing** in `classify_task()` using window/app context
6. **Add model validation** before inference (check Ollama availability)
7. **Add embedding keyword intent disambiguation** (e.g., "search" alone vs "search for similar code")

### Medium-term (P2)
8. **Add `embedding_model` to config** for user configurability
9. **Add context sanitization** for routing decisions (prevent injection via window titles)
10. **Add config key validation** with warnings for unrecognized/misspelled keys
11. **Unify deep thresholds** across all routing paths
12. **Add ContextReader caching** with TTL to reduce D-Bus overhead

### Long-term (P3)
13. **Add telemetry/metrics** for routing decisions (track accuracy, false positives)
14. **Implement learning routing** based on user feedback/corrections
15. **Add test coverage** for edge cases (empty strings, Unicode, injection attempts, context-aware scenarios)

---

## Test Coverage Assessment

The existing test suite (`tests/test_ai_router.py`) covers basic happy paths:
- Simple speed/general/deep/embedding classification (14 tests)
- Model selection (5 tests)
- Helper methods (9 tests)
- Singleton behavior (1 test)

**Missing test coverage:**
- Empty/whitespace input
- Unicode/multilingual input
- Very long prompts
- Context parameter behavior
- Short code-fix requests ("fix the bug")
- Single embedding keywords
- Config edge cases (missing keys, extra keys, typos)
- Model fallback chains when Ollama is unavailable
- Thread safety of singleton router

---

## Files Modified
None (debug-only analysis).

## Files Created
- `debug-reports/agent-02-ai-routing/01-pattern-matching.md`
- `debug-reports/agent-02-ai-routing/02-model-fallback.md`
- `debug-reports/agent-02-ai-routing/03-context-routing.md`
- `debug-reports/agent-02-ai-routing/SUMMARY.md` (this file)
