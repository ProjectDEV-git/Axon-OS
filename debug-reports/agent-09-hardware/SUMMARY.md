# Agent 09: Hardware Profiling & Configuration — Summary

**Date**: 2026-07-02
**Files analyzed**: `hardware_profiler.py`, `brain_service.py`, `spaces_manager.py`, `clipboard_store.py`, test files

---

## Critical Bugs (3)

### 1. `spaces.json` saves are NOT atomic — data loss on crash
**File**: `apps/intent-bar/spaces_manager.py:93`
**Impact**: `write_text()` directly overwrites the file. A power loss or SIGKILL during write truncates the file, destroying all space data.
**Fix**: Write to `.tmp` then `Path.replace()`, matching the pattern already used in `brain_service.py:save_config()`.

### 2. Low-RAM systems get impossible model recommendations
**File**: `services/axon-brain/hardware_profiler.py:164-180`
**Impact**: Systems with 2-4 GB RAM are recommended `llama3.2:3b` (~2 GB model) or `qwen2.5:1.5b` (~1 GB). With OS overhead, these will OOM or thrash. The 8 GB default fallback masks this on systems where `/proc/meminfo` is unreadable.
**Fix**: Add `ram < 4.0` branch that recommends only `llama3.2:1b` models.

### 3. Intel Arc GPUs severely undersold
**File**: `services/axon-brain/hardware_profiler.py:90-96`
**Impact**: Intel Arc A770 (16 GB VRAM) is detected as "Intel Integrated Graphics" with 2.0 GB VRAM and `cpu_shared` status. User gets 3B models instead of 14B+.
**Fix**: Parse lspci model string for "Arc" keyword to distinguish discrete from integrated.

---

## Warnings (6)

| # | Area | Issue |
|---|------|-------|
| 1 | Config | Corrupted `config.toml` silently replaced with defaults — user customizations lost |
| 2 | Config | All TOML values serialized as strings — breaks if numeric config keys are added |
| 3 | Config | Incomplete TOML escaping (newlines, tabs not handled) |
| 4 | Config | No schema version for migration support in any of the 3 config stores |
| 5 | GPU | Multi-GPU systems only detect first GPU — VRAM is severely underreported |
| 6 | Spaces | No thread locking in `SpacesManager` — concurrent D-Bus calls can corrupt state |

---

## Recommendations Summary

### Priority 1: Fix data safety
- [ ] Make `spaces.json` saves atomic (tmp+rename)
- [ ] Add thread lock to `SpacesManager`
- [ ] Back up corrupted config files before overwriting

### Priority 2: Fix GPU detection
- [ ] Add RAM floor: systems < 4 GB RAM → 1B models only
- [ ] Add 1.5 GB VRAM safety margin at threshold boundaries
- [ ] Distinguish Intel Arc (discrete) from UHD/Iris (integrated)
- [ ] Distinguish AMD APU from discrete GPU
- [ ] Handle multi-GPU (aggregate VRAR or pick highest)

### Priority 3: Improve robustness
- [ ] Use `tomli_w` for TOML serialization
- [ ] Add hardware change detection and re-profile capability
- [ ] Add `schema_version` to config.toml and spaces.json
- [ ] Check disk space before recommending large models
- [ ] Add missing test cases (low RAM, Arc GPU, AMD APU, multi-GPU)

---

## Detailed Reports

- [01-gpu-detection.md](./01-gpu-detection.md) — GPU detection across vendors
- [02-config-persistence.md](./02-config-persistence.md) — Config persistence and atomicity
- [03-model-recommendations.md](./03-model-recommendations.md) — Model recommendation accuracy