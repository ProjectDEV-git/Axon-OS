"""Shared pytest setup for Axon OS.

Service code lives in hyphenated directories (``services/axon-brain`` …) so the
on-disk layout matches the D-Bus/service naming convention. Hyphens are not
valid Python identifiers, so the test suite imports those modules under the
equivalent underscore package names (``services.axon_brain.brain_service``).

This conftest registers the underscore package names as aliases that point at
the real hyphenated directories, letting the import system load submodules from
them. It is import-only — no service is instantiated here.
"""

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Service subdirectories that tests import from directly.
for _subdir in (
    "services",
    "services/axon-search",
    "services/axon-voice",
    "services/axon-sandbox",
    "services/axon-gui-agent",
    "services/axon-brain",
    "apps/axon-installer",
    "apps/axon-terminal",
):
    _full = str(ROOT / _subdir)
    if _full not in sys.path:
        sys.path.insert(0, _full)

# Underscore package name -> real hyphenated directory.
_SERVICE_PACKAGES = {
    "services.axon_brain": ROOT / "services" / "axon-brain",
    "services.axon_context": ROOT / "services" / "axon-context",
    "services.axon_search": ROOT / "services" / "axon-search",
    "services.axon_voice": ROOT / "services" / "axon-voice",
    "services.axon_gui_agent": ROOT / "services" / "axon-gui-agent",
    "services.axon_sandbox": ROOT / "services" / "axon-sandbox",
}


def _ensure_namespace(name: str, path: Path) -> None:
    """Register ``name`` as a package whose submodules load from ``path``."""
    if name in sys.modules:
        return
    pkg = types.ModuleType(name)
    pkg.__path__ = [str(path)]  # marks it a package for the import machinery
    sys.modules[name] = pkg


# 'services' is a real directory but ships no __init__.py; expose it as a
# namespace package so the dotted aliases below have a parent to hang off.
_ensure_namespace("services", ROOT / "services")
for _pkg_name, _directory in _SERVICE_PACKAGES.items():
    if _directory.is_dir():
        _ensure_namespace(_pkg_name, _directory)
