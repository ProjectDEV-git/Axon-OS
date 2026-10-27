"""Upgrading Axon OS from Ubuntu 24.04 to 26.04: checks, snapshot, rollback, fix-ups."""

import json
from datetime import datetime
from pathlib import Path

import pytest
import release_upgrade as ru

NOW = datetime(2026, 11, 1, 9, 30, 5)
ROOT = Path(__file__).resolve().parent.parent


def good_facts(**changes):
    base = {
        "codename": "noble",
        "fs_type": "btrfs",
        "root_free_mb": 40_000,
        "boot_free_mb": 800,
        "on_battery": False,
        "reboot_required": False,
        "held_packages": [],
        "axon_version": "1.1.0",
        "live_session": False,
    }
    base.update(changes)
    return ru.Facts(**base)


# -- checks ------------------------------------------------------------------------------


@pytest.mark.unit
def test_a_healthy_24_04_system_may_upgrade():
    assert ru.preflight(good_facts()) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("changes", "word"),
    [
        ({"root_free_mb": 3000}, "Free at least"),
        ({"boot_free_mb": 50}, "/boot"),
        ({"on_battery": True}, "power adapter"),
        ({"reboot_required": True}, "Restart first"),
        ({"held_packages": ["linux-image-generic", "gdm3"]}, "gdm3, linux-image-generic"),
        ({"axon_version": None}, "not installed"),
        ({"axon_version": "1.0.9"}, "1.1.0 or newer"),
        ({"live_session": True}, "live session"),
        ({"fs_type": "ext4"}, "--no-snapshot"),
    ],
)
def test_each_blocker_is_reported(changes, word):
    problems = ru.preflight(good_facts(**changes))
    assert len(problems) == 1 and word in problems[0]


@pytest.mark.unit
def test_ext4_may_upgrade_when_the_user_accepts_no_snapshot():
    assert ru.preflight(good_facts(fs_type="ext4"), allow_no_snapshot=True) == []


@pytest.mark.unit
@pytest.mark.parametrize("codename", ["resolute", "", "jammy"])
def test_nothing_to_upgrade_to(codename):
    problems = ru.preflight(good_facts(codename=codename))
    assert len(problems) == 1


@pytest.mark.unit
def test_target_and_version_comparison():
    assert ru.target_for("noble") == "resolute" and ru.target_for("resolute") is None
    assert ru.version_key("1.10.0") > ru.version_key("1.9.3")
    assert ru.version_key("1.2.0-3") == (1, 2, 0)


@pytest.mark.unit
def test_the_minimum_axon_version_is_not_newer_than_this_checkout():
    text = (ROOT / "pyproject.toml").read_text()
    version = text.split('version = "', 1)[1].split('"', 1)[0]
    assert ru.version_key(ru.MIN_AXON_VERSION) <= ru.version_key(version)


# -- snapshot and rollback -----------------------------------------------------------------


@pytest.mark.unit
def test_snapshot_commands_and_names():
    assert ru.snapshot_name(NOW) == "@axon-pre-upgrade-20261101-093005"
    assert ru.snapshot_steps("/mnt/top", "@axon-pre-upgrade-1") == [
        ["btrfs", "subvolume", "snapshot", "/mnt/top/@", "/mnt/top/@axon-pre-upgrade-1"]
    ]
    # writable: GRUB boots it as a system, and it becomes @ again on rollback
    assert "-r" not in ru.snapshot_steps("/t", "x")[0]


@pytest.mark.unit
def test_latest_snapshot_ignores_other_subvolumes():
    names = [
        "@",
        "@home",
        "@axon-fallback",
        "@axon-pre-upgrade-20261101-093005",
        "@axon-pre-upgrade-20270201-101010",
        "@axon-pre-upgrade-oops",
    ]
    assert ru.latest_snapshot(names) == "@axon-pre-upgrade-20270201-101010"
    assert ru.latest_snapshot(["@", "@home"]) is None


@pytest.mark.unit
def test_restore_keeps_the_failed_system_aside():
    steps = ru.restore_steps("/t", "@axon-pre-upgrade-1", "2")
    assert steps[0] == ["mv", "/t/@", "/t/@axon-failed-upgrade-2"]
    assert steps[1] == ["btrfs", "subvolume", "snapshot", "/t/@axon-pre-upgrade-1", "/t/@"]


@pytest.mark.unit
def test_source_device_drops_the_subvolume_suffix():
    assert ru.source_device("/dev/mapper/cryptroot[/@]\n") == "/dev/mapper/cryptroot"
    assert ru.source_device("/dev/sda2\n") == "/dev/sda2"


# -- fix-ups -------------------------------------------------------------------------------


@pytest.mark.unit
def test_os_release_names_the_new_ubuntu_release():
    old = 'NAME="Axon OS"\nUBUNTU_CODENAME=noble\nLOGO=axon-os\n'
    new = ru.fix_os_release(old, "resolute")
    assert "UBUNTU_CODENAME=resolute" in new and "noble" not in new and "LOGO=axon-os" in new
    assert ru.fix_os_release("NAME=x\n", "resolute").endswith("UBUNTU_CODENAME=resolute\n")


@pytest.mark.unit
def test_release_prompt_is_set_to_lts():
    assert "Prompt=lts" in ru.set_prompt_lts("[DEFAULT]\n# c\nPrompt=normal\n")
    assert ru.set_prompt_lts("").count("Prompt=lts") == 1
    assert "normal" not in ru.set_prompt_lts("Prompt=normal\n")


@pytest.mark.unit
def test_axon_source_is_switched_back_on():
    off = "Types: deb\nURIs: https://x\nEnabled: no\nSuites: stable\n"
    assert not ru.axon_source_enabled(off)
    on = ru.enable_axon_source(off)
    assert ru.axon_source_enabled(on) and "Enabled" not in on and "Suites: stable" in on
    assert not ru.axon_source_enabled("# Types: deb\n")


@pytest.mark.unit
def test_the_axon_source_text_in_the_package_matches_what_we_expect():
    deb = (ROOT / "packaging" / "build-deb.sh").read_text()
    assert "Suites: stable" in deb, "the Axon source is not tied to an Ubuntu codename"


@pytest.mark.unit
def test_upgrade_command_keeps_third_party_sources_and_asks_nothing():
    cmd = ru.upgrade_command()
    assert cmd[0] == "do-release-upgrade"
    assert "DistUpgradeViewNonInteractive" in cmd and "--allow-third-party" in cmd
    # the sources were already handled by us; the upgrader must leave the Axon one on


# -- reading the machine -------------------------------------------------------------------


@pytest.mark.unit
def test_codename_prefers_axon_os_release(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "lsb-release").write_text("DISTRIB_CODENAME=noble\n")
    assert ru.read_codename(tmp_path) == "noble"
    (tmp_path / "etc" / "os-release").write_text('UBUNTU_CODENAME="resolute"\n')
    assert ru.read_codename(tmp_path) == "resolute"
    assert ru.read_codename(tmp_path / "nowhere") == ""


def power_supply(root, **supplies):
    for name, (kind, online) in supplies.items():
        d = root / "sys" / "class" / "power_supply" / name
        d.mkdir(parents=True)
        (d / "type").write_text(kind + "\n")
        if online is not None:
            (d / "online").write_text(f"{online}\n")


@pytest.mark.unit
def test_battery_detection(tmp_path):
    assert ru.on_battery(tmp_path) is None  # nothing there: a desktop
    power_supply(tmp_path, BAT0=("Battery", None), AC=("Mains", 0))
    assert ru.on_battery(tmp_path) is True


@pytest.mark.unit
def test_plugged_in_laptop_is_not_on_battery(tmp_path):
    power_supply(tmp_path, BAT0=("Battery", None), AC=("Mains", 1))
    assert ru.on_battery(tmp_path) is False


@pytest.mark.unit
def test_desktop_with_only_mains_has_no_battery(tmp_path):
    power_supply(tmp_path, AC=("Mains", 1))
    assert ru.on_battery(tmp_path) is None


# -- the whole run, with a pretend machine -------------------------------------------------


class Machine:
    """A runner that records commands and answers the ones the upgrade asks about."""

    def __init__(self, root, fail=None, fs="btrfs", version="1.1.0", source="/dev/sda2[/@]"):
        self.root = root
        self.calls: list[list[str]] = []
        self.fail = fail or {}
        self.fs = fs
        self.version = version
        self.source = source
        self.progress: list[tuple[float, str]] = []

    def __call__(self, cmd):
        cmd = list(cmd)
        self.calls.append(cmd)
        key = cmd[0]
        for prefix, output in self.fail.items():
            if " ".join(cmd).startswith(prefix):
                return 1, output
        if cmd[:3] == ["findmnt", "-no", "FSTYPE"]:
            return 0, self.fs
        if cmd[:3] == ["findmnt", "-no", "SOURCE"]:
            return 0, self.source
        if key == "apt-mark":
            return 0, ""
        if key == "dpkg-query":
            return (0, self.version) if self.version else (1, "")
        return 0, ""

    def report(self, fraction, phase, message):
        self.progress.append((fraction, message))


def tree(tmp_path, codename="noble"):
    (tmp_path / "etc" / "update-manager").mkdir(parents=True)
    (tmp_path / "etc" / "apt" / "sources.list.d").mkdir(parents=True)
    (tmp_path / "etc" / "os-release").write_text(f"UBUNTU_CODENAME={codename}\nLOGO=axon-os\n")
    (tmp_path / "etc" / "update-manager" / "release-upgrades").write_text("Prompt=normal\n")
    (tmp_path / "etc" / "apt" / "sources.list.d" / "axon-os.sources").write_text(
        "Types: deb\nURIs: https://projectdev-git.github.io/Axon-OS/apt\nSuites: stable\n"
        "Components: main\n"
    )
    (tmp_path / "etc" / "apt" / "sources.list.d" / "brave.sources").write_text(
        "Types: deb\nURIs: https://brave-browser-apt-release.s3.brave.com\nSuites: stable\n"
    )
    return tmp_path


def go(machine, root, **kwargs):
    return ru.run_release_upgrade(
        machine,
        machine.report,
        lambda _path: 50_000,
        lambda: "/mnt/top",
        lambda _path: None,
        NOW,
        root=root,
        **kwargs,
    )


@pytest.mark.unit
def test_a_full_upgrade_snapshots_first_then_fixes_up(tmp_path):
    root = tree(tmp_path)
    machine = Machine(root)
    assert go(machine, root) is None

    cmds = [" ".join(c) for c in machine.calls]
    snap = next(i for i, c in enumerate(cmds) if c.startswith("btrfs subvolume snapshot"))
    upgrade = next(i for i, c in enumerate(cmds) if c.startswith("do-release-upgrade -f"))
    assert snap < upgrade, "the snapshot is taken before anything is upgraded"
    assert "mount -o subvolid=5 /dev/sda2 /mnt/top" in cmds and "umount /mnt/top" in cmds
    assert cmds[-1] == "update-grub"
    assert "UBUNTU_CODENAME=resolute" in (root / "etc/os-release").read_text()
    axon = (root / "etc/apt/sources.list.d/axon-os.sources").read_text()
    assert "Enabled" not in axon, "the Axon source stays on"
    assert "Enabled: no" in (root / "etc/apt/sources.list.d/brave.sources").read_text()
    assert any("Turned off 1 outside" in m for _f, m in machine.progress)
    assert any("--enable-sources" in m for _f, m in machine.progress)
    brave_off = [i for i, c in enumerate(cmds) if c.startswith("do-release-upgrade -f")]
    assert brave_off, "upgrade ran after the sources were changed"
    assert "Prompt=lts" in (root / "etc/update-manager/release-upgrades").read_text()
    record = json.loads((root / ru.RECORD).read_text())
    assert record == {
        "from": "noble",
        "to": "resolute",
        "snapshot": "@axon-pre-upgrade-20261101-093005",
    }
    fractions = [f for f, _m in machine.progress]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0


@pytest.mark.unit
def test_nothing_runs_when_a_check_fails(tmp_path):
    root = tree(tmp_path)
    machine = Machine(root, version="1.0.0")
    error = go(machine, root)
    assert error and "1.1.0" in error
    assert not any(c[0] in ("apt-get", "do-release-upgrade", "btrfs") for c in machine.calls)


@pytest.mark.unit
def test_a_failed_snapshot_stops_before_the_upgrade(tmp_path):
    root = tree(tmp_path)
    machine = Machine(root, fail={"btrfs": "ERROR: no space"})
    error = go(machine, root)
    assert error and "snapshot failed" in error
    assert not any(c[:2] == ["do-release-upgrade", "-f"] for c in machine.calls)
    assert ["umount", "/mnt/top"] in machine.calls, "the top level is unmounted again"


@pytest.mark.unit
def test_a_failed_upgrade_says_how_to_go_back(tmp_path):
    root = tree(tmp_path)
    machine = Machine(root, fail={"do-release-upgrade -f": "dpkg: error"})
    error = go(machine, root)
    assert error and "dpkg: error" in error
    assert "before the upgrade" in error and "--rollback-upgrade" in error
    assert "UBUNTU_CODENAME=noble" in (root / "etc/os-release").read_text(), "not marked as done"


@pytest.mark.unit
def test_not_yet_offered_is_reported_without_a_snapshot(tmp_path):
    root = tree(tmp_path)

    class NotOffered(Machine):
        def __call__(self, cmd):
            if list(cmd) == ["do-release-upgrade", "-c"]:
                self.calls.append(list(cmd))
                return 1, ""
            return super().__call__(cmd)

    machine = NotOffered(root)
    error = go(machine, root)
    assert error and "26.04" in error
    assert not any(c[0] == "btrfs" for c in machine.calls)


@pytest.mark.unit
def test_without_btrfs_there_is_no_snapshot_but_the_upgrade_runs(tmp_path):
    root = tree(tmp_path)
    machine = Machine(root, fs="ext4")
    assert go(machine, root, allow_no_snapshot=True) is None
    assert not any(c[0] in ("btrfs", "mount") for c in machine.calls)
    assert not (root / ru.RECORD).exists()


@pytest.mark.unit
def test_rollback_from_the_snapshot(tmp_path):
    machine = Machine(tmp_path, source="/dev/sda2[/@axon-pre-upgrade-20261101-093005]")
    error = ru.run_rollback(machine, lambda: "/mnt/top", lambda _p: None, NOW)
    assert error is None
    cmds = [" ".join(c) for c in machine.calls]
    assert "mv /mnt/top/@ /mnt/top/@axon-failed-upgrade-20261101-093005" in cmds
    assert any(
        c == "btrfs subvolume snapshot /mnt/top/@axon-pre-upgrade-20261101-093005 /mnt/top/@"
        for c in cmds
    )
    assert cmds[-1] == "umount /mnt/top"


@pytest.mark.unit
def test_rollback_refuses_to_run_from_the_normal_system(tmp_path):
    machine = Machine(tmp_path, source="/dev/sda2[/@]")
    error = ru.run_rollback(machine, lambda: "/mnt/top", lambda _p: None, NOW)
    assert error and "boot menu" in error
    assert not any(c[0] in ("mv", "mount") for c in machine.calls)


# -- GRUB entry for the snapshot ------------------------------------------------------------

ROLLBACK = ROOT / "build" / "config" / "grub.d-42_axon_rollback"


def run_rollback_script(top):
    import os
    import subprocess

    env = {**os.environ, "AXON_ROLLBACK_TOP": str(top), "AXON_ROLLBACK_UUID": "abcd-1234"}
    return subprocess.run(
        ["sh", str(ROLLBACK)], env=env, capture_output=True, text=True, timeout=30
    )


def fake_snapshot(top, name, kernel="6.8.0-147-generic"):
    boot = top / name / "boot"
    boot.mkdir(parents=True)
    (boot / f"vmlinuz-{kernel}").write_text("k")
    (boot / f"initrd.img-{kernel}").write_text("i")


@pytest.mark.unit
def test_grub_lists_the_newest_upgrade_snapshot_before_the_factory_one(tmp_path):
    fake_snapshot(tmp_path, "@axon-fallback")
    fake_snapshot(tmp_path, "@axon-pre-upgrade-20261101-093005")
    fake_snapshot(tmp_path, "@axon-pre-upgrade-20270301-080000", kernel="6.8.0-150-generic")
    result = run_rollback_script(tmp_path)
    assert result.returncode == 0
    out = result.stdout
    assert out.count("menuentry") == 2
    upgrade, factory = out.index("before the upgrade"), out.index("factory snapshot")
    assert upgrade < factory
    assert "/@axon-pre-upgrade-20270301-080000/boot/vmlinuz-6.8.0-150-generic" in out
    assert "20261101" not in out, "only the newest upgrade snapshot"
    assert "rootflags=subvol=@axon-pre-upgrade-20270301-080000" in out
    assert "axon.rollback=upgrade" in out and "root=UUID=abcd-1234" in out


@pytest.mark.unit
def test_grub_factory_entry_is_unchanged_without_an_upgrade_snapshot(tmp_path):
    fake_snapshot(tmp_path, "@axon-fallback")
    out = run_rollback_script(tmp_path).stdout
    assert out.count("menuentry") == 1
    assert "linux /@axon-fallback/boot/vmlinuz-6.8.0-147-generic root=UUID=abcd-1234" in out
    assert "initrd /@axon-fallback/boot/initrd.img-6.8.0-147-generic" in out
    assert "axon.rollback=1" in out


@pytest.mark.unit
def test_grub_emits_nothing_for_a_snapshot_without_a_kernel(tmp_path):
    (tmp_path / "@axon-pre-upgrade-20261101-093005" / "boot").mkdir(parents=True)
    result = run_rollback_script(tmp_path)
    assert result.returncode == 0 and result.stdout == ""


@pytest.mark.unit
def test_grub_output_is_a_valid_script(tmp_path):
    import shutil
    import subprocess

    if shutil.which("grub-script-check") is None:
        pytest.skip("needs grub-common")
    fake_snapshot(tmp_path, "@axon-fallback")
    fake_snapshot(tmp_path, "@axon-pre-upgrade-20261101-093005")
    cfg = tmp_path / "grub.cfg"
    cfg.write_text(run_rollback_script(tmp_path).stdout)
    check = subprocess.run(["grub-script-check", str(cfg)], capture_output=True, text=True)
    assert check.returncode == 0, check.stderr


# -- Third-party apt sources ---------------------------------------------------------------

UBUNTU_SOURCES = """Types: deb
URIs: http://archive.ubuntu.com/ubuntu
Suites: noble noble-updates noble-backports
Components: main restricted universe multiverse

Types: deb
URIs: http://security.ubuntu.com/ubuntu
Suites: noble-security
Components: main
"""


@pytest.mark.unit
@pytest.mark.parametrize(
    ("uris", "suites", "official"),
    [
        (["http://archive.ubuntu.com/ubuntu"], ["noble", "noble-updates"], True),
        (["https://security.ubuntu.com/ubuntu/"], ["noble-security"], True),
        (["https://mirror.example.edu/ubuntu/"], ["noble"], True),  # a mirror
        (["https://archive.canonical.com/ubuntu"], ["noble"], True),  # partner
        (["https://ppa.launchpadcontent.net/x/y/ubuntu"], ["noble"], False),
        (["https://dl.google.com/linux/chrome/deb/"], ["stable"], False),
        (["http://archive.ubuntu.com/ubuntu"], ["jammy"], False),  # another release
        ([], ["noble"], False),
    ],
)
def test_what_counts_as_ubuntu(uris, suites, official):
    assert ru.is_official_source(uris, suites, "noble") is official


@pytest.mark.unit
def test_third_party_sources_are_turned_off_and_ubuntu_and_axon_stay(tmp_path):
    d = tmp_path / "etc/apt/sources.list.d"
    d.mkdir(parents=True)
    (d / "ubuntu.sources").write_text(UBUNTU_SOURCES)
    (d / "axon-os.sources").write_text(
        "Types: deb\nURIs: https://projectdev-git.github.io/Axon-OS/apt\nSuites: stable\n"
    )
    (d / "multi.sources").write_text(
        "Types: deb\nURIs: https://one.example/apt\nSuites: stable\n\n"
        "Types: deb\nURIs: http://archive.ubuntu.com/ubuntu\nSuites: noble\n"
    )
    (d / "ppa.list").write_text(
        "deb https://ppa.launchpadcontent.net/a/b/ubuntu noble main\n# deb https://old.example/ noble main\n"
        "deb [arch=amd64 signed-by=/k.gpg] https://vendor.example/deb stable main\n"
    )
    (tmp_path / "etc/apt/sources.list").write_text(
        "deb http://archive.ubuntu.com/ubuntu noble main\n"
    )
    before = {p.name: p.read_text() for p in d.iterdir()}

    changed = ru.disable_third_party(tmp_path, "noble")

    assert sorted(c.summary for c in changed) == [
        "https://one.example/apt stable",
        "https://ppa.launchpadcontent.net/a/b/ubuntu noble",
        "https://vendor.example/deb stable",
    ]
    assert (d / "ubuntu.sources").read_text() == before["ubuntu.sources"]
    assert (d / "axon-os.sources").read_text() == before["axon-os.sources"]
    multi = (d / "multi.sources").read_text()
    assert multi.count("Enabled: no") == 1 and "archive.ubuntu.com" in multi.split("\n\n")[1]
    listed = (d / "ppa.list").read_text()
    assert listed.splitlines()[0].startswith(ru.LIST_PREFIX)
    assert "# deb https://old.example/ noble main" in listed, "comments are left alone"
    assert (
        tmp_path / "etc/apt/sources.list"
    ).read_text() == "deb http://archive.ubuntu.com/ubuntu noble main\n"
    # a second run finds nothing more to turn off
    assert ru.disable_third_party(tmp_path, "noble") == []


@pytest.mark.unit
def test_turned_off_sources_can_be_listed_and_turned_back_on(tmp_path):
    d = tmp_path / "etc/apt/sources.list.d"
    d.mkdir(parents=True)
    original = {
        "multi.sources": "Types: deb\nURIs: https://one.example/apt\nSuites: stable\n\n"
        "Types: deb\nURIs: http://archive.ubuntu.com/ubuntu\nSuites: noble\n",
        "ppa.list": "deb https://ppa.launchpadcontent.net/a/b/ubuntu noble main\n",
        "other.list": "deb https://other.example/ stable main\n",
    }
    for name, text in original.items():
        (d / name).write_text(text)
    ru.disable_third_party(tmp_path, "noble")

    off = ru.disabled_sources(tmp_path)
    assert {(o.path.split("/")[-1], o.summary) for o in off} == {
        ("multi.sources", "https://one.example/apt stable"),
        ("ppa.list", "https://ppa.launchpadcontent.net/a/b/ubuntu noble"),
        ("other.list", "https://other.example/ stable"),
    }
    # one file back on
    assert [o.path for o in ru.enable_sources(tmp_path, "ppa.list")] == [
        "etc/apt/sources.list.d/ppa.list"
    ]
    assert (d / "ppa.list").read_text().strip() == original["ppa.list"].strip()
    assert len(ru.disabled_sources(tmp_path)) == 2
    # everything else back on: the files read as before (up to blank lines)
    assert len(ru.enable_sources(tmp_path)) == 2
    assert ru.disabled_sources(tmp_path) == []
    for name, text in original.items():
        assert (d / name).read_text().split() == text.split(), name


@pytest.mark.unit
def test_preview_changes_nothing(tmp_path):
    d = tmp_path / "etc/apt/sources.list.d"
    d.mkdir(parents=True)
    (d / "x.list").write_text("deb https://x.example/ stable main\n")
    assert [p.summary for p in ru.third_party_preview(tmp_path, "noble")] == [
        "https://x.example/ stable"
    ]
    assert (d / "x.list").read_text() == "deb https://x.example/ stable main\n"
    assert ru.disabled_sources(tmp_path) == []
