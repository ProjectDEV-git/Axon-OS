"""Entry point for the Axon OS Intent Bar application."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio

if not __package__:
    # Started as a script (``python3 .../intent-bar/main.py``, as the .desktop
    # launcher does). The directory name has a hyphen, so it cannot be imported
    # as a package: register it as ``intent_bar`` so the relative imports work.
    import importlib.util
    from pathlib import Path

    _here = Path(__file__).resolve().parent
    _spec = importlib.util.spec_from_file_location(
        "intent_bar", _here / "__init__.py", submodule_search_locations=[str(_here)]
    )
    assert _spec is not None and _spec.loader is not None
    _pkg = importlib.util.module_from_spec(_spec)
    sys.modules["intent_bar"] = _pkg
    _spec.loader.exec_module(_pkg)
    __package__ = "intent_bar"

from .ollama_client import OllamaClient
from .spaces_manager import SpacesManager
from .ui.window import IntentBarWindow


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
