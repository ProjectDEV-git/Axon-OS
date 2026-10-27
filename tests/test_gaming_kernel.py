"""The gaming kernel (Ubuntu's HWE kernel): install, removal, NVIDIA modules, ntsync, GRUB fallback."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from catalog import (
    gaming_kernel_item,
    gaming_kernel_offered,
    gaming_kernel_removal_item,
    hwe_kernel_removal,
    hwe_nvidia_modules,
    load_catalog,
    signed_modules_package,
    ubuntu_codename,
)
from catalog_installer import installed_linux_packages

ROOT = Path(__file__).resolve().parent.parent
WATCHDOG = ROOT / "build" / "config" / "grub.d-06_axon_watchdog"
FALLBACK = ROOT / "build" / "config" / "grub.d-41_axon_kernel_fallback"

INSTALLED = [
    "linux-image-generic",
    "linux-image-6.8.0-147-generic",
    "linux-modules-6.8.0-147-generic",
    "linux-image-generic-hwe-24.04",
    "linux-image-7.0.0-38-generic",
    "linux-modules-7.0.0-38-generic",
    "linux-modules-extra-7.0.0-38-generic",
    "linux-modules-nvidia-580-open-generic",
    "linux-modules-nvidia-580-open-6.8.0-147-generic",
    "linux-modules-nvidia-580-open-generic-hwe-24.04",
    "linux-modules-nvidia-580-open-7.0.0-38-generic",
]


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


# -- catalog entry and package choices -------------------------------------------------------------


@pytest.mark.unit
def test_gaming_kernel_is_ubuntus_own_signed_hwe_kernel(catalog):
    item = catalog.items["gaming-kernel"]
    assert item.source == {"type": "apt", "packages": ["linux-image-generic-hwe-24.04"]}
    assert item.category == "kernel"
    assert "Secure Boot" in (item.notes or "")


@pytest.mark.unit
def test_nvidia_modules_for_the_new_kernel_come_with_it(catalog):
    base = catalog.items["gaming-kernel"]
    plain = gaming_kernel_item(base, ["linux-image-generic", "linux-image-6.8.0-147-generic"])
    assert plain.source["packages"] == ["linux-image-generic-hwe-24.04"]
    with_nvidia = gaming_kernel_item(base, INSTALLED)
    assert with_nvidia.source["packages"] == [
        "linux-image-generic-hwe-24.04",
        "linux-modules-nvidia-580-open-generic-hwe-24.04",
    ]
    assert hwe_nvidia_modules(INSTALLED) == ["linux-modules-nvidia-580-open-generic-hwe-24.04"]


@pytest.mark.unit
def test_removal_takes_out_the_hwe_kernel_and_leaves_the_standard_one():
    remove = hwe_kernel_removal(INSTALLED, "6.8.0-147-generic")
    assert remove is not None
    assert "linux-image-7.0.0-38-generic" in remove
    assert "linux-modules-7.0.0-38-generic" in remove
    assert "linux-modules-nvidia-580-open-7.0.0-38-generic" in remove
    assert "linux-image-generic-hwe-24.04" in remove
    assert "linux-modules-nvidia-580-open-generic-hwe-24.04" in remove
    standard = [
        p
        for p in remove
        if "6.8.0" in p or p in ("linux-image-generic", "linux-modules-nvidia-580-open-generic")
    ]
    assert standard == [], "the standard kernel and its NVIDIA modules stay"


@pytest.mark.unit
def test_the_running_gaming_kernel_cannot_be_removed():
    assert hwe_kernel_removal(INSTALLED, "7.0.0-38-generic") is None


@pytest.mark.unit
def test_removal_item_is_none_while_running_it_and_keeps_the_catalog_item_otherwise(catalog):
    base = catalog.items["gaming-kernel"]
    assert gaming_kernel_removal_item(base, INSTALLED, "7.0.0-38-generic") is None
    item = gaming_kernel_removal_item(base, INSTALLED, "6.8.0-147-generic")
    assert item is not None and item.id == "gaming-kernel"
    assert "linux-image-7.0.0-38-generic" in item.source["packages"]
    assert base.source["packages"] == ["linux-image-generic-hwe-24.04"], (
        "the catalog item is unchanged"
    )


@pytest.mark.unit
def test_installed_linux_packages_reads_dpkg_status():
    def runner(argv, on_line):
        for line in (
            "ii  linux-image-7.0.0-38-generic",
            "rc  linux-image-6.5.0-1-generic",
            "ii  linux-firmware",
        ):
            on_line(line)
        return 0

    assert installed_linux_packages(runner) == ["linux-image-7.0.0-38-generic", "linux-firmware"]


# -- ntsync --------------------------------------------------------------------------------------------


@pytest.mark.unit
def test_ntsync_loads_only_on_kernels_that_have_the_module():
    unit = (
        ROOT / "packaging/axon-gaming-tweaks/files/usr/lib/systemd/system/axon-ntsync.service"
    ).read_text()
    assert "ConditionPathExistsGlob=/lib/modules/%v/kernel/drivers/misc/ntsync.ko*" in unit
    assert "ExecStart=/sbin/modprobe ntsync" in unit
    post = (ROOT / "packaging/axon-gaming-tweaks/postinst").read_text()
    assert "rm -f /etc/modules-load.d/axon-ntsync.conf" in post, (
        "the old install-time check is gone"
    )
    assert "modinfo" not in post


# -- GRUB fallback to the standard kernel ------------------------------------------------------------


def boot_dir(tmp_path: Path, kernels: list[str]) -> Path:
    boot = tmp_path / "boot"
    boot.mkdir()
    for ver in kernels:
        (boot / f"vmlinuz-{ver}").write_text("k")
        (boot / f"initrd.img-{ver}").write_text("i")
    return boot


def grub_tools(tmp_path: Path, fs: str = "ext4") -> dict:
    bin_dir = tmp_path / "tools"
    bin_dir.mkdir()
    probe = bin_dir / "grub-probe"
    probe.write_text(
        '#!/bin/sh\ncase "$*" in\n'
        f'  *fs_uuid*) echo 1234-abcd ;;\n  *"--target=fs "*) echo {fs} ;;\n  *) echo {fs} ;;\nesac\n'
    )
    relpath = bin_dir / "grub-mkrelpath"
    relpath.write_text('#!/bin/sh\ncase "$1" in\n  /) echo /@ ;;\n  *) echo /@/boot ;;\nesac\n')
    for tool in (probe, relpath):
        tool.chmod(0o755)
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}"}


def run_fallback(tmp_path: Path, kernels: list[str], fs: str = "ext4"):
    env = {
        **grub_tools(tmp_path, fs),
        "AXON_BOOT_DIR": str(boot_dir(tmp_path, kernels)),
        "GRUB_CMDLINE_LINUX": "",
        "GRUB_CMDLINE_LINUX_DEFAULT": "quiet splash",
    }
    return subprocess.run(
        ["sh", str(FALLBACK)], env=env, capture_output=True, text=True, timeout=30
    )


@pytest.mark.unit
def test_fallback_entry_boots_the_standard_kernel_when_a_newer_one_exists(tmp_path):
    result = run_fallback(tmp_path, ["6.8.0-147-generic", "7.0.0-38-generic"])
    assert result.returncode == 0
    out = result.stdout
    assert "--id axon-ga-kernel" in out
    assert "vmlinuz-6.8.0-147-generic root=UUID=1234-abcd ro" in out
    assert "initrd.img-6.8.0-147-generic" in out
    assert "7.0.0" not in out
    assert 'if [ "${axon_try_ga}" = "1" ]' in out and "set default='axon-ga-kernel'" in out


@pytest.mark.unit
def test_fallback_uses_the_newest_standard_kernel(tmp_path):
    out = run_fallback(
        tmp_path, ["6.8.0-100-generic", "6.8.0-147-generic", "6.8.0-9-generic", "7.0.0-38-generic"]
    ).stdout
    assert "vmlinuz-6.8.0-147-generic" in out


@pytest.mark.unit
def test_btrfs_subvolume_is_passed_to_the_kernel(tmp_path):
    out = run_fallback(tmp_path, ["6.8.0-147-generic", "7.0.0-38-generic"], fs="btrfs").stdout
    assert "rootflags=subvol=@" in out and "insmod btrfs" in out


@pytest.mark.unit
@pytest.mark.parametrize(
    "kernels",
    [
        ["6.8.0-147-generic"],  # nothing newer: the standard kernel is already first
        ["7.0.0-38-generic"],  # no standard kernel to fall back to
        [],
    ],
)
def test_fallback_emits_nothing_when_there_is_nothing_to_fall_back_to(tmp_path, kernels):
    result = run_fallback(tmp_path, kernels)
    assert result.returncode == 0 and result.stdout == ""


@pytest.mark.unit
def test_fallback_never_fails_grub_when_tools_are_missing(tmp_path):
    boot = boot_dir(tmp_path, ["6.8.0-147-generic", "7.0.0-38-generic"])
    stub = tmp_path / "broken"
    stub.mkdir()
    (stub / "grub-probe").write_text("#!/bin/sh\nexit 1\n")
    (stub / "grub-probe").chmod(0o755)
    result = subprocess.run(
        ["sh", str(FALLBACK)],
        env={"PATH": f"{stub}:/usr/bin:/bin", "AXON_BOOT_DIR": str(boot)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0 and result.stdout == ""


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("grub-script-check") is None, reason="needs grub-common")
def test_emitted_grub_script_is_valid(tmp_path):
    out = run_fallback(tmp_path, ["6.8.0-147-generic", "7.0.0-38-generic"]).stdout
    cfg = tmp_path / "grub.cfg"
    cfg.write_text(out)
    check = subprocess.run(["grub-script-check", str(cfg)], capture_output=True, text=True)
    assert check.returncode == 0, check.stderr


@pytest.mark.unit
def test_watchdog_asks_for_the_standard_kernel_on_the_second_attempt_only():
    text = WATCHDOG.read_text()
    first = text.index('if [ "${boot_attempts}" = "1" ]; then')
    second = text.index('elif [ "${boot_attempts}" = "2" ]; then')
    assert "set axon_try_ga=1" in text[first:second], "attempt 1 -> 2 tries the previous kernel"
    assert "axon_try_ga" not in text[second:], "attempt 3 goes to the factory rollback"
    assert "Recovery rollback" in text[second:]


@pytest.mark.unit
def test_the_fallback_script_is_installed_between_the_kernel_and_rollback_entries():
    deb = (ROOT / "packaging" / "build-deb.sh").read_text()
    assert '"${R}/etc/grub.d/41_axon_kernel_fallback"' in deb
    # 06 (counter) < 10_linux (entries) < 41 (fallback sets the default) < 42 (rollback)
    assert "06_axon_watchdog" in deb and "42_axon_rollback" in deb


# -- Ubuntu 26.04 (resolute) -------------------------------------------------------------


@pytest.mark.unit
def test_the_fallback_is_the_oldest_kernel_series_after_an_upgrade_to_26_04(tmp_path):
    # 24.04 -> 26.04 leaves the 6.8 kernel installed next to the 7.0 one
    out = run_fallback(
        tmp_path, ["6.8.0-147-generic", "7.0.0-37-generic", "7.0.0-38-generic"]
    ).stdout
    assert "vmlinuz-6.8.0-147-generic" in out and "7.0.0" not in out


@pytest.mark.unit
def test_no_fallback_between_two_builds_of_the_same_series(tmp_path):
    result = run_fallback(tmp_path, ["7.0.0-37-generic", "7.0.0-38-generic"])
    assert result.returncode == 0 and result.stdout == ""


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("UBUNTU_CODENAME=noble\nVERSION_CODENAME=pulse\n", "noble"),
        ('VERSION_CODENAME="resolute"\n', "resolute"),
        ("NAME=x\n", ""),
    ],
)
def test_ubuntu_codename_comes_from_os_release(tmp_path, text, expected):
    path = tmp_path / "os-release"
    path.write_text(text)
    assert ubuntu_codename(path) == expected
    assert ubuntu_codename(tmp_path / "missing") == ""


@pytest.mark.unit
def test_the_gaming_kernel_is_offered_on_24_04_only():
    assert gaming_kernel_offered("noble")
    assert not gaming_kernel_offered("resolute")
    assert not gaming_kernel_offered("")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kernel", "codename", "expected"),
    [
        ("6.8.0-147-generic", "noble", "linux-modules-nvidia-580-open-generic"),
        ("7.0.0-38-generic", "noble", "linux-modules-nvidia-580-open-generic-hwe-24.04"),
        # 26.04's own kernel uses the plain name, whatever its version
        ("7.0.0-38-generic", "resolute", "linux-modules-nvidia-580-open-generic"),
        ("7.4.0-3-generic", "resolute", "linux-modules-nvidia-580-open-generic"),
        ("7.0.0-38-lowlatency", "resolute", None),
    ],
)
def test_signed_nvidia_modules_follow_the_release(kernel, codename, expected):
    assert signed_modules_package("nvidia-driver-580-open", kernel, codename) == expected
