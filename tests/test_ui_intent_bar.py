"""GTK smoke tests for the Intent Bar window (skipped without a display)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.gtk_helpers import load_intent_bar_window, pump, require_gtk

Gtk, Adw, GLib = require_gtk()
window_mod = load_intent_bar_window()


class FakeOllama:
    def __init__(self, tokens: list[str] | None = None, available: bool = True) -> None:
        self.model = "llama3.2:3b"
        self.tokens = tokens or []
        self.available = available

    def is_available(self) -> bool:
        return self.available

    def generate_stream(self, prompt: str, system: str = "") -> Iterator[str]:
        yield from self.tokens


class FakeSpace:
    name = "Code"


class FakeSpaces:
    def get_current_space(self) -> FakeSpace:
        return FakeSpace()


def make_window(tokens: list[str]) -> Any:
    win = window_mod.IntentBarWindow(ollama_client=FakeOllama(tokens), spaces_manager=FakeSpaces())
    pump(GLib)
    return win


def run_query(win: Any, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Run _do_query synchronously and record what the label showed."""
    shown: list[str] = []
    real_append = win._append_token

    def spy(token: str) -> bool:
        real_append(token)
        shown.append(win._response_label.get_text())
        return False

    monkeypatch.setattr(win, "_append_token", spy)
    monkeypatch.setattr(window_mod.GLib, "idle_add", lambda fn, *a: fn(*a))
    win._do_query("anything")
    return shown


def test_action_json_never_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    win = make_window(['{"action": ', '"open_app", ', '"app": "firefox"}'])
    executed: list[dict[str, Any]] = []
    monkeypatch.setattr(win, "_execute_action", executed.append)
    shown = run_query(win, monkeypatch)
    assert shown == []
    assert win._response_label.get_text() == "Opening firefox…"
    assert executed == [{"action": "open_app", "app": "firefox"}]
    win.destroy()


def test_plain_text_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    win = make_window(["Hello", " world"])
    shown = run_query(win, monkeypatch)
    assert shown == ["Hello", "Hello world"]
    assert win._response_label.get_text() == "Hello world"
    win.destroy()


def test_json_that_is_not_an_action_is_shown_at_the_end(monkeypatch: pytest.MonkeyPatch) -> None:
    win = make_window(['{"name": ', '"x"}'])
    shown = run_query(win, monkeypatch)
    assert shown == []
    assert win._response_label.get_text() == '{"name": "x"}'
    win.destroy()
