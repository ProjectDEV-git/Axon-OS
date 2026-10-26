"""GTK smoke tests for the AI panel (skipped without a display)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.gtk_helpers import load_module, pump, require_gtk

Gtk, Adw, GLib = require_gtk()
panel_mod = load_module("axon_ai_panel_ui", "apps/axon-ai-panel/ui/panel.py")


class FakeReport:
    def __init__(self, results: list[Any]) -> None:
        self.results = results


@pytest.fixture(autouse=True)
def _no_real_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    """The status section must not run systemctl/HTTP checks in tests."""
    monkeypatch.setattr(panel_mod, "run_diagnostics", lambda only=None: FakeReport([]))


class FakeClient:
    """Stands in for the Brain D-Bus client."""

    def __init__(self, available: bool = True, chunks: list[str] | None = None) -> None:
        self.model = "llama3.2:3b"
        self.available = available
        self.chunks = chunks if chunks is not None else ["Hello", " there"]
        self.sent: list[str] = []

    def is_available(self) -> bool:
        return self.available

    def list_models(self) -> list[str]:
        return []

    def list_conversations(self) -> list[dict[str, Any]]:
        return []

    def get_messages(self, _conv_id: str) -> list[dict[str, Any]]:
        return []

    def create_conversation(self, title: str = "") -> str:
        return "conv-1"

    def delete_conversation(self, _conv_id: str) -> None:
        pass

    def send_message_stream(
        self, _conv_id: str, message: str, model: str = "", ctx: str = ""
    ) -> Iterator[str]:
        self.sent.append(message)
        yield from self.chunks


class FakeContext:
    def build_context_string(self) -> str:
        return "Active window: Terminal"


@pytest.fixture
def window() -> Any:
    win = panel_mod.AIPanelWindow(ollama_client=FakeClient(), context_reader=FakeContext())
    pump(GLib)
    yield win
    win.destroy()
    pump(GLib)


def test_chip_fills_entry_without_sending(window: Any) -> None:
    window._on_chip_clicked("Summarize: ")
    assert window._entry.get_text() == "Summarize: "
    assert window._client.sent == []
    assert not window._streaming


def test_chip_keeps_existing_text(window: Any) -> None:
    window._entry.set_text("this paragraph")
    window._on_chip_clicked("Translate: ")
    assert window._entry.get_text() == "Translate: this paragraph"
    window._on_chip_clicked("Translate: ")
    assert window._entry.get_text() == "Translate: this paragraph"


def test_offline_check_runs_in_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    from tests.gtk_helpers import ThreadRecorder

    rec = ThreadRecorder(panel_mod, monkeypatch)
    client = FakeClient(available=False)
    seen: list[str] = []
    client.is_available = lambda: seen.append(threading.current_thread().name) or False  # type: ignore[method-assign]
    win = panel_mod.AIPanelWindow(ollama_client=client, context_reader=FakeContext())
    win._send_message("hi")
    assert win._streaming  # UI responds at once; the check happens later
    rec.join()
    pump(GLib)
    assert seen and threading.main_thread().name not in seen
    assert not win._streaming
    win.destroy()


def test_message_streams_into_bubble(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.gtk_helpers import ThreadRecorder

    rec = ThreadRecorder(panel_mod, monkeypatch)
    win = panel_mod.AIPanelWindow(ollama_client=FakeClient(), context_reader=FakeContext())
    win._send_message("hi")
    bubble = win._stream_bubble
    rec.join()
    pump(GLib)
    assert bubble._text == "Hello there"
    assert win._client.sent == ["hi"]
    win.destroy()


class BlockingClient(FakeClient):
    def __init__(self) -> None:
        import threading

        super().__init__()
        self.release = threading.Event()
        self.cancelled = False

    def send_message_stream(
        self, _conv_id: str, message: str, model: str = "", ctx: str = ""
    ) -> Iterator[str]:
        yield "Once upon"
        self.release.wait(5)
        yield " a time"

    def cancel(self) -> None:
        self.cancelled = True
        self.release.set()


def test_escape_stops_stream_then_hides(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.gtk_helpers import ThreadRecorder

    rec = ThreadRecorder(panel_mod, monkeypatch)
    client = BlockingClient()
    win = panel_mod.AIPanelWindow(ollama_client=client, context_reader=FakeContext())
    win.present()
    win._send_message("story")
    assert win._stop_btn.get_visible() and not win._send_btn.get_visible()
    bubble = win._stream_bubble
    for _ in range(100):  # wait for the first chunk to reach the bubble
        pump(GLib)
        if bubble.get_text():
            break
        import time

        time.sleep(0.01)
    escape = panel_mod.Gdk.KEY_Escape
    win._on_key_pressed(None, escape, 0, None)
    assert win.get_visible()
    assert not win._streaming
    assert bubble.get_text() == "Once upon\n\n(Cancelled)"
    assert win._send_btn.get_visible() and not win._stop_btn.get_visible()
    rec.join()
    pump(GLib)
    assert client.cancelled
    assert bubble.get_text() == "Once upon\n\n(Cancelled)"
    win._on_key_pressed(None, escape, 0, None)
    assert not win.get_visible()
    win.destroy()


def test_header_uses_symbolic_icons_and_no_duplicate_badge(window: Any) -> None:
    assert window._clear_btn.get_icon_name() == "user-trash-symbolic"
    assert not hasattr(window, "_model_badge")
    bubble = panel_mod.MessageBubble("assistant", "hi")
    assert bubble._copy_btn.get_icon_name() == "edit-copy-symbolic"
    assert bubble._copy_btn.get_tooltip_text() == "Copy to clipboard"


def test_offline_error_bubble_offers_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.gtk_helpers import ThreadRecorder

    rec = ThreadRecorder(panel_mod, monkeypatch)
    client = FakeClient(available=False)
    win = panel_mod.AIPanelWindow(ollama_client=client, context_reader=FakeContext())
    win._send_message("hi")
    bubble = win._stream_bubble
    rec.join()
    pump(GLib)
    assert bubble._inner.has_css_class("bubble-error")
    assert bubble._error_icon.get_visible()
    labels = []
    child = bubble._btn_row.get_first_child()
    while child is not None:
        labels.append(child.get_label())
        child = child.get_next_sibling()
    assert "Try again" in labels and "Diagnostics" in labels

    client.available = True
    win._retry(bubble, "hi")
    assert bubble.get_parent() is None  # replaced by a fresh answer
    rec.join()
    pump(GLib)
    assert client.sent == ["hi"]
    win.destroy()


def test_stream_error_marker_becomes_error_bubble(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.gtk_helpers import ThreadRecorder

    rec = ThreadRecorder(panel_mod, monkeypatch)
    client = FakeClient(chunks=["Half", "\n[error] model crashed"])
    win = panel_mod.AIPanelWindow(ollama_client=client, context_reader=FakeContext())
    win._send_message("hi")
    bubble = win._stream_bubble
    rec.join()
    pump(GLib)
    assert bubble._inner.has_css_class("bubble-error")
    assert bubble.get_text() == "Half\n\nmodel crashed"
    win.destroy()


def test_default_height_fits_the_monitor(window: Any) -> None:
    import axon_gtk_utils

    _, height = window.get_default_size()
    _, monitor_h = axon_gtk_utils.monitor_size(window.get_display())
    assert monitor_h > 0
    assert height <= max(320, monitor_h - 48)


class CloudClient(FakeClient):
    def routing_info(self) -> tuple[list[dict[str, Any]], dict[str, str]]:
        providers = [
            {"id": "ollama", "label": "Ollama", "base_url": "http://localhost:11434"},
            {"id": "openai", "label": "OpenAI", "base_url": "https://api.openai.com/v1"},
        ]
        return providers, {"general_model": "llama3.2:3b", "deep_model": "@openai/gpt-4.1"}

    def list_models(self) -> list[str]:
        return ["@openai/gpt-4.1"]


def test_origin_label_never_claims_local_for_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.gtk_helpers import ThreadRecorder

    rec = ThreadRecorder(panel_mod, monkeypatch)
    client = CloudClient()
    client.model = ""
    win = panel_mod.AIPanelWindow(ollama_client=client, context_reader=FakeContext())
    assert win._origin_label.get_text() == "Model: checking…"
    rec.join()
    pump(GLib)
    # Automatic routing may use the cloud deep model
    assert win._origin_label.get_text() == "Cloud · OpenAI · gpt-4.1"
    win._model_dropdown.set_selected(win._model_index("llama3.2:3b"))
    assert client.model == "llama3.2:3b"
    assert win._origin_label.get_text() == "Local · llama3.2:3b"
    win._model_dropdown.set_selected(win._model_index("@openai/gpt-4.1"))
    assert win._origin_label.get_text().startswith("Cloud · OpenAI")
    win._model_dropdown.set_selected(0)
    assert client.model == ""
    win.destroy()


class SnapshotContext:
    """A context reader with the per-message API."""

    SNAP = {  # noqa: RUF012
        "active_window": {"title": "main.py", "app": "Code"},
        "active_space": "Code",
        "open_files": ["/p/main.py"],
        "clipboard_history": ["token"],
    }

    def __init__(self) -> None:
        import context_settings

        self.cs = context_settings
        self.calls: list[tuple[frozenset[str], Any]] = []

    def snapshot(self) -> dict[str, Any]:
        return dict(self.SNAP)

    def settings(self) -> Any:
        return self.cs.ContextSettings()

    def context_items(self, snapshot: dict[str, Any] | None = None) -> list[tuple[str, str]]:
        return self.cs.available_items(snapshot or self.SNAP, self.settings())

    def build_context_string(self, exclude: Any = frozenset(), snapshot: Any = None) -> str:
        self.calls.append((frozenset(exclude), snapshot))
        return self.cs.format_context(snapshot or self.SNAP, self.settings(), exclude)


def make_ctx_window(monkeypatch: pytest.MonkeyPatch, client: Any) -> tuple[Any, Any, Any]:
    from tests.gtk_helpers import ThreadRecorder

    rec = ThreadRecorder(panel_mod, monkeypatch)
    reader = SnapshotContext()
    win = panel_mod.AIPanelWindow(ollama_client=client, context_reader=reader)
    rec.join()
    pump(GLib)
    return win, reader, rec


def chip_labels(win: Any) -> list[str]:
    out = []
    child = win._ctx_flow.get_first_child()
    while child is not None:
        btn = child.get_child()
        out.append(btn.get_child().get_first_child().get_label())
        child = child.get_next_sibling()
    return out


def test_context_row_lists_and_removes_items(monkeypatch: pytest.MonkeyPatch) -> None:
    win, reader, rec = make_ctx_window(monkeypatch, FakeClient())
    assert chip_labels(win) == ["Code", "main.py", "clipboard"]
    win._exclude_context("clipboard")
    assert chip_labels(win) == ["Code", "main.py"]
    win._send_message("hi")  # local model: no warning
    rec.join()
    pump(GLib)
    exclude, _snap = reader.calls[-1]
    assert exclude == frozenset({"clipboard"})
    assert win._ctx_excluded == set()  # removal applied to one message only
    win.destroy()


class CloudModelClient(CloudClient):
    def __init__(self) -> None:
        super().__init__()
        self.model = "@openai/gpt-4.1"


def test_cloud_warning_before_first_sensitive_send(monkeypatch: pytest.MonkeyPatch) -> None:
    win, reader, rec = make_ctx_window(monkeypatch, CloudModelClient())
    assert win.current_origin().leaves_computer
    asked: list[str] = []
    monkeypatch.setattr(win, "_confirm_cloud_send", asked.append)
    win._send_message("summarize")
    assert asked == ["summarize"]
    assert win._client.sent == []  # nothing sent before the user answers

    # Closing / cancelling the dialog sends nothing
    win._on_cloud_response(None, "cancel", "summarize")
    assert win._client.sent == []

    # "Send without it" strips clipboard/files/terminal for this message
    win._on_cloud_response(None, "without", "summarize")
    rec.join()
    pump(GLib)
    assert reader.calls[-1][0] >= {"clipboard", "open_files", "terminal_history"}
    assert win._needs_cloud_warning()  # still asks next time

    # "Send" remembers the answer for this conversation
    win._on_cloud_response(None, "send", "again")
    rec.join()
    pump(GLib)
    assert reader.calls[-1][0] == frozenset()
    assert not win._needs_cloud_warning()
    win.destroy()


def test_cloud_dialog_builds_and_defaults_to_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    win, _reader, _rec = make_ctx_window(monkeypatch, CloudModelClient())
    shown: list[Any] = []
    monkeypatch.setattr(panel_mod.Adw.MessageDialog, "present", lambda d: shown.append(d))
    win._confirm_cloud_send("hi")
    dialog = shown[0]
    assert dialog.get_close_response() == "cancel"
    assert dialog.get_default_response() == "cancel"
    assert "clipboard" in dialog.get_body() and "OpenAI" in dialog.get_body()
    dialog.destroy()
    win.destroy()


def test_status_section_shows_problem_and_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    from diagnostics import FAIL, OK, CheckResult

    from tests.gtk_helpers import ThreadRecorder

    results = [
        CheckResult("Axon services", "axon-brain.service", OK, "running"),
        CheckResult(
            "AI", "Ollama", FAIL, "Ollama is not responding", hint="sudo systemctl start ollama"
        ),
    ]
    monkeypatch.setattr(panel_mod, "run_diagnostics", lambda only=None: FakeReport(results))
    rec = ThreadRecorder(panel_mod, monkeypatch)
    client = FakeClient()
    client.search_stats = lambda: {"files": 10}  # type: ignore[attr-defined]
    client.pull_progress = {"model": "llama3.2", "completed": 1, "total": 2, "status": "pulling"}  # type: ignore[attr-defined]
    win = panel_mod.AIPanelWindow(ollama_client=client, context_reader=FakeContext())
    rec.join()
    pump(GLib)
    assert win._status_headline.get_text() == "Axon status: Ollama: Ollama is not responding"
    assert win._status_expander.get_expanded()
    assert win._status_fix.get_text() == "Fix: sudo systemctl start ollama"
    names = [r.name for r in win._status.rows]
    assert names == ["Axon Brain", "Ollama", "Model", "Download", "Semantic index", "Voice"]
    win.destroy()


class ConvClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.convs = [
            {"id": "c1", "title": "Taxes", "updated_at": "2026-02", "archived": 0},
            {"id": "c2", "title": "Old trip", "updated_at": "2026-01", "archived": 1},
        ]
        self.messages = {"c1": [{"role": "user", "content": "hi"}], "c2": []}
        self.calls: list[tuple] = []

    def list_conversations(self) -> list[dict[str, Any]]:
        return [dict(c) for c in self.convs]

    def get_messages(self, conv_id: str) -> list[dict[str, Any]]:
        return self.messages.get(conv_id, [])

    def create_conversation(self, title: str = "") -> str:
        self.calls.append(("create", title))
        return "new"

    def rename_conversation(self, conv_id: str, title: str) -> bool:
        self.calls.append(("rename", conv_id, title))
        return True

    def archive_conversation(self, conv_id: str, archived: bool) -> bool:
        self.calls.append(("archive", conv_id, archived))
        return True

    def delete_conversation(self, conv_id: str) -> None:
        self.calls.append(("delete", conv_id))


def test_conversation_list(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.gtk_helpers import ThreadRecorder

    rec = ThreadRecorder(panel_mod, monkeypatch)
    client = ConvClient()
    win = panel_mod.AIPanelWindow(ollama_client=client, context_reader=FakeContext())
    assert win._conv_id == "c1"  # first non-archived conversation
    win._refresh_conversations()
    rec.join()
    pump(GLib)
    assert [c["id"] for c in win._conv_rows] == ["c1"]
    win._show_archived.set_active(True)
    rec.join()
    pump(GLib)
    assert [c["id"] for c in win._conv_rows] == ["c1", "c2"]

    win._open_conversation("c2")
    rec.join()
    pump(GLib)
    assert win._conv_id == "c2"

    win._archive_conversation("c2", False)
    win._on_delete_response(None, "cancel", "c1")
    win._on_delete_response(None, "delete", "c1")
    rec.join()
    pump(GLib)
    assert ("archive", "c2", False) in client.calls
    assert ("delete", "c1") in client.calls
    assert next(c for c in client.calls if c[0] == "delete") == ("delete", "c1")

    # Deleting the open conversation starts a new one
    win._on_delete_response(None, "delete", "c2")
    rec.join()
    assert win._conv_id == "new"
    win.destroy()


def test_rename_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.gtk_helpers import ThreadRecorder

    rec = ThreadRecorder(panel_mod, monkeypatch)
    client = ConvClient()
    win = panel_mod.AIPanelWindow(ollama_client=client, context_reader=FakeContext())
    row = win._conversation_row(client.convs[0])
    label = row.get_child().get_first_child()
    win._start_rename(row, label, client.convs[0])
    entry = label.get_next_sibling()
    entry.set_text("2026 taxes")
    entry.emit("activate")
    rec.join()
    assert ("rename", "c1", "2026 taxes") in client.calls
    win.destroy()
