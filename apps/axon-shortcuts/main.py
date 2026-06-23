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
from gi.repository import Adw, Gdk, Gtk, Pango

# Shortcut definitions grouped by category
SHORTCUTS = [
    {
        "title": "Spaces & Navigation",
        "icon": "🏠",
        "bindings": [
            ("Super + 1–9", "Switch to Space 1–9"),
            ("Super + Shift + 1–9", "Move window to Space 1–9"),
            ("Super + Tab", "Switch between Spaces"),
            ("Alt + Tab", "Switch between windows"),
        ],
    },
    {
        "title": "AI & Assistant",
        "icon": "🧠",
        "bindings": [
            ("Super + Space", "Open Intent Bar (AI command palette)"),
            ("Super + A", "Toggle AI Side Panel"),
            ("Super + V", "Push-to-talk Voice Input"),
            ("Ctrl + Shift + A", "Terminal AI helper (in Axon Terminal)"),
        ],
    },
    {
        "title": "System",
        "icon": "⚙️",
        "bindings": [
            ("Super", "Open Start Menu"),
            ("Super + /", "Show this shortcut overlay"),
            ("Super + L", "Lock screen"),
            ("Ctrl + Alt + T", "Open terminal"),
            ("Print Screen", "Screenshot"),
        ],
    },
    {
        "title": "Window Management",
        "icon": "🪟",
        "bindings": [
            ("Super + ↑", "Maximize window"),
            ("Super + ↓", "Restore / unmaximize"),
            ("Super + ←", "Tile window left"),
            ("Super + →", "Tile window right"),
            ("Alt + F4", "Close window"),
        ],
    },
    {
        "title": "Axon Terminal",
        "icon": "💻",
        "bindings": [
            ("Ctrl + Shift + T", "New tab"),
            ("Ctrl + Shift + W", "Close tab"),
            ("Ctrl + Page Up/Down", "Switch tabs"),
            ("Ctrl + Shift + C", "Copy selection"),
            ("Ctrl + Shift + V", "Paste"),
            ("Ctrl + Shift + A", "AI command bar"),
        ],
    },
]


class ShortcutsWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.set_title("Keyboard Shortcuts")
        self.set_default_size(720, 560)
        self.set_resizable(True)

        # Load CSS
        css_path = Path(__file__).resolve().parent / "main.css"
        if css_path.exists():
            css_provider = Gtk.CssProvider()
            css_provider.load_from_path(str(css_path))
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

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

        title_icon = Gtk.Label(label="⌨")
        title_icon.add_css_class("title-icon")
        title_box.append(title_icon)

        title_label = Gtk.Label(label="Axon Keyboard Shortcuts")
        title_label.add_css_class("overlay-title")
        title_box.append(title_label)

        root.append(title_box)

        subtitle = Gtk.Label(label="Press Escape to close")
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
        icon_label = Gtk.Label(label=group["icon"])
        icon_label.add_css_class("group-icon")
        header_box.append(icon_label)

        title = Gtk.Label(label=group["title"])
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
            key_label.set_size_request(200, -1)
            row.append(key_label)

            # Description
            desc_label = Gtk.Label(label=description)
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
