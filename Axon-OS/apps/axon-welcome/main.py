#!/usr/bin/env python3
"""Axon OS Welcome App — entry point."""

import sys
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw
from ui.welcome import WelcomeWindow


class WelcomeApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.axon_os.Welcome")

    def do_activate(self):
        win = WelcomeWindow(self)
        win.present()


app = WelcomeApp()

if __name__ == "__main__":
    sys.exit(app.run(sys.argv))
