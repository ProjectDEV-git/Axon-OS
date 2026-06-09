#!/usr/bin/env python3
"""
main.py — Entry point for the Axon Settings Assistant.
A GTK4 + libadwaita assistant to configure system settings via natural language.
"""

from __future__ import annotations

import sys
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango

# Sibling import resolution
sys.path.insert(0, str(Path(__file__).resolve().parent))
from settings_executor import SettingsExecutor


class AxonSettingsWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.set_title("Axon Assistant")
        self.set_default_size(560, 420)
        self.set_resizable(False)

        self._executor = SettingsExecutor()

        # Load styling sheet
        css_path = Path(__file__).resolve().parent / "main.css"
        if css_path.exists():
            css_provider = Gtk.CssProvider()
            css_provider.load_from_path(str(css_path))
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

        # Main Layout
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(root)

        # Header Bar
        header = Adw.HeaderBar()
        header.add_css_class("settings-header")
        root.append(header)

        # Content Container
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.set_spacing(16)
        content_box.set_margin_start(24)
        content_box.set_margin_end(24)
        content_box.set_margin_top(16)
        content_box.set_margin_bottom(16)
        content_box.set_vexpand(True)
        root.append(content_box)

        # AI Status Icon & Title
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        title_box.set_spacing(12)
        title_box.set_halign(Gtk.Align.CENTER)

        ai_badge = Gtk.Label(label="⬡")
        ai_badge.add_css_class("ai-badge-icon")
        title_box.append(ai_badge)

        app_title = Gtk.Label(label="System Assistant")
        app_title.add_css_class("app-title-label")
        title_box.append(app_title)

        content_box.append(title_box)

        # Feedback Bubble / Card
        self._feedback_card = Gtk.Frame()
        self._feedback_card.add_css_class("feedback-card")

        feedback_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        feedback_inner.set_spacing(8)
        feedback_inner.set_margin_start(16)
        feedback_inner.set_margin_end(16)
        feedback_inner.set_margin_top(16)
        feedback_inner.set_margin_bottom(16)

        self._feedback_text = Gtk.Label(
            label="Tell me what you'd like to adjust. Try: 'toggle dark mode', 'mute system volume', or 'turn off wifi'."
        )
        self._feedback_text.set_wrap(True)
        self._feedback_text.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._feedback_text.set_xalign(0.5)
        self._feedback_text.add_css_class("feedback-text")

        feedback_inner.append(self._feedback_text)
        self._feedback_card.set_child(feedback_inner)
        content_box.append(self._feedback_card)

        # Quick Control Panel (visual representation of system actions)
        quick_grid = Gtk.Grid()
        quick_grid.set_column_spacing(16)
        quick_grid.set_row_spacing(12)
        quick_grid.set_halign(Gtk.Align.CENTER)
        quick_grid.set_margin_top(8)

        # Dark Mode button
        dark_btn = Gtk.Button(label="Toggle Theme")
        dark_btn.add_css_class("quick-action-btn")
        dark_btn.connect("clicked", lambda _b: self._run_command("toggle dark mode"))
        quick_grid.attach(dark_btn, 0, 0, 1, 1)

        # WiFi button
        wifi_btn = Gtk.Button(label="Toggle Wi-Fi")
        wifi_btn.add_css_class("quick-action-btn")
        wifi_btn.connect("clicked", lambda _b: self._run_command("toggle wifi"))
        quick_grid.attach(wifi_btn, 1, 0, 1, 1)

        # Mute button
        mute_btn = Gtk.Button(label="Toggle Mute")
        mute_btn.add_css_class("quick-action-btn")
        mute_btn.connect("clicked", lambda _b: self._run_command("mute audio"))
        quick_grid.attach(mute_btn, 0, 1, 1, 1)

        # Max Brightness button
        bright_btn = Gtk.Button(label="Max Brightness")
        bright_btn.add_css_class("quick-action-btn")
        bright_btn.connect("clicked", lambda _b: self._run_command("set brightness to 100%"))
        quick_grid.attach(bright_btn, 1, 1, 1, 1)

        content_box.append(quick_grid)

        # Input box at the bottom
        input_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        input_container.set_spacing(10)
        input_container.add_css_class("input-container")
        input_container.set_margin_start(24)
        input_container.set_margin_end(24)
        input_container.set_margin_bottom(24)

        self._entry = Gtk.Entry()
        self._entry.set_hexpand(True)
        self._entry.set_placeholder_text("Ask the assistant to configure something...")
        self._entry.connect("activate", self._on_entry_activated)
        self._entry.add_css_class("settings-input-entry")
        input_container.append(self._entry)

        send_btn = Gtk.Button(label="Apply")
        send_btn.add_css_class("settings-apply-btn")
        send_btn.connect("clicked", lambda _b: self._on_entry_activated(self._entry))
        input_container.append(send_btn)

        root.append(input_container)

        self._entry.grab_focus()

    def _on_entry_activated(self, entry: Gtk.Entry) -> None:
        text = entry.get_text().strip()
        if not text:
            return
        entry.set_text("")
        self._run_command(text)

    def _run_command(self, query: str) -> None:
        self._feedback_text.set_text("Applying settings change...")
        self._feedback_card.add_css_class("loading")
        
        # Async execution helper
        def worker():
            res = self._executor.execute_command(query)
            GLib.idle_add(self._on_command_completed, res)

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _on_command_completed(self, result: dict) -> None:
        self._feedback_card.remove_css_class("loading")
        msg = result.get("message", "Request completed.")
        self._feedback_text.set_text(msg)
        if result.get("success"):
            self._feedback_card.add_css_class("success")
            GLib.timeout_add(1500, lambda: self._feedback_card.remove_css_class("success") or False)
        else:
            self._feedback_card.add_css_class("error")
            GLib.timeout_add(2000, lambda: self._feedback_card.remove_css_class("error") or False)


class AxonSettingsApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="io.github.axon_os.SettingsAssistant")
        self._window: AxonSettingsWindow | None = None

    def do_activate(self) -> None:
        if self._window is None:
            self._window = AxonSettingsWindow(application=self)
        self._window.present()


def main() -> int:
    app = AxonSettingsApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
