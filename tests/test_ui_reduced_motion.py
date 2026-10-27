"""Reduced motion: GTK animations stop when the user turns them off."""

from __future__ import annotations

from tests.gtk_helpers import load_module, require_gtk

Gtk, Adw, GLib = require_gtk()
overlay_mod = load_module("axon_voice_overlay_main", "apps/axon-voice-overlay/main.py")


def test_voice_waves_hold_still_without_animations() -> None:
    settings = Gtk.Settings.get_default()
    original = settings.get_property("gtk-enable-animations")
    win = overlay_mod.VoiceOverlay()
    try:
        settings.set_property("gtk-enable-animations", False)
        before = win.phase
        win.on_tick()
        assert win.phase == before
        settings.set_property("gtk-enable-animations", True)
        win.on_tick()
        assert win.phase != before
    finally:
        settings.set_property("gtk-enable-animations", original)
        win.destroy()
