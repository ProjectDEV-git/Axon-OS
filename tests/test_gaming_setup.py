"""The welcome app's gaming step: choices become one background install command."""

import gaming_setup
import pytest
from catalog import driver_item, load_catalog


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


@pytest.mark.unit
def test_presets_select_their_items(catalog):
    assert gaming_setup.preset_ids(catalog, "starter") == [
        "heroic", "lutris", "protonplus", "goverlay", "discord",
    ]  # fmt: skip
    assert gaming_setup.preset_ids(catalog, "none") == []
    assert gaming_setup.preset_ids(catalog, "nonsense") == []
    assert gaming_setup.preset_ids(catalog, "streamer")[-2:] == ["obs", "gpu-screen-recorder"]
    assert gaming_setup.preset_ids(catalog, "retro") == ["retroarch", "dolphin", "pcsx2", "ppsspp"]


@pytest.mark.unit
def test_selected_ids_keep_catalog_order_and_drop_unknown_and_components(catalog):
    chosen = {"discord", "heroic", "bogus", "ai-models", "ollama-cuda"}
    # the AI model download belongs to the AI services; the GPU libraries install from here
    assert gaming_setup.selected_ids(catalog, chosen) == ["heroic", "discord", "ollama-cuda"]


@pytest.mark.unit
def test_install_argv_is_a_background_install(catalog):
    argv = gaming_setup.install_argv(["heroic", "lutris"])
    assert argv[-4:] == ["--background", "--install", "heroic", "lutris"]
    with_driver = gaming_setup.install_argv(["heroic"], "nvidia-driver-550")
    assert with_driver[-2:] == ["--driver", "nvidia-driver-550"]
    assert gaming_setup.install_argv([], "nvidia-driver-550")[-2:] == [
        "--driver",
        "nvidia-driver-550",
    ]


@pytest.mark.unit
def test_nothing_chosen_installs_nothing():
    assert gaming_setup.install_argv([]) is None
    assert gaming_setup.install_argv([], None) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "name", ["nvidia-driver-550", "nvidia-driver-570-open", "nvidia-driver-535-server"]
)
def test_driver_item_accepts_nvidia_driver_packages(name):
    item = driver_item(name)
    assert item.source == {"type": "apt", "packages": [name]}


@pytest.mark.unit
@pytest.mark.parametrize(
    "name", ["bash", "nvidia-driver-550; rm -rf /", "nvidia-driver-", "../nvidia-driver-550", ""]
)
def test_driver_item_rejects_anything_else(name):
    with pytest.raises(ValueError):
        driver_item(name)
