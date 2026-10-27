"""Phase 1 gaming packaging: metapackages, ISO policy and the Proton runner helper."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "packaging"


def _packages_list() -> list[str]:
    lines = (ROOT / "build" / "config" / "packages.list").read_text().splitlines()
    return [ln.strip() for ln in lines if not re.match(r"^\s*(#|$)", ln)]


def _control(name: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in (PKG / name / "control").read_text().splitlines():
        if line and not line.startswith(" ") and ":" in line:
            key, _, value = line.partition(":")
            fields[key] = value.strip()
    return fields


@pytest.mark.unit
def test_gaming_core_depends_on_the_headline_stack():
    depends = {d.strip() for d in _control("axon-gaming-core")["Depends"].split(",")}
    for want in (
        "steam-installer",
        "steam-devices",
        "gamemode",
        "libgamemode0:i386",
        "mangohud",
        "mesa-vulkan-drivers:i386",
        "libgl1-mesa-dri:i386",
        "libglx-mesa0:i386",
        "libvulkan1:i386",
    ):
        assert want in depends


@pytest.mark.unit
def test_iso_policy_keeps_download_later_apps_out():
    packages = set(_packages_list())
    banned = {
        "lutris",
        "libreoffice",
        "docker.io",
        "docker-compose-v2",
        "golang",
        "nodejs",
        "npm",
        "ffmpeg",
        "libavcodec-extra",
        "gstreamer1.0-plugins-bad",
        "gstreamer1.0-plugins-ugly",
        "gstreamer1.0-libav",
    }
    assert not packages & banned
    chroot_setup = (ROOT / "build" / "config" / "chroot-setup.sh").read_text()
    assert "flatpak install" not in chroot_setup, "no Flatpak apps in the ISO"


@pytest.mark.unit
def test_tweaks_ship_the_documented_settings():
    base = PKG / "axon-gaming-tweaks" / "files" / "etc"
    assert "vm.max_map_count = 2147483642" in (base / "sysctl.d/90-axon-gaming.conf").read_text()
    assert "kernel.split_lock_mitigate = 0" in (base / "sysctl.d/90-axon-gaming.conf").read_text()
    for manager in ("system", "user"):
        conf = (base / f"systemd/{manager}.conf.d/90-axon-limits.conf").read_text()
        assert "DefaultLimitNOFILE=1024:1048576" in conf
    zram = (base / "systemd/zram-generator.conf").read_text()
    assert "min(ram / 2, 8192)" in zram
    assert "zstd" in zram


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="needs dpkg-deb")
def test_build_pkg_builds_every_small_package(tmp_path):
    subprocess.run(["bash", str(PKG / "build-pkg.sh"), "--all", str(tmp_path)], check=True)
    debs = sorted(p.name for p in tmp_path.glob("*.deb"))
    assert [d.split("_")[0] for d in debs] == ["axon-gaming-core", "axon-gaming-tweaks"]
    info = subprocess.run(
        ["dpkg-deb", "-f", str(tmp_path / debs[0]), "Version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert "@VERSION@" not in info and info


def _runners(home: Path, *args: str) -> subprocess.CompletedProcess:
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
    }
    return subprocess.run(
        ["sh", str(ROOT / "scripts" / "axon-proton-runners"), *args],
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.unit
def test_proton_runners_list_select_and_resolve(tmp_path):
    ge = tmp_path / ".local/share/Steam/compatibilitytools.d/GE-Proton9-1/files/bin"
    ge.mkdir(parents=True)
    (ge / "wine").write_text("#!/bin/sh\n")
    (ge / "wine").chmod(0o755)
    (tmp_path / ".local/share/Steam/compatibilitytools.d/Empty").mkdir()

    listed = _runners(tmp_path, "list").stdout.splitlines()
    assert [row.split("\t")[0] for row in listed] == ["GE-Proton9-1"]
    assert _runners(tmp_path, "get").stdout.strip() == "system"
    assert _runners(tmp_path, "resolve").stdout.strip() == ""

    assert _runners(tmp_path, "set", "GE-Proton9-1").returncode == 0
    resolved = _runners(tmp_path, "resolve").stdout.strip()
    assert resolved.endswith("GE-Proton9-1/files")

    assert _runners(tmp_path, "set", "nope").returncode == 1
    assert _runners(tmp_path, "clear").returncode == 0
    assert _runners(tmp_path, "get").stdout.strip() == "system"


@pytest.mark.unit
def test_proton_runner_choice_is_dropped_when_the_build_is_removed(tmp_path):
    ge = tmp_path / ".local/share/Steam/compatibilitytools.d/GE-Proton9-1/files/bin"
    ge.mkdir(parents=True)
    (ge / "wine").write_text("#!/bin/sh\n")
    (ge / "wine").chmod(0o755)
    _runners(tmp_path, "set", "GE-Proton9-1")
    shutil.rmtree(tmp_path / ".local/share/Steam/compatibilitytools.d/GE-Proton9-1")
    assert _runners(tmp_path, "resolve").stdout.strip() == ""
