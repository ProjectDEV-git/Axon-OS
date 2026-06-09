#!/usr/bin/env python3
"""Axon OS Files App — entry point."""

import sys
import os
from pathlib import Path

# Add the directory containing main.py to sys.path to resolve internal imports
sys.path.insert(0, str(Path(__file__).parent.resolve()))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw
from ui import FilesWindow

class FilesApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.axonos.Files")

    def do_activate(self):
        win = FilesWindow(self)
        win.present()

app = FilesApp()

if __name__ == "__main__":
    sys.exit(app.run(sys.argv))
