"""GTK smoke tests for the AI panel (skipped without a display)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.gtk_helpers import load_module, pump, require_gtk

Gtk, Adw, GLib = require_gtk()
panel_mod = load_module("axon_ai_panel_ui", "apps/axon-ai-panel/ui/panel.py")


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
