#!/usr/bin/env python3
"""
Axon Shortcuts Overlay — Keyboard shortcut cheat sheet for Axon OS.

Activated via Super+/ or Super+? to display all available keybindings
in a floating overlay grouped by category. Press Escape or the shortcut
key again to dismiss.
"""

from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gtk, Pango

# Shared GTK helpers live in apps/shared next to every app
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
from axon_gtk_utils import load_app_css

# Translations (services/i18n.py); plain English when services/ isn't found
for _base in (*Path(__file__).resolve().parents, Path("/usr/lib/axon")):
    if (_base / "services" / "i18n.py").is_file():
        sys.path.insert(0, str(_base / "services"))
        break
try:
    from i18n import N_, _
except ImportError:

    def _(text: str) -> str:  # type: ignore[no-redef]
        return text

    N_ = _


# Shortcut definitions grouped by category
SHORTCUTS = [
    {
        "title": N_("Spaces and Navigation"),
        "icon": "view-grid-symbolic",
        "bindings": [
            ("Super + 1–9", N_("Switch to Space 1–9")),
            ("Super + Shift + 1–9", N_("Move window to Space 1–9")),
            ("Super + Tab", N_("Switch between Spaces")),
            ("Super + J", N_("Search Spaces and windows, then jump")),
            ("Alt + Tab", N_("Switch between windows")),
        ],
    },
    {
        "title": N_("AI and Assistant"),
        "icon": "axon-ai-panel-symbolic",
        "bindings": [
            ("Super + Space", N_("Open Intent Bar (AI command palette)")),
            ("Super + A", N_("Toggle AI Side Panel")),
            ("Super + V", N_("Push-to-talk Voice Input")),
            ("Ctrl + Shift + A", N_("Terminal AI helper (in Axon Terminal)")),
        ],
    },
    {
        "title": N_("System"),
        "icon": "axon-settings-symbolic",
        "bindings": [
            ("Super", N_("Open Start Menu")),
            ("Super + /", N_("Show this shortcut overlay")),
            ("Super + L", N_("Lock screen")),
            ("Ctrl + Alt + T", N_("Open terminal")),
            ("Print Screen", N_("Screenshot")),
        ],
    },
    {
        "title": N_("Windows"),
        "icon": "focus-windows-symbolic",
        "bindings": [
            ("Super + ↑", N_("Maximize window")),
            ("Super + ↓", N_("Restore / unmaximize")),
            ("Super + ←", N_("Tile window left")),
            ("Super + →", N_("Tile window right")),
            ("Alt + F4", N_("Close window")),
        ],
    },
    {
        "title": N_("Gaming"),
        "icon": "input-gaming-symbolic",
        "bindings": [
            ("Right Shift + F12", N_("Show or hide the FPS overlay (in games)")),
            ("Right Shift + F11", N_("Move the FPS overlay (in games)")),
            ("Shift + Tab", N_("Steam overlay (in games)")),
        ],
    },
    {
        "title": N_("Axon Terminal"),
        "icon": "axon-terminal-symbolic",
        "bindings": [
            ("Ctrl + Shift + T", N_("New tab")),
            ("Ctrl + Shift + W", N_("Close tab")),
            ("Ctrl + Page Up/Down", N_("Switch tabs")),
            ("Ctrl + Shift + C", N_("Copy selection")),
            ("Ctrl + Shift + V", N_("Paste")),
            ("Ctrl + Shift + A", N_("AI command bar")),
        ],
    },
]


class ShortcutsWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.set_title(_("Keyboard Shortcuts"))
        self.set_default_size(720, 560)
        self.set_resizable(True)

        load_app_css(Path(__file__).resolve().parent / "main.css")

        # Close on Escape key
        esc_controller = Gtk.EventControllerKey()
        esc_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(esc_controller)

        # Main layout
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(root)

        # Header bar
        header = Adw.HeaderBar()
        header.add_css_class("shortcuts-header")
        root.append(header)

        # Title area
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        title_box.set_halign(Gtk.Align.CENTER)
        title_box.set_margin_top(16)
        title_box.set_margin_bottom(8)

        title_icon = Gtk.Image.new_from_icon_name("axon-shortcuts")
        title_icon.set_pixel_size(32)
        title_icon.add_css_class("title-icon")
        title_box.append(title_icon)

        title_label = Gtk.Label(label=_("Keyboard Shortcuts"))
        title_label.add_css_class("overlay-title")
        title_box.append(title_label)

        root.append(title_box)

        subtitle = Gtk.Label(label=_("Press Escape to close"))
        subtitle.add_css_class("overlay-subtitle")
        subtitle.set_margin_bottom(12)
        root.append(subtitle)

        # Scrollable content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        root.append(scrolled)

        # Two-column flow layout using a Gtk.FlowBox-like approach with Grid
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_start(32)
        content.set_margin_end(32)
        content.set_margin_top(8)
        content.set_margin_bottom(32)
        scrolled.set_child(content)

        # Build shortcut groups in a 2-column grid
        grid = Gtk.Grid()
        grid.set_column_spacing(24)
        grid.set_row_spacing(16)
        grid.set_column_homogeneous(True)
        content.append(grid)

        for idx, group in enumerate(SHORTCUTS):
            col = idx % 2
            row = idx // 2
            card = self._build_group_card(group)
            grid.attach(card, col, row, 1, 1)

    def _build_group_card(self, group: dict) -> Gtk.Widget:
        """Build a styled card for a shortcut group."""
        frame = Gtk.Frame()
        frame.add_css_class("shortcut-card")

        card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card_box.set_margin_start(16)
        card_box.set_margin_end(16)
        card_box.set_margin_top(14)
        card_box.set_margin_bottom(14)
        frame.set_child(card_box)

        # Group header
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        icon_label = Gtk.Image.new_from_icon_name(group["icon"])
        icon_label.add_css_class("group-icon")
        header_box.append(icon_label)

        title = Gtk.Label(label=_(group["title"]))
        title.add_css_class("group-title")
        title.set_xalign(0)
        header_box.append(title)
        card_box.append(header_box)

        # Separator
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.add_css_class("group-separator")
        card_box.append(sep)

        # Bindings list
        for keys, description in group["bindings"]:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.set_margin_top(4)
            row.set_margin_bottom(4)

            # Key badge(s)
            key_label = Gtk.Label(label=keys)
            key_label.add_css_class("key-badge")
            key_label.set_halign(Gtk.Align.START)
            # Fixed key column so descriptions share one left edge and get
            # the rest of the width instead of wrapping word by word
            key_label.set_size_request(150, -1)
            key_label.set_valign(Gtk.Align.CENTER)
            row.append(key_label)

            # Description
            desc_label = Gtk.Label(label=_(description))
            desc_label.add_css_class("key-desc")
            desc_label.set_xalign(0)
            desc_label.set_hexpand(True)
            desc_label.set_wrap(True)
            desc_label.set_wrap_mode(Pango.WrapMode.WORD)
            row.append(desc_label)

            card_box.append(row)

        return frame

    def _on_key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False


class ShortcutsApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="io.github.axon_os.ShortcutsOverlay")
        self._window: ShortcutsWindow | None = None

    def do_activate(self) -> None:
        if self._window is None:
            self._window = ShortcutsWindow(application=self)
        self._window.present()


def main() -> int:
    app = ShortcutsApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
