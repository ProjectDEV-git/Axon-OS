"""Rebuilding newer Ubuntu packages for 24.04: the list, the script, the workflow and the apt pin."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LIST = ROOT / "packaging" / "backports" / "backports.list"
SCRIPT = ROOT / "scripts" / "build-backports.sh"


def entries() -> list[tuple[str, str, str]]:
    rows = [
        ln.split() for ln in LIST.read_text().splitlines() if ln.strip() and not ln.startswith("#")
    ]
    return [(r[0], r[1], r[2]) for r in rows]


@pytest.mark.unit
def test_every_entry_is_a_pinned_source_package_from_a_named_release():
    rows = entries()
    assert rows
    for name, suite, version in rows:
        assert re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", name), name
        assert suite in ("questing", "plucky", "resolute"), f"{name}: later than noble only"
        assert version and not version.startswith(("latest", "*")), f"{name}: pin an exact version"
    assert len({n for n, _, _ in rows}) == len(rows), "a package is listed twice"


@pytest.mark.unit
def test_libraries_are_built_before_the_packages_that_need_them():
    order = [n for n, _, _ in entries()]
    assert order[-1] == "gamescope"
    assert order.index("wayland") < order.index("wlroots") < order.index("gamescope")
    for lib in ("libliftoff", "vkroots", "xcb-util-errors"):
        assert order.index(lib) < order.index("gamescope")


@pytest.mark.unit
def test_gamescope_is_what_the_console_session_and_the_image_wait_for():
    assert "gamescope" in {n for n, _, _ in entries()}
    console = (ROOT / "packaging" / "axon-console" / "control").read_text()
    assert re.search(r"^Depends:.*\bgamescope\b", console, re.M)
    core = (ROOT / "packaging" / "axon-gaming-core" / "control").read_text()
    assert re.search(r"^Recommends:\s*gamescope\s*$", core, re.M), (
        "in the image whenever the repository has it"
    )


@pytest.mark.unit
def test_patches_are_proper_quilt_patches_for_listed_packages():
    names = {n for n, _, _ in entries()}
    for patch in (ROOT / "packaging" / "backports" / "patches").glob("*/*.patch"):
        assert patch.parent.name in names, f"{patch}: not a listed package"
        text = patch.read_text()
        assert text.startswith("Description:"), f"{patch}: say why"
        assert "\n--- a/" in text and "\n+++ b/" in text


@pytest.mark.unit
def test_the_wlroots_patch_keeps_meson_1_3_away_from_the_c23_standard():
    patch = (
        ROOT / "packaging/backports/patches/wlroots/0001-meson-c23-needs-meson-1.4.patch"
    ).read_text()
    assert "-\t\t'c_std=' + (meson.version().version_compare('>=1.3.0')" in patch
    assert "+\t\t'c_std=' + (meson.version().version_compare('>=1.4.0')" in patch


@pytest.mark.unit
def test_script_syntax_and_usage():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
    result = subprocess.run(["bash", str(SCRIPT), "--bogus"], capture_output=True, text=True)
    assert result.returncode == 1 and "unknown option" in result.stderr


@pytest.mark.unit
@pytest.mark.skipif(
    not all(shutil.which(t) for t in ("sbuild", "mmdebstrap", "dch", "dpkg-source", "apt-get")),
    reason="needs sbuild, mmdebstrap and devscripts",
)
@pytest.mark.parametrize(
    "line",
    [
        "onlyone",
        "name questing",
        "bad_name questing 1.0",
        "name Questing 1.0",
        "a questing 1 extra",
    ],
)
def test_script_rejects_malformed_list_lines_before_building_anything(tmp_path, line):
    bad = tmp_path / "list"
    bad.write_text(line + "\n")
    result = subprocess.run(
        ["bash", str(SCRIPT), "--out", str(tmp_path / "out")],
        env={"PATH": "/usr/bin:/bin", "AXON_BACKPORTS_LIST": str(bad), "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1, result.stdout
    assert "bad line" in result.stderr or "bad name" in result.stderr


@pytest.mark.unit
def test_versions_sort_below_any_later_official_ubuntu_version():
    script = SCRIPT.read_text()
    assert 'SUFFIX="${AXON_BACKPORT_SUFFIX:-~24.04.axon1}"' in script
    if shutil.which("dpkg"):
        official, ours = "0.5.0-1.1", "0.5.0-1.1~24.04.axon1"
        assert subprocess.run(["dpkg", "--compare-versions", ours, "lt", official]).returncode == 0


# -- publishing -----------------------------------------------------------------------------------------


def workflow() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load((ROOT / ".github" / "workflows" / "publish-apt.yml").read_text())


@pytest.mark.unit
def test_backports_are_only_built_when_asked_and_do_not_block_publishing_when_skipped():
    wf = workflow()
    triggers = wf[True]
    assert triggers["workflow_dispatch"]["inputs"]["backports"]["default"] is False
    assert triggers["workflow_call"]["inputs"]["backports"]["default"] is False
    jobs = wf["jobs"]
    assert jobs["backports"]["if"] == "inputs.backports"
    assert jobs["publish"]["needs"] == "backports"
    assert "'skipped'" in jobs["publish"]["if"] and "'success'" in jobs["publish"]["if"]


@pytest.mark.unit
def test_publishing_refuses_packages_github_pages_would_reject():
    text = (ROOT / ".github" / "workflows" / "publish-apt.yml").read_text()
    assert "-size +95M" in text and "too big for GitHub Pages" in text
    assert 'ls "${pool}/${name}"_*.deb' in text, "retention covers every architecture"


@pytest.mark.unit
def test_the_repository_pin_stops_rebuilt_libraries_replacing_ubuntus_own():
    deb = (ROOT / "packaging" / "build-deb.sh").read_text()
    block = deb[deb.index("preferences.d/axon-os") :]
    assert "Pin: release o=Axon OS\nPin-Priority: 100" in block
    assert "Package: axon-*\nPin: release o=Axon OS\nPin-Priority: 500" in block
    assert 'Origin="Axon OS"' in (ROOT / ".github/workflows/publish-apt.yml").read_text(), (
        "the pin matches the Release file"
    )


@pytest.mark.unit
def test_image_reads_the_axon_repository_before_installing_the_gaming_packages():
    text = (ROOT / "build" / "config" / "chroot-setup.sh").read_text()
    update = text.index("could not read the Axon repository")
    gaming = text.index("apt-cache policy gamescope")
    assert update < gaming
