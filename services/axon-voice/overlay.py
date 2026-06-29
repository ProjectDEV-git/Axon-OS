"""Glowing waveform overlay shown while Axon Voice is listening.

A small undecorated GTK4 window pinned to the bottom-center of the screen
with an animated bar visualiser, in the spirit of Siri / Copilot capture
chrome. Fades out automatically when listening stops.
"""

from __future__ import annotations

import math
from typing import Any

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib, Gtk

_BAR_COUNT = 24
_ACCENT = (0.545, 0.361, 0.965)  # #8b5cf6 — Axon violet


class VoiceOverlay:
    """Lazy GTK overlay; safe to construct before Gtk.init() has run."""

    def __init__(self) -> None:
        self._window: Gtk.Window | None = None
        self._area: Gtk.DrawingArea | None = None
        self._tick = 0.0
        self._timer_id = 0
        self._status = "Listening..."

    # -- public API ------------------------------------------------------

    def show(self, status: str = "Listening...") -> None:
        self._status = status
        if self._window is None:
            self._build()
        assert self._window is not None
        self._window.present()
        if not self._timer_id:
            self._timer_id = GLib.timeout_add(33, self._on_tick)

    def set_status(self, status: str) -> None:
        self._status = status
        if self._area is not None:
            self._area.queue_draw()

    def hide(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        if self._window is not None:
            self._window.set_visible(False)

    # -- internals -------------------------------------------------------

    def _build(self) -> None:
        win = Gtk.Window()
        win.set_decorated(False)
        win.set_resizable(False)
        win.set_default_size(380, 96)
        win.set_title("Axon Voice")

        css = Gtk.CssProvider()
        css.load_from_data(
            b"""
            .voice-overlay {
                background-color: rgba(9, 9, 15, 0.92);
                border: 1px solid rgba(139, 92, 246, 0.55);
                border-radius: 24px;
            }
            .voice-status {
                color: #c4b5fd;
                font-size: 12px;
            }
            """
        )
        Gtk.StyleContext.add_provider_for_display(
            win.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("voice-overlay")
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(10)
        box.set_margin_end(10)

        area = Gtk.DrawingArea()
        area.set_content_width(340)
        area.set_content_height(44)
        area.set_draw_func(self._draw_wave)
        box.append(area)

        label = Gtk.Label()
        label.add_css_class("voice-status")
        box.append(label)
        self._status_label = label

        win.set_child(box)
        self._window = win
        self._area = area

    def _on_tick(self) -> bool:
        self._tick += 0.18
        if self._area is not None:
            self._area.queue_draw()
        if hasattr(self, "_status_label"):
            self._status_label.set_text(self._status)
        return True

    def _draw_wave(self, _area: Any, cr: Any, width: int, height: int) -> None:
        bar_w = width / (_BAR_COUNT * 1.6)
        gap = bar_w * 0.6
        mid = height / 2.0
        r, g, b = _ACCENT
        for i in range(_BAR_COUNT):
            # Two superimposed sines give an organic "speech" wobble.
            amp = (
                math.sin(self._tick + i * 0.55) * 0.5 + math.sin(self._tick * 1.7 + i * 0.23) * 0.5
            )
            h = max(3.0, abs(amp) * (height * 0.85))
            x = i * (bar_w + gap) + gap
            cr.set_source_rgba(r, g, b, 0.45 + 0.55 * abs(amp))
            cr.rectangle(x, mid - h / 2.0, bar_w, h)
            cr.fill()
