"""Check the GNOME Shell extension's JavaScript with node.

GNOME Shell itself can't run in CI, so this covers what can be checked
without it: every extension file must parse as an ES module, and the pure
logic modules (no ``gi://`` imports) are unit tested by ``tests/js/*.test.mjs``.

Files are copied to a temp dir as ``.mjs`` so any node version treats them as
ES modules. Skipped when node is not installed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SHELL_DIR = ROOT / "shell" / "axon-shell"
JS_TESTS = ROOT / "tests" / "js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

_LOCAL_IMPORT = re.compile(r"""(from\s+['"]\./[\w.-]+)\.js(['"])""")


def _copy_as_mjs(dest: Path) -> list[Path]:
    copied = []
    for src in sorted(SHELL_DIR.glob("*.js")):
        target = dest / (src.stem + ".mjs")
        target.write_text(_LOCAL_IMPORT.sub(r"\1.mjs\2", src.read_text(encoding="utf-8")))
        copied.append(target)
    return copied


def test_extension_files_parse(tmp_path: Path) -> None:
    for path in _copy_as_mjs(tmp_path):
        result = subprocess.run(
            [str(NODE), "--check", str(path)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, f"{path.name}: {result.stderr}"


def test_pure_logic_unit_tests(tmp_path: Path) -> None:
    _copy_as_mjs(tmp_path)
    tests = []
    for src in sorted(JS_TESTS.glob("*.test.mjs")):
        shutil.copy(src, tmp_path / src.name)
        tests.append(str(tmp_path / src.name))
    assert tests, "no JS tests found"
    result = subprocess.run(
        [str(NODE), "--test", *tests], capture_output=True, text=True, check=False, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr
