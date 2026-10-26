"""Static accessibility checks on app and Shell source."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ICON_BUTTON = re.compile(
    r"(\w+(?:\.\w+)*)\s*=\s*Gtk\.(?:Button|MenuButton)(?:\.new_from_icon_name\(|\(icon_name=)"
)


def _python_files() -> list[Path]:
    return sorted(p for p in (ROOT / "apps").rglob("*.py") if "__pycache__" not in p.parts)


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_icon_only_buttons_have_accessible_labels(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        m = ICON_BUTTON.search(line)
        if not m:
            continue
        name = m.group(1)
        window = "\n".join(lines[i : i + 12])
        assert (
            f"{name}.update_property([Gtk.AccessibleProperty.LABEL]" in window
        ), f"{path.relative_to(ROOT)}:{i + 1}: icon-only button {name} needs an accessible label"


def test_shell_icon_buttons_have_accessible_names() -> None:
    for path in sorted((ROOT / "shell" / "axon-shell").glob("*.js")):
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"new St\.Button\(\{(.*?)\}\);", src, re.DOTALL):
            body = m.group(1)
            if "child: new St.Icon" in body:
                assert "accessible_name" in body, f"{path.name}: icon St.Button without a name"
