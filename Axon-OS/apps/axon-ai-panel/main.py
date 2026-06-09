"""
main.py — Entry point for the Axon AI Panel application.
"""

from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio  # noqa: E402

# Make the ui sub-package importable when running as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from context_reader import ContextReader  # noqa: E402
from ui.panel import AIPanelWindow, OllamaClient  # noqa: E402


class AIPanelApp(Adw.Application):
    """Axon AI Panel application."""

    def __init__(self) -> None:
        super().__init__(application_id="io.github.axon_os.AIPanel")
        self._window: AIPanelWindow | None = None

        # Allow toggling via a D-Bus action so external callers (e.g. the
        # intent-bar or a keybind daemon) can show/hide the panel.
        toggle_action = Gio.SimpleAction.new("toggle", None)
        toggle_action.connect("activate", self._on_toggle_action)
        self.add_action(toggle_action)

    # ------------------------------------------------------------------

    def do_activate(self) -> None:  # type: ignore[override]
        if self._window is None:
            client = OllamaClient()
            ctx_reader = ContextReader()
            self._window = AIPanelWindow(
                ollama_client=client,
                context_reader=ctx_reader,
            )
            self._window.set_application(self)

        self._window.toggle()

    # ------------------------------------------------------------------

    def _on_toggle_action(
        self, action: Gio.SimpleAction, param: None
    ) -> None:
        if self._window is not None:
            self._window.toggle()


def main() -> int:
    app = AIPanelApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
