#!/usr/bin/env python3
"""Axon OS Installer — entry point."""

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ui.wizard import InstallerWindow


class InstallerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.axonos.Installer")

    def do_activate(self):
        window = self.get_active_window()
        if not window:
            window = InstallerWindow(self)
        window.present()


def main() -> int:
    app = InstallerApp()
    return app.run(None)


if __name__ == "__main__":
    sys.exit(main())
