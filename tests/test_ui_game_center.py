"""Game Center window: pages, install flow, offline behaviour, settings."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tests.gtk_helpers import ROOT, load_module, pump, require_gtk

Gtk, Adw, GLib = require_gtk()

ui = load_module("axon_game_center_ui", "apps/axon-game-center/ui.py")
gc_main = load_module("axon_game_center_main", "apps/axon-game-center/main.py")
gpu_drivers = __import__("gpu_drivers")


class FakeRunner:
    def __init__(self, flatpaks=()):
        self.flatpaks = set(flatpaks)
        self.calls = []

    def __call__(self, argv, on_line):
        self.calls.append(argv)
        if argv[:2] == ["flatpak", "list"]:
            for ref in sorted(self.flatpaks):
                on_line(ref)
        if argv[:3] == ["flatpak", "install", "--user"]:
            self.flatpaks.add(argv[-1])
        if argv[:3] == ["flatpak", "uninstall", "--user"] and "--unused" not in argv:
            self.flatpaks.discard(argv[-1])
        if argv[0] == "dpkg-query":
            return 1
        return 0


NO_GPU = {"gpus": [], "recommended": None, "nvidia_version": None, "secure_boot": None}


def make(tmp_path, runner=None, online=True, detect=None):
    runner = runner or FakeRunner()
    win = ui.GameCenterWindow(
        runner=runner,
        online=lambda: online,
        config_dir=tmp_path / "config",
        gaming_path=tmp_path / "gaming.json",
        detect=lambda: detect or NO_GPU,
    )
    settle(win)
    return win, runner


def settle(win, until=lambda: True, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pump(GLib, 200)
        if until():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def buttons(row):
    out = []
    child = row._suffix.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Button):
            out.append(child)
        child = child.get_next_sibling()
    return out


def row_for(win, item_id, mode="discover"):
    return next(r for r in win._rows if r.item.id == item_id and r._mode == mode)


def test_discover_lists_the_whole_catalog_with_legal_notes(tmp_path):
    win, _ = make(tmp_path)
    ids = {r.item.id for r in win._rows}
    assert ids == set(win.catalog.items)
    assert "legally obtained" in row_for(win, "dolphin").get_subtitle()
    assert win._stack.get_pages().get_n_items() == 5
    win.destroy()


def test_starter_pack_card_shows_names_and_total_size(tmp_path):
    win, _ = make(tmp_path)
    subtitle = win._starter_row.get_subtitle()
    for name in ("Heroic Games Launcher", "Lutris", "ProtonPlus", "GOverlay", "Discord"):
        assert name in subtitle
    expected = ui.gb(win.catalog.total_size_mb(list(win.catalog.presets["starter"].items)))
    assert expected in subtitle
    assert win._starter_button.get_sensitive()
    assert win._runtime_note.get_visible()
    win.destroy()


def test_install_button_runs_the_job_and_marks_the_app_installed(tmp_path):
    win, runner = make(tmp_path)
    row = row_for(win, "heroic")
    assert [b.get_label() for b in buttons(row)] == ["Install"]
    buttons(row)[0].emit("clicked")
    settle(win, lambda: "heroic" in win.installed)
    assert ["flatpak", "install", "--user", "--noninteractive", "-y", "flathub",
            "com.heroicgameslauncher.hgl"] in runner.calls  # fmt: skip
    settle(win, lambda: not buttons(row))
    assert row_for(win, "heroic", "discover")._suffix.get_first_child().get_label() == "Installed"
    win.destroy()


def test_starter_pack_installs_all_five_one_after_another(tmp_path):
    win, runner = make(tmp_path)
    win._starter_button.emit("clicked")
    starter = set(win.catalog.presets["starter"].items)
    settle(win, lambda: starter <= win.installed, timeout=10)
    installs = [c[-1] for c in runner.calls if c[:3] == ["flatpak", "install", "--user"]]
    assert len(installs) == 5
    assert "Everything in the Starter Pack is installed" in win._starter_row.get_subtitle()
    assert not win._starter_button.get_sensitive()
    win.destroy()


def test_installed_page_offers_open_update_remove_and_removes(tmp_path):
    runner = FakeRunner(flatpaks={"com.heroicgameslauncher.hgl"})
    win, runner = make(tmp_path, runner)
    row = row_for(win, "heroic", "installed")
    assert [b.get_label() for b in buttons(row)] == ["Open", "Update", "Remove"]
    win.jobs_queue.remove(win.catalog.items["heroic"])
    settle(win, lambda: "heroic" not in win.installed)
    assert any(c[:3] == ["flatpak", "uninstall", "--user"] for c in runner.calls)
    assert win._installed_box.get_first_child().get_title() == "Nothing installed yet"
    win.destroy()


def test_offline_disables_installing_and_says_why(tmp_path):
    win, runner = make(tmp_path, online=False)
    assert win._banner.get_revealed()
    assert "Connect to the internet" in win._banner.get_title()
    assert not buttons(row_for(win, "heroic"))[0].get_sensitive()
    assert not win._starter_button.get_sensitive()
    # a direct request still fails cleanly instead of running anything
    win.queue_install([win.catalog.items["heroic"]])
    settle(win, lambda: win.jobs["heroic"].state == "failed")
    assert win.jobs["heroic"].message == "Connect to the internet to install apps."
    assert not any(c[:2] == ["flatpak", "install"] for c in runner.calls)
    win.destroy()


def test_failed_install_can_be_retried(tmp_path):
    win, _ = make(tmp_path, online=False)
    win.queue_install([win.catalog.items["heroic"]])
    settle(win, lambda: win.jobs["heroic"].state == "failed")
    row = row_for(win, "heroic")
    assert [b.get_label() for b in buttons(row)] == ["Retry"]
    win.destroy()


def test_performance_switches_write_settings_and_overlay_file(tmp_path):
    import gaming_settings

    win, _ = make(tmp_path)
    win._perf_switches["use_cloud"].set_active(True)
    assert gaming_settings.load(tmp_path / "gaming.json")["use_cloud"] is True
    overlay = tmp_path / "config" / "environment.d" / "90-axon-mangohud.conf"
    assert not overlay.exists()
    win._fps_row.set_active(True)
    assert overlay.read_text() == "MANGOHUD=1\n"
    win._fps_row.set_active(False)
    assert not overlay.exists()
    win.destroy()


def test_drivers_page_offers_the_nvidia_driver_and_warns_about_secure_boot(tmp_path):
    gpu = gpu_drivers.Gpu("nvidia", "NVIDIA GeForce RTX 4070", "10de:2783", "", 12.0)
    info = {"gpus": [gpu], "recommended": "nvidia-driver-550", "nvidia_version": None,
            "secure_boot": True}  # fmt: skip
    win, runner = make(tmp_path, detect=info)
    settle(win, lambda: len(win._extra_rows) >= 3)
    titles = [r.get_title() for r in win._extra_rows]
    assert "Recommended driver: nvidia-driver-550" in titles
    assert "Secure Boot is on" in titles
    assert "Ollama GPU libraries (NVIDIA CUDA)" in titles
    driver_row = win._extra_rows[0]
    driver_row.get_last_child()  # the suffix box holds the Install button
    win._install_driver("nvidia-driver-550")
    settle(win, lambda: ["pkcon", "-y", "install", "nvidia-driver-550"] in runner.calls)
    win.destroy()


def test_drivers_page_without_nvidia_says_nothing_to_install(tmp_path):
    gpu = gpu_drivers.Gpu("amd", "AMD Radeon RX 7800 XT", "1002:747e", "amdgpu", 16.0)
    win, _ = make(tmp_path, detect={**NO_GPU, "gpus": [gpu]})
    settle(win, lambda: len(win._extra_rows) >= 1)
    assert "Nothing to install" in win._extra_rows[0].get_subtitle()
    assert "16 GB" in win._gpu_rows[0].get_subtitle()
    win.destroy()


def test_show_page(tmp_path):
    win, _ = make(tmp_path)
    win.show_page("drivers")
    assert win._stack.get_visible_child_name() == "drivers"
    win.show_page("bogus")
    assert win._stack.get_visible_child_name() == "drivers"
    win.destroy()


# -- command line ------------------------------------------------------------


def test_command_line_resolves_presets_and_ids_without_repeats():
    catalog = ui.load_catalog()
    items = gc_main.resolve_items(catalog, ["obs", "heroic"], ["starter"])
    ids = [i.id for i in items]
    assert ids[:5] == list(catalog.presets["starter"].items)
    assert ids.count("heroic") == 1 and "obs" in ids
    with pytest.raises(KeyError):
        gc_main.resolve_items(catalog, ["nope"], [])
    with pytest.raises(KeyError):
        gc_main.resolve_items(catalog, [], ["nope"])


def test_command_line_parser():
    args = gc_main.build_parser().parse_args(["--install", "heroic", "lutris", "--background"])
    assert args.install == ["heroic", "lutris"] and args.background and not args.preset
    args = gc_main.build_parser().parse_args(["--preset", "starter", "--page", "drivers"])
    assert args.preset == ["starter"] and args.page == "drivers"


def test_game_center_has_a_desktop_entry():
    entry = (ROOT / "data" / "applications" / "org.axonos.GameCenter.desktop").read_text()
    assert "Exec=python3 AXON_APPS_DIR/axon-game-center/main.py" in entry
    assert "Categories=Game" in entry or "Game;" in entry
    assert Path(ROOT / "apps" / "axon-game-center" / "main.css").is_file()


def test_command_line_accepts_only_nvidia_driver_packages():
    from catalog import driver_item

    assert driver_item("nvidia-driver-550").id == "gpu-driver"
    with pytest.raises(ValueError):
        driver_item("sudo")
