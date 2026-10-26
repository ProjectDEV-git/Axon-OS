"""Layouts survive GNOME's Large Text (1.25× and 1.5× text scaling)."""

from __future__ import annotations

from typing import Any

import pytest

from tests.gtk_helpers import load_intent_bar_window, load_module, pump, require_gtk

Gtk, Adw, GLib = require_gtk()
panel_mod = load_module("axon_ai_panel_ui_lt", "apps/axon-ai-panel/ui/panel.py")
window_mod = load_intent_bar_window()


class Client:
    model = ""

    def is_available(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return []

    def list_conversations(self) -> list[dict[str, Any]]:
        return []

    def get_messages(self, _conv: str) -> list[dict[str, Any]]:
        return []

    def create_conversation(self, title: str = "") -> str:
        return "c"


class Context:
    def build_context_string(self) -> str:
        return ""


class Spaces:
    def get_current_space(self) -> Any:
        class S:
            name = "Code"

        return S()


@pytest.fixture(params=[1.25, 1.5])
def text_scale(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> float:
    settings = Gtk.Settings.get_default()
    original = settings.get_property("gtk-xft-dpi")
    settings.set_property("gtk-xft-dpi", int(96 * 1024 * request.param))
    monkeypatch.setattr(
        panel_mod, "run_diagnostics", lambda only=None: type("R", (), {"results": []})()
    )
    yield request.param
    settings.set_property("gtk-xft-dpi", original)


def min_width(win: Any) -> int:
    pump(GLib, 100)
    return int(win.get_content().measure(Gtk.Orientation.HORIZONTAL, -1)[0])


def test_ai_panel_fits_its_default_width(text_scale: float) -> None:
    win = panel_mod.AIPanelWindow(ollama_client=Client(), context_reader=Context())
    assert min_width(win) <= 420
    win.destroy()


def test_intent_bar_fits_its_smallest_width(text_scale: float) -> None:
    win = window_mod.IntentBarWindow(ollama_client=Client(), spaces_manager=Spaces())
    assert min_width(win) <= 480  # clamp() lower bound of the bar's width
    win.destroy()
