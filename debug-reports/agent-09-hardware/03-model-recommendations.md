# Model Recommendation Accuracy Analysis

**File analyzed**: `services/axon-brain/hardware_profiler.py` — `profile_hardware()`, `recommend_models`
**Date**: 2026-07-02

---

## 1. Recommendation Logic Overview

The profiler generates three model tiers based on detected hardware:

| Tier | Purpose | Default Model |
|------|---------|---------------|
| `speed` | Quick commands, intent parsing | llama3.2:1b |
| `general` | Everyday chat, summaries | llama3.2:3b |
| `deep` | Code gen, complex reasoning | qwen2.5:7b |

Adjustments are made based on GPU vendor and VRAM:

| Condition | deep model | general model |
|-----------|-----------|---------------|
| NVIDIA/AMD, VRAM >= 12 GB | qwen2.5:14b | qwen2.5:7b |
| NVIDIA/AMD, VRAM >= 6 GB | llama3:8b | (unchanged) |
| NVIDIA/AMD, VRAM < 6 GB | llama3.2:3b | qwen2.5:1.5b |
| CPU, RAM >= 16 GB | llama3:8b | (unchanged) |
| CPU, RAM < 16 GB | llama3.2:3b | qwen2.5:1.5b |

The `speed` tier is never adjusted — it always recommends `llama3.2:1b`.

---

## 2. VRAM-to-Model Size Accuracy

### Reference: Ollama Model VRAM Usage (approximate)

| Model | Parameters | FP16 VRAM | Q4_K_M VRAM | Q8 VRAM |
|-------|-----------|-----------|-------------|--------|
| llama3.2:1b | 1B | ~2 GB | ~0.7 GB | ~1.1 GB |
| qwen2.5:1.5b | 1.5B | ~3 GB | ~1.0 GB | ~1.6 GB |
| llama3.2:3b | 3B | ~6 GB | ~2.0 GB | ~3.2 GB |
| qwen2.5:7b | 7B | ~14 GB | ~4.7 GB | ~7.5 GB |
| llama3:8b | 8B | ~16 GB | ~5.0 GB | ~8.5 GB |
| qwen2.5:14b | 14B | ~28 GB | ~9.0 GB | ~15 GB |

### Threshold Analysis

| VRAM Available | Recommended deep | Actual VRAM needed (Q4) | Verdict |
|----------------|------------------|------------------------|---------|
| 2-5 GB | llama3.2:3b | ~2.0 GB | **OK** — fits with headroom |
| 6-11 GB | llama3:8b | ~5.0 GB | **OK** — fits with headroom |
| 12+ GB | qwen2.5:14b | ~9.0 GB | **OK** — fits with headroom |

**The VRAM thresholds are reasonable for Q4 quantized models**, which is what Ollama uses by default. However:

| # | Severity | Issue |
|---|----------|-------|
| 1 | **MEDIUM** | **No awareness of quantization level** — The profiler assumes Q4 quantization (Ollama default). If a user pulls a Q8 or FP16 variant, VRAM requirements double or more. A 14B Q8 model needs ~15 GB — tight on a 12 GB GPU. Should check available VRAM with more margin or detect the quantization level. |
| 2 | **MEDIUM** | **No VRAM margin for OS/driver overhead** — NVIDIA drivers, desktop compositors, and background processes consume 0.5-2 GB of VRAM. A system with 6.0 GB reported VRAM might have only 4.0 GB free. The 6 GB threshold for llama3:8b (5 GB model) is dangerously tight. Recommend adding a 1.0-1.5 GB safety margin. |
| 3 | **LOW** | **No speed model adjustment** — `speed` is always `llama3.2:1b`. On a system with 24 GB VRAM, a slightly larger speed model (e.g., llama3.2:3b) could provide better intent classification with negligible latency impact. |

---

## 3. Edge Case Handling

### 2 GB System RAM

| Path | Behavior | Verdict |
|------|----------|--------|
| `/proc/meminfo` readable | Returns ~2.0 GB → CPU path → `ram < 16` → deep=3b, general=1.5b | **PROBLEM**: llama3.2:3b needs ~2 GB for the model alone, plus OS overhead. On a 2 GB system, this will likely OOM or swap thrash. Should recommend 1b models only. |
| `/proc/meminfo` not readable | Returns 8.0 GB fallback → CPU path → `ram >= 8 but < 16` → deep=3b | **PROBLEM**: Same issue. The 8 GB default is too generous — masks the reality of low-RAM systems. |

**BUG**: Systems with 2-4 GB RAM will get recommendations they cannot run.

### No GPU (CPU-only)

| Behavior | Verdict |
|----------|--------|
| Falls to CPU path. RAM >= 16 → 8B on CPU. RAM < 16 → 3B on CPU. | **OK** but misleading. CPU inference of 8B model takes 30-60 seconds per response. The description says "moderate response latency" but this is an understatement for interactive use. Should warn more explicitly. |

### Server-Class Hardware (Multi-GPU)

| Behavior | Verdict |
|----------|--------|
| Only first GPU detected (multi-GPU bug from Section 1). VRAM is single-GPU only. | **PROBLEM**: A 4x A100 (80 GB each) server would see only one A100 and cap at 14B. Should aggregate VRAM or detect multi-GPU setups. |

### GPU with 0 VRAM (driver error)

| Behavior | Verdict |
|----------|--------|
| `vram == 0` with vendor NVIDIA → enters the `vram < 6` branch → 3b/1.5b | **OK** — fails safe to small models. But status is `detected` which is misleading when VRAM is 0. Should be `error` or `driver_error`. |

### ARM / Raspberry Pi

| Behavior | Verdict |
|----------|--------|
| ARM CPUs won't have nvidia-smi/rocm-smi. lspci likely absent. Falls to CPU fallback with 8 GB default RAM (if /proc/meminfo works). Recommends 3B model. | **PROBLEM**: Many ARM SBCs have 1-4 GB RAM. 8 GB default is wrong for these. The /proc/meminfo path works on Linux ARM, but the recommendation may still be too aggressive. |

---

## 4. Profile Caching

### Current Behavior

There is **no runtime cache** of the hardware profile. Instead, the profile is used to generate the config file on first run (`config.toml`), and subsequent loads read from the config file directly:

```python
# brain_service.py load_config()
if CONFIG_FILE.exists():
    self.config = tomllib.load(f)  # Reads saved models
    if all keys present:
        return  # Skip re-profiling
# Only if config is missing/corrupt:
profile = hardware_profiler.profile_hardware()  # Re-profile
self.save_config()
```

### Assessment

| Check | Status | Notes |
|-------|--------|-------|
| First-run profiling | **OK** | Hardware is profiled and saved |
| Subsequent runs | **OK** | Config file is used, no re-profiling |
| Hardware change detection | **MISSING** | If user updates GPU driver, swaps GPU, or connects external GPU, the old config persists. No mechanism to detect hardware changes. |
| Manual re-profile | **MISSING** | No CLI command or config option to force re-profiling |

### Issues Found

| # | Severity | Issue |
|---|----------|-------|
| 1 | **MEDIUM** | **No hardware change detection** — After GPU driver update or hardware change, stale model recommendations persist. Should periodically re-profile (e.g., on service start, compare current hardware hash with saved one). |
| 2 | **MEDIUM** | **No forced re-profile mechanism** — User has no way to regenerate recommendations without manually deleting `config.toml`. Should provide a D-Bus method like `ReProfileHardware()`. |
| 3 | **LOW** | **Cache invalidation timing** — Even if hardware change were detected, the in-memory `self.config` in `BrainService` would need to be refreshed and the `AIRouter` re-initialized. This is not currently possible without restarting the service. |

---

## 5. Test Coverage Analysis

### What is tested

| Test | Coverage |
|------|----------|
| `get_system_ram()` — valid, invalid, empty, missing file | **Good** |
| `get_gpu_info()` — NVIDIA via nvidia-smi | **Good** |
| `get_gpu_info()` — AMD via rocm-smi | **Good** |
| `get_gpu_info()` — NVIDIA/AMD/Intel via lspci fallback | **Good** |
| `get_gpu_info()` — CPU fallback | **Good** |
| `profile_hardware()` — high VRAM NVIDIA | **Good** |
| `profile_hardware()` — low VRAM NVIDIA | **Good** |
| `profile_hardware()` — CPU with high/low RAM | **Good** |

### What is NOT tested

| Missing Test | Risk |
|-------------|------|
| 2 GB RAM → should recommend 1B models | High — the current code does NOT handle this correctly, and no test catches it |
| Multi-GPU NVIDIA → should aggregate VRAM | Medium |
| Intel Arc GPU → should detect discrete GPU | High — currently broken, no test |
| AMD APU → should detect as cpu_shared | High — currently broken, no test |
| nvidia-smi returns empty output | Medium |
| nvidia-smi returns error text (driver mismatch) | Low |
| VRAM exactly at boundary (6.0 GB, 12.0 GB) | Low |
| rocm-smi with non-standard output format | Medium |
| `profile_hardware()` with `speed` tier recommendations | Low — speed is never tested for changes |
| Corrupted config → fallback to re-profiling | Medium |

### Test Import Path Issue

```python
# test_hardware_profiler.py
import hardware_profiler  # Relative import via sys.path manipulation

# test_hardware_profiler_extended.py
from services.axon_brain.hardware_profiler import ...  # Absolute import
```

The two test files use different import strategies. The extended tests import via the package path (`services.axon_brain.hardware_profiler`), while the original tests use `sys.path` manipulation. This inconsistency could cause import failures depending on the test runner's working directory.

---

## 6. Recommendation Logic Gaps

### Missing: RAM Consideration for GPU Models

The `profile_hardware()` function checks VRAM for GPU recommendations but does NOT consider system RAM. Running a 14B model on a GPU with 12 GB VRAM but only 8 GB system RAM is fine (model loads into VRAM). But if the model partially offloads to CPU (e.g., due to context window), it needs system RAM too. Should verify `RAM >= VRAM + 2 GB`.

### Missing: Disk Space

Ollama models consume disk space. A 14B model is ~9 GB on disk. The profiler does not check available disk space. A recommendation to pull a model the user cannot store is poor UX.

### Missing: CPU Cores for CPU Inference

For CPU-only inference, the number of threads matters significantly. `/proc/cpuinfo` could provide core count. A 2-core system running llama3:8b on CPU will be extremely slow.

---

## 7. Recommendations

1. **[CRITICAL]** Add RAM floor check: systems with < 4 GB RAM should only get 1B models
2. **[HIGH]** Add 1.5 GB VRAM safety margin to prevent OOM on models at boundary thresholds
3. **[HIGH]** Detect Intel Arc as discrete GPU (check lspci model string for "Arc")
4. **[HIGH]** Detect AMD APU as cpu_shared (no rocm-smi = likely APU, not discrete)
5. **[MEDIUM]** Add hardware change detection on service start (hash GPU info, compare with saved)
6. **[MEDIUM]** Provide `ReProfileHardware()` D-Bus method
7. **[MEDIUM]** Check available disk space before recommending large models
8. **[LOW]** Adjust speed model based on available VRAM (larger model on high-end GPUs)
9. **[LOW]** Add test for 2 GB RAM edge case
10. **[LOW]** Unify test import strategy across test files