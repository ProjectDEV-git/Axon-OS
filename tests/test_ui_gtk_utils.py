"""Tests for apps/shared/axon_gtk_utils.py (skipped without a display)."""

from __future__ import annotations

from tests.gtk_helpers import load_module, require_gtk

Gtk, Adw, GLib = require_gtk()
utils = load_module("axon_gtk_utils", "apps/shared/axon_gtk_utils.py")


def test_fit_default_size_clamps_to_monitor() -> None:
    win = Gtk.Window()
    mon_w, mon_h = utils.monitor_size(win.get_display())
    assert mon_w > 0 and mon_h > 0
    width, height = utils.fit_default_size(win, 5000, 5000)
    assert (width, height) == (mon_w - 48, mon_h - 48)
    assert win.get_default_size() == (width, height)
    assert utils.fit_default_size(win, 400, 300) == (400, 300)  # small sizes are kept
    win.destroy()
