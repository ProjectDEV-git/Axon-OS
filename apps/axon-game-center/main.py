#!/usr/bin/env python3
"""Axon Game Center — install and manage gaming apps.

    axon-game-center [--page discover|installed|compatibility|performance|drivers]
    axon-game-center --install ID [ID ...] [--background]
    axon-game-center --preset starter|streamer|retro [--background]
    axon-game-center --driver nvidia-driver-550 [--background]

--background installs without a window (the welcome app uses it) and shows a
notification when everything is done.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib

sys.path.insert(0, str(Path(__file__).resolve().parent))
# services/ is beside apps/ (source tree, .deb) or inside the install dir.
for _base in (Path(__file__).resolve().parents[2], Path(__file__).resolve().parents[1]):
    if (_base / "services").is_dir():
        sys.path.insert(0, str(_base / "services"))
        sys.path.insert(0, str(_base / "services" / "axon-gaming"))
        break
from catalog import CatalogError, driver_item, load_catalog
from i18n import _
from ui import GameCenterWindow, inst, log


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="axon-game-center", description=__doc__.split("\n")[0])
    parser.add_argument("--page", default="", help="open this page")
    parser.add_argument("--install", nargs="+", metavar="ID", default=[], help="catalog item ids")
    parser.add_argument("--preset", action="append", default=[], help="starter, streamer or retro")
    parser.add_argument("--driver", metavar="PACKAGE", help="an NVIDIA driver package to install")
    parser.add_argument("--background", action="store_true", help="install without a window")
    return parser


def resolve_items(catalog, ids: list[str], presets: list[str]):
    """Catalog items for ids and presets, in order, without repeats. Unknown names raise."""
    wanted: list[str] = []
    for preset in presets:
        if preset not in catalog.presets:
            raise KeyError(preset)
        wanted += catalog.presets[preset].items
    wanted += ids
    items = []
    for item_id in dict.fromkeys(wanted):
        if item_id not in catalog.items:
            raise KeyError(item_id)
        items.append(catalog.items[item_id])
    return items


class GameCenterApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id="org.axonos.GameCenter",
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self._window: GameCenterWindow | None = None
        self._headless: inst.JobQueue | None = None
        self._outstanding: set[str] = set()
        self._failures: list[str] = []

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        try:
            args = build_parser().parse_args(command_line.get_arguments()[1:])
        except SystemExit:
            return 2
        try:
            catalog = load_catalog()
            items = resolve_items(catalog, args.install, args.preset)
            if args.driver:
                driver = driver_item(args.driver)
                catalog.items[driver.id] = driver
                items.append(driver)
        except (CatalogError, KeyError, ValueError) as exc:
            log.error("cannot start: %s", exc)
            command_line.printerr_literal(f"axon-game-center: {exc}\n")
            return 1
        if args.background and items:
            self._install_in_background(items)
            return 0
        if self._window is None:
            self._window = GameCenterWindow(catalog=catalog, application=self)
        if args.page:
            self._window.show_page(args.page)
        self._window.present()
        self._window.queue_install(items)
        return 0

    def _install_in_background(self, items) -> None:
        """Install with no window; one notification at the end."""
        self.hold()
        self._outstanding = {i.id for i in items}
        names = {i.id: i.name for i in items}

        def on_event(event: inst.Event) -> None:
            if event.state == inst.FAILED:
                self._failures.append(f"{names.get(event.item_id, event.item_id)}: {event.message}")
            if event.state in (inst.DONE, inst.FAILED):
                self._outstanding.discard(event.item_id)
                if not self._outstanding:
                    GLib.idle_add(self._finish_background, len(items))

        self._headless = inst.JobQueue(lambda ev: GLib.idle_add(on_event, ev) and False)
        if not inst.is_online():
            self._failures.append(_(inst.OFFLINE_MESSAGE))
            self._finish_background(len(items))
            return
        log.info("background install: %s", ", ".join(names))
        for item in items:
            if not self._headless.install(item):
                self._outstanding.discard(item.id)

    def _finish_background(self, total: int) -> bool:
        note = Gio.Notification.new(_("Game Center"))
        if self._failures:
            note.set_body("\n".join(self._failures[:3]))
        else:
            note.set_body(_("Your gaming apps are ready."))
        self.send_notification("install-done", note)
        if self._headless is not None:
            self._headless.close()
        self.release()
        return False


if __name__ == "__main__":
    sys.exit(GameCenterApp().run(sys.argv))
