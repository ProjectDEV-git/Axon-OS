"""The welcome app's gaming step: choices become one background install command."""

import gaming_setup
import pytest
from catalog import driver_item, load_catalog, signed_modules_package


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
    item = driver_item(name, kernel_release="6.10.0-1-generic")
    assert item.source == {"type": "apt", "packages": [name]}  # no signed modules for this kernel


@pytest.mark.unit
@pytest.mark.parametrize(
    "name", ["bash", "nvidia-driver-550; rm -rf /", "nvidia-driver-", "../nvidia-driver-550", ""]
)
def test_driver_item_rejects_anything_else(name):
    with pytest.raises(ValueError):
        driver_item(name)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("driver", "modules"),
    [
        ("nvidia-driver-580-open", "linux-modules-nvidia-580-open-generic"),
        ("nvidia-driver-580", "linux-modules-nvidia-580-generic"),
        ("nvidia-driver-535-server", "linux-modules-nvidia-535-server-generic"),
        ("nvidia-driver-535-server-open", "linux-modules-nvidia-535-server-open-generic"),
    ],
)
def test_signed_modules_are_installed_first_so_apt_does_not_pick_dkms(driver, modules):
    item = driver_item(driver, kernel_release="6.8.0-147-generic")
    assert item.source["packages"] == [modules, driver]
    assert signed_modules_package(driver, "6.8.0-147-generic") == modules


@pytest.mark.unit
@pytest.mark.parametrize(
    "kernel",
    [
        "6.10.0-37-generic",
        "6.9.0-1-generic",
        "6.8.0-1015-nvidia",
        "6.8.0-147-lowlatency",
        "6.14.0-37-lowlatency",
        "",
    ],
)
def test_no_signed_modules_for_kernels_ubuntu_does_not_sign_them_for(kernel):
    assert signed_modules_package("nvidia-driver-580-open", kernel) is None
    assert driver_item("nvidia-driver-580-open", kernel).source["packages"] == [
        "nvidia-driver-580-open"
    ]


@pytest.mark.unit
def test_signed_modules_only_for_nvidia_driver_names():
    assert signed_modules_package("bash", "6.8.0-147-generic") is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "kernel", ["7.0.0-38-generic", "6.14.0-37-generic", "6.11.0-1-generic", "6.17.0-5-generic"]
)
def test_hwe_kernels_use_the_hwe_signed_module_packages(kernel):
    assert signed_modules_package("nvidia-driver-580-open", kernel) == (
        "linux-modules-nvidia-580-open-generic-hwe-24.04"
    )
    item = driver_item("nvidia-driver-580-open", kernel)
    assert item.source["packages"] == [
        "linux-modules-nvidia-580-open-generic-hwe-24.04",
        "nvidia-driver-580-open",
    ]


# -- NVIDIA branch by architecture -----------------------------------------------------------


def _nv(name):
    from gpu_drivers import Gpu

    return Gpu("nvidia", name, "10de:0000", "")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "generation"),
    [
        ("NVIDIA Corporation GP106 [GeForce GTX 1060 6GB]", "maxwell-volta"),
        ("NVIDIA Corporation GM204 [GeForce GTX 970]", "maxwell-volta"),
        ("NVIDIA Corporation GV100 [TITAN V]", "maxwell-volta"),
        ("NVIDIA Corporation GP108 [GeForce GT 1030]", "maxwell-volta"),
        ("NVIDIA Corporation TU117 [GeForce GTX 1650]", "turing+"),  # GTX, but Turing
        ("NVIDIA Corporation TU106 [GeForce RTX 2060]", "turing+"),
        ("NVIDIA Corporation AD104 [GeForce RTX 4070]", "turing+"),
        ("NVIDIA Corporation GB202 [GeForce RTX 5090]", "turing+"),
        ("GeForce GTX 1660 Ti", "turing+"),  # no chip code: the name decides
        ("GeForce GTX 1070", "maxwell-volta"),
        ("NVIDIA Corporation GK107 [GeForce GT 640]", "older"),
        ("Something New", "unknown"),
    ],
)
def test_nvidia_generation(name, generation):
    from gpu_drivers import nvidia_generation

    assert nvidia_generation(name) == generation


@pytest.mark.unit
def test_driver_branch_follows_the_architecture():
    from gpu_drivers import choose_nvidia_driver

    turing = _nv("NVIDIA Corporation TU106 [GeForce RTX 2060]")
    pascal = _nv("NVIDIA Corporation GP106 [GeForce GTX 1060 6GB]")
    kepler = _nv("NVIDIA Corporation GK107 [GeForce GT 640]")
    assert choose_nvidia_driver([turing], "nvidia-driver-580-open") == "nvidia-driver-595-open"
    assert choose_nvidia_driver([pascal], "nvidia-driver-595-open") == "nvidia-driver-580"
    # the older card decides in a mixed machine: 580 supports both
    assert choose_nvidia_driver([turing, pascal], None) == "nvidia-driver-580"
    # not placed: ubuntu-drivers knows the PCI ids better than the name does
    assert choose_nvidia_driver([kepler], "nvidia-driver-470") == "nvidia-driver-470"
    assert choose_nvidia_driver([_nv("Something New")], "nvidia-driver-580") == "nvidia-driver-580"
    # no NVIDIA card: nothing changes
    from gpu_drivers import Gpu

    assert choose_nvidia_driver([Gpu("amd", "Radeon", "1002:0", "")], None) is None


@pytest.mark.unit
def test_both_branches_have_signed_modules_on_both_releases():
    for driver, modules in (
        ("nvidia-driver-595-open", "linux-modules-nvidia-595-open-generic"),
        ("nvidia-driver-580", "linux-modules-nvidia-580-generic"),
    ):
        assert signed_modules_package(driver, "6.8.0-147-generic", "noble") == modules
        assert signed_modules_package(driver, "7.0.0-38-generic", "resolute") == modules
        assert signed_modules_package(driver, "7.0.0-38-generic", "noble") == (
            modules + "-hwe-24.04"
        )
