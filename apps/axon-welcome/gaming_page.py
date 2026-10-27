"""The welcome app's "Gaming setup" step."""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk

for _base in (Path(__file__).resolve().parents[2], Path(__file__).resolve().parents[1]):
    if (_base / "services").is_dir():
        sys.path.insert(0, str(_base / "services"))
        sys.path.insert(0, str(_base / "services" / "axon-gaming"))
        break
import catalog_installer
import gaming_setup
from catalog import Catalog, load_catalog
from i18n import _

PRESETS = ("starter", "streamer", "retro", gaming_setup.NONE_PRESET)


def gb(mb: int) -> str:
    return f"{mb} MB" if mb < 1000 else f"{mb / 1000:.1f} GB"


class GamingPage(Gtk.Box):
    """Pick a preset (or individual apps), then install in the background."""

    def __init__(
        self,
        on_back: Callable[[], None],
        on_next: Callable[[], None],
        catalog: Catalog | None = None,
        detect: Callable[[], dict] | None = None,
        online: Callable[[], bool] = catalog_installer.is_online,
        launch: Callable[[list[str]], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._catalog = catalog or load_catalog()
        self._detect = detect
        self._online = online
        self._launch = launch or self._spawn
        self._on_next = on_next
        self._driver: str | None = None
        self._boxes: dict[str, Gtk.CheckButton] = {}
        self._syncing = False
        self.set_margin_top(24)
        self.set_margin_bottom(24)
        self.set_margin_start(24)
        self.set_margin_end(24)

        title = Gtk.Label(label=_("Gaming setup"), xalign=0)
        title.add_css_class("title-2")
        self.append(title)
        self._intro = Gtk.Label(
            label=_(
                "Pick what to install. It downloads in the background while you use the desktop."
            ),
            wrap=True,
            xalign=0,
        )
        self.append(self._intro)

        self._driver_check = Gtk.CheckButton(active=True, visible=False)
        self.append(self._driver_check)
        # Local AI on the graphics card (Ollama's CUDA or ROCm libraries)
        self._ai_check = Gtk.CheckButton(active=False, visible=False)
        self._ai_item: str | None = None
        self.append(self._ai_check)
        self._ai_check.connect("toggled", lambda _b: self._update_summary())

        self._choices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.append(self._choices)
        self._preset_buttons: dict[str, Gtk.CheckButton] = {}
        first: Gtk.CheckButton | None = None
        for preset in PRESETS:
            if preset == gaming_setup.NONE_PRESET:
                name, summary = (
                    _("Nothing for now"),
                    _("You can install apps later from Game Center"),
                )
            else:
                p = self._catalog.presets[preset]
                size = gb(self._catalog.total_size_mb(list(p.items)))
                name, summary = p.name, f"{p.summary} · {size}"
            button = Gtk.CheckButton()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            top = Gtk.Label(xalign=0, label=name)
            top.add_css_class("heading")
            sub = Gtk.Label(xalign=0, label=summary, wrap=True)
            sub.add_css_class("dim-label")
            box.append(top)
            box.append(sub)
            button.set_child(box)
            if first is None:
                first = button
            else:
                button.set_group(first)
            button.connect("toggled", self._on_preset_toggled, preset)
            self._preset_buttons[preset] = button
            self._choices.append(button)

        self._expander = Gtk.Expander(label=_("Customize"))
        custom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for category, cat_name in self._catalog.categories.items():
            items = [i for i in self._catalog.in_category(category) if i.kind != "component"]
            if not items or category == "media":
                continue
            heading = Gtk.Label(label=cat_name, xalign=0, margin_top=8)
            heading.add_css_class("heading")
            custom.append(heading)
            for item in items:
                check = Gtk.CheckButton(label=f"{item.name} ({gb(item.size_mb)})")
                check.connect("toggled", self._on_item_toggled)
                self._boxes[item.id] = check
                custom.append(check)
        scroll = Gtk.ScrolledWindow(min_content_height=200, max_content_height=300)
        scroll.set_child(custom)
        self._expander.set_child(scroll)
        self._choices.append(self._expander)

        codecs = self._catalog.items.get(gaming_setup.CODECS_ID)
        self._codecs = Gtk.CheckButton(
            label=_("Install media codecs") + (f" ({gb(codecs.size_mb)})" if codecs else ""),
            active=False,
        )
        self._codecs.set_tooltip_text(
            _("MP3, H.264 and other formats. Some are covered by patents in some countries.")
        )
        self._codecs.connect("toggled", lambda _b: self._update_summary())
        self._choices.append(self._codecs)
        self._codecs_note = Gtk.Label(
            label=_(
                "Needed to play some music and video files. Some formats are covered by patents in some countries."
            ),
            wrap=True,
            xalign=0,
        )
        self._codecs_note.add_css_class("dim-label")
        self._choices.append(self._codecs_note)

        self._summary = Gtk.Label(xalign=0, wrap=True)
        self.append(self._summary)

        nav = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=12)
        back = Gtk.Button(label=_("← Back"))
        back.connect("clicked", lambda _b: on_back())
        skip = Gtk.Button(label=_("Skip"))
        skip.connect("clicked", lambda _b: on_next())
        self._continue = Gtk.Button(label=_("Install and continue →"))
        self._continue.add_css_class("suggested-action")
        self._continue.connect("clicked", lambda _b: self.apply())
        for button in (back, skip, self._continue):
            nav.append(button)
        self.append(nav)

        self._preset_buttons["starter"].set_active(True)
        self.refresh()

    # -- state -------------------------------------------------------------------

    def refresh(self) -> None:
        """Check the network and the graphics card (off the GTK thread)."""

        def work() -> None:
            online = self._online()
            info = self._detect() if self._detect and online else None
            GLib.idle_add(self._apply_environment, online, info)

        threading.Thread(target=work, daemon=True).start()

    def _apply_environment(self, online: bool, info: dict | None) -> bool:
        self._choices.set_visible(online)
        self._summary.set_visible(online)
        self._continue.set_visible(online)
        if not online:
            self._intro.set_label(_("You can install these later from Game Center."))
            return False
        gpus = (info or {}).get("gpus", [])
        recommended = (info or {}).get("recommended")
        needs_driver = (
            any(g.vendor == "nvidia" for g in gpus)
            and recommended
            and not (info or {}).get("nvidia_version")
        )
        if needs_driver:
            self._driver = recommended
            name = next(g.name for g in gpus if g.vendor == "nvidia")
            self._driver_check.set_label(
                _("Install the {driver} graphics driver for {gpu}").format(
                    driver=recommended, gpu=name
                )
            )
            self._driver_check.set_visible(True)
        self._offer_ai_acceleration(gpus, bool(needs_driver), (info or {}).get("nvidia_version"))
        self._update_summary()
        return False

    def _offer_ai_acceleration(
        self, gpus: list, needs_driver: bool, nvidia_version: str | None
    ) -> None:
        """Offer Ollama's GPU libraries for the first card they exist for.

        NVIDIA: on by default when the driver is (or is being) installed, since CUDA works
        with it. AMD: off by default, because only recent cards are supported and
        otherwise the 1 GB would be wasted.
        """
        for gpu in gpus:
            item_id = gaming_setup.AI_ACCELERATION.get(gpu.vendor)
            item = self._catalog.items.get(item_id or "")
            if item is None:
                continue
            if gpu.vendor == "nvidia" and not (needs_driver or nvidia_version):
                continue  # CUDA needs the NVIDIA driver, which this card does not have
            self._ai_item = item.id
            vendor = "NVIDIA" if gpu.vendor == "nvidia" else "AMD"
            self._ai_check.set_label(
                _("Run local AI on your {vendor} graphics card ({size} download)").format(
                    vendor=vendor, size=gb(item.size_mb)
                )
            )
            self._ai_check.set_active(gpu.vendor == "nvidia")
            self._ai_check.set_visible(True)
            return

    def selected(self) -> set[str]:
        chosen = {i for i, b in self._boxes.items() if b.get_active()}
        if self._ai_item and self._ai_check.get_active():
            chosen.add(self._ai_item)
        if self._codecs.get_active():
            chosen.add(gaming_setup.CODECS_ID)
        return chosen

    def _on_preset_toggled(self, button: Gtk.CheckButton, preset: str) -> None:
        if not button.get_active():
            return
        wanted = set(gaming_setup.preset_ids(self._catalog, preset))
        self._syncing = True
        for item_id, check in self._boxes.items():
            check.set_active(item_id in wanted)
        self._syncing = False
        self._update_summary()

    def _on_item_toggled(self, _check: Gtk.CheckButton) -> None:
        if not self._syncing:
            self._update_summary()

    def _update_summary(self) -> None:
        ids = gaming_setup.selected_ids(self._catalog, self.selected())
        driver = self._driver if self._driver_check.get_active() else None
        if not ids and not driver:
            self._summary.set_label(_("Nothing will be installed."))
            return
        size = self._catalog.total_size_mb(ids) + (300 if driver else 0)
        self._summary.set_label(_("Download: about {size}").format(size=gb(size)))

    def apply(self) -> None:
        """Start the background install and move on."""
        ids = gaming_setup.selected_ids(self._catalog, self.selected())
        driver = self._driver if self._driver_check.get_active() else None
        argv = gaming_setup.install_argv(ids, driver)
        if argv is not None:
            self._launch(argv)
        self._on_next()

    @staticmethod
    def _spawn(argv: list[str]) -> None:
        # Detached, so the install carries on if the welcome window is closed
        subprocess.Popen(  # nosec B603 - our own command, ids come from the catalog
            argv, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
