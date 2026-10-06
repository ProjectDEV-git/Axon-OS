"""Service entry points must be importable the way systemd runs them.

systemd starts each service as ``python3 services/<dir>/<file>.py``, so only the
script's own directory is on ``sys.path``. Shared modules that live in
``services/`` (``_log_helper``, ``service_base``, ``constants``, ...) can only be
imported after the script adds ``services/`` to ``sys.path``. conftest.py fixes
the path for the rest of the suite, which is why this is checked statically.
"""

import ast
import re
from pathlib import Path

import pytest

SERVICES = Path(__file__).resolve().parents[1] / "services"


def _entry_points():
    entries = set()
    for unit in SERVICES.rglob("*.service"):
        for line in unit.read_text().splitlines():
            m = re.match(r"ExecStart=\S*python3\s+AXON_SERVICES_DIR/(\S+\.py)", line)
            if m:
                entries.add(SERVICES / m.group(1))
    return sorted(entries)


def _shared_modules():
    return {p.stem for p in SERVICES.glob("*.py")}


def _imported_names(node):
    if isinstance(node, ast.Import):
        return [a.name.split(".")[0] for a in node.names]
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        return [node.module.split(".")[0]]
    return []


def _inserts_services_dir(node):
    """True for an ``if ...: sys.path.insert(...)`` or a bare insert."""
    src = ast.unparse(node)
    return "sys.path.insert" in src


def test_entry_points_found():
    assert len(_entry_points()) >= 5


@pytest.mark.parametrize("script", _entry_points(), ids=lambda p: p.parent.name + "/" + p.name)
def test_shared_imports_follow_sys_path_setup(script):
    shared = _shared_modules()
    own_dir = {p.stem for p in script.parent.glob("*.py")}
    path_ready = False
    for node in ast.parse(script.read_text()).body:
        if _inserts_services_dir(node):
            path_ready = True
            continue
        for name in _imported_names(node):
            if name in shared and name not in own_dir:
                assert path_ready, (
                    f"{script.name}:{node.lineno} imports '{name}' from services/ "
                    "before adding services/ to sys.path; the systemd unit would crash"
                )
