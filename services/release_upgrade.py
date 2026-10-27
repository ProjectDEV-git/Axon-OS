"""Upgrade an Axon OS install from Ubuntu 24.04 to Ubuntu 26.04.

The upgrade itself is Ubuntu's own ``do-release-upgrade``; this module adds what Axon OS needs
around it:

* checks first (free space, power, a restart waiting, held packages, the Axon package up to
  date) so the upgrade does not start on a machine that cannot finish it,
* a BTRFS snapshot of the system right before it starts, with a GRUB entry that boots the
  snapshot (``build/config/grub.d-42_axon_rollback``) and a command that makes it the system
  again (``axon-update --rollback-upgrade``),
* the Axon-specific fix-ups afterwards (``/etc/os-release``, the Axon apt source, GRUB).

Everything that decides something is a plain function over plain values, so it is tested
without a machine to upgrade; the functions that run commands take the runner as an argument.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

try:
    from i18n import _
except ImportError:  # plain English when services/ is not on the path

    def _(text: str) -> str:
        return text


# Source release -> the release it is upgraded to (Ubuntu offers LTS to LTS from the first
# point release; 26.04.1 was released 2026-08, checked against meta-release-lts 2026-10-27).
UPGRADE_TARGETS = {"noble": "resolute"}
RELEASE_NAMES = {"noble": "24.04", "resolute": "26.04"}

# The first axon-os package that lists GNOME Shell 50 and runs on 26.04. The upgrade needs it
# installed beforehand: the extension would otherwise be disabled when the new shell starts.
MIN_AXON_VERSION = "1.1.0"

MIN_FREE_ROOT_MB = 10_000  # downloads (~4 GB) plus unpacking, with room for the snapshot to diverge
MIN_FREE_BOOT_MB = 300  # a second kernel and its initrd
SNAPSHOT_PREFIX = "@axon-pre-upgrade-"
ROOT_SUBVOLUME = "@"

# Run a command: (exit code, combined output). The real one is system/axon-updater.py's.
Runner = Callable[[Sequence[str]], tuple[int, str]]


@dataclass(frozen=True)
class Facts:
    """What the checks need to know about this machine."""

    codename: str = ""
    fs_type: str = ""  # of "/": "btrfs", "ext4" ...
    root_free_mb: int = 0
    boot_free_mb: int = 0
    on_battery: bool | None = None  # None: no battery, or unknown
    reboot_required: bool = False
    held_packages: list[str] = field(default_factory=list)
    axon_version: str | None = None  # installed axon-os package, None if not installed
    live_session: bool = False


def target_for(codename: str) -> str | None:
    """The release to upgrade to from ``codename``, or None if there is nothing to upgrade to."""
    return UPGRADE_TARGETS.get(codename)


def version_key(version: str) -> tuple[int, ...]:
    """Numbers of a version like ``1.2.0`` or ``1.2.0-3`` to compare; non-numbers are ignored."""
    return tuple(int(n) for n in re.findall(r"\d+", version.split("-", 1)[0]))


def preflight(facts: Facts, allow_no_snapshot: bool = False) -> list[str]:
    """Reasons the upgrade must not start now, in plain words. Empty means go ahead."""
    problems: list[str] = []
    if facts.live_session:
        problems.append(_("This is the live session. Install Axon OS first."))
    target = target_for(facts.codename)
    if target is None:
        if facts.codename in RELEASE_NAMES.values() or facts.codename in RELEASE_NAMES:
            problems.append(_("This system is already on the newest release Axon OS supports."))
        else:
            problems.append(_("This system's Ubuntu release could not be determined."))
        return problems
    if facts.axon_version is None:
        problems.append(_("The axon-os package is not installed."))
    elif version_key(facts.axon_version) < version_key(MIN_AXON_VERSION):
        problems.append(
            _("Update Axon OS first: the upgrade needs axon-os {version} or newer.").format(
                version=MIN_AXON_VERSION
            )
        )
    if facts.fs_type != "btrfs" and not allow_no_snapshot:
        problems.append(
            _(
                "This system is not on BTRFS, so there is no snapshot to go back to if the "
                "upgrade fails. Back up your files first, then run again with --no-snapshot."
            )
        )
    if facts.root_free_mb < MIN_FREE_ROOT_MB:
        problems.append(
            _("Free at least {need} GB on the system disk (now {have} GB).").format(
                need=MIN_FREE_ROOT_MB // 1000, have=facts.root_free_mb // 1000
            )
        )
    if facts.boot_free_mb < MIN_FREE_BOOT_MB:
        problems.append(
            _("/boot has only {have} MB free; {need} MB are needed for the new kernel.").format(
                have=facts.boot_free_mb, need=MIN_FREE_BOOT_MB
            )
        )
    if facts.on_battery:
        problems.append(_("Plug in the power adapter: the upgrade takes a long time."))
    if facts.reboot_required:
        problems.append(_("Restart first: an earlier update is waiting for a restart."))
    if facts.held_packages:
        problems.append(
            _("Packages are on hold and would block the upgrade: {names}").format(
                names=", ".join(sorted(facts.held_packages))
            )
        )
    return problems


# -- Snapshot ---------------------------------------------------------------------------


def snapshot_name(now: datetime) -> str:
    return f"{SNAPSHOT_PREFIX}{now:%Y%m%d-%H%M%S}"


def latest_snapshot(names: Sequence[str]) -> str | None:
    """The newest ``@axon-pre-upgrade-*`` among top-level subvolume names (the stamp sorts)."""
    found = sorted(n for n in names if re.fullmatch(re.escape(SNAPSHOT_PREFIX) + r"\d{8}-\d{6}", n))
    return found[-1] if found else None


def snapshot_steps(top: str, name: str) -> list[list[str]]:
    """Commands that snapshot ``@`` as ``name``; ``top`` is the mounted BTRFS top level.

    The snapshot is writable (like the factory ``@axon-fallback``): GRUB boots it as a normal
    system, and it can become ``@`` again.
    """
    return [["btrfs", "subvolume", "snapshot", f"{top}/{ROOT_SUBVOLUME}", f"{top}/{name}"]]


def restore_steps(top: str, snapshot: str, stamp: str) -> list[list[str]]:
    """Commands that make ``snapshot`` the system again. Run while *booted from the snapshot*.

    The running system is the snapshot, so ``@`` is not in use: it is kept aside as
    ``@axon-failed-upgrade-<stamp>`` (for the logs) and a fresh ``@`` is made from the snapshot,
    which stays where it is as the way back to the way back.
    """
    return [
        ["mv", f"{top}/{ROOT_SUBVOLUME}", f"{top}/@axon-failed-upgrade-{stamp}"],
        ["btrfs", "subvolume", "snapshot", f"{top}/{snapshot}", f"{top}/{ROOT_SUBVOLUME}"],
    ]


def source_device(findmnt_output: str) -> str:
    """``/dev/sda2[/@]`` (findmnt on a subvolume) -> ``/dev/sda2``."""
    return findmnt_output.strip().split("[", 1)[0]


# -- Commands ---------------------------------------------------------------------------

# Prompt=lts: offer 26.04 from 24.04 (and not an interim release in between)
RELEASE_UPGRADES_CONF = "/etc/update-manager/release-upgrades"


def upgrader_install_command() -> list[str]:
    return ["apt-get", "install", "-y", "ubuntu-release-upgrader-core", "update-manager-core"]


def check_available_command() -> list[str]:
    """Exit code 0 when the upgrade is offered."""
    return ["do-release-upgrade", "-c"]


def upgrade_command() -> list[str]:
    """Ubuntu's upgrader without questions. Third-party sources stay: that keeps the Axon one,
    whose suite is ``stable`` and so needs no change."""
    return [
        "do-release-upgrade",
        "-f",
        "DistUpgradeViewNonInteractive",
        "--allow-third-party",
    ]


def set_prompt_lts(text: str) -> str:
    """``release-upgrades`` with ``Prompt=lts``."""
    if re.search(r"^Prompt=", text, re.M):
        return re.sub(r"^Prompt=.*$", "Prompt=lts", text, flags=re.M)
    return text.rstrip("\n") + "\n[DEFAULT]\nPrompt=lts\n"


def fix_os_release(text: str, codename: str) -> str:
    """Axon's own ``/etc/os-release`` (a file, not Ubuntu's symlink) says which Ubuntu it is
    built on; the upgrade does not touch it."""
    if re.search(r"^UBUNTU_CODENAME=", text, re.M):
        return re.sub(r"^UBUNTU_CODENAME=.*$", f"UBUNTU_CODENAME={codename}", text, flags=re.M)
    return text.rstrip("\n") + f"\nUBUNTU_CODENAME={codename}\n"


AXON_SOURCE = Path("/etc/apt/sources.list.d/axon-os.sources")


def axon_source_enabled(text: str) -> bool:
    """False if the file is missing its ``Types: deb`` or the upgrader switched it off."""
    return bool(re.search(r"^Types:\s*deb\b", text, re.M)) and not re.search(
        r"^Enabled:\s*no\b", text, re.M | re.I
    )


def enable_axon_source(text: str) -> str:
    """The same source with any ``Enabled: no`` line removed."""
    return re.sub(r"^Enabled:\s*no\s*\n", "", text, flags=re.M | re.I)


def post_upgrade_commands() -> list[list[str]]:
    """What runs after the new release is installed, before the restart."""
    return [
        ["apt-get", "-y", "-f", "install"],
        ["update-grub"],
    ]


# -- Reading the machine ----------------------------------------------------------------

RECORD = "var/lib/axon/release-upgrade.json"


def read_codename(root: Path = Path("/")) -> str:
    """The Ubuntu release: ``UBUNTU_CODENAME`` of Axon's os-release, else lsb-release."""
    for rel, key in (
        ("etc/os-release", "UBUNTU_CODENAME"),
        ("etc/lsb-release", "DISTRIB_CODENAME"),
    ):
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        match = re.search(rf"^{key}=[\"']?([a-z]+)", text, re.M)
        if match:
            return match.group(1)
    return ""


def on_battery(root: Path = Path("/")) -> bool | None:
    """True if running on a battery with no mains supply online; None if there is no battery."""
    supplies = root / "sys" / "class" / "power_supply"
    has_battery = False
    mains_online = False
    try:
        entries = list(supplies.iterdir())
    except OSError:
        return None
    for entry in entries:
        try:
            kind = (entry / "type").read_text().strip()
            if kind == "Battery":
                has_battery = True
            elif kind in ("Mains", "USB"):
                mains_online = mains_online or (entry / "online").read_text().strip() == "1"
        except OSError:
            continue
    if not has_battery:
        return None
    return not mains_online


def collect_facts(run: Runner, disk_free_mb: Callable[[str], int], root: Path = Path("/")) -> Facts:
    """Gather what ``preflight`` needs. ``disk_free_mb`` maps a path to megabytes free."""
    _rc, fs = run(["findmnt", "-no", "FSTYPE", "/"])
    rc, held = run(["apt-mark", "showhold"])
    rc_v, version = run(["dpkg-query", "-W", "-f=${Version}", "axon-os"])
    try:
        cmdline = (root / "proc" / "cmdline").read_text()
    except OSError:
        cmdline = ""
    return Facts(
        codename=read_codename(root),
        fs_type=fs.strip(),
        root_free_mb=disk_free_mb("/"),
        boot_free_mb=disk_free_mb("/boot"),
        on_battery=on_battery(root),
        reboot_required=(root / "var" / "run" / "reboot-required").exists(),
        held_packages=held.split() if rc == 0 else [],
        axon_version=version.strip() if rc_v == 0 and version.strip() else None,
        live_session="boot=casper" in cmdline,
    )


# -- Running it -------------------------------------------------------------------------

Progress = Callable[[float, str, str], None]


def _with_top_level(
    run: Runner,
    mkdtemp: Callable[[], str],
    rmdir: Callable[[str], None],
    work: Callable[[str], str | None],
) -> str | None:
    """Mount the BTRFS top level, call ``work(top)``, unmount. Returns an error text or None."""
    rc, source = run(["findmnt", "-no", "SOURCE", "/"])
    if rc != 0 or not source.strip():
        return _("Could not find the system disk.")
    top = mkdtemp()
    try:
        rc, out = run(["mount", "-o", "subvolid=5", source_device(source), top])
        if rc != 0:
            return _("Could not mount the BTRFS top level: {error}").format(
                error=out.strip()[-200:]
            )
        try:
            return work(top)
        finally:
            run(["umount", top])
    finally:
        rmdir(top)


def take_snapshot(
    run: Runner, now: datetime, mkdtemp: Callable[[], str], rmdir: Callable[[str], None]
) -> tuple[str | None, str | None]:
    """Snapshot ``@``. Returns (snapshot name, None) or (None, error)."""
    name = snapshot_name(now)

    def work(top: str) -> str | None:
        for step in snapshot_steps(top, name):
            rc, out = run(step)
            if rc != 0:
                return _("The snapshot failed: {error}").format(error=out.strip()[-200:])
        return None

    error = _with_top_level(run, mkdtemp, rmdir, work)
    return (None, error) if error else (name, None)


def run_release_upgrade(
    run: Runner,
    progress: Progress,
    disk_free_mb: Callable[[str], int],
    mkdtemp: Callable[[], str],
    rmdir: Callable[[str], None],
    now: datetime,
    allow_no_snapshot: bool = False,
    root: Path = Path("/"),
) -> str | None:
    """Upgrade to the next Ubuntu release. Returns None when done, else the reason it stopped."""
    facts = collect_facts(run, disk_free_mb, root)
    problems = preflight(facts, allow_no_snapshot)
    if problems:
        return "\n".join(problems)
    target = target_for(facts.codename) or ""

    progress(0.05, _("Step 1 / 5"), _("Bringing this release up to date…"))
    for cmd in (["apt-get", "update"], ["apt-get", "dist-upgrade", "-y", "-q"]):
        rc, out = run(cmd)
        if rc != 0:
            return _("Updating the current release failed: {error}").format(
                error=out.strip()[-200:]
            )

    progress(0.15, _("Step 2 / 5"), _("Getting the upgrade tool…"))
    rc, out = run(upgrader_install_command())
    if rc != 0:
        return _("Could not install the upgrade tool: {error}").format(error=out.strip()[-200:])
    conf = root / RELEASE_UPGRADES_CONF.lstrip("/")
    try:
        old = conf.read_text(encoding="utf-8") if conf.exists() else ""
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(set_prompt_lts(old), encoding="utf-8")
    except OSError as exc:
        return _("Could not set the upgrade policy: {error}").format(error=exc)
    rc, _out = run(check_available_command())
    if rc != 0:
        return _("Ubuntu {version} is not offered for this system yet.").format(
            version=RELEASE_NAMES[target]
        )

    snapshot = None
    if facts.fs_type == "btrfs":
        progress(0.25, _("Step 3 / 5"), _("Taking a snapshot to go back to…"))
        snapshot, error = take_snapshot(run, now, mkdtemp, rmdir)
        if error:
            return error
        record = root / RECORD
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(
            f'{{"from": "{facts.codename}", "to": "{target}", "snapshot": "{snapshot}"}}\n',
            encoding="utf-8",
        )

    progress(
        0.30,
        _("Step 4 / 5"),
        _("Upgrading to Ubuntu {version} (this takes a while)…").format(
            version=RELEASE_NAMES[target]
        ),
    )
    rc, out = run(upgrade_command())
    way_back = (
        _(
            " Restart and choose “Axon OS — before the upgrade” to go back, then run "
            "axon-update --rollback-upgrade."
        )
        if snapshot
        else ""
    )
    if rc != 0:
        return _("The upgrade failed: {error}").format(error=out.strip()[-300:]) + way_back

    progress(0.90, _("Step 5 / 5"), _("Finishing up…"))
    release_file = root / "etc" / "os-release"
    try:
        release_file.write_text(
            fix_os_release(release_file.read_text(encoding="utf-8"), target), encoding="utf-8"
        )
        source = root / AXON_SOURCE.relative_to("/")
        if source.exists():
            text = source.read_text(encoding="utf-8")
            if not axon_source_enabled(text):
                source.write_text(enable_axon_source(text), encoding="utf-8")
    except OSError as exc:
        return _("Could not finish the upgrade: {error}").format(error=exc) + way_back
    for cmd in post_upgrade_commands():
        rc, out = run(cmd)
        if rc != 0:
            return (
                _("A step after the upgrade failed ({cmd}): {error}").format(
                    cmd=cmd[0], error=out.strip()[-200:]
                )
                + way_back
            )
    progress(
        1.0,
        "",
        _("Upgrade complete. Restart to start Ubuntu {version}.").format(
            version=RELEASE_NAMES[target]
        ),
    )
    return None


def run_rollback(
    run: Runner,
    mkdtemp: Callable[[], str],
    rmdir: Callable[[str], None],
    now: datetime,
) -> str | None:
    """Make the pre-upgrade snapshot the system again. Returns None when done, else the reason."""
    rc, source = run(["findmnt", "-no", "SOURCE", "/"])
    match = re.search(r"\[/(" + re.escape(SNAPSHOT_PREFIX) + r"\d{8}-\d{6})\]", source)
    if rc != 0 or not match:
        return _(
            "Restart and choose “Axon OS — before the upgrade” in the boot menu first; this "
            "command runs from the snapshot."
        )
    snapshot = match.group(1)
    stamp = f"{now:%Y%m%d-%H%M%S}"

    def work(top: str) -> str | None:
        for step in restore_steps(top, snapshot, stamp):
            rc, out = run(step)
            if rc != 0:
                return _("Going back failed at “{cmd}”: {error}").format(
                    cmd=" ".join(step[:2]), error=out.strip()[-200:]
                )
        return None

    return _with_top_level(run, mkdtemp, rmdir, work)
