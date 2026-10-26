"""GTK smoke tests for the Intent Bar window (skipped without a display)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.gtk_helpers import load_intent_bar_window, pump, require_gtk

Gtk, Adw, GLib = require_gtk()
window_mod = load_intent_bar_window()


@pytest.fixture(autouse=True)
def _tmp_history(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never touch the real ~/.axon history from tests."""
    monkeypatch.setattr(window_mod, "_HISTORY_FILE", tmp_path / "history.json")


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
    assert win._response_label.get_text().startswith("Opening firefox…")
    assert executed == []  # waits for Enter (action preview)
    win._on_activate(win._entry)
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


def test_loading_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    win = make_window([])
    stages: list[str | None] = []
    real = win._set_stage

    def spy(stage: str | None, request_id: int | None = None) -> bool:
        stages.append(stage)
        return real(stage, request_id)

    monkeypatch.setattr(win, "_set_stage", spy)
    win._ollama.tokens = ['{"action"', ': "open_app", "app": "x"}']
    monkeypatch.setattr(win, "_execute_action", lambda _a: None)
    monkeypatch.setattr(window_mod.GLib, "idle_add", lambda fn, *a: fn(*a))
    rid = win._requests.start()
    win._set_busy(True)
    win._do_query("open x", rid)
    assert stages[:2] == [window_mod.STAGE_WAITING, "Planning action…"]
    assert not win._stage_label.get_visible()  # cleared when done
    win.destroy()


def test_empty_state_examples_fill_entry_and_hide() -> None:
    win = make_window([])
    assert win._empty_box.get_visible()
    labels = []
    child = win._empty_box.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Button):
            labels.append(child.get_label())
        child = child.get_next_sibling()
    assert window_mod.EXAMPLE_PROMPTS[0] in labels
    win._fill_entry("Open Firefox")
    assert win._entry.get_text() == "Open Firefox"
    assert not win._empty_box.get_visible()
    win._entry.set_text("")
    assert win._empty_box.get_visible()
    win.destroy()


def test_history_is_saved_and_offered(monkeypatch: pytest.MonkeyPatch) -> None:
    win = make_window([])
    win._push_history("df -h please")
    win.destroy()
    again = make_window([])
    assert again._history[0] == "df -h please"
    texts = []
    child = again._empty_box.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Button):
            texts.append(child.get_label())
        child = child.get_next_sibling()
    assert "df -h please" in texts
    again.destroy()


class SpacesWithList(FakeSpaces):
    def get_spaces(self) -> list[Any]:
        class S:
            def __init__(self, name: str) -> None:
                self.name = name

        return [S("Code"), S("Web")]


def suggestion_window(monkeypatch: pytest.MonkeyPatch, tokens: list[str] | None = None) -> Any:
    monkeypatch.setattr(
        window_mod, "_installed_apps", lambda: [("firefox.desktop", "Firefox", "web browser")]
    )
    win = window_mod.IntentBarWindow(
        ollama_client=FakeOllama(tokens or []), spaces_manager=SpacesWithList()
    )
    pump(GLib)
    return win


def test_typing_shows_grouped_suggestions_with_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    win = suggestion_window(monkeypatch)
    win._entry.set_text("fire")
    assert win._suggest_list.get_visible()
    assert [s.category for s in win._suggestions] == ["Applications", "Files", "Ask AI"]
    assert win._preview_label.get_text() == "Enter: Open Firefox"
    win._move_selection(1)
    assert win._preview_label.get_text() == "Enter: Search your files for “fire”"
    win._move_selection(5)  # clamps at the last row
    assert win._selected_suggestion().category == "Ask AI"
    win.destroy()


def test_up_down_navigate_suggestions_not_history(monkeypatch: pytest.MonkeyPatch) -> None:
    win = suggestion_window(monkeypatch)
    win._history = ["older query"]
    win._entry.set_text("web")
    win._on_key_pressed(None, window_mod.Gdk.KEY_Down, 0, None)
    assert win._entry.get_text() == "web"
    assert win._selected_suggestion() is not None
    win.destroy()


def test_enter_runs_command_suggestion_through_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    win = suggestion_window(monkeypatch)
    ran: list[str] = []
    monkeypatch.setattr(win, "_run_command", ran.append)
    win._entry.set_text("disk")
    while win._selected_suggestion().category != "Commands":
        win._move_selection(1)
    win._on_activate(win._entry)
    assert ran == ["df -h"]
    win.destroy()


def test_ask_ai_row_sends_to_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    win = suggestion_window(monkeypatch)
    started: list[str] = []
    monkeypatch.setattr(win, "_do_query", lambda q, rid: started.append(q))
    monkeypatch.setattr(window_mod.threading, "Thread", _InlineThread)
    win._entry.set_text("what is a symlink")
    assert win._selected_suggestion().category == "Ask AI"  # questions go to the AI
    win._on_activate(win._entry)
    assert started == ["what is a symlink"]
    assert not win._suggest_list.get_visible()
    win.destroy()


class _InlineThread:
    def __init__(self, target: Any = None, args: tuple = (), daemon: bool = False) -> None:
        self._target, self._args = target, args

    def start(self) -> None:
        if self._target is not None:
            self._target(*self._args)


def test_ai_open_app_waits_for_enter(monkeypatch: pytest.MonkeyPatch) -> None:
    win = make_window(['{"action": "open_app", "app": "firefox"}'])
    executed: list[dict[str, Any]] = []
    monkeypatch.setattr(win, "_execute_action", executed.append)
    run_query(win, monkeypatch)
    assert executed == []
    assert "Press Enter to confirm" in win._response_label.get_text()
    win._on_activate(win._entry)
    assert executed == [{"action": "open_app", "app": "firefox"}]
    win.destroy()


def test_escape_cancels_a_waiting_action(monkeypatch: pytest.MonkeyPatch) -> None:
    win = make_window(['{"action": "open_app", "app": "firefox"}'])
    closed: list[bool] = []
    monkeypatch.setattr(win, "close", lambda: closed.append(True))
    run_query(win, monkeypatch)
    press_escape(win)
    assert win._pending_action is None and closed == []
    assert win._response_label.get_text() == "Cancelled"
    win.destroy()


def test_command_output_is_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess as sp

    win = make_window([])

    class Proc:
        def communicate(self, timeout: float) -> tuple[str, None]:
            return "Filesystem Size\n/dev/sda1 100G\n", None

    monkeypatch.setattr(window_mod, "confirm_and_exec", lambda cmd, **kw: Proc())
    monkeypatch.setattr(window_mod.threading, "Thread", _InlineThread)
    monkeypatch.setattr(window_mod.GLib, "idle_add", lambda fn, *a: fn(*a))
    win._run_command("df -h")
    assert win._response_label.get_text() == "Filesystem Size\n/dev/sda1 100G"
    monkeypatch.setattr(window_mod, "confirm_and_exec", lambda cmd, **kw: None)
    win._run_command("df -h")
    assert win._response_label.get_text() == "Not run."
    assert sp  # subprocess is used for the PIPE flags in the real call
    win.destroy()
