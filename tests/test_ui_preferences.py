"""Tests for the Axon Settings preferences window and shell settings helper."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.gtk_helpers import ROOT, load_module, pump, require_gtk

Gtk, Adw, GLib = require_gtk()
from gi.repository import Gio

shell_settings = load_module("axon_shell_settings", "apps/axon-settings/shell_settings.py")
prefs = load_module("axon_preferences_window", "apps/axon-settings/preferences_window.py")
SCHEMA_DIR = ROOT / "shell" / "axon-shell" / "schemas"


def memory_settings() -> Any:
    source = Gio.SettingsSchemaSource.new_from_directory(str(SCHEMA_DIR), None, False)
    schema = source.lookup(shell_settings.SCHEMA_ID, False)
    return Gio.Settings.new_full(schema, Gio.memory_settings_backend_new(), None)


def test_schema_dirs_order(tmp_path: Path) -> None:
    dirs = shell_settings.schema_dirs(home=tmp_path, data_dirs="/a:/b")
    assert dirs[0] == tmp_path / ".local/share/gnome-shell/extensions/axon-shell@axon-os/schemas"
    assert Path("/a/gnome-shell/extensions/axon-shell@axon-os/schemas") in dirs
    assert dirs[-1] == SCHEMA_DIR


def test_load_from_checkout() -> None:
    settings = shell_settings.load_shell_settings([SCHEMA_DIR])
    assert settings is not None
    assert "auto-route-windows" in settings.props.settings_schema.list_keys()


def test_load_missing(tmp_path: Path) -> None:
    assert shell_settings.load_shell_settings([tmp_path]) is None


def test_describe_rule() -> None:
    assert shell_settings.describe_rule("gimp", "__never__") == ("gimp", "Never moved")
    assert shell_settings.describe_rule("firefox", "Web") == ("firefox", "Always goes to Web")


def test_rules_listed_and_removable() -> None:
    settings = memory_settings()
    settings.set_value(
        "space-rules", GLib.Variant("a{ss}", {"firefox": "Web", "gimp": "__never__"})
    )
    win = prefs.PreferencesWindow(shell_settings=settings)
    pump(GLib)
    assert len(win._rule_rows) == 2
    win._remove_rule("gimp")
    pump(GLib)
    assert dict(settings.get_value("space-rules").unpack()) == {"firefox": "Web"}
    assert len(win._rule_rows) == 1
    win.destroy()


def test_without_extension_shows_explanation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prefs, "load_shell_settings", lambda: None)
    win = prefs.PreferencesWindow()
    assert win._shell is None
    win.destroy()


def test_privacy_switches_write_context_settings(tmp_path: Path) -> None:
    import context_settings

    path = tmp_path / "context.json"
    win = prefs.PreferencesWindow(shell_settings=memory_settings(), context_path=path)
    win._context_switches["share_clipboard"].set_active(False)
    assert context_settings.load_settings(path).share_clipboard is False
    win._context_master.set_active(False)
    loaded = context_settings.load_settings(path)
    assert loaded.share_context is False
    assert not win._context_switches["share_open_files"].get_sensitive()
    win.destroy()


def test_always_allowed_commands_listed_and_cleared(tmp_path: Path) -> None:
    import command_approvals

    path = tmp_path / "approved.json"
    command_approvals.add_approval("df -h", path)
    command_approvals.add_approval("uptime", path)
    win = prefs.PreferencesWindow(
        shell_settings=memory_settings(), context_path=tmp_path / "c.json", approvals_path=path
    )
    assert [r.get_title() for r in win._approval_rows] == ["uptime", "df -h"]
    win._remove_approval("uptime")
    assert command_approvals.load_approvals(path) == ["df -h"]
    win._clear_approvals()
    assert command_approvals.load_approvals(path) == []
    assert [r.get_title() for r in win._approval_rows] == ["None"]
    win.destroy()
