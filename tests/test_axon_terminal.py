"""Axon Terminal must load VTE's GTK 4 binding and track shell exits correctly.

The app is GTK 4, but it requested ``Vte 2.91`` (VTE's GTK 3 build), which
cannot load next to GTK 4, so the terminal never started. Once it did, two
bugs surfaced: ``child-exited`` reports a raw wait status (``exit 3`` -> 768)
that was stored as the exit code, and ``get_text()`` asserts in VTE 0.76 and
returned nothing, so failure diagnosis had no output to work with.
"""

import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TERMINAL_WIDGET = ROOT / "apps" / "axon-terminal" / "terminal_widget.py"


@pytest.mark.unit
def test_requests_gtk4_vte_binding():
    source = TERMINAL_WIDGET.read_text()
    assert re.search(r'require_version\("Gtk", "4\.0"\)', source)
    # Vte 3.91 is the GTK 4 build; 2.91 is GTK 3 and fails to load with GTK 4
    assert re.search(r'require_version\("Vte", "3\.91"\)', source)
    assert '"Vte", "2.91"' not in source


@pytest.mark.unit
def test_iso_ships_gtk4_vte():
    # Parsed exactly like chroot-setup.sh: grep -vE '^\s*(#|$)', one package
    # per line. An inline comment would become part of the apt argument.
    lines = (ROOT / "build" / "config" / "packages.list").read_text().splitlines()
    packages = [ln for ln in lines if not re.match(r"^\s*(#|$)", ln)]
    bad = [p for p in packages if not re.fullmatch(r"[a-z0-9][a-z0-9+.\-]+(:[a-z0-9]+)?", p)]
    assert not bad, f"not bare package names: {bad}"
    assert "gir1.2-vte-3.91" in packages


def _terminal_widget():
    gi = pytest.importorskip("gi", reason="Requires PyGObject (Linux only)")
    try:
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        gi.require_version("Vte", "3.91")
    except ValueError as exc:
        pytest.skip(f"GTK 4 VTE not installed (gir1.2-vte-3.91): {exc}")
    import terminal_widget  # apps/axon-terminal is on sys.path (conftest)

    return terminal_widget


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status", "expected"),
    [(0, 0), (3 << 8, 3), (127 << 8, 127), (9, -9)],  # exit 0, exit 3, not found, SIGKILL
)
def test_exit_code_from_wait_status(status, expected):
    assert _terminal_widget().exit_code_from_status(status) == expected


class _OfflineAI:
    """Stand-in for AIHelper: this test is about VTE, and the real helper would
    open the process-wide shared D-Bus session connection without a main loop,
    breaking later tests that need one."""

    is_available = False


@pytest.mark.integration
def test_shell_runs_and_failed_exit_is_recorded(monkeypatch):
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        pytest.skip("no display (run under xvfb-run)")
    tw_mod = _terminal_widget()
    from gi.repository import GLib, Gtk, Vte

    if not Gtk.init_check():
        pytest.skip("cannot open a display")

    monkeypatch.setattr(tw_mod, "AIHelper", _OfflineAI)
    widget = tw_mod.TerminalWidget()
    window = Gtk.Window()
    window.set_child(widget)
    window.present()
    tab = widget._get_active_tab()
    assert tab is not None

    context = GLib.MainContext.default()

    def run_until(predicate, timeout=10.0):
        deadline = GLib.get_monotonic_time() + int(timeout * 1e6)
        while not predicate() and GLib.get_monotonic_time() < deadline:
            context.iteration(False)
        return predicate()

    try:
        assert run_until(lambda: tab.pid > 0), "shell did not spawn"
        tab.terminal.feed_child(b"echo AXON_$((6*7))_OK; exit 3\n")
        assert run_until(lambda: tab.last_exit_code != 0), "child-exited not seen"
        assert tab.last_exit_code == 3
        assert "AXON_42_OK" in tab.stderr_capture
        assert "AXON_42_OK" in (tab.terminal.get_text_format(Vte.Format.TEXT) or "")
    finally:
        window.destroy()
