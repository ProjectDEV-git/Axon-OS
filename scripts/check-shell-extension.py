#!/usr/bin/env python3
"""Check the Axon Shell extension against a release's GNOME Shell, without running it.

Downloads gnome-shell and libmutter-<N>-dev for a release from the Ubuntu archive, pulls the
shell's own JavaScript out of libshell, and checks that

* every ``resource:///org/gnome/shell/...`` module the extension imports exists,
* every member of Main, PanelMenu, PopupMenu, MessageTray, AppFavorites and Config the
  extension uses is still exported,
* every Clutter and Meta name the extension uses is still in that release's GIR files,
* ``metadata.json`` lists the shell's major version.

It cannot see St and Shell classes (their GIR is not shipped), so a clean result means "no API the
extension names has gone", not "it runs". Test on a real session before shipping.

Usage: scripts/check-shell-extension.py [--ubuntu-release resolute]
Exit code 1 if something is missing, 2 if the archive cannot be reached.
"""

from __future__ import annotations

import argparse
import json
import lzma
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "shell" / "axon-shell"
ARCHIVE = "https://archive.ubuntu.com/ubuntu"
MODULES = {
    "Main": "ui/main.js",
    "PanelMenu": "ui/panelMenu.js",
    "PopupMenu": "ui/popupMenu.js",
    "MessageTray": "ui/messageTray.js",
    "AppFavorites": "ui/appFavorites.js",
    "Config": "misc/config.js",
}


def say(message: str) -> None:
    sys.stdout.write(message + "\n")


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=180) as resp:  # nosec B310 - https
        return resp.read()


def package_index(release: str) -> dict[str, str]:
    """Package -> pool path of its newest version, over main and universe, release and updates."""
    found: dict[str, str] = {}
    for pocket in (release, f"{release}-updates"):
        for component in ("main", "universe"):
            text = lzma.decompress(
                fetch(f"{ARCHIVE}/dists/{pocket}/{component}/binary-amd64/Packages.xz")
            ).decode("utf-8", "replace")
            for stanza in text.split("\n\n"):
                name = re.search(r"^Package: (.+)$", stanza, re.M)
                path = re.search(r"^Filename: (.+)$", stanza, re.M)
                if name and path:
                    found[name.group(1)] = path.group(1)
    return found


def unpack(path: str, dest: Path) -> None:
    deb = dest / "pkg.deb"
    deb.write_bytes(fetch(f"{ARCHIVE}/{path}"))
    subprocess.run(["dpkg-deb", "-x", str(deb), str(dest / "root")], check=True)  # nosec B603 B607


def shell_resources(libshell: Path, work: Path) -> Path:
    """Extract the shell's JavaScript, compiled into libshell as a GResource, to ``work/js``."""
    data = libshell.read_bytes()
    for match in re.finditer(b"GVariant", data):
        blob = work / "shell.gresource"
        blob.write_bytes(data[match.start() :])
        listed = subprocess.run(  # nosec B603 B607
            ["gresource", "list", str(blob)], capture_output=True, text=True, check=False
        )
        if "/org/gnome/shell/ui/main.js" in listed.stdout:
            for res in listed.stdout.split():
                if res.endswith(".js"):
                    out = work / "js" / res.removeprefix("/org/gnome/shell/")
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(
                        subprocess.run(  # nosec B603 B607
                            ["gresource", "extract", str(blob), res],
                            capture_output=True,
                            check=True,
                        ).stdout
                    )
            return work / "js"
    raise SystemExit("could not find the shell's JavaScript in libshell")


def exports(source: str) -> set[str]:
    names = set(
        re.findall(r"export (?:const|let|var|function\*?|class|async function)\s+(\w+)", source)
    )
    for group in re.findall(r"export \{([^}]*)\}", source):
        names |= set(re.findall(r"\w+", group))
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--ubuntu-release", default="resolute")
    args = parser.parse_args()
    sources = sorted(EXT.glob("*.js"))
    text = {p.name: p.read_text(encoding="utf-8") for p in sources}
    problems = 0
    try:
        index = package_index(args.ubuntu_release)
        shell_pkg = index["gnome-shell"]
        dev_pkg = next(index[n] for n in sorted(index) if re.fullmatch(r"libmutter-\d+-dev", n))
    except (OSError, KeyError, StopIteration) as exc:
        say(f"archive unreachable or package missing: {exc}")
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for label, path in (("shell", shell_pkg), ("mutter", dev_pkg)):
            (work / label).mkdir()
            unpack(path, work / label)
        js = shell_resources(next((work / "shell" / "root").rglob("libshell-*.so")), work)
        version = re.search(r"PACKAGE_VERSION = '(\d+)", (js / "misc/config.js").read_text())
        major = version.group(1) if version else "?"
        say(f"GNOME Shell {major} in {args.ubuntu_release}")

        for body in text.values():
            for module in re.findall(r"resource:///org/gnome/shell/([\w/]+\.js)", body):
                if not (js / module).is_file():
                    say(f"MISSING module {module}")
                    problems += 1
        for ns, module in MODULES.items():
            have = exports((js / module).read_text())
            used = {m for body in text.values() for m in re.findall(rf"\b{ns}\.(\w+)", body)}
            for name in sorted(used - have):
                # Instances (Main.panel, Main.wm ...) are `export let`, covered above; anything
                # else is reported
                say(f"MISSING {ns}.{name}")
                problems += 1
        for ns in ("Clutter", "Meta"):
            gir = next((work / "mutter" / "root").rglob(f"{ns}-*.gir")).read_text()
            used = {
                m for body in text.values() for m in re.findall(rf"\b{ns}\.([A-Za-z_]\w*)", body)
            }
            for name in sorted(used):
                if f'name="{name}"' not in gir and name not in ("EVENT_STOP", "EVENT_PROPAGATE"):
                    say(f"MISSING {ns}.{name}")
                    problems += 1
        meta = json.loads((EXT / "metadata.json").read_text())
        if major not in meta["shell-version"]:
            say(f"metadata.json does not list shell version {major}")
            problems += 1
    say(f"{problems} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
