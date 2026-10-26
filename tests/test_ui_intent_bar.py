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

    def spy(token: str, request_id: int) -> bool:
        real_append(token, request_id)
        shown.append(win._response_label.get_text())
        return False

    monkeypatch.setattr(win, "_append_token", spy)
    monkeypatch.setattr(window_mod.GLib, "idle_add", lambda fn, *a: fn(*a))
    win._do_query("anything", win._requests.start())
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


def test_long_answer_scrolls_instead_of_growing() -> None:
    win = make_window([])
    win._response_label.set_text("line\n" * 400)
    win._response_label.set_visible(True)
    pump(GLib)
    assert win._response_scroll.get_visible()
    _, natural = win._response_scroll.measure(Gtk.Orientation.VERTICAL, 600)[:2]
    assert natural <= window_mod._RESPONSE_MAX_HEIGHT
    win._response_label.set_visible(False)
    assert not win._response_scroll.get_visible()
    win.destroy()


class RecordingOllama(FakeOllama):
    def __init__(self, available: bool) -> None:
        super().__init__(["ok"], available)
        self.checked_on: list[str] = []

    def is_available(self) -> bool:
        import threading

        self.checked_on.append(threading.current_thread().name)
        return self.available


def test_availability_checked_off_main_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    from tests.gtk_helpers import ThreadRecorder

    rec = ThreadRecorder(window_mod, monkeypatch)
    ollama = RecordingOllama(available=False)
    win = window_mod.IntentBarWindow(ollama_client=ollama, spaces_manager=FakeSpaces())
    win._entry.set_text("hello")
    win._on_activate(win._entry)
    rec.join()
    pump(GLib)
    main = threading.main_thread().name
    assert ollama.checked_on and main not in ollama.checked_on
    assert win._start_ollama_btn.get_visible()
    assert "not running" in win._response_label.get_text()
    assert win._entry.get_sensitive()
    win.destroy()


class BlockingOllama(FakeOllama):
    """Streams one token, then waits until cancelled."""

    def __init__(self) -> None:
        import threading

        super().__init__(["partial"])
        self.release = threading.Event()
        self.cancelled = False

    def generate_stream(self, prompt: str, system: str = "") -> Iterator[str]:
        yield "partial answer"
        self.release.wait(5)
        yield " late"

    def cancel(self) -> None:
        self.cancelled = True
        self.release.set()


def press_escape(win: Any) -> None:
    win._on_key_pressed(None, window_mod.Gdk.KEY_Escape, 0, None)


def test_escape_cancels_first_then_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.gtk_helpers import ThreadRecorder

    rec = ThreadRecorder(window_mod, monkeypatch)
    ollama = BlockingOllama()
    win = window_mod.IntentBarWindow(ollama_client=ollama, spaces_manager=FakeSpaces())
    closed: list[bool] = []
    monkeypatch.setattr(win, "close", lambda: closed.append(True))
    win._entry.set_text("tell me a story")
    win._on_activate(win._entry)
    assert win._stop_btn.get_visible()
    assert not win._entry.get_sensitive()

    press_escape(win)
    assert closed == []
    assert win._response_label.get_text() == "Cancelled"
    assert not win._stop_btn.get_visible()
    assert win._entry.get_sensitive()

    rec.join()
    pump(GLib)
    assert ollama.cancelled
    assert win._response_label.get_text() == "Cancelled"  # late tokens dropped

    press_escape(win)
    assert closed == [True]
    win.destroy()


def test_errors_have_icon_style_and_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    win = make_window(["Partial", "\n[error] connection lost"])
    run_query(win, monkeypatch)
    assert win._response_label.has_css_class("response-error")
    assert win._error_icon.get_visible()
    assert win._response_label.get_text() == "Partial\n\nconnection lost"
    win._set_response("fine")
    assert not win._response_label.has_css_class("response-error")
    assert not win._error_icon.get_visible()
    win._show_ai_offline()
    assert win._start_ollama_btn.get_visible()
    assert win._response_label.get_text() == "Ollama is not running."
    win.destroy()
