#!/usr/bin/env python3
"""Check that every package the ISO build asks for exists in the Ubuntu archive.

Reads build/package-lists/*.list and the Depends of the metapackages in packaging/,
and looks each name up in the release's Packages indexes (amd64, and i386 for
":i386" names). Also checks that every source package in packaging/backports/backports.list
exists, at exactly that version, in the release it is taken from. A name that is missing would otherwise only show up as a warning
or a failed build hours into an ISO build.

Usage: scripts/verify-packages.py [--ubuntu-release resolute|noble]
Exit code 1 if a package is missing, 2 if the archive cannot be reached.
"""

from __future__ import annotations

import argparse
import lzma
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AXON_REPO_PACKAGES = {"gamescope"}
ARCHIVE = "https://archive.ubuntu.com/ubuntu/dists/{suite}/{component}/binary-{arch}/Packages.xz"
COMPONENTS = ("main", "restricted", "universe", "multiverse")
POCKETS = ("{release}", "{release}-updates", "{release}-security")


def say(message: str) -> None:
    sys.stdout.write(message + "\n")


def names_in_lists(release: str) -> dict[str, list[str]]:
    """package -> where it is asked for, from the package lists that apply to ``release``.

    common.<release>.list belongs to that release only; the other release's file is skipped.
    """
    found: dict[str, list[str]] = {}
    for path in sorted((ROOT / "build" / "package-lists").glob("*.list")):
        parts = path.name.split(".")
        if len(parts) == 3 and parts[1] != release:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                found.setdefault(line.strip(), []).append(path.name)
    return found


def names_in_metapackages() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for control in sorted((ROOT / "packaging").glob("*/control")):
        text = control.read_text(encoding="utf-8")
        match = re.search(r"^Depends:\s*(.+)$", text, re.M)
        if not match:
            continue
        for dep in match.group(1).split(","):
            # "a | b" alternatives: either is fine, check the first
            name = dep.split("|")[0].strip().split(" ")[0]
            if name:
                found.setdefault(name, []).append(f"{control.parent.name}/control")
    return found


def archive_index(release: str, arch: str) -> set[str] | None:
    names: set[str] = set()
    for pocket in POCKETS:
        for component in COMPONENTS:
            url = ARCHIVE.format(
                suite=pocket.format(release=release), component=component, arch=arch
            )
            try:
                with urllib.request.urlopen(url, timeout=90) as resp:  # nosec B310 - https
                    text = lzma.decompress(resp.read()).decode("utf-8", "replace")
            except (urllib.error.URLError, OSError):
                return None
            names.update(ln[9:].strip() for ln in text.splitlines() if ln.startswith("Package: "))
    return names


SOURCES = "https://archive.ubuntu.com/ubuntu/dists/{suite}/{component}/source/Sources.xz"


def source_versions(suite: str) -> dict[str, set[str]] | None:
    """Source package -> versions available in a release (all components), or None."""
    found: dict[str, set[str]] = {}
    for component in COMPONENTS:
        url = SOURCES.format(suite=suite, component=component)
        try:
            with urllib.request.urlopen(url, timeout=180) as resp:  # nosec B310 - https
                text = lzma.decompress(resp.read()).decode("utf-8", "replace")
        except (urllib.error.URLError, OSError):
            return None
        for stanza in text.split("\n\n"):
            name = re.search(r"^Package: (.+)$", stanza, re.M)
            version = re.search(r"^Version: (.+)$", stanza, re.M)
            if name and version:
                found.setdefault(name.group(1), set()).add(version.group(1))
    return found


def check_backports() -> int:
    """Number of backport entries that do not exist, or -1 if the archive cannot be reached."""
    listing = ROOT / "packaging" / "backports" / "backports.list"
    if not listing.is_file():
        return 0
    entries = [
        ln.split()
        for ln in listing.read_text().splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    indexes: dict[str, dict[str, set[str]] | None] = {}
    missing = 0
    for name, suite, version in entries:
        if suite not in indexes:
            indexes[suite] = source_versions(suite)
        index = indexes[suite]
        if index is None:
            return -1
        if version not in index.get(name, set()):
            say(f"MISSING  backport {name} {version} in {suite}")
            missing += 1
    say(f"checked {len(entries)} backport sources: {missing} missing")
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--ubuntu-release", default="resolute")
    args = parser.parse_args()

    wanted = names_in_lists(args.ubuntu_release)
    for name, where in names_in_metapackages().items():
        wanted.setdefault(name, []).extend(where)
    # Packages built in this repository do not come from the archive
    own = {p.parent.name for p in (ROOT / "packaging").glob("*/control")}
    # Binary packages the Axon repository provides from packaging/backports (not in noble;
    # resolute has them in the archive)
    if args.ubuntu_release == "noble":
        own |= AXON_REPO_PACKAGES
    indexes: dict[str, set[str] | None] = {}
    missing = 0
    for name in sorted(wanted):
        if name in own:
            continue
        base, _, arch = name.partition(":")
        arch = arch or "amd64"
        if arch not in indexes:
            indexes[arch] = archive_index(args.ubuntu_release, arch)
        index = indexes[arch]
        if index is None:
            say("archive unreachable; nothing verified")
            return 2
        if base not in index:
            say(f"MISSING  {name}  (in {', '.join(sorted(set(wanted[name])))})")
            missing += 1
    say(f"checked {len(wanted)} package names against {args.ubuntu_release}: {missing} missing")
    # The backports are rebuilt for 24.04 only; 26.04 takes these packages from its own archive
    backports = check_backports() if args.ubuntu_release == "noble" else 0
    if backports < 0:
        say("archive unreachable; backports not verified")
        return 2
    return 1 if missing or backports else 0


if __name__ == "__main__":
    sys.exit(main())
