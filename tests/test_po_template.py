"""po/axon-os.pot lists every string marked for translation."""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
POT = ROOT / "po" / "axon-os.pot"
JS_CALL = re.compile(r"""\b_t\(\s*(?:'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)")""")


def _pot_entries() -> dict[str, set[str]]:
    """msgid -> files named in its ``#:`` reference comments."""
    entries: dict[str, set[str]] = {}
    refs: set[str] = set()
    current: list[str] | None = None
    for line in [*POT.read_text(encoding="utf-8").splitlines(), ""]:
        if line.startswith("msgid "):
            current = [ast.literal_eval(line[6:])]
            continue
        if line.startswith('"') and current is not None:
            current.append(ast.literal_eval(line))
            continue
        if current is not None:
            entries["".join(current)] = refs
            current, refs = None, set()
        if line.startswith("#: "):
            refs |= set(line[3:].split())
    return entries


def _python_literals() -> list[tuple[str, str]]:
    found = []
    for base in ("apps", "services"):
        for path in sorted((ROOT / base).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in ("_", "N_")
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    found.append((f"{path.relative_to(ROOT)}:{node.lineno}", node.args[0].value))
    return found


def _js_literals() -> list[tuple[str, str]]:
    found = []
    for path in sorted((ROOT / "shell" / "axon-shell").glob("*.js")):
        text = path.read_text(encoding="utf-8")
        for m in JS_CALL.finditer(text):
            raw = m.group(1) if m.group(1) is not None else m.group(2)
            line = text.count("\n", 0, m.start()) + 1
            found.append((f"{path.relative_to(ROOT)}:{line}", re.sub(r"\\(.)", r"\1", raw)))
    return found


def test_marked_strings_are_in_the_template() -> None:
    entries = _pot_entries()
    marked = _python_literals() + _js_literals()
    assert len(marked) > 100
    # The file must be listed too: xgettext skips calls it can't parse (for
    # example inside f-strings) even when another file has the same msgid.
    missing = [
        f"{where}: {text!r}"
        for where, text in marked
        if where.split(":")[0] not in entries.get(text, set())
    ]
    assert not missing, "run scripts/update-pot.sh; missing from po/axon-os.pot:\n" + "\n".join(
        missing
    )


def test_template_header_is_utf8() -> None:
    header = POT.read_text(encoding="utf-8")[:1000]
    assert "charset=UTF-8" in header
    assert '"Project-Id-Version: axon-os\\n"' in header


def test_update_script_is_valid_sh() -> None:
    script = ROOT / "scripts" / "update-pot.sh"
    subprocess.run(["sh", "-n", str(script)], check=True)


def test_shell_metadata_declares_the_domain() -> None:
    import json

    meta = json.loads((ROOT / "shell" / "axon-shell" / "metadata.json").read_text())
    assert meta["gettext-domain"] == "axon-os"


@pytest.mark.skipif(
    subprocess.run(["sh", "-c", "command -v xgettext"], capture_output=True).returncode != 0,
    reason="xgettext not installed",
)
def test_template_is_up_to_date() -> None:
    result = subprocess.run(
        ["sh", str(ROOT / "scripts" / "update-pot.sh"), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
