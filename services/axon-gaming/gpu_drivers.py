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


# The driver branch each NVIDIA generation gets. 595 (590 and later) dropped Maxwell, Pascal and
# Volta, which 580 still supports with security updates until 2028; both are in the 24.04 and
# 26.04 archives (checked 2026-10-27; on 26.04 the "590" names are transitional packages for
# 595). Turing and newer use the open kernel modules, which pre-Turing cards cannot.
NEW_BRANCH_DRIVER = "nvidia-driver-595-open"
LEGACY_BRANCH_DRIVER = "nvidia-driver-580"

_TURING_PLUS_CHIP = re.compile(r"\b(?:TU|GA|AD|GB|GH)\d{2,3}[A-Z]?\b")
_PRE_TURING_CHIP = re.compile(r"\b(?:GM|GP|GV)\d{3}[A-Z]?\b")
_OLDER_CHIP = re.compile(r"\b(?:GK|GF|G[0-9]{2}|GT2[0-9]{2})[0-9A-Z]*\b")
_TURING_PLUS_NAME = re.compile(
    r"\bRTX\b|\bGTX 16\d{2}\b|\bTITAN RTX\b|\bT[0-9]{3,4}\b|\b(?:A|L|H|B)[0-9]{2,3}\b", re.I
)
_PRE_TURING_NAME = re.compile(
    r"\bGTX (?:9\d{2}|10\d{2}|TITAN)|\bGT 10\d{2}\b|\bTITAN (?:X|Xp|V)\b|\bQuadro [PMG][0-9]|\bTesla [PMV]",
    re.I,
)


def nvidia_generation(name: str) -> str:
    """ "turing+", "maxwell-volta", "older" (Kepler and before) or "unknown", from lspci's name.

    The chip code ("GP106") says more than the marketing name, so it goes first.
    """
    if _TURING_PLUS_CHIP.search(name):
        return "turing+"
    if _PRE_TURING_CHIP.search(name):
        return "maxwell-volta"
    if _OLDER_CHIP.search(name):
        return "older"
    if _PRE_TURING_NAME.search(name):
        return "maxwell-volta"
    if _TURING_PLUS_NAME.search(name):
        return "turing+"
    return "unknown"


def choose_nvidia_driver(gpus: list[Gpu], recommended: str | None) -> str | None:
    """The driver package to offer, chosen by the cards' generation, not just "NVIDIA".

    Turing and newer get the current branch with open modules; Maxwell, Pascal and Volta get
    580. A card this cannot place (older, or a name it does not know) keeps whatever
    ``ubuntu-drivers`` recommended, which knows the exact PCI ids.
    """
    generations = {nvidia_generation(g.name) for g in gpus if g.vendor == "nvidia"}
    if not generations:
        return recommended
    if generations & {"older", "unknown"}:
        return recommended
    if "maxwell-volta" in generations:
        return LEGACY_BRANCH_DRIVER
    return NEW_BRANCH_DRIVER


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
    from catalog import signed_modules_package

    gpus = detect_gpus()
    nvidia = any(g.vendor == "nvidia" for g in gpus)
    recommended = choose_nvidia_driver(gpus, recommended_nvidia_driver() if nvidia else None)
    return {
        "gpus": gpus,
        "recommended": recommended,
        "signed_modules": bool(recommended and signed_modules_package(recommended)),
        "nvidia_version": nvidia_driver_version() if nvidia else None,
        "secure_boot": secure_boot_enabled() if nvidia else None,
    }
