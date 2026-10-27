"""Graphics cards, their drivers and the driver Ubuntu recommends.

Reads ``lspci``, sysfs and ``ubuntu-drivers``; nothing here changes the system.
Installing a driver goes through ``installer`` like any other apt package.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

VENDOR_IDS = {"10de": "nvidia", "1002": "amd", "8086": "intel"}
_DISPLAY_CLASSES = ("VGA compatible controller", "3D controller", "Display controller")


@dataclass(frozen=True)
class Gpu:
    vendor: str  # nvidia, amd, intel or other
    name: str
    pci_id: str  # vendor:device
    driver: str  # kernel driver in use, "" if none
    vram_gb: float | None = None


def parse_lspci(text: str) -> list[Gpu]:
    """Graphics devices from ``lspci -nnk`` output."""
    gpus: list[Gpu] = []
    blocks = re.split(r"\n(?=\S)", text.strip())
    for block in blocks:
        first, _, rest = block.partition("\n")
        if not any(cls in first for cls in _DISPLAY_CLASSES):
            continue
        ids = re.search(r"\[([0-9a-f]{4}):([0-9a-f]{4})\]", first)
        if not ids:
            continue
        name = re.sub(r"^\S+\s+[^:]+:\s+", "", first)
        name = re.sub(r"\s*\[[0-9a-f]{4}:[0-9a-f]{4}\].*$", "", name).strip()
        driver = re.search(r"Kernel driver in use:\s*(\S+)", rest)
        gpus.append(
            Gpu(
                vendor=VENDOR_IDS.get(ids.group(1), "other"),
                name=name,
                pci_id=f"{ids.group(1)}:{ids.group(2)}",
                driver=driver.group(1) if driver else "",
            )
        )
    return gpus


def sysfs_vram_gb(drm_root: Path = Path("/sys/class/drm")) -> dict[str, float]:
    """Dedicated video memory by PCI vendor id ("1002"), from amdgpu's sysfs files."""
    found: dict[str, float] = {}
    for card in sorted(drm_root.glob("card[0-9]*")):
        device = card / "device"
        try:
            vendor = (device / "vendor").read_text().strip().removeprefix("0x")
            total = int((device / "mem_info_vram_total").read_text().strip())
        except (OSError, ValueError):
            continue
        found[vendor] = max(found.get(vendor, 0.0), round(total / 1024**3, 1))
    return found


def parse_recommended(text: str) -> str | None:
    """The package from ``ubuntu-drivers list --recommended`` ("nvidia-driver-550, (...)")."""
    for line in text.splitlines():
        match = re.match(r"^([a-z0-9][a-z0-9+.-]*)\s*(?:,|$)", line.strip())
        if match and match.group(1).startswith(("nvidia-driver", "nvidia-")):
            return match.group(1)
    return None


def _run(argv: list[str], timeout: float = 15.0) -> str:
    try:
        return subprocess.run(  # nosec B603 B607 - fixed argument list
            argv, capture_output=True, text=True, timeout=timeout, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def detect_gpus() -> list[Gpu]:
    gpus = parse_lspci(_run(["lspci", "-nnk"]))
    vram = sysfs_vram_gb()
    return [
        Gpu(g.vendor, g.name, g.pci_id, g.driver, vram.get(g.pci_id.split(":")[0])) for g in gpus
    ]


def recommended_nvidia_driver() -> str | None:
    return parse_recommended(_run(["ubuntu-drivers", "list", "--recommended"], timeout=60))


def nvidia_driver_version() -> str | None:
    out = _run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"]).strip()
    return out.splitlines()[0] if out else None


def secure_boot_enabled() -> bool | None:
    """True/False from mokutil, None if it cannot tell."""
    out = _run(["mokutil", "--sb-state"]).lower()
    if "enabled" in out:
        return True
    if "disabled" in out:
        return False
    return None


def detect_graphics() -> dict:
    """Everything the Drivers page and the welcome app need. Slow (ubuntu-drivers)."""
    gpus = detect_gpus()
    nvidia = any(g.vendor == "nvidia" for g in gpus)
    return {
        "gpus": gpus,
        "recommended": recommended_nvidia_driver() if nvidia else None,
        "nvidia_version": nvidia_driver_version() if nvidia else None,
        "secure_boot": secure_boot_enabled() if nvidia else None,
    }
