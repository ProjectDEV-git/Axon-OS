#!/usr/bin/env python3
import os
import re
import subprocess
import shutil
import json

def get_system_ram():
    """Returns total system RAM in GB."""
    try:
        with open('/proc/meminfo', 'r') as f:
            meminfo = f.read()
        matched = re.search(r'MemTotal:\s+(\d+)\s+kB', meminfo)
        if matched:
            return int(matched.group(1)) / (1024 * 1024)
    except Exception:
        pass
    return 8.0  # Default fallback

def get_gpu_info():
    """
    Detects GPU vendor, model, and VRAM (in GB).
    Returns a dict with keys: 'vendor', 'model', 'vram', 'status'
    """
    # 1. Try NVIDIA
    if shutil.which("nvidia-smi"):
        try:
            # Query GPU name and total memory in MB
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True
            )
            line = result.stdout.strip().split('\n')[0]
            parts = [p.strip() for p in line.split(',')]
            if len(parts) >= 2:
                name = parts[0]
                vram_mb = float(parts[1])
                return {
                    "vendor": "NVIDIA",
                    "model": name,
                    "vram": vram_mb / 1024.0,
                    "status": "detected"
                }
        except Exception as e:
            pass

    # 2. Try AMD ROCm
    if shutil.which("rocm-smi"):
        try:
            # Try to get VRAM from rocm-smi
            # ROCm-smi output formats vary, we search for card info or usage
            result = subprocess.run(
                ["rocm-smi", "--showmeminfo", "vram"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True
            )
            # Find lines like "VRAM Total Memory (B): 8589934592" or "VRAM Size: ... MB"
            vram_bytes = re.search(r'VRAM Total Memory \(B\):\s+(\d+)', result.stdout)
            if vram_bytes:
                vram_gb = int(vram_bytes.group(1)) / (1024 * 1024 * 1024)
                return {
                    "vendor": "AMD",
                    "model": "Radeon GPU (ROCm)",
                    "vram": vram_gb,
                    "status": "detected"
                }
        except Exception:
            pass

    # 3. Inspect /sys/class/drm or lspci as fallback for general GPUs
    try:
        # Check lspci for VGA/3D controller
        lspci_out = subprocess.check_output(["lspci"], text=True)
        if "NVIDIA" in lspci_out:
            return {"vendor": "NVIDIA", "model": "NVIDIA GPU (unconfigured)", "vram": 4.0, "status": "unsupported_driver"}
        elif "Advanced Micro Devices" in lspci_out or "ATI" in lspci_out:
            return {"vendor": "AMD", "model": "AMD Radeon GPU (unconfigured)", "vram": 4.0, "status": "unsupported_driver"}
        elif "Intel" in lspci_out:
            return {"vendor": "Intel", "model": "Intel Integrated Graphics", "vram": 2.0, "status": "cpu_shared"}
    except Exception:
        pass

    return {
        "vendor": "CPU",
        "model": "Generic System CPU",
        "vram": 0.0,
        "status": "fallback"
    }

def profile_hardware():
    ram = get_system_ram()
    gpu = get_gpu_info()
    
    # Model recommendations database
    # speed: fast model
    # general: balanced model
    # deep: high reasoning model
    
    rec = {
        "hardware": {
            "ram_gb": round(ram, 2),
            "gpu_vendor": gpu["vendor"],
            "gpu_model": gpu["model"],
            "gpu_vram_gb": round(gpu["vram"], 2) if gpu["vram"] > 0 else 0,
            "status": gpu["status"]
        },
        "recommendations": {
            "speed": {
                "model": "llama3.2:1b",
                "description": "Llama 3.2 1B — extremely fast, low memory usage, perfect for short command parsing and quick tasks."
            },
            "general": {
                "model": "llama3.2:3b",
                "description": "Llama 3.2 3B — fast, private, well-suited for everyday chat, content summary, and ambient context."
            },
            "deep": {
                "model": "qwen2.5:7b",
                "description": "Qwen 2.5 7B — high capability, great logic, ideal for code generation and deep reasoning."
            }
        }
    }
    
    vram = gpu["vram"]
    # Adjust recommendations based on capabilities
    if gpu["vendor"] == "NVIDIA" or gpu["vendor"] == "AMD":
        if vram >= 12.0:
            rec["recommendations"]["deep"] = {
                "model": "qwen2.5:14b",
                "description": "Qwen 2.5 14B — high intelligence, code-expert, runs fast on your 12GB+ GPU."
            }
            rec["recommendations"]["general"] = {
                "model": "qwen2.5:7b",
                "description": "Qwen 2.5 7B — smart, fast general conversationalist with high context capabilities."
            }
        elif vram >= 6.0:
            # 6GB-8GB VRAM (Sweet spot for 8B models)
            rec["recommendations"]["deep"] = {
                "model": "llama3:8b",
                "description": "Llama 3 8B — industry standard for deep reasoning, programming, and complex workflows."
            }
        else:
            # Low VRAM GPU (<6GB)
            rec["recommendations"]["deep"] = {
                "model": "llama3.2:3b",
                "description": "Llama 3.2 3B — used as deep task model since GPU memory is limited."
            }
            rec["recommendations"]["general"] = {
                "model": "qwen2.5:1.5b",
                "description": "Qwen 2.5 1.5B — fast, medium capacity, handles lightweight panel chats on low VRAM."
            }
    else:
        # CPU or Integrated GPU fallback
        # If CPU has plenty of RAM, we can still run a 3B/8B model but it will be slow
        if ram >= 16.0:
            rec["recommendations"]["deep"] = {
                "model": "llama3:8b",
                "description": "Llama 3 8B — high reasoning, runs on CPU RAM (will have moderate response latency)."
            }
        else:
            rec["recommendations"]["deep"] = {
                "model": "llama3.2:3b",
                "description": "Llama 3.2 3B — balanced CPU fallback."
            }
            rec["recommendations"]["general"] = {
                "model": "qwen2.5:1.5b",
                "description": "Qwen 2.5 1.5B — fast response on standard system RAM."
            }
            
    return rec

if __name__ == "__main__":
    profile = profile_hardware()
    print(json.dumps(profile, indent=2))
