# Agent 09: Hardware Profiling & Configuration — Fix Report

**Date**: 2026-07-02
**Agent**: 09-hardware
**Status**: All 6 fixes applied and verified

---

## Fixes Applied

### FIX 1: Atomic writes for `spaces.json` (CRITICAL)
**File**: `apps/intent-bar/spaces_manager.py` — `_save()` method
**Before**: `SPACES_FILE.write_text(json.dumps(data, indent=2))` — direct overwrite, truncates on crash.
**After**: Writes to a temp file via `tempfile.mkstemp()`, then `Path.replace()` for atomic POSIX rename. Temp file is cleaned up on exception.

### FIX 2: Low-RAM model recommendations (CRITICAL)
**File**: `services/axon-brain/hardware_profiler.py` — `profile_hardware()` CPU fallback branch
**Before**: Systems with 2-4 GB RAM fell into the `else` branch recommending `llama3.2:3b` (~2 GB), which OOMs.
**After**: New `ram < 4.0` branch forces all three tiers (speed/general/deep) to `llama3.2:1b` (~1.3 GB) with explicit warning that deep reasoning is unavailable.

### FIX 3: Intel Arc discrete GPU detection (CRITICAL)
**File**: `services/axon-brain/hardware_profiler.py` — `get_gpu_info()` lspci fallback
**Before**: All Intel GPUs returned `{"vendor": "Intel", "model": "Intel Integrated Graphics", "vram": 2.0, "status": "cpu_shared"}`.
**After**: Parses the lspci model string for VGA/3D/Display controllers. If `"Arc"` is present, dispatches to a sub-table:
- Arc A770 → 16.0 GB
- Arc A750 → 8.0 GB
- Arc A380 → 6.0 GB
- Unknown Arc → 8.0 GB (conservative default)
- Non-Arc Intel → unchanged (integrated, 2 GB)

### FIX 4: Corrupted config backup (WARNING)
**File**: `services/axon-brain/brain_service.py` — `load_config()` exception handler
**Before**: Corrupted `config.toml` silently overwritten with hardware-profiled defaults.
**After**: Before replacement, copies corrupted file to `config.toml.bak` via `shutil.copy2()`. Logs both success and failure of the backup operation. Added `import shutil`.

### FIX 5: Thread locking for SpacesManager (WARNING)
**File**: `apps/intent-bar/spaces_manager.py` — entire `SpacesManager` class
**Before**: No synchronization. Concurrent D-Bus calls to `create_space`, `delete_space`, etc. could corrupt the `_spaces` dict.
**After**: Added `threading.Lock()` as `self._lock`. All reads and writes to `self._spaces` are wrapped in `with self._lock:`. Includes `_load`, `get_spaces`, `get_current_space`, `set_current_space`, `create_space`, `update_space`, `delete_space`, and `add_app_to_space`. Added `import threading`.

### FIX 6: Numeric TOML serialization (WARNING)
**File**: `services/axon-brain/brain_service.py` — `save_config()` method
**Before**: All config values serialized as quoted strings: `speed_model = "llama3.2:1b"` even for numbers like `context_length = "4096"`.
**After**: Type-dispatched serialization:
- `bool` → TOML bare `true`/`false`
- `int` / `float` → unquoted numeric literal
- `str` → quoted string (existing escape logic preserved)

---

## Files Modified

| File | Lines Before | Lines After | Changes |
|------|-------------|-------------|---------|
| `apps/intent-bar/spaces_manager.py` | 163 | 187 | Atomic save + thread lock |
| `services/axon-brain/hardware_profiler.py` | 190 | 228 | Arc GPU + low-RAM branch |
| `services/axon-brain/brain_service.py` | ~600 | ~607 | Config backup + numeric TOML |

## Validation

- **Syntax check**: All three files pass `py_compile.compile()` with no errors.
- **Unit validation**: Custom test script verified all 6 fixes:
  - FIX 1: Atomic write produces correct file; original survives failed write
  - FIX 2: 2GB/3GB RAM -> all 1B; 8GB -> 3B/1.5B; 16GB -> 8B; NVIDIA 24GB -> 14B
  - FIX 3: Arc A770->16GB, A750->8GB, A380->6GB, UHD 770->2GB integrated, unknown Arc->8GB
  - FIX 4: Corrupted config backed up to `.toml.bak` before replacement
  - FIX 5: 10 concurrent threads x 100 writes = 1000 entries, zero corruption
  - FIX 6: Strings quoted, numbers bare, bools as `true`/`false`
- **Regression tests**: `pytest tests/test_hardware_profiler.py tests/test_hardware_profiler_extended.py` — **18/18 passed**
- **Pre-existing issue**: `tests/test_brain_service.py` fails to import due to missing `service_base` module (unrelated to our changes)

## Remaining Warnings (Not Fixed — Out of Scope)

| # | Issue | Reason |
|---|-------|--------|
| 1 | Multi-GPU systems only detect first GPU | Requires architectural change to aggregate VRAM |
| 2 | AMD APU vs discrete GPU not distinguished | Requires additional lspci parsing |
| 3 | No schema version in config.toml or spaces.json | Design decision for migration framework |
| 4 | TOML escaping incomplete (newlines, tabs) | Needs `tomli_w` library adoption |
| 5 | No disk space check before model recommendations | Requires `shutil.disk_usage()` integration |
| 6 | No hardware change detection/re-profile | Feature addition, not a bug fix |
