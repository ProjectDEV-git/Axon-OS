"""Tests for the Axon command confirmation card (apps/axon-confirm)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.gtk_helpers import load_module, pump, require_gtk

confirm = load_module("axon_confirm_main", "apps/axon-confirm/main.py")


@pytest.fixture(autouse=True)
def _tmp_approvals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import command_approvals

    path = tmp_path / "approved.json"
    monkeypatch.setattr(command_approvals, "APPROVALS_PATH", path)
    return path


def test_blocked_command_is_declined_without_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(confirm, "build_window", lambda *a: pytest.fail("no UI for blocked"))
    assert confirm.main(["--command", "rm -rf /"]) == confirm.DECLINED
    assert confirm.main(["--command", "ls; rm -rf /"]) == confirm.DECLINED


def test_always_allowed_command_skips_the_card(monkeypatch: pytest.MonkeyPatch) -> None:
    import command_approvals

    command_approvals.add_approval("uptime")
    monkeypatch.setattr(confirm, "build_window", lambda *a: pytest.fail("should not ask"))
    assert confirm.main(["--command", "uptime"]) == confirm.APPROVED


def test_errors_mean_no(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any) -> Any:
        raise RuntimeError("no display")

    monkeypatch.setattr(confirm, "is_approved", boom)
    assert confirm.main(["--command", "ls"]) == confirm.DECLINED


class TestCard:
    @pytest.fixture
    def gtk(self) -> Any:
        return require_gtk()

    def make(self, gtk: Any, command: str, timeout: int = 60) -> Any:
        _Gtk, Adw, _GLib = gtk
        app = Adw.Application(application_id="io.github.axon_os.ConfirmTest")
        app.register(None)
        return confirm.build_window(app, command, "You said: list files", timeout)

    def test_defaults_to_no(self, gtk: Any) -> None:
        win = self.make(gtk, "ls -la")
        assert win.result == confirm.DECLINED
        assert win.command_label.get_text() == "ls -la"
        assert win.command_label.get_use_markup() is False
        win.close()
        assert win.result == confirm.DECLINED

    def test_allow_once(self, gtk: Any, _tmp_approvals: Path) -> None:
        win = self.make(gtk, "df -h")
        win.approve()
        assert win.result == confirm.APPROVED
        assert not _tmp_approvals.exists()  # once, not always

    def test_always_allow_saves_exact_command(self, gtk: Any) -> None:
        import command_approvals

        win = self.make(gtk, "df -h")
        assert win.always_check is not None
        win.always_check.set_active(True)
        assert win.allow_btn.get_label() == "Always Allow"
        win.approve()
        assert command_approvals.load_approvals() == ["df -h"]

    def test_no_always_option_for_risky_commands(self, gtk: Any) -> None:
        win = self.make(gtk, "sort -o out.txt in.txt")
        assert win.always_check is None
        win.destroy()

    def test_timeout_declines(self, gtk: Any) -> None:
        _Gtk, _Adw, GLib = gtk
        win = self.make(gtk, "ls", timeout=1)
        win._remaining = 0
        win._tick()
        pump(GLib)
        assert win.result == confirm.DECLINED

    def test_escape_declines(self, gtk: Any) -> None:
        win = self.make(gtk, "ls")
        win._on_key(None, confirm_keyval_escape(), 0, None)
        assert win.result == confirm.DECLINED


def confirm_keyval_escape() -> int:
    from gi.repository import Gdk

    return int(Gdk.KEY_Escape)
