"""The welcome app's Gaming setup page."""

from __future__ import annotations

import time

from tests.gtk_helpers import load_module, pump, require_gtk

Gtk, Adw, GLib = require_gtk()

page_mod = load_module("axon_welcome_gaming_page", "apps/axon-welcome/gaming_page.py")
gpu_drivers = __import__("gpu_drivers")

NONE = {"gpus": [], "recommended": None, "nvidia_version": None, "secure_boot": None}


def make(online=True, detect=None):
    launched, moves = [], []
    page = page_mod.GamingPage(
        on_back=lambda: moves.append("back"),
        on_next=lambda: moves.append("next"),
        detect=lambda: detect or NONE,
        online=lambda: online,
        launch=launched.append,
    )
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        pump(GLib, 100)
        time.sleep(0.01)
        if page._summary.get_label():
            break
    return page, launched, moves


def test_starter_pack_is_selected_by_default():
    page, _, _ = make()
    assert page.selected() == {"heroic", "lutris", "protonplus", "goverlay", "discord"}
    assert page._preset_buttons["starter"].get_active()
    assert "Download: about" in page._summary.get_label()


def test_picking_a_preset_changes_the_apps_and_the_size():
    page, _, _ = make()
    before = page._summary.get_label()
    page._preset_buttons["retro"].set_active(True)
    assert page.selected() == {"retroarch", "dolphin", "pcsx2", "ppsspp"}
    assert page._summary.get_label() != before
    page._preset_buttons["none"].set_active(True)
    assert page.selected() == set()
    assert page._summary.get_label() == "Nothing will be installed."


def test_customize_checkboxes_add_apps():
    page, _, _ = make()
    page._boxes["obs"].set_active(True)
    assert "obs" in page.selected()


def test_codecs_are_off_by_default_and_opt_in():
    page, launched, _ = make()
    assert "media-codecs" not in page.selected()
    page._codecs.set_active(True)
    page.apply()
    assert "media-codecs" in launched[0]


def test_continue_launches_one_background_install_then_moves_on():
    page, launched, moves = make()
    page.apply()
    assert len(launched) == 1
    argv = launched[0]
    assert "--background" in argv and argv[argv.index("--install") + 1] == "heroic"
    assert moves == ["next"]


def test_nothing_for_now_installs_nothing():
    page, launched, moves = make()
    page._preset_buttons["none"].set_active(True)
    page.apply()
    assert launched == [] and moves == ["next"]


def test_offline_hides_the_choices_and_points_to_game_center():
    page, launched, _ = make(online=False)
    assert not page._choices.get_visible() and not page._continue.get_visible()
    assert page._intro.get_label() == "You can install these later from Game Center."
    assert launched == []


def test_nvidia_driver_toggle_defaults_on_and_is_passed_along():
    gpu = gpu_drivers.Gpu("nvidia", "NVIDIA GeForce RTX 4070", "10de:2783", "", 12.0)
    info = {
        "gpus": [gpu],
        "recommended": "nvidia-driver-550",
        "nvidia_version": None,
        "secure_boot": False,
    }
    page, launched, _ = make(detect=info)
    assert page._driver_check.get_visible() and page._driver_check.get_active()
    page.apply()
    assert launched[0][-2:] == ["--driver", "nvidia-driver-550"]


def test_driver_toggle_can_be_turned_off_and_is_hidden_when_not_needed():
    gpu = gpu_drivers.Gpu("nvidia", "NVIDIA GeForce RTX 4070", "10de:2783", "", 12.0)
    info = {
        "gpus": [gpu],
        "recommended": "nvidia-driver-550",
        "nvidia_version": None,
        "secure_boot": False,
    }
    page, launched, _ = make(detect=info)
    page._driver_check.set_active(False)
    page.apply()
    assert "--driver" not in launched[0]
    page, _, _ = make(detect={**info, "nvidia_version": "550.1"})
    assert not page._driver_check.get_visible()
    page, _, _ = make()
    assert not page._driver_check.get_visible()


def test_back_and_skip_do_not_install():
    page, launched, moves = make()
    nav = page.get_last_child()
    buttons = []
    child = nav.get_first_child()
    while child is not None:
        buttons.append(child)
        child = child.get_next_sibling()
    buttons[0].emit("clicked")
    buttons[1].emit("clicked")
    assert moves == ["back", "next"] and launched == []
