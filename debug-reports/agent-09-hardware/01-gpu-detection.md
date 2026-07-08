# GPU Detection Analysis — Hardware Profiler

**File analyzed**: `services/axon-brain/hardware_profiler.py` — `get_gpu_info()`
**Date**: 2026-07-02

---

## 1. NVIDIA Detection

### How it works
- Checks for `nvidia-smi` binary via `shutil.which()`
- Runs `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits`
- Parses the first line of CSV output for model name and VRAM in MB

### Strengths
- Uses NVIDIA's official CLI, which works headless (no display server needed)
- CSV format is machine-readable and stable across driver versions
- nvidia-smi binary presence reliably indicates driver installation

### Issues Found

| # | Severity | Issue |
|---|----------|-------|
| 1 | **HIGH** | **Multi-GPU: only reads `split("\n")[0]`** — On multi-GPU systems (e.g., RTX 4090 + RTX 3060), only the first GPU is detected. A server with 4x A100s would only see GPU 0. Should aggregate VRAM or expose all GPUs. |
| 2 | **MEDIUM** | **Laptop Optimus/hybrid not distinguished** — On Optimus systems (e.g., Intel iGPU + NVIDIA dGPU), both nvidia-smi and lspci will detect the NVIDIA GPU. The profiler does not detect that the Intel iGPU also exists. For AI workloads, only the dGPU matters, so this is acceptable but undocumented. |
| 3 | **MEDIUM** | **No validation of parsed VRAM value** — If nvidia-smi returns unexpected text (driver error message, partial output), `float(parts[1])` could raise `ValueError` or return NaN. The broad `except Exception` catches this, but the error is silently swallowed as a debug log. |
| 4 | **LOW** | **No driver version tracking** — Driver version affects CUDA compatibility. A user with CUDA 11.x driver trying to run a model requiring CUDA 12.x will get cryptic Ollama errors with no hint from the profiler. |
| 5 | **INFO** | **Headless detection is correct** — nvidia-smi works without X11/Wayland, so headless servers (including WSL2) are properly handled. |

### nvidia-smi Output Parsing Robustness

The parsing `line.split(",")[0]` and `[1]` is fragile for edge cases:
- GPU names containing commas (rare but possible in OEM models) would break parsing
- The `nounits` flag prevents "MiB" suffixes, which is good
- Empty stdout (nvidia-smi present but driver broken) → `parts` will have < 2 elements → returns None → falls through to lspci (correct behavior)

---

## 2. AMD Detection

### How it works
- Checks for `rocm-smi` binary
- Runs `rocm-smi --showmeminfo vram` and searches for `VRAM Total Memory (B): <bytes>`
- Hardcodes model name as "Radeon GPU (ROCm)"

### Issues Found

| # | Severity | Issue |
|---|----------|-------|
| 1 | **HIGH** | **Only one ROCm output format handled** — ROCm-smi output varies significantly across versions. Older versions use `"GPU Memory Used: ..."` and some use `"VRAM Total Memory (B)"` while newer ROCm 6.x uses `rocm-smi --showmeminfo vram` with a different layout. Systems with ROCm < 5.0 will fall through to lspci with a hardcoded 4GB guess. |
| 2 | **HIGH** | **AMD APU (integrated GPU) not detected** — AMD APUs (e.g., Ryzen 7 8840U, Ryzen AI) have integrated Radeon GPUs that share system RAM. These will not appear via `rocm-smi` (ROCm doesn't support APUs). The lspci fallback detects "Advanced Micro Devices" but assigns 4.0 GB VRAM, which is wrong — APUs have no dedicated VRAM. Status should be `cpu_shared` like Intel, not `unsupported_driver`. |
| 3 | **MEDIUM** | **Model name is hardcoded** — Actual GPU model (e.g., "Radeon RX 7900 XTX") is lost. The profiler could parse `rocm-smi` output or lspci model string. |
| 4 | **MEDIUM** | **No VRAM validation** — If the regex doesn't match, no AMD VRAM is returned. Falls through silently to lspci with inaccurate 4.0 GB. |
| 5 | **LOW** | **No RDNA 3a / Ryzen AI NPU detection** — Newer AMD hardware includes NPUs (XDNA) that could be relevant for AI inference. Not detected at all. |

---

## 3. Intel Detection

### How it works
- Only detected via the `lspci` fallback (no Intel-specific tool)
- Checks for "Intel" string in lspci output
- Hardcodes: model="Intel Integrated Graphics", VRAM=2.0 GB, status="cpu_shared"

### Issues Found

| # | Severity | Issue |
|---|----------|-------|
| 1 | **HIGH** | **Intel Arc GPUs not distinguished** — Intel Arc (A770, A750, etc.) are discrete GPUs with 8-16 GB dedicated VRAM. The profiler treats them as integrated (2.0 GB, cpu_shared). This massively undersells Arc capability. lspci can distinguish: "Arc" in the model string vs "UHD" / "Iris". |
| 2 | **MEDIUM** | **Intel GPUs not detected via oneAPI/level-zero** — Intel provides `sycl-ls` or `oneinfo` tools for GPU detection, which work for both integrated and Arc. These are not checked. |
| 3 | **LOW** | **Hardcoded 2.0 GB is misleading** — Intel integrated GPUs share system RAM dynamically. The actual available portion depends on BIOS settings and system load. 2.0 GB is a reasonable estimate for most configurations but should be documented as approximate. |

---

## 4. lspci Fallback Behavior

### How it works
- If `nvidia-smi` and `rocm-smi` are both absent or fail, falls back to `lspci`
- If `lspci` also fails, returns CPU fallback with 0 GB VRAM

### Issues Found

| # | Severity | Issue |
|---|----------|-------|
| 1 | **MEDIUM** | **lspci may not be installed** — Minimal container/server installs (e.g., Ubuntu minimal) may lack `pciutils`. In this case, the entire GPU detection chain silently falls to CPU fallback. Should log a warning. |
| 2 | **MEDIUM** | **Hardcoded VRAM guesses** — lspci fallback uses 4.0 GB for NVIDIA, 4.0 GB for AMD, 2.0 GB for Intel. These are arbitrary. A GT 710 has 1-2 GB; an RTX 4090 has 24 GB. The profiler cannot distinguish them without driver tools, but the hardcoded 4 GB is misleading. |
| 3 | **LOW** | **No /sys/class/drm fallback** — The comment mentions checking `/sys/class/drm` but the code doesn't implement it. Reading `/sys/class/drm/card*/device/vendor` could provide vendor detection even without lspci. |
| 4 | **INFO** | **CPU fallback is correct** — Returning `{"vendor": "CPU", "vram": 0.0}` is the right behavior for systems with no detectable GPU. |

---

## 5. VRAM Detection Accuracy

| Method | Accuracy | Notes |
|--------|----------|-------|
| nvidia-smi CSV | **High** (±1 MB) | NVIDIA driver reports exact VRAM |
| rocm-smi VRAM | **Medium** | Depends on ROCm version; byte-level precision is good but format matching is fragile |
| lspci guess | **Low** | Hardcoded 4 GB / 2 GB; could be off by 10x |
| CPU fallback | **N/A** | 0.0 GB is correct — no dedicated VRAM |

---

## 6. Recommendations

1. **[CRITICAL]** Add multi-GPU awareness — aggregate VRAM or select the highest-VRAM GPU
2. **[CRITICAL]** Distinguish Intel Arc (discrete) from Intel UHD/Iris (integrated) using lspci model string
3. **[HIGH]** Handle AMD APU correctly — detect `cpu_shared` status for APUs instead of `unsupported_driver`
4. **[HIGH]** Add multiple rocm-smi output format patterns for cross-version compatibility
5. **[MEDIUM]** Add VRAM sanity validation (clamp to reasonable ranges, log anomalies)
6. **[MEDIUM]** Log a warning when lspci is not available
7. **[LOW]** Consider checking `/sys/class/drm` as an additional fallback
8. **[LOW]** Track CUDA/cuDNN driver version for NVIDIA systems