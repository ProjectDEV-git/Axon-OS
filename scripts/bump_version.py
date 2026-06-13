"""Simple version bump utility for Axon OS.

Usage:
    python scripts/bump_version.py 0.2.1

This updates the `version` field in `pyproject.toml` and optionally updates other files.
"""
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"

if len(sys.argv) < 2:
    print("Usage: bump_version.py <new-version>")
    sys.exit(2)

new_version = sys.argv[1].strip()
if not re.match(r"^\d+\.\d+\.\d+(-[A-Za-z0-9.]+)?$", new_version):
    print("Version should be in semver form: X.Y.Z or X.Y.Z-prerelease")
    sys.exit(2)

text = PYPROJECT.read_text()
if "version" not in text:
    print("pyproject.toml does not contain a version field to update")
    sys.exit(1)

new_text, count = re.subn(r'(?m)^(version\s*=\s*")([^"]+)(")', rf'\1{new_version}\3', text)
if count == 0:
    print("Failed to update version in pyproject.toml")
    sys.exit(1)

PYPROJECT.write_text(new_text)
print(f"Updated pyproject.toml to version {new_version}")

# Optionally update DEVELOPING.md or other files if they contain the version string
DEV_MD = PROJECT_ROOT / "DEVELOPING.md"
if DEV_MD.exists():
    dtext = DEV_MD.read_text()
    dnew, dcount = re.subn(r"(?m)^Generated:\s+.*$", f"Generated: {new_version}", dtext)
    if dcount:
        DEV_MD.write_text(dnew)
        print("Updated DEVELOPING.md Generated line to new version")
