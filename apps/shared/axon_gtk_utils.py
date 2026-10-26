"""Small GTK helpers shared by the Axon apps.

Apps add ``apps/shared`` to ``sys.path`` and import this module; it sits next
to the apps in the source tree, the .deb and install.sh layouts.
"""

from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk

# services/ is beside apps/ (source tree, .deb) or inside the install dir.
for _base in (Path(__file__).resolve().parents[2], Path(__file__).resolve().parents[1]):
    if (_base / "services").is_dir():
        if str(_base / "services") not in sys.path:
            sys.path.insert(0, str(_base / "services"))
        break
from ui_logic import fit_to_screen


def monitor_size(display: Gdk.Display | None = None) -> tuple[int, int]:
    """Logical size of the first monitor, or (0, 0) when unknown.

    GTK4 has no portable work-area API (Wayland hides it), so callers keep a
    margin for panels and docks.
    """
    display = display or Gdk.Display.get_default()
    if display is None:
        return 0, 0
    monitors = display.get_monitors()
    monitor = monitors.get_item(0) if monitors.get_n_items() else None
    if monitor is None:
        return 0, 0
    geometry = monitor.get_geometry()
    return int(geometry.width), int(geometry.height)


def fit_default_size(window: Gtk.Window, width: int, height: int) -> tuple[int, int]:
    """``set_default_size`` clamped so the window fits a small screen.

    Targets include 1366×768 at 100% and 1280×800 at 125% (1024×640 logical).
    """
    mon_w, mon_h = monitor_size(window.get_display())
    size = (fit_to_screen(width, mon_w), fit_to_screen(height, mon_h))
    window.set_default_size(*size)
    return size
