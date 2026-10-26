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
