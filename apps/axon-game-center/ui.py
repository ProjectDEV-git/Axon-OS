"""Axon Game Center window: browse, install and manage gaming apps."""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

# Shared GTK helpers live in apps/shared next to every app
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
from axon_gtk_utils import announce, close_on_escape, fit_default_size, load_app_css

# services/ is beside apps/ (source tree, .deb) or inside the install dir.
for _base in (Path(__file__).resolve().parents[2], Path(__file__).resolve().parents[1]):
    if (_base / "services").is_dir():
        sys.path.insert(0, str(_base / "services"))
        sys.path.insert(0, str(_base / "services" / "axon-gaming"))
        break
import catalog_installer as inst
import gaming_settings
import gpu_drivers
from _log_helper import resolve_logger
from catalog import Catalog, Item, driver_item, load_catalog
from constants import AXON_CONFIG_DIR
from i18n import _

log = resolve_logger("axon-game-center")

PAGES = ("discover", "installed", "compatibility", "performance", "drivers")
PROTONDB_URL = "https://www.protondb.com"
ANTICHEAT_URL = "https://areweanticheatyet.com"


def gb(mb: int) -> str:
    """A download size for people: 450 MB, 1.2 GB."""
    return f"{mb} MB" if mb < 1000 else f"{mb / 1000:.1f} GB"


def find_script(name: str) -> str | None:
    """A helper script from PATH, or from the source tree's scripts/ folder."""
    found = shutil.which(name)
    if found:
        return found
    local = Path(__file__).resolve().parents[2] / "scripts" / name
    return str(local) if local.is_file() else None


def run_in_thread(work: Callable[[], object], done: Callable[[object], None]) -> None:
    """Run ``work`` off the GTK thread and hand its result to ``done`` on it."""

    def target() -> None:
        try:
            result = work()
        except Exception as exc:  # shown by the caller as "nothing found"
            log.warning("background task failed: %s", exc)
            result = None
        GLib.idle_add(lambda: (done(result), False)[1])

    threading.Thread(target=target, daemon=True).start()


class ItemRow(Adw.ActionRow):
    """One catalog item with the right button for its state."""

    def __init__(self, window: GameCenterWindow, item: Item, mode: str) -> None:
        super().__init__(title=item.name)
        self.item = item
        self._window = window
        self._mode = mode  # "discover" or "installed"
        sub = f"{item.summary} · {gb(item.size_mb)}"
        if item.notes:
            sub += f"\n{item.notes}"
        self.set_subtitle(GLib.markup_escape_text(sub))
        self.set_subtitle_lines(0)
        self._suffix = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        self.add_suffix(self._suffix)
        self.refresh()

    def _clear(self) -> None:
        child = self._suffix.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._suffix.remove(child)
            child = nxt

    def _button(
        self, label: str, callback: Callable[[], None], suggested: bool = False
    ) -> Gtk.Button:
        button = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
        if suggested:
            button.add_css_class("suggested-action")
        button.update_property([Gtk.AccessibleProperty.LABEL], [f"{label}: {self.item.name}"])
        button.connect("clicked", lambda _b: callback())
        self._suffix.append(button)
        return button

    def refresh(self) -> None:
        w, item = self._window, self.item
        self._clear()
        self.remove_css_class("error")
        job = w.jobs.get(item.id)
        installed = item.id in w.installed
        if job is not None and job.state in (inst.QUEUED, inst.RUNNING):
            working = {
                "install": _("Installing…"),
                "update": _("Updating…"),
                "remove": _("Removing…"),
            }
            label = Gtk.Label(
                label=_("Waiting…") if job.state == inst.QUEUED else working.get(job.action, "")
            )
            label.add_css_class("dim-label")
            bar = Gtk.ProgressBar(valign=Gtk.Align.CENTER)
            bar.add_css_class("item-progress")
            if job.fraction:
                bar.set_fraction(job.fraction)
            else:
                bar.pulse()
            self._suffix.append(label)
            self._suffix.append(bar)
            return
        if job is not None and job.state == inst.FAILED:
            self.set_subtitle(GLib.markup_escape_text(job.message))
            self.add_css_class("error")
        if installed and self._mode == "installed":
            if self._can_open():
                self._button(_("Open"), lambda: w.open_item(item))
            self._button(_("Update"), lambda: w.queue_update(item)).set_sensitive(w.online)
            self._button(_("Remove"), lambda: w.confirm_remove(item))
        elif installed:
            label = Gtk.Label(label=_("Installed"))
            label.add_css_class("dim-label")
            self._suffix.append(label)
        elif not inst.is_supported(item):
            label = Gtk.Label(label=_("Managed by Axon OS"))
            label.add_css_class("dim-label")
            self._suffix.append(label)
        else:
            button = self._button(
                _("Retry") if job is not None and job.state == inst.FAILED else _("Install"),
                lambda: w.queue_install([item]),
                suggested=True,
            )
            button.set_sensitive(w.online)

    def _can_open(self) -> bool:
        return self.item.kind == "flatpak"


class GameCenterWindow(Adw.ApplicationWindow):
    def __init__(
        self,
        catalog: Catalog | None = None,
        runner: inst.Runner = inst.run_command,
        online: Callable[[], bool] = inst.is_online,
        config_dir: Path | None = None,
        gaming_path: Path | None = None,
        detect: Callable[[], dict] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        load_app_css(Path(__file__).resolve().parent / "main.css", self.get_display())
        self.set_title(_("Game Center"))
        fit_default_size(self, 900, 700)
        close_on_escape(self)

        self.catalog = catalog or load_catalog()
        self._runner = runner
        self._online_check = online
        self._config_dir = config_dir or Path(GLib.get_user_config_dir())
        self._gaming_path = gaming_path or AXON_CONFIG_DIR / "gaming.json"
        self._detect = detect or gpu_drivers.detect_graphics
        self.online = True
        self.installed: set[str] = set()
        self.jobs: dict[str, inst.Event] = {}
        self.runtime_present = False
        self._rows: list[ItemRow] = []
        self._open_after: set[str] = set()
        self.jobs_queue = inst.JobQueue(
            lambda ev: GLib.idle_add(self._on_event, ev) and False, runner=runner, online=online
        )
        self.connect("close-request", self._on_close)

        self._stack = Adw.ViewStack()
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        switcher = Adw.ViewSwitcher(stack=self._stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)
        toolbar.add_top_bar(header)
        self._banner = Adw.Banner(title=_(inst.OFFLINE_MESSAGE))
        toolbar.add_top_bar(self._banner)
        toolbar.set_content(self._stack)
        bar = Adw.ViewSwitcherBar(stack=self._stack)
        toolbar.add_bottom_bar(bar)
        breakpoint_ = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 600sp"))
        breakpoint_.add_setter(bar, "reveal", True)
        breakpoint_.add_setter(switcher, "policy", Adw.ViewSwitcherPolicy.NARROW)
        self.add_breakpoint(breakpoint_)
        self.set_content(toolbar)

        self._add_page(self._build_discover(), "discover", _("Discover"), "system-search-symbolic")
        self._add_page(self._build_installed(), "installed", _("Installed"), "emblem-ok-symbolic")
        self._add_page(
            self._build_compat(),
            "compatibility",
            _("Compatibility"),
            "applications-engineering-symbolic",
        )
        self._add_page(
            self._build_performance(), "performance", _("Performance"), "speedometer-symbolic"
        )
        self._add_page(self._build_drivers(), "drivers", _("Drivers"), "computer-symbolic")
        self.refresh_state()

    # -- plumbing ------------------------------------------------------------

    def _add_page(self, content: Gtk.Widget, name: str, title: str, icon: str) -> None:
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        clamp = Adw.Clamp(
            maximum_size=760, margin_top=12, margin_bottom=24, margin_start=12, margin_end=12
        )
        clamp.set_child(content)
        scroll.set_child(clamp)
        page = self._stack.add_titled(scroll, name, title)
        page.set_icon_name(icon)

    def show_page(self, name: str) -> None:
        if name in PAGES:
            self._stack.set_visible_child_name(name)

    def _group(self, title: str = "", description: str = "") -> Adw.PreferencesGroup:
        return Adw.PreferencesGroup(title=title, description=description)

    def _on_close(self, *_args: object) -> bool:
        busy = [j for j in self.jobs.values() if j.state in (inst.QUEUED, inst.RUNNING)]
        if busy:
            # Keep installing after the window closes; the app stays alive until done
            log.info("window closed with %d job(s) running", len(busy))
        return False

    def refresh_state(self) -> None:
        """Re-read what is installed and whether we are online (off the GTK thread)."""

        def work() -> tuple[set[str], bool, bool]:
            online = self._online_check()
            return (
                inst.installed_ids(list(self.catalog.items.values()), self._runner),
                online,
                inst.flatpak_runtime_present(self._runner),
            )

        def done(result: object) -> None:
            if result is None:
                return
            self.installed, self.online, self.runtime_present = result  # type: ignore[misc]
            self._banner.set_revealed(not self.online)
            self._update_starter_card()
            self.refresh_rows()
            self._refresh_installed_page()

        run_in_thread(work, done)

    def refresh_rows(self) -> None:
        for row in self._rows:
            row.refresh()

    # -- installing ----------------------------------------------------------

    def queue_install(self, items: list[Item]) -> None:
        for item in items:
            if item.id in self.installed:
                continue
            log.info("install requested: %s", item.id)
            self.jobs_queue.install(item)

    def queue_update(self, item: Item) -> None:
        log.info("update requested: %s", item.id)
        self.jobs_queue.update(item)

    def confirm_remove(self, item: Item) -> None:
        dialog = Adw.AlertDialog(
            heading=_("Remove {name}?").format(name=item.name),
            body=_("The app is removed from this computer. Its settings and saved games are kept."),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("remove", _("Remove"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        check = None
        if item.kind == "flatpak":
            check = Gtk.CheckButton(label=_("Also remove runtimes no other app uses"))
            dialog.set_extra_child(check)

        def on_response(_d: Adw.AlertDialog, response: str) -> None:
            if response == "remove":
                log.info("remove requested: %s", item.id)
                self.jobs_queue.remove(item, remove_unused=bool(check and check.get_active()))

        dialog.connect("response", on_response)
        dialog.present(self)

    def _on_event(self, event: inst.Event) -> None:
        self.jobs[event.item_id] = event
        item = self.catalog.items.get(event.item_id)
        if event.state in (inst.DONE, inst.FAILED):
            log.info("%s %s: %s %s", event.action, event.item_id, event.state, event.message)
            name = item.name if item else event.item_id
            if event.state == inst.DONE:
                if event.action == "remove":
                    self.installed.discard(event.item_id)
                else:
                    self.installed.add(event.item_id)
                text = _("{name} {done}").format(
                    name=name, done=_("removed") if event.action == "remove" else _("is ready")
                )
                self._notify(text)
                announce(self, text)
                if event.item_id in self._open_after and item is not None:
                    self._open_after.discard(event.item_id)
                    self.open_item(item)
                self.refresh_state()  # runtime and shared dependencies
            else:
                self._notify(f"{name}: {event.message}")
                announce(self, f"{name}: {event.message}", important=True)
        self.refresh_rows()
        self._refresh_installed_page()
        self._update_starter_card()

    def _notify(self, text: str) -> None:
        app = self.get_application()
        if app is None:
            return
        note = Gio.Notification.new(_("Game Center"))
        note.set_body(text)
        app.send_notification(None, note)

    def open_item(self, item: Item) -> None:
        if item.kind != "flatpak":
            return
        info = Gio.DesktopAppInfo.new(item.source["ref"] + ".desktop")
        if info is not None and info.launch([], None):
            return
        flatpak = shutil.which("flatpak")
        if flatpak:
            subprocess.Popen([flatpak, "run", item.source["ref"]], start_new_session=True)  # nosec B603

    def _item_row(self, item: Item, mode: str) -> ItemRow:
        row = ItemRow(self, item, mode)
        self._rows.append(row)
        return row

    # -- Discover ------------------------------------------------------------

    def _build_discover(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self._runtime_note = Gtk.Label(
            label=_(
                "The first Flatpak app also downloads a shared runtime "
                "(about {size}) that many apps use."
            ).format(size=gb(800)),
            wrap=True,
            xalign=0,
        )
        self._runtime_note.add_css_class("dim-label")
        self._starter_group = self._group(
            _("Starter Pack"),
        )
        self._starter_button = Gtk.Button(label=_("Install"), valign=Gtk.Align.CENTER)
        self._starter_button.add_css_class("suggested-action")
        self._starter_button.connect("clicked", lambda _b: self.install_preset("starter"))
        self._starter_row = Adw.ActionRow(title=self.catalog.presets["starter"].name)
        self._starter_row.add_suffix(self._starter_button)
        self._starter_group.add(self._starter_row)
        box.append(self._starter_group)
        box.append(self._runtime_note)
        self._update_starter_card()
        for category, name in self.catalog.categories.items():
            items = self.catalog.in_category(category)
            if not items:
                continue
            group = self._group(name)
            for item in items:
                group.add(self._item_row(item, "discover"))
            box.append(group)
        return box

    def install_preset(self, preset_id: str) -> None:
        self.queue_install(self.catalog.preset_items(preset_id))

    def _update_starter_card(self) -> None:
        preset = self.catalog.presets["starter"]
        missing = [i for i in preset.items if i not in self.installed]
        names = ", ".join(self.catalog.items[i].name for i in preset.items)
        size = self.catalog.total_size_mb(missing, self.runtime_present)
        if missing:
            self._starter_row.set_subtitle(
                GLib.markup_escape_text(
                    _("Install {names} — about {size}").format(names=names, size=gb(size))
                )
            )
        else:
            self._starter_row.set_subtitle(_("Everything in the Starter Pack is installed"))
        busy = any(
            self.jobs.get(i) and self.jobs[i].state in (inst.QUEUED, inst.RUNNING)
            for i in preset.items
        )
        self._starter_button.set_sensitive(bool(missing) and self.online and not busy)
        self._runtime_note.set_visible(not self.runtime_present)

    # -- Installed -----------------------------------------------------------

    def _build_installed(self) -> Gtk.Widget:
        self._installed_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self._installed_rows: list[ItemRow] = []
        return self._installed_box

    def _refresh_installed_page(self) -> None:
        box = self._installed_box
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt
        self._rows = [r for r in self._rows if r not in self._installed_rows]
        self._installed_rows = []
        mine = [
            i
            for i in self.catalog.items.values()
            if i.id in self.installed and inst.is_supported(i)
        ]
        if not mine:
            status = Adw.StatusPage(
                title=_("Nothing installed yet"),
                description=_("Apps you install from Discover show up here."),
                icon_name="applications-games-symbolic",
            )
            box.append(status)
            return
        group = self._group(_("Installed from Game Center"))
        for item in mine:
            row = ItemRow(self, item, "installed")
            self._installed_rows.append(row)
            self._rows.append(row)
            group.add(row)
        box.append(group)

    # -- Compatibility ---------------------------------------------------------

    def _build_compat(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self._runner_group = self._group(
            _("Proton and Wine versions"),
            _("Windows programs started with Axon APEX use the version you choose here."),
        )
        box.append(self._runner_group)
        self._runner_rows: list[Gtk.Widget] = []
        self._runner_script = find_script("axon-proton-runners")
        self._refresh_runners()

        manage = self._group(_("Get more versions"))
        row = Adw.ActionRow(
            title=_("ProtonPlus"),
            subtitle=_("Download Proton-GE, Wine-GE and other compatibility tools"),
        )
        button = Gtk.Button(label=_("Open ProtonPlus"), valign=Gtk.Align.CENTER)
        button.connect("clicked", lambda _b: self.open_or_install("protonplus"))
        row.add_suffix(button)
        manage.add(row)
        box.append(manage)

        links = self._group(
            _("Will it run?"),
            _(
                "Most Windows games run through Proton. Some games whose anti-cheat "
                "needs a Windows kernel driver do not run on Linux at all."
            ),
        )
        for title, url in (
            (_("ProtonDB: player reports for each game"), PROTONDB_URL),
            (_("Are We Anti-Cheat Yet: which anti-cheats work"), ANTICHEAT_URL),
        ):
            link = Adw.ActionRow(title=title, activatable=True)
            link.add_suffix(Gtk.Image.new_from_icon_name("adw-external-link-symbolic"))
            link.connect(
                "activated", lambda _r, u=url: Gtk.UriLauncher.new(u).launch(self, None, None, None)
            )
            links.add(link)
        box.append(links)
        return box

    def open_or_install(self, item_id: str) -> None:
        item = self.catalog.items[item_id]
        if item.id in self.installed:
            self.open_item(item)
        elif self.online:
            self._open_after.add(item.id)
            self.queue_install([item])

    def _runner_output(self, *args: str) -> str:
        if not self._runner_script:
            return ""
        out = subprocess.run(  # nosec B603 - our own script, fixed arguments
            [self._runner_script, *args], capture_output=True, text=True, timeout=10, check=False
        )
        return out.stdout

    def _refresh_runners(self) -> None:
        def work() -> tuple[str, list[str]]:
            return self._runner_output("get").strip(), self._runner_output("list").splitlines()

        def done(result: object) -> None:
            for row in self._runner_rows:
                self._runner_group.remove(row)
            self._runner_rows = []
            selected, lines = result or ("system", [])  # type: ignore[misc]
            entries = [("", _("System Wine"), "system")]
            for line in lines:
                name, _t, path = line.partition("\t")
                entries.append((name, name, path))
            first: Gtk.CheckButton | None = None
            for name, title, path in entries:
                row = Adw.ActionRow(title=title, activatable=True)
                check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
                if first is None:
                    first = check
                else:
                    check.set_group(first)
                check.set_active(path == selected or (not name and selected == "system"))
                check.connect("toggled", self._on_runner_toggled, name)
                row.add_prefix(check)
                row.set_activatable_widget(check)
                if name and path:
                    row.set_subtitle(GLib.markup_escape_text(path))
                self._runner_group.add(row)
                self._runner_rows.append(row)
            if len(entries) == 1:
                self._runner_group.set_description(
                    _("No Proton or Wine versions found yet. Open ProtonPlus below to get one.")
                )

        run_in_thread(work, done)

    def _on_runner_toggled(self, check: Gtk.CheckButton, name: str) -> None:
        if not check.get_active():
            return
        args = ("set", name) if name else ("clear",)
        log.info("runner choice: %s", name or "system")
        run_in_thread(lambda: self._runner_output(*args), lambda _r: None)

    # -- Performance ---------------------------------------------------------------

    def _build_performance(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        status = self._group(_("Game mode"))
        self._gm_row = Adw.ActionRow(title=_("Status"), subtitle=_("Checking…"))
        status.add(self._gm_row)
        box.append(status)
        self._watch_game_mode()

        settings = gaming_settings.load(self._gaming_path)
        self._perf_switches: dict[str, Adw.SwitchRow] = {}
        rows = (
            (
                "pause_ai",
                _("Pause AI during games"),
                _("Unloads local AI models so they don't take GPU memory from your game"),
            ),
            (
                "use_cloud",
                _("Use cloud provider during games"),
                _(
                    "Answers questions with your cloud model instead of pausing. Your prompts leave this computer."
                ),
            ),
            (
                "pause_indexing",
                _("Pause file indexing during games"),
                _("Stops scanning your files until the game ends"),
            ),
        )
        group = self._group(_("While you play"))
        for key, title, subtitle in rows:
            row = Adw.SwitchRow(title=title, subtitle=subtitle)
            row.set_active(settings[key])
            row.connect("notify::active", self._on_perf_toggled, key)
            group.add(row)
            self._perf_switches[key] = row
        self._perf_switches["use_cloud"].set_sensitive(settings["pause_ai"])
        box.append(group)

        overlay = self._group(_("Overlay"))
        self._fps_row = Adw.SwitchRow(
            title=_("Show FPS overlay in all games"),
            subtitle=_("Uses MangoHud. Takes effect the next time you sign in."),
        )
        self._fps_row.set_active(self._overlay_file().exists())
        self._fps_row.connect("notify::active", self._on_fps_toggled)
        overlay.add(self._fps_row)
        goverlay = Adw.ActionRow(title=_("GOverlay"), subtitle=_("Choose what the overlay shows"))
        button = Gtk.Button(label=_("Open GOverlay"), valign=Gtk.Align.CENTER)
        button.connect("clicked", lambda _b: self.open_or_install("goverlay"))
        goverlay.add_suffix(button)
        overlay.add(goverlay)
        box.append(overlay)
        return box

    def _overlay_file(self) -> Path:
        return self._config_dir / "environment.d" / "90-axon-mangohud.conf"

    def _on_fps_toggled(self, row: Adw.SwitchRow, _pspec: object) -> None:
        path = self._overlay_file()
        try:
            if row.get_active():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("MANGOHUD=1\n", encoding="utf-8")
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("could not change the overlay setting: %s", exc)

    def _on_perf_toggled(self, row: Adw.SwitchRow, _pspec: object, key: str) -> None:
        value = row.get_active()
        if key == "pause_ai":
            self._perf_switches["use_cloud"].set_sensitive(value)
        settings = gaming_settings.load(self._gaming_path)
        settings[key] = value
        try:
            gaming_settings.save(self._gaming_path, settings)
        except OSError:
            return
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call(
                "org.axonos.Gaming",
                "/org/axonos/Gaming",
                "org.axonos.Gaming",
                "SetSetting",
                GLib.Variant("(sb)", (key, value)),
                None,
                Gio.DBusCallFlags.NO_AUTO_START,
                3000,
                None,
                None,
            )
        except GLib.Error:
            pass

    def _watch_game_mode(self) -> None:
        def on_proxy(_src: object, result: Gio.AsyncResult) -> None:
            try:
                proxy = Gio.DBusProxy.new_for_bus_finish(result)
            except GLib.Error:
                self._gm_row.set_subtitle(_("The game mode service isn't running"))
                return
            self._gm_proxy = proxy
            proxy.connect("g-properties-changed", lambda *_a: self._show_game_mode())
            self._show_game_mode()

        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.DO_NOT_AUTO_START | Gio.DBusProxyFlags.GET_INVALIDATED_PROPERTIES,
            None,
            "org.axonos.Gaming",
            "/org/axonos/Gaming",
            "org.axonos.Gaming",
            None,
            on_proxy,
        )

    def _show_game_mode(self) -> None:
        active = self._gm_proxy.get_cached_property("Active")
        if active is None:
            self._gm_row.set_subtitle(_("The game mode service isn't running"))
        elif active.get_boolean():
            games = self._gm_proxy.get_cached_property("ActiveGames")
            names = sorted({g[1] or _("A game") for g in games.unpack()}) if games else []
            self._gm_row.set_subtitle(
                _("On: {games}. AI is paused.").format(games=", ".join(names) or _("a game"))
            )
        else:
            self._gm_row.set_subtitle(_("Off. It turns on by itself when a game starts."))

    # -- Drivers -----------------------------------------------------------------------

    def _build_drivers(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self._gpu_group = self._group(_("Graphics"))
        self._gpu_group.add(Adw.ActionRow(title=_("Looking for graphics cards…")))
        box.append(self._gpu_group)
        self._driver_group = self._group(_("Driver"))
        box.append(self._driver_group)
        self._ai_group = self._group(
            _("AI acceleration"),
            _("Lets local AI models use your graphics card instead of only the processor."),
        )
        box.append(self._ai_group)
        self._gpu_rows: list[Gtk.Widget] = []
        run_in_thread(self._detect, self._show_graphics)
        return box

    def _show_graphics(self, info: object) -> None:
        for row in self._gpu_rows:
            self._gpu_group.remove(row)
        self._gpu_rows = []
        for row in getattr(self, "_extra_rows", []):
            row.get_parent().remove(row)
        self._extra_rows: list[Gtk.Widget] = []
        info = info or {
            "gpus": [],
            "recommended": None,
            "nvidia_version": None,
            "secure_boot": None,
        }
        gpus = info["gpus"]  # type: ignore[index]
        if not gpus:
            row = Adw.ActionRow(title=_("No graphics card found"))
            self._gpu_group.add(row)
            self._gpu_rows.append(row)
        for gpu in gpus:
            vram = f" · {gpu.vram_gb:g} GB" if gpu.vram_gb else ""
            row = Adw.ActionRow(
                title=GLib.markup_escape_text(gpu.name),
                subtitle=GLib.markup_escape_text(
                    _("Driver in use: {driver}").format(driver=gpu.driver or _("none")) + vram
                ),
            )
            self._gpu_group.add(row)
            self._gpu_rows.append(row)
        nvidia = any(g.vendor == "nvidia" for g in gpus)
        recommended = info["recommended"]  # type: ignore[index]
        version = info["nvidia_version"]  # type: ignore[index]
        if nvidia and recommended and not version:
            row = Adw.ActionRow(
                title=_("Recommended driver: {name}").format(name=recommended),
                subtitle=_(
                    "The open-source driver is slow in games. The NVIDIA driver is much faster."
                ),
            )
            button = Gtk.Button(label=_("Install"), valign=Gtk.Align.CENTER)
            button.add_css_class("suggested-action")
            button.connect("clicked", lambda _b: self._install_driver(recommended))
            button.set_sensitive(self.online)
            row.add_suffix(button)
            self._driver_group.add(row)
            self._extra_rows.append(row)
            if info["secure_boot"]:  # type: ignore[index]
                warn = Adw.ActionRow(
                    title=_("Secure Boot is on"),
                    subtitle=_(
                        "After installing, restart and enroll the key when asked, or the driver will not load."
                    ),
                )
                self._driver_group.add(warn)
                self._extra_rows.append(warn)
        elif nvidia and version:
            row = Adw.ActionRow(
                title=_("NVIDIA driver {version} is installed").format(version=version)
            )
            self._driver_group.add(row)
            self._extra_rows.append(row)
        else:
            row = Adw.ActionRow(
                title=_("The open-source graphics driver is in use"),
                subtitle=_("Nothing to install. AMD and Intel graphics work out of the box."),
            )
            self._driver_group.add(row)
            self._extra_rows.append(row)
        wanted = {"nvidia": "ollama-cuda", "amd": "ollama-rocm"}
        for gpu in gpus:
            item = self.catalog.items.get(wanted.get(gpu.vendor, ""))
            if item is None:
                continue
            row = Adw.ActionRow(
                title=item.name,
                subtitle=GLib.markup_escape_text(f"{item.summary} · {gb(item.size_mb)}"),
            )
            button = Gtk.Button(label=_("Install"), valign=Gtk.Align.CENTER, sensitive=False)
            button.set_tooltip_text(_("Not available yet. It will arrive in an update."))
            row.add_suffix(button)
            self._ai_group.add(row)
            self._extra_rows.append(row)

    def _install_driver(self, package: str) -> None:
        item = driver_item(package)
        self.catalog.items[item.id] = item
        log.info("driver install requested: %s", package)
        self.jobs_queue.install(item)
