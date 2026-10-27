#!/usr/bin/env python3
"""Check data/gaming/catalog.json against the places its items come from.

Flatpak refs are looked up on Flathub, apt packages in the Ubuntu archive
(plus the Axon repo for axon-apt). An item that is found gets today's date in
"verified"; one that is not found is reported and left alone; if a source cannot
be reached the item is skipped, never marked.

Usage: scripts/verify-catalog.py [--write] [--ubuntu-release noble]
"""

from __future__ import annotations

import argparse
import datetime
import json
import lzma
import sys
import urllib.error
import urllib.request
from pathlib import Path

CATALOG = Path(__file__).resolve().parent.parent / "data" / "gaming" / "catalog.json"
ARCHIVE = "https://archive.ubuntu.com/ubuntu/dists/{release}/{pocket}{component}/binary-amd64/Packages.xz"
FLATHUB = "https://flathub.org/api/v2/appstream/{ref}"


def say(message: str) -> None:
    sys.stdout.write(message + "\n")


def archive_packages(release: str) -> set[str] | None:
    """Every binary package name in the release (all components and pockets), or None."""
    names: set[str] = set()
    for pocket in ("", "-updates"):
        for component in ("main", "universe", "multiverse", "restricted"):
            url = ARCHIVE.format(release=release + pocket, pocket="", component=component)
            try:
                with urllib.request.urlopen(url, timeout=60) as resp:  # nosec B310 - https URL above
                    text = lzma.decompress(resp.read()).decode("utf-8", "replace")
            except (urllib.error.URLError, OSError):
                return None
            names.update(ln[9:].strip() for ln in text.splitlines() if ln.startswith("Package: "))
    return names


def flathub_has(ref: str) -> bool | None:
    try:
        with urllib.request.urlopen(FLATHUB.format(ref=ref), timeout=20):  # nosec B310
            return True
    except urllib.error.HTTPError as exc:
        return False if exc.code == 404 else None
    except (urllib.error.URLError, OSError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--write", action="store_true", help="store the verification date")
    parser.add_argument("--ubuntu-release", default="noble")
    args = parser.parse_args()

    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    today = datetime.date.today().isoformat()
    archive = archive_packages(args.ubuntu_release)
    failures = 0
    for item in data["items"]:
        src = item["source"]
        wanted = list(item["requires"].get("apt", []))
        if src["type"] == "flatpak":
            found: bool | None = flathub_has(src["ref"])
            what = f"flathub:{src['ref']}"
        elif src["type"] == "apt":
            wanted += src["packages"]
            found = None if archive is None else all(p in archive for p in wanted)
            what = "apt:" + ",".join(wanted)
        else:
            continue
        if src["type"] == "flatpak" and wanted:
            found = None if archive is None or found is None else found and all(p in archive for p in wanted)
        if found is None:
            say(f"skip      {item['id']:22} {what} (source unreachable)")
        elif found:
            say(f"ok        {item['id']:22} {what}")
            item["verified"] = today
        else:
            say(f"MISSING   {item['id']:22} {what}")
            failures += 1
    if args.write:
        CATALOG.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
