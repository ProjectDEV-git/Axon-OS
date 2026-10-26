"""App stylesheets: no colours in Python, and every CSS file parses."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MOVED = [
    "apps/intent-bar/ui/window.py",
    "apps/axon-ai-panel/ui/panel.py",
    "apps/axon-welcome/ui/welcome.py",
    "apps/axon-installer/ui/wizard.py",
]
APP_CSS = [
    "apps/intent-bar/ui/intent-bar.css",
    "apps/axon-ai-panel/ui/panel.css",
    "apps/axon-welcome/ui/welcome.css",
    "apps/axon-installer/ui/wizard.css",
]
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")


@pytest.mark.parametrize("path", MOVED)
def test_no_embedded_css_or_hex_colours(path: str) -> None:
    src = (ROOT / path).read_text(encoding="utf-8")
    assert "load_from_data" not in src
    assert not HEX.search(src), HEX.search(src)


@pytest.mark.parametrize("path", APP_CSS)
def test_app_css_uses_tokens_not_hex(path: str) -> None:
    css = (ROOT / path).read_text(encoding="utf-8")
    assert not HEX.search(css), f"{path}: use @axon_* tokens"
    assert "@axon_" in css


@pytest.mark.parametrize("variant", ["", "-light", "-hc"])
def test_css_parses_with_each_token_variant(variant: str) -> None:
    gi = pytest.importorskip("gi")
    try:
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
    except (ImportError, ValueError) as exc:
        pytest.skip(f"GTK4 not available: {exc}")
    errors: list[str] = []
    tokens = Gtk.CssProvider()
    tokens.connect("parsing-error", lambda _p, _s, e: errors.append(f"tokens: {e.message}"))
    tokens.load_from_path(str(ROOT / f"apps/shared/axon-design-tokens{variant}.css"))
    for path in APP_CSS:
        provider = Gtk.CssProvider()
        provider.connect(
            "parsing-error", lambda _p, _s, e, n=path: errors.append(f"{n}: {e.message}")
        )
        provider.load_from_path(str(ROOT / path))
    assert errors == []
