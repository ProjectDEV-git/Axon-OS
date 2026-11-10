"""Build checks for the Windows ABI kernel module prototype (kernel/axon-winabi).

The module is not shipped in release images, but it has to keep compiling and
linking: it once called a kernel function that is not exported to modules,
which only shows up at the modpost step.
"""

from __future__ import annotations

import glob
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
KMOD = ROOT / "kernel" / "axon-winabi"

# Kernel functions a loadable module cannot link against
UNEXPORTED = ("do_group_exit", "do_exit")


def _sources() -> list[Path]:
    return sorted(KMOD.glob("*.c"))


def test_module_does_not_call_unexported_kernel_functions():
    for src in _sources():
        code = re.sub(r"/\*.*?\*/|//[^\n]*", "", src.read_text(), flags=re.DOTALL)
        for name in UNEXPORTED:
            assert not re.search(rf"\b{name}\s*\(", code), f"{src.name} calls {name}()"


def test_user_copies_are_checked():
    """copy_to_user/copy_from_user report a fault only through their return value.

    A call whose result is ignored stands alone as a statement: the code
    before it ends a statement or block (``;``, ``{``, ``}``) or closes an
    ``if (...)`` condition. A checked call sits inside a condition.
    """
    for src in _sources():
        code = re.sub(r"/\*.*?\*/|//[^\n]*", "", src.read_text(), flags=re.DOTALL)
        for match in re.finditer(r"\bcopy_(?:to|from)_user\s*\(", code):
            before = code[: match.start()].rstrip()
            line = code.count("\n", 0, match.start()) + 1
            assert before[-1:] not in (
                ";",
                "{",
                "}",
                ")",
            ), f"{src.name}:{line} ignores the result of a user copy"


@pytest.mark.slow
@pytest.mark.timeout(600)
def test_module_builds_against_installed_headers(tmp_path):
    headers = sorted(glob.glob("/usr/src/linux-headers-*-generic"))
    if not headers or not shutil.which("make") or not shutil.which("gcc"):
        pytest.skip("needs linux-headers-*-generic, make and gcc")
    build = tmp_path / "axon-winabi"
    shutil.copytree(KMOD, build, ignore=shutil.ignore_patterns("tests", "dlls"))
    proc = subprocess.run(
        ["make", f"KDIR={headers[-1]}"], cwd=build, capture_output=True, text=True, timeout=570
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, output[-3000:]
    assert (build / "axon-winabi.ko").is_file()
    warnings = [
        ln for ln in output.splitlines() if "warning:" in ln and "compiler differs" not in ln
    ]
    assert not warnings, "\n".join(warnings)


@pytest.mark.parametrize("shim", sorted(p.name for p in (KMOD / "dlls").glob("*.c")))
def test_dll_shims_compile(shim):
    if not shutil.which("gcc"):
        pytest.skip("needs gcc")
    proc = subprocess.run(
        ["gcc", "-fsyntax-only", "-Wall", "-Werror", "-fPIC", str(KMOD / "dlls" / shim)],
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert proc.returncode == 0, proc.stderr
