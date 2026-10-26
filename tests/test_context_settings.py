"""Tests for services/context_settings.py."""

from __future__ import annotations

import json
from pathlib import Path

from services.context_settings import (
    ACTIVE_APP,
    CLIPBOARD,
    OPEN_FILES,
    TERMINAL,
    ContextSettings,
    available_items,
    format_context,
    load_settings,
    save_settings,
    sends_sensitive,
)

SNAPSHOT = {
    "active_window": {"title": "main.py - Code", "app": "Code"},
    "active_space": "Code",
    "open_files": ["/home/u/proj/main.py", "/home/u/proj/util.py", "/home/u/notes.md"],
    "terminal_commands": ["pytest -q"],
    "last_stderr": "AssertionError",
    "clipboard_history": ["secret token"],
}


def test_defaults_send_everything_like_before() -> None:
    text = format_context(SNAPSHOT, ContextSettings())
    for fragment in (
        "Active window: main.py - Code",
        "Current space: Code",
        "main.py",
        "$ pytest -q",
        "AssertionError",
        "secret token",
    ):
        assert fragment in text


def test_master_switch_sends_nothing() -> None:
    settings = ContextSettings(share_context=False)
    assert format_context(SNAPSHOT, settings) == ""
    assert available_items(SNAPSHOT, settings) == []


def test_each_switch() -> None:
    settings = ContextSettings(share_clipboard=False, share_terminal_history=False)
    text = format_context(SNAPSHOT, settings)
    assert "secret token" not in text and "pytest" not in text and "AssertionError" not in text
    assert "main.py" in text
    settings = ContextSettings(share_open_files=False, share_active_app=False)
    text = format_context(SNAPSHOT, settings)
    assert "util.py" not in text and "Active window" not in text and "Current space" not in text


def test_exclude_for_one_message() -> None:
    text = format_context(SNAPSHOT, ContextSettings(), exclude={CLIPBOARD, OPEN_FILES})
    assert "secret token" not in text and "util.py" not in text
    assert "pytest" in text


def test_available_items_describe_what_is_sent() -> None:
    items = available_items(SNAPSHOT, ContextSettings())
    assert items == [
        (ACTIVE_APP, "Code"),
        (OPEN_FILES, "main.py, util.py +1"),
        (TERMINAL, "terminal history"),
        (CLIPBOARD, "clipboard"),
    ]
    assert sends_sensitive(items, set())
    assert not sends_sensitive(items, {OPEN_FILES, TERMINAL, CLIPBOARD})
    assert not sends_sensitive([(ACTIVE_APP, "Code")], set())


def test_empty_snapshot() -> None:
    assert format_context({}, ContextSettings()) == ""
    assert (
        available_items({"active_window": {"title": "None", "app": "None"}}, ContextSettings())
        == []
    )


def test_load_and_save_keep_other_keys(tmp_path: Path) -> None:
    path = tmp_path / "axon-os" / "context.json"
    assert load_settings(path) == ContextSettings()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"track_clipboard": False, "share_clipboard": "yes"}))
    assert load_settings(path).share_clipboard is True  # non-bool ignored
    save_settings(ContextSettings(share_clipboard=False), path)
    data = json.loads(path.read_text())
    assert data["track_clipboard"] is False and data["share_clipboard"] is False
    assert load_settings(path).share_clipboard is False
    assert (path.stat().st_mode & 0o777) == 0o600


def test_load_bad_file(tmp_path: Path) -> None:
    path = tmp_path / "context.json"
    path.write_text("[1, 2]")
    assert load_settings(path) == ContextSettings()
    path.write_text("{bad")
    assert load_settings(path) == ContextSettings()


def test_context_service_honours_settings(monkeypatch) -> None:
    from unittest.mock import MagicMock

    from services.axon_context import context_service as cs

    service = cs.ContextService.__new__(cs.ContextService)
    service.active_window_title = "Inbox"
    service.active_window_app = "Thunderbird"
    service.active_space = "Personal"
    service._get_open_files = MagicMock(return_value=["/tmp/a.txt"])
    service._get_terminal_commands = MagicMock(return_value=["ls"])
    service._get_last_stderr = MagicMock(return_value=None)
    service._get_clipboard_snapshot = MagicMock(return_value=["copied"])
    getter = cs.ContextService.GetContextString.__wrapped__  # skip the rate limiter

    monkeypatch.setattr(cs, "load_settings", lambda: ContextSettings(share_clipboard=False))
    text = getter(service)
    assert "Inbox" in text and "a.txt" in text and "copied" not in text
    service._get_clipboard_snapshot.assert_not_called()  # not even collected

    monkeypatch.setattr(cs, "load_settings", lambda: ContextSettings(share_context=False))
    assert getter(service) == ""


def test_ai_panel_context_reader(monkeypatch) -> None:
    import importlib.util
    import sys

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "axon_panel_context_reader", root / "apps/axon-ai-panel/context_reader.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["axon_panel_context_reader"] = mod
    spec.loader.exec_module(mod)

    class FakeContextObj:
        def GetActiveContext(self) -> str:
            return json.dumps(SNAPSHOT)

    reader = mod.ContextReader.__new__(mod.ContextReader)
    reader.bus = None
    reader.context_obj = FakeContextObj()
    monkeypatch.setattr(mod, "load_settings", lambda: ContextSettings(share_terminal_history=False))
    assert [k for k, _ in reader.context_items()] == [ACTIVE_APP, OPEN_FILES, CLIPBOARD]
    text = reader.build_context_string(exclude={CLIPBOARD})
    assert "main.py" in text and "pytest" not in text and "secret token" not in text
    monkeypatch.setattr(mod, "load_settings", lambda: ContextSettings(share_context=False))
    assert reader.build_context_string() == ""
    reader.context_obj = None
    assert reader.snapshot() == {}


def test_sent_files() -> None:
    from services.context_settings import sent_files

    assert sent_files(SNAPSHOT, ContextSettings()) == SNAPSHOT["open_files"]
    assert sent_files(SNAPSHOT, ContextSettings(), exclude={OPEN_FILES}) == []
    assert sent_files(SNAPSHOT, ContextSettings(share_open_files=False)) == []
    assert sent_files(None, ContextSettings()) == []
