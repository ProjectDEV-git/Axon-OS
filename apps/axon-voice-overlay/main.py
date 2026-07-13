#!/usr/bin/env python3
import math
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, GLib, Gtk


class VoiceOverlay(Gtk.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.set_title("Axon Voice Overlay")
        self.set_default_size(600, 150)

        # Configure borderless & transparent window
        self.set_decorated(False)

        # Create a drawing area for the waves
        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_draw_func(self.on_draw)
        self.set_child(self.drawing_area)

        # Phase offset for animation
        self.phase = 0.0

        if not hasattr(VoiceOverlay, "_css_loaded"):
            css_provider = Gtk.CssProvider()
            css_provider.load_from_data(
                """
                window {
                    background-color: transparent;
                    background: transparent;
                }
            """,
                -1,
            )
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            VoiceOverlay._css_loaded = True

        # Animation loop (60 FPS)
        self._timer_id = GLib.timeout_add(16, self.on_tick)
        self.connect(
            "destroy", lambda _: GLib.source_remove(self._timer_id) if self._timer_id else None
        )

        # Center the window at the bottom of the screen
        self.present()

    def on_tick(self):
        self.phase += 0.1
        if self.phase > 2 * math.pi:
            self.phase -= 2 * math.pi
        self.drawing_area.queue_draw()
        return True

    def on_draw(self, drawing_area, cr, width, height):
        # Draw translucent overlapping waves (purple, cyan, violet)
        # Main purple wave
        self.draw_wave(
            cr,
            width,
            height,
            amplitude=35,
            frequency=0.015,
            phase_offset=self.phase,
            color=(0.54, 0.36, 0.96, 0.4),
        )

        # Secondary cyan wave
        self.draw_wave(
            cr,
            width,
            height,
            amplitude=25,
            frequency=0.02,
            phase_offset=self.phase * 1.5 + 1.0,
            color=(0.12, 0.73, 0.88, 0.3),
        )

        # Third deep violet wave
        self.draw_wave(
            cr,
            width,
            height,
            amplitude=20,
            frequency=0.01,
            phase_offset=-self.phase * 0.8 + 2.0,
            color=(0.4, 0.2, 0.8, 0.3),
        )

    def draw_wave(self, cr, width, height, amplitude, frequency, phase_offset, color):
        cr.set_source_rgba(*color)
        cr.set_line_width(4)

        mid_y = height / 2.0
        cr.move_to(0, mid_y)

        for x in range(0, width, 2):
            # Apply a gaussian-like envelope so waves taper at the edges
            envelope = math.sin(math.pi * x / width) ** 2
            y = mid_y + math.sin(x * frequency + phase_offset) * amplitude * envelope
            cr.line_to(x, y)

        cr.stroke()


class VoiceOverlayApp(Adw.Application):
    def __init__(self, **kwargs):
        super().__init__(application_id="org.axonos.VoiceOverlay", **kwargs)

    def do_activate(self):
        win = VoiceOverlay(application=self)

        # Apply Wayland/X11 specific window positions
        # In a real compositor, window configuration is handled via shell constraints
        win.set_keep_above(True)


if __name__ == "__main__":
    app = VoiceOverlayApp()
    sys.exit(app.run(sys.argv))
