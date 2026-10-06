"""Unit tests for Axon Terminal's shell-exit handling in terminal_widget."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

try:
    import terminal_widget
except (ImportError, ValueError) as exc:  # PyGObject or a Gtk 4 / Adw / Vte 3.91 typelib missing
    pytest.skip(f"Axon Terminal GI dependencies unavailable: {exc}", allow_module_level=True)

_on_child_exited = terminal_widget.TerminalWidget._on_child_exited


def _make_tab() -> terminal_widget._TerminalTab:
    terminal = MagicMock()
    terminal.get_text_format.return_value = "$ make\nmake: *** No targets.  Stop.\n"
    return terminal_widget._TerminalTab(terminal=terminal, label="Terminal", pid=4242)


def _make_widget(*tabs: terminal_widget._TerminalTab) -> SimpleNamespace:
    """Stand-in for TerminalWidget: the handler only needs _tabs and _show_diagnosis_for."""
    return SimpleNamespace(_tabs=list(tabs), _show_diagnosis_for=MagicMock())


class TestChildExited:
    def test_closed_tab_is_not_diagnosed(self):
        tab = _make_tab()
        widget = _make_widget()  # _on_close_page already untracked the tab
        _on_child_exited(widget, tab.terminal, 1, tab)  # shell killed by SIGHUP
        widget._show_diagnosis_for.assert_not_called()
        tab.terminal.get_text_format.assert_not_called()

    def test_live_tab_failure_is_diagnosed(self):
        tab = _make_tab()
        widget = _make_widget(tab)
        _on_child_exited(widget, tab.terminal, 3 << 8, tab)  # shell ran `exit 3`
        widget._show_diagnosis_for.assert_called_once_with(tab)
        tab.terminal.get_text_format.assert_called_once_with(terminal_widget.Vte.Format.TEXT)
        assert "No targets" in tab.stderr_capture
        assert tab.last_exit_code == 3 << 8

    def test_live_tab_clean_exit_is_not_diagnosed(self):
        tab = _make_tab()
        widget = _make_widget(tab)
        _on_child_exited(widget, tab.terminal, 0, tab)
        widget._show_diagnosis_for.assert_not_called()
