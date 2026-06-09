"""
main.py — Entry point for the Axon Terminal application.

A GTK4 + libadwaita AI-powered terminal emulator for Axon OS.
Features:
  • Multi-tab VTE terminal with dark Axon-branded theme
  • AI diagnosis overlay on command failures (via org.axonos.Brain)
  • Natural language command bar (Ctrl+Shift+A)
  • Smart command suggestions after errors
"""

from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

# Ensure sibling modules are importable when running as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from terminal_widget import TerminalWidget  # noqa: E402


class AxonTerminalWindow(Adw.ApplicationWindow):
    """Main window for Axon Terminal."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)

        self.set_title("Axon Terminal")
        self.set_default_size(900, 600)
        self.set_icon_name("utilities-terminal")

        # ---- Load CSS ------------------------------------------------------
        css_path = Path(__file__).resolve().parent / "main.css"
        if css_path.exists():
            css_provider = Gtk.CssProvider()
            css_provider.load_from_path(str(css_path))
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

        # ---- Root layout ---------------------------------------------------
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(root)

        # ---- Header bar ----------------------------------------------------
        header = Adw.HeaderBar()
        header.add_css_class("header-bar")

        # New tab button
        new_tab_btn = Gtk.Button(icon_name="tab-new-symbolic")
        new_tab_btn.set_tooltip_text("New tab (Ctrl+Shift+T)")
        new_tab_btn.connect("clicked", self._on_new_tab)
        header.pack_start(new_tab_btn)

        # AI bar toggle button
        ai_btn = Gtk.Button(label="⬡ AI")
        ai_btn.add_css_class("ai-toggle-btn")
        ai_btn.set_tooltip_text("Toggle AI command bar (Ctrl+Shift+A)")
        ai_btn.connect("clicked", self._on_ai_toggle)
        header.pack_end(ai_btn)

        root.append(header)

        # ---- Terminal widget -----------------------------------------------
        self._terminal_widget = TerminalWidget()
        self._terminal_widget.set_vexpand(True)
        root.append(self._terminal_widget)

        # ---- Keyboard shortcuts --------------------------------------------
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_new_tab(self, button: Gtk.Button) -> None:
        """Open a new terminal tab."""
        self._terminal_widget.new_tab()

    def _on_ai_toggle(self, button: Gtk.Button) -> None:
        """Toggle the NL command bar."""
        self._terminal_widget.toggle_nl_bar()

    def _on_key_pressed(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        """Handle global keyboard shortcuts."""
        ctrl_shift = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK

        if (state & ctrl_shift) == ctrl_shift:
            # Ctrl+Shift+A — toggle NL command bar
            if keyval in (Gdk.KEY_a, Gdk.KEY_A):
                self._terminal_widget.toggle_nl_bar()
                return True
            # Ctrl+Shift+T — new tab
            if keyval in (Gdk.KEY_t, Gdk.KEY_T):
                self._terminal_widget.new_tab()
                return True

        return False


class AxonTerminalApp(Adw.Application):
    """Axon Terminal application."""

    def __init__(self) -> None:
        super().__init__(application_id="io.github.axon_os.Terminal")
        self._window: Optional[AxonTerminalWindow] = None  # noqa: F821

    def do_activate(self) -> None:  # type: ignore[override]
        """Create and present the main window."""
        if self._window is None:
            self._window = AxonTerminalWindow(application=self)

        self._window.present()


# Optional type hint import at module scope
from typing import Optional  # noqa: E402


def main() -> int:
    """Application entry point."""
    app = AxonTerminalApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
