"""Entry point for the Axon OS Intent Bar application."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio  # noqa: E402

from .ollama_client import OllamaClient  # noqa: E402
from .spaces_manager import SpacesManager  # noqa: E402
from .ui.window import IntentBarWindow  # noqa: E402


class IntentBarApp(Adw.Application):
    """The top-level Adwaita application for Intent Bar."""

    def __init__(self) -> None:
        super().__init__(
            application_id="io.github.axon_os.IntentBar",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self._ollama = OllamaClient()
        self._spaces = SpacesManager()

    def do_activate(self) -> None:  # type: ignore[override]
        window = IntentBarWindow(
            ollama_client=self._ollama,
            spaces_manager=self._spaces,
        )
        window.set_application(self)
        window.present()

    def do_shutdown(self) -> None:  # type: ignore[override]
        self._ollama.close()
        super().do_shutdown()


if __name__ == "__main__":
    app = IntentBarApp()
    sys.exit(app.run(sys.argv))
