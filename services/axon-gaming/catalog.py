"""The gaming catalog: what Game Center and the welcome app can install.

The data lives in ``catalog.json`` (schema: ``catalog.schema.json``), shipped by the
``axon-gaming-catalog`` package so a new list reaches installed systems through apt.
This module only reads it.
"""

from __future__ import annotations

import json
import platform
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

# Installed location first, then the source tree (services/axon-gaming/ -> data/gaming/)
CATALOG_PATHS = (
    Path("/usr/share/axon/gaming/catalog.json"),
    Path(__file__).resolve().parents[2] / "data" / "gaming" / "catalog.json",
)

# Flatpak apps share runtimes; the first app also pulls the runtime in.
FLATPAK_RUNTIME_MB = 800


class CatalogError(Exception):
    """The catalog file is missing or not valid."""


@dataclass(frozen=True)
class Item:
    id: str
    name: str
    summary: str
    category: str
    source: dict[str, Any]
    size_mb: int
    starter_pack: bool = False
    aliases: tuple[str, ...] = ()
    requires: dict[str, Any] = field(default_factory=dict)
    notes: str | None = None
    verified: str | None = None

    @property
    def kind(self) -> str:
        """flatpak, apt, axon-apt or component."""
        return str(self.source["type"])

    @property
    def backend(self) -> str:
        """Installs of the same backend run one at a time."""
        return "apt" if self.kind in ("apt", "axon-apt") else self.kind

    @property
    def is_emulator(self) -> bool:
        return self.category == "emulators"


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    summary: str
    items: tuple[str, ...]


def _normal(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


class Catalog:
    def __init__(self, data: dict[str, Any]) -> None:
        self.version: int = data["version"]
        self.updated: str = data["updated"]
        self.categories: dict[str, str] = {c["id"]: c["name"] for c in data["categories"]}
        self.items: dict[str, Item] = {}
        for raw in data["items"]:
            item = Item(
                id=raw["id"],
                name=raw["name"],
                summary=raw["summary"],
                category=raw["category"],
                source=raw["source"],
                size_mb=raw["size_mb_estimate"],
                starter_pack=raw["starter_pack"],
                aliases=tuple(raw.get("aliases", ())),
                requires=raw["requires"],
                notes=raw.get("notes"),
                verified=raw.get("verified"),
            )
            self.items[item.id] = item
        self.presets: dict[str, Preset] = {
            p["id"]: Preset(p["id"], p["name"], p["summary"], tuple(p["items"]))
            for p in data["presets"]
        }

    def item(self, item_id: str) -> Item | None:
        return self.items.get(item_id)

    def in_category(self, category: str) -> list[Item]:
        return [i for i in self.items.values() if i.category == category]

    def preset_items(self, preset_id: str) -> list[Item]:
        return [self.items[i] for i in self.presets[preset_id].items if i in self.items]

    def total_size_mb(self, ids: list[str], flatpak_runtime_present: bool = False) -> int:
        """Download size of ``ids`` including a Flatpak runtime if none is installed yet."""
        chosen = [self.items[i] for i in ids if i in self.items]
        total = sum(i.size_mb for i in chosen)
        if not flatpak_runtime_present and any(i.kind == "flatpak" for i in chosen):
            total += FLATPAK_RUNTIME_MB
        return total

    def find(self, text: str) -> Item | None:
        """The item a person means by ``text``: its id, name or an alias, then prefixes."""
        q = _normal(text)
        if not q:
            return None
        names = {
            i.id: [_normal(i.id), _normal(i.name), *map(_normal, i.aliases)]
            for i in self.items.values()
        }
        for item_id, options in names.items():
            if q in options:
                return self.items[item_id]
        for item_id, options in names.items():
            if any(o.startswith(q + " ") or (len(q) >= 3 and o.startswith(q)) for o in options):
                return self.items[item_id]
        return None


def load_catalog(path: Path | None = None) -> Catalog:
    """Read the catalog from ``path`` or the first default location that exists."""
    candidates = [path] if path else list(CATALOG_PATHS)
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            try:
                return Catalog(json.loads(candidate.read_text(encoding="utf-8")))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise CatalogError(f"{candidate}: {exc}") from exc
    raise CatalogError("gaming catalog not found")


HWE_KERNEL_META = "linux-image-generic-hwe-24.04"
# Ubuntu 26.04 ships a 7.0 kernel (NTSYNC, sched_ext) from the start, so the gaming kernel is an
# Ubuntu 24.04 item only (checked against the resolute archive, 2026-10-27).
GAMING_KERNEL_RELEASES = ("noble",)
OS_RELEASE = Path("/etc/os-release")
_HWE_IMAGE_RE = re.compile(r"^linux-image-(?:unsigned-)?((\d+)\.(\d+)\.\d+-\d+)-generic$")
_GA_MAJOR_MINOR = (6, 8)  # the kernel Axon OS ships; Ubuntu's HWE kernels are newer


def ubuntu_codename(os_release: Path = OS_RELEASE) -> str:
    """The Ubuntu release this system is based on ("noble", "resolute"), or "" if unknown."""
    try:
        text = os_release.read_text(encoding="utf-8")
    except OSError:
        return ""
    fields = dict(
        line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#")
    )
    return (fields.get("UBUNTU_CODENAME") or fields.get("VERSION_CODENAME") or "").strip("\"' ")


def gaming_kernel_offered(codename: str) -> bool:
    """True where installing Ubuntu's newer (HWE) kernel makes sense: Ubuntu 24.04 only."""
    return codename in GAMING_KERNEL_RELEASES


def _running_version(release: str) -> str:
    return release.removesuffix("-generic")


def hwe_kernel_removal(installed: list[str], running_release: str) -> list[str] | None:
    """Packages that remove the gaming (HWE) kernel and what came with it.

    ``installed`` is every installed ``linux-*`` package name. Returns None while the
    running kernel is one of the HWE kernels: it cannot be removed from under itself, so the
    caller should ask for a restart into the standard kernel first. The standard 6.8 kernel
    is never in the list.
    """
    present = set(installed)
    versions = set()
    for name in installed:
        m = _HWE_IMAGE_RE.match(name)
        if m and (int(m.group(2)), int(m.group(3))) > _GA_MAJOR_MINOR:
            versions.add(m.group(1))
    if _running_version(running_release) in versions:
        return None
    remove = [
        meta
        for meta in (
            "linux-generic-hwe-24.04",
            HWE_KERNEL_META,
            "linux-headers-generic-hwe-24.04",
        )
        if meta in present
    ]
    remove += sorted(
        n for n in present if re.fullmatch(r"linux-modules-nvidia-.*-generic-hwe-24\.04", n)
    )
    for v in sorted(versions):
        for pattern in (
            f"linux-image-{v}-generic",
            f"linux-image-unsigned-{v}-generic",
            f"linux-modules-{v}-generic",
            f"linux-modules-extra-{v}-generic",
            f"linux-headers-{v}-generic",
            f"linux-headers-{v}",
            f"linux-tools-{v}-generic",
        ):
            if pattern in present:
                remove.append(pattern)
        remove += sorted(
            n
            for n in present
            if re.fullmatch(rf"linux-modules-nvidia-.*-{re.escape(v)}-generic", n)
        )
    return remove


def hwe_nvidia_modules(installed: list[str]) -> list[str]:
    """Signed NVIDIA modules for the HWE kernel that match the driver already installed.

    The standard kernel's ``linux-modules-nvidia-580-open-generic`` has a twin for the HWE
    kernel, ``...-generic-hwe-24.04``. Without it a restart into the new kernel would have no
    NVIDIA driver, and the NVIDIA edition blacklists the open driver.
    """
    return [
        n + "-hwe-24.04"
        for n in sorted(set(installed))
        if re.fullmatch(r"linux-modules-nvidia-[0-9]+(-open|-server|-server-open)?-generic", n)
    ]


def gaming_kernel_item(base: Item, installed: list[str]) -> Item:
    """The catalog's gaming kernel with the NVIDIA modules that must come with it."""
    packages = [*base.source["packages"], *hwe_nvidia_modules(installed)]
    return replace(base, source={**base.source, "packages": packages})


def gaming_kernel_removal_item(
    base: Item, installed: list[str], running_release: str
) -> Item | None:
    """The gaming kernel as an item whose removal takes out everything that came with it."""
    packages = hwe_kernel_removal(installed, running_release)
    if packages is None:
        return None
    return replace(
        base, source={**base.source, "packages": packages or list(base.source["packages"])}
    )


_DRIVER_RE = re.compile(r"^nvidia-driver-([0-9]+)(-open|-server|-server-open)?$")
# Ubuntu builds and signs NVIDIA kernel modules only for its own kernels; the 6.8 GA kernel
# (the one Axon OS ships) has them for every driver branch.
_SIGNED_KERNEL_RE = re.compile(r"^6\.8\.0-[0-9]+-generic$")
# Ubuntu's HWE kernels (6.11 and newer) have their own metapackages, for the same driver branches
_HWE_KERNEL_RE = re.compile(
    r"^(?:6\.(?:1[1-9]|[2-9][0-9])|(?:[7-9]|[1-9][0-9]+)\.[0-9]+)\.[0-9]+-[0-9]+-generic$"
)


# Ubuntu 26.04's own kernel (7.0 and later point releases) carries the plain "-generic" modules
_RELEASE_KERNEL_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+-[0-9]+-generic$")


def signed_modules_package(
    package: str, kernel_release: str | None = None, codename: str | None = None
) -> str | None:
    """The prebuilt, Secure Boot signed kernel modules for an NVIDIA driver package, if any.

    ``nvidia-driver-580-open`` on kernel ``6.8.0-147-generic`` gives
    ``linux-modules-nvidia-580-open-generic``; on an HWE kernel (6.11 or newer) the same name
    ends in ``-hwe-24.04``. On Ubuntu 26.04 (``resolute``) every generic kernel uses the plain
    name. With them there is no DKMS build, and no key to enroll: Canonical's signature is
    already trusted by Secure Boot. On other kernels the driver would be built with DKMS and
    unsigned, so this returns None.
    """
    match = _DRIVER_RE.match(package)
    kernel = platform.release() if kernel_release is None else kernel_release
    if not match:
        return None
    base = f"linux-modules-nvidia-{match.group(1)}{match.group(2) or ''}-generic"
    if (ubuntu_codename() if codename is None else codename) == "resolute":
        return base if _RELEASE_KERNEL_RE.match(kernel) else None
    if _SIGNED_KERNEL_RE.match(kernel):
        return base
    if _HWE_KERNEL_RE.match(kernel):
        return base + "-hwe-24.04"
    return None


def driver_item(package: str, kernel_release: str | None = None) -> Item:
    """A catalog-style item for a graphics driver package ``ubuntu-drivers`` recommended.

    Only NVIDIA driver package names are accepted, so a name taken from a command
    line or another process can never install something else. The signed modules are
    installed first when they exist, so apt does not pick the DKMS alternative.
    """
    if not _DRIVER_RE.match(package):
        raise ValueError(f"not an NVIDIA driver package: {package!r}")
    modules = signed_modules_package(package, kernel_release)
    return Item(
        id="gpu-driver",
        name=package,
        summary="Graphics driver",
        category="system",
        source={"type": "apt", "packages": [modules, package] if modules else [package]},
        size_mb=300,
        requires={"gpu": "nvidia", "arch": "x86_64"},
    )
