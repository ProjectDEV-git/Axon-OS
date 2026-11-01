"""AI panel: clickable files the agent used, and the memories popover."""

from typing import Any

import pytest
from gi.repository import GLib
from ollama_client import tool_sources

from tests.test_ui_ai_panel import FakeClient, FakeContext, panel_mod, pump


class AgentClient(FakeClient):
    def __init__(self) -> None:
        super().__init__(chunks=["The tax form is in Documents."])
        self.tool_steps: list[dict[str, Any]] = []
        self.facts = [{"id": 1, "text": "Prefers metric units"}]
        self.calls: list[tuple] = []

    def send_message_stream(self, _conv_id, message, model="", ctx=""):
        self.tool_steps = [
            {"name": "search_files", "result": "/home/u/Documents/tax.pdf: 2025 return"},
            {"name": "calculate", "result": "14"},
        ]
        yield from self.chunks

    def list_memories(self):
        return list(self.facts)

    def add_memory(self, text):
        self.calls.append(("add", text))
        self.facts.insert(0, {"id": 9, "text": text})
        return True

    def forget_memory(self, fid):
        self.calls.append(("forget", fid))
        self.facts = [f for f in self.facts if f["id"] != fid]
        return True

    def clear_memories(self):
        self.calls.append(("clear",))
        self.facts = []
        return True


class TestToolSources:
    def test_parses_and_dedupes(self):
        steps = [
            {"name": "search_files", "result": "/a/x.txt: hi\n/b/y.md: there\n/a/x.txt: again"},
            {"name": "ask_files", "result": "[/c/z.pdf]\npassage\n\n[/a/x.txt]\nmore"},
            {"name": "search_files", "result": "error: Search offline"},
            {"name": "calculate", "result": "/not/a/file: 3"},
        ]
        assert tool_sources(steps) == ["/a/x.txt", "/b/y.md", "/c/z.pdf"]

    def test_limit(self):
        result = "\n".join(f"/f{i}: s" for i in range(20))
        assert len(tool_sources([{"name": "search_files", "result": result}])) == 8


@pytest.fixture
def agent_window(monkeypatch):
    from tests.gtk_helpers import ThreadRecorder

    rec = ThreadRecorder(panel_mod, monkeypatch)
    win = panel_mod.AIPanelWindow(ollama_client=AgentClient(), context_reader=FakeContext())
    yield win, rec
    win.destroy()
    pump(GLib)


def test_answer_shows_used_files_and_tools(agent_window):
    win, rec = agent_window
    win._send_message("where is my tax form")
    bubble = win._stream_bubble
    rec.join()
    pump(GLib)
    assert len(bubble.found_files()) == 1
    assert bubble.found_files()[0].endswith("Documents/tax.pdf")
    assert "search_files, calculate" in bubble._meta_label.get_text()


def test_memories_list_add_forget(agent_window):
    win, rec = agent_window
    win._refresh_memories()
    rec.join()
    pump(GLib)
    assert [f["text"] for f in win._memories] == ["Prefers metric units"]
    win._mem_entry.set_text("Uses fish shell")
    win._add_memory()
    rec.join()
    pump(GLib)
    assert ("add", "Uses fish shell") in win._client.calls
    assert win._mem_entry.get_text() == ""
    win._forget_memory(1)
    rec.join()
    pump(GLib)
    assert [f["text"] for f in win._memories] == ["Uses fish shell"]


def test_forget_all_needs_confirmation(agent_window):
    win, rec = agent_window
    win._on_clear_memories_response(None, "cancel")
    rec.join()
    assert win._client.calls == []
    win._on_clear_memories_response(None, "forget")
    rec.join()
    pump(GLib)
    assert win._client.calls == [("clear",)]
    assert win._memories == []
    assert not win._mem_clear_btn.get_sensitive()
