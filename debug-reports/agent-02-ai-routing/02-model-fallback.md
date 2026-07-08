# Model Selection Fallback Chains

**Files analyzed:** `services/axon-brain/ai_router.py`, `services/axon-brain/brain_service.py`, `services/constants.py`
**Date:** 2026-07-02

---

## 1. Model Configuration Flow

### Config Loading (brain_service.py:load_config)

```
CONFIG_FILE = ~/.local/share/axon/config.toml
```

1. If `config.toml` exists, load it via `tomllib.load()`
2. Verify all three keys exist: `speed_model`, `general_model`, `deep_model`
3. If any key is missing, fall through to profiling
4. If file doesn't exist or load fails, run `hardware_profiler.profile_hardware()` and save defaults

### AIRouter Initialization (ai_router.py:__init__)

```python
self._speed_model  = config.get("speed_model",  "llama3.2:3b")
self._general_model = config.get("general_model", "mistral:7b")
self._deep_model   = config.get("deep_model",    "qwen2.5:7b")
self._embed_model  = "nomic-embed-text"  # hardcoded, no config fallback
```

---

## 2. Fallback Chain Analysis

### Scenario A: Config file has all three keys
- AIRouter reads `speed_model`, `general_model`, `deep_model` from config dict
- **Result:** Correct. All three models are set from config.

### Scenario B: Config file is missing one or more keys
- `brain_service.py:load_config()` checks `all(k in self.config for k in (...))` at line 123-125
- If ANY key is missing, it re-profiles hardware and saves defaults
- **BUT:** `AIRouter.__init__()` also has its own defaults via `config.get()` with fallbacks
- **Issue:** The two-level fallback means `load_config()` might set config to hardware-profiled values (which could differ from AIRouter defaults), while AIRouter also has hardcoded defaults. This is **redundant but not buggy** -- the profiled values will always be present because `load_config()` guarantees the keys exist before returning.

### Scenario C: Config file exists but has extra/misspelled keys
- `load_config()` only checks for the presence of the three required keys
- If config has `speed_model` and `general_model` but not `deep_model`, it re-profiles EVERYTHING (not just the missing key)
- **Potential issue:** A typo like `depp_model` would cause a full re-profile, overwriting all correct values

### Scenario D: hardware_profiler fails
- If `hardware_profiler.profile_hardware()` raises an exception, `load_config()` has no try/except around the profiling path
- **CRITICAL BUG:** An unhandled exception in `hardware_profiler` would crash the Brain service on startup. No graceful degradation to hardcoded defaults.

### Scenario E: `_resolve_model` (NOT FOUND)
- There is **no `_resolve_model` method** anywhere in the codebase. The search returned 0 matches.
- Model resolution happens at two levels:
  1. `AIRouter.__init__()`: config dict -> hardcoded defaults
  2. `BrainService.load_config()`: file -> hardware profiler -> config dict
- **Finding:** The fallback chain is simpler than expected -- there is no `_resolve_model`. The name was referenced in the task description but does not exist.

---

## 3. Model Existence Validation

### Problem: No validation that selected model exists in Ollama

The router returns model names like `"llama3.2:3b"`, `"mistral:7b"`, `"qwen2.5:7b"`. These are then passed to Ollama's `/api/generate` or `/api/chat` endpoints.

**What happens if the model isn't pulled?**

1. `BrainService.Generate()` calls `self.router.select_model()` -> returns model name
2. Calls `_do_generate_sync()` or `_do_generate_stream()` with that model name
3. Ollama's API returns an error (model not found)
4. Error is caught by the broad `except Exception` and logged
5. Returns `"[Error: AI generation unavailable]"` or fires `GenerationCompleted(tx_id, False, ...)`

**Issue:** There's **no pre-flight check** that the selected model is actually available. The user gets a generic error message with no indication that the model needs to be pulled.

**Missing feature:** `GetStatus()` method (line 197-209) already fetches `active_models` from Ollama. This could be used to validate the selected model, but it's never called during model selection.

---

## 4. Embedding Model Fallback Bug

### GetEmbeddings default model is WRONG

In `brain_service.py:GetEmbeddings()` (line 417-449):

```python
def GetEmbeddings(self, prompt, model):
    if not model:
        # Try speed model, general model or default nomic-embed-text
        model = self.config.get("speed_model", "nomic-embed-text")
```

**CRITICAL BUG:** When no model is specified, it falls back to `speed_model` (e.g., `llama3.2:3b`), NOT an embedding model! The comment says "Try speed model, general model or default nomic-embed-text" but the code only checks `speed_model`.

- `speed_model` is a language model, not an embedding model
- Passing a language model to `/api/embed` will fail or produce nonsensical embeddings
- The intended fallback should be `self.config.get("embedding_model", "nomic-embed-text")` but `embedding_model` is never set in config (it's hardcoded in AIRouter)

**Correct fallback should be:**
```python
model = self.config.get("embedding_model", "nomic-embed-text")
```

Or simply:
```python
model = "nomic-embed-text"
```

---

## 5. Model Tier Consistency

### AIRouter model tiers (constants)
```
SPEED    = "speed"     -> self._speed_model    (default: "llama3.2:3b")
GENERAL  = "general"   -> self._general_model   (default: "mistral:7b")
DEEP     = "deep"      -> self._deep_model      (default: "qwen2.5:7b")
EMBEDDING = "embedding" -> self._embed_model     (default: "nomic-embed-text")
```

### Config keys used
- `config["speed_model"]` -> AIRouter reads it
- `config["general_model"]` -> AIRouter reads it
- `config["deep_model"]` -> AIRouter reads it
- `config["embedding_model"]` -> **NEVER SET in config, NEVER READ from config**

### Consistency issues:
1. **Embedding model is hardcoded** in AIRouter (`"nomic-embed-text"`) and never configurable
2. **GetEmbeddings uses `speed_model` as fallback** instead of embedding model
3. **get_model_for_chat()** uses `message_length > 500` to decide deep vs general -- this is a different threshold than the router's `len(text) > 200` for deep classification
4. **get_model_for_generate()** calls `classify_task()` but without context parameter (unlike `select_model` which passes context)

---

## 6. Singleton Behavior

```python
_router: AIRouter | None = None
_router_lock = threading.Lock()

def get_router(config: dict | None = None) -> AIRouter:
    global _router
    if _router is None:
        with _router_lock:
            if _router is None:
                _router = AIRouter(config)
    return _router
```

**Issue:** The singleton is created with the FIRST config passed to `get_router()`. If config changes later (e.g., user updates model settings), the router is NOT recreated. `BrainService.__init__()` calls `AIRouter(self.config)` directly, bypassing the singleton, so the singleton behavior is only relevant if other services call `get_router()`.

**Potential bug:** If `BrainService` and another service both create routers, they'll have different config states. The singleton is not used by BrainService itself (line 97: `self.router = AIRouter(self.config)`), so it only affects other callers.

---

## 7. Summary of Bugs

| # | Severity | Description |
|---|----------|-------------|
| 1 | **CRITICAL** | `GetEmbeddings` falls back to `speed_model` instead of embedding model |
| 2 | **HIGH** | No pre-flight model existence check; user gets generic error |
| 3 | **HIGH** | `load_config()` has no try/except around `hardware_profiler.profile_hardware()` -- crash on failure |
| 4 | **MEDIUM** | Embedding model is not configurable via config file |
| 5 | **MEDIUM** | Config typo (e.g., `depp_model`) causes full re-profile, overwriting correct values |
| 6 | **LOW** | `get_model_for_generate()` doesn't pass context to `classify_task()` |
| 7 | **LOW** | Singleton `get_router()` is not used by BrainService, creating inconsistency |

---

## 8. Recommendations

1. **Fix GetEmbeddings fallback** to use embedding model, not speed model
2. **Add model validation** in `select_model()`: check against `GetStatus()` active_models list
3. **Wrap `hardware_profiler.profile_hardware()` in try/except** with hardcoded fallback defaults
4. **Add `embedding_model` to config** for user configurability
5. **Add config key validation** with warning for unrecognized keys
6. **Unify deep thresholds**: `get_model_for_chat` (>500) vs `classify_task` (>200) use different boundaries
