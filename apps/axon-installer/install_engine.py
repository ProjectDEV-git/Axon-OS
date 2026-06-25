#!/usr/bin/env python3
"""Axon OS Installer Engine — performs the real installation.

Runs as root (via sudo/pkexec), driven by a JSON config file written by the
installer UI (apps/axon-installer). Pure stdlib so it can run inside the
minimal live environment.

Progress protocol (one line per event on stdout):
    AXON-PROGRESS:<percent>:<message>
    AXON-ERROR:<message>
    AXON-DONE

Config schema (JSON):
{
  "target_disk": "/dev/sda",
  "install_mode": "erase" | "alongside",
  "user": {
    "full_name": "Ada Lovelace",
    "username": "ada",
    "password": "secret",
    "hostname": "axon"
  },
  "ai": {
    "install_ollama": true,
    "ollama_model": "llama3.2:3b",
    "providers": [{"id": "anthropic", "api_key": "sk-..."}]
  }
}
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

TARGET = "/target"
MIN_INSTALL_MIB = 16384  # 16 GiB minimum for the root partition
ESP_MIB = 512
BIOS_GRUB_MIB = 2
ESP_PARTTYPE_GUID = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"

USERNAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]{0,62}$")

KNOWN_PROVIDERS = ("ollama", "anthropic", "openai", "google", "openrouter")

PROVIDER_DEFAULTS = {
    "anthropic": {"base_url": "https://api.anthropic.com", "model": "claude-sonnet-4-6"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "google": {
        "base_url": "https://generativelanguage.googleapis.com",
        "model": "gemini-2.0-flash",
    },
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "openrouter/auto"},
}


# ---------------------------------------------------------------------------
# Progress / process helpers
# ---------------------------------------------------------------------------


def emit(percent: int, message: str) -> None:
    print(f"AXON-PROGRESS:{percent}:{message}", flush=True)  # noqa: T201


def fail(message: str) -> None:
    print(f"AXON-ERROR:{message}", flush=True)  # noqa: T201
    sys.exit(1)


def run(cmd, check=True, input_text=None, capture=False):
    """Run a command; on failure raise with stderr attached."""
    result = subprocess.run(
        cmd,
        input=input_text,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\n{result.stderr.strip()}"
        )
    return result.stdout if capture else result.returncode


def run_chroot(cmd, check=True, input_text=None):
    return run(["chroot", TARGET, *cmd], check=check, input_text=input_text)


# ---------------------------------------------------------------------------
# Config validation (pure — also exercised by the test suite)
# ---------------------------------------------------------------------------


def validate_config(cfg: dict) -> list:
    """Return a list of human-readable problems; empty list means valid."""
    problems = []

    disk = cfg.get("target_disk", "")
    if not disk.startswith("/dev/"):
        problems.append(f"target_disk must be a /dev path, got: {disk!r}")

    if cfg.get("install_mode") not in ("erase", "alongside"):
        problems.append("install_mode must be 'erase' or 'alongside'")

    user = cfg.get("user", {})
    if not user.get("full_name", "").strip():
        problems.append("user.full_name is required")
    if not USERNAME_RE.match(user.get("username", "")):
        problems.append("user.username must match ^[a-z][a-z0-9-]{0,31}$")
    if len(user.get("password", "")) < 4:
        problems.append("user.password must be at least 4 characters")
    if not HOSTNAME_RE.match(user.get("hostname", "")):
        problems.append("user.hostname is not a valid hostname")

    ai = cfg.get("ai", {})
    for provider in ai.get("providers", []):
        if provider.get("id") not in KNOWN_PROVIDERS:
            problems.append(f"unknown AI provider: {provider.get('id')!r}")
        if provider.get("id") != "ollama" and not provider.get("api_key", "").strip():
            problems.append(f"provider {provider.get('id')} is missing an api_key")

    return problems


# ---------------------------------------------------------------------------
# Disk inspection helpers
# ---------------------------------------------------------------------------


def part_node(disk: str, number: int) -> str:
    """Partition device path: /dev/sda + 1 -> /dev/sda1, nvme0n1 -> nvme0n1p1."""
    suffix = f"p{number}" if disk[-1].isdigit() else str(number)
    return f"{disk}{suffix}"


def is_uefi() -> bool:
    return os.path.isdir("/sys/firmware/efi")


def live_medium_disk() -> str:
    """Whole-disk device backing the live medium (/cdrom), or ''."""
    for mount in ("/cdrom", "/run/live/medium"):
        if not os.path.ismount(mount):
            continue
        try:
            source = run(["findmnt", "-n", "-o", "SOURCE", mount], capture=True).strip()
            pk = run(["lsblk", "-no", "PKNAME", source], capture=True).strip().splitlines()
            if pk and pk[0]:
                return f"/dev/{pk[0]}"
            return source
        except Exception:
            continue
    return ""


def list_partitions(disk: str) -> set:
    out = run(["lsblk", "-J", "-o", "PATH,TYPE", disk], capture=True)
    data = json.loads(out)
    parts = set()

    def walk(nodes):
        for node in nodes:
            if node.get("type") == "part":
                parts.add(node["path"])
            walk(node.get("children", []))

    walk(data.get("blockdevices", []))
    return parts


def find_existing_esp(disk: str) -> str:
    out = run(["lsblk", "-J", "-o", "PATH,TYPE,PARTTYPE,FSTYPE", disk], capture=True)
    data = json.loads(out)

    def walk(nodes):
        for node in nodes:
            if node.get("type") == "part":
                if (node.get("parttype") or "").lower() == ESP_PARTTYPE_GUID:
                    return node["path"]
            found = walk(node.get("children", []))
            if found:
                return found
        return ""

    return walk(data.get("blockdevices", []))


def disk_label(disk: str) -> str:
    """Partition table type: 'gpt', 'msdos', or '' (unpartitioned)."""
    out = run(["parted", "-ms", disk, "print"], check=False, capture=True) or ""
    for line in out.splitlines():
        if line.startswith("/dev/"):
            fields = line.split(":")
            if len(fields) > 5:
                return fields[5]
    return ""


def free_regions_mib(disk: str):
    """List of (start_mib, end_mib, size_mib) free regions, largest last."""
    out = run(["parted", "-ms", disk, "unit", "MiB", "print", "free"], capture=True)
    regions = []
    for line in out.splitlines():
        if not line.endswith("free;"):
            continue
        fields = line.split(":")
        try:
            start = float(fields[1].rstrip("MiB"))
            end = float(fields[2].rstrip("MiB"))
            size = float(fields[3].rstrip("MiB"))
        except (IndexError, ValueError):
            continue
        regions.append((start, end, size))
    regions.sort(key=lambda r: r[2])
    return regions


def settle(disk: str) -> None:
    run(["partprobe", disk], check=False)
    run(["udevadm", "settle"], check=False)


# ---------------------------------------------------------------------------
# Partitioning
# ---------------------------------------------------------------------------


def partition_erase(disk: str):
    """Wipe disk, fresh GPT: bios_grub + ESP + root. Works for BIOS and UEFI."""
    run(["wipefs", "-a", disk])
    run(["sgdisk", "--zap-all", disk])
    run(["sgdisk", "-n", f"1:0:+{BIOS_GRUB_MIB}MiB", "-t", "1:ef02", "-c", "1:BIOS boot", disk])
    run(["sgdisk", "-n", f"2:0:+{ESP_MIB}MiB", "-t", "2:ef00", "-c", "2:EFI System", disk])
    run(["sgdisk", "-n", "3:0:0", "-t", "3:8300", "-c", "3:AxonOS", disk])
    settle(disk)
    return part_node(disk, 2), part_node(disk, 3), True  # esp, root, esp_is_new


def partition_alongside(disk: str):
    """Create Axon partitions in the largest free region; never touch existing data."""
    label = disk_label(disk)
    if label not in ("gpt", "msdos"):
        raise RuntimeError(
            f"disk {disk} has no recognizable partition table ({label or 'none'}); "
            "use 'Erase disk' mode instead"
        )

    regions = free_regions_mib(disk)
    if not regions:
        raise RuntimeError("no unallocated space found on the disk")
    start, end, size = regions[-1]

    esp = find_existing_esp(disk)
    need_esp = is_uefi() and not esp
    need_bios_grub = (not is_uefi()) and label == "gpt"

    required = MIN_INSTALL_MIB
    if need_esp:
        required += ESP_MIB
    if need_bios_grub:
        required += BIOS_GRUB_MIB
    if size < required:
        raise RuntimeError(
            f"largest unallocated region is {size / 1024:.1f} GiB but at least "
            f"{required / 1024:.1f} GiB is needed — shrink a partition first "
            "(GNOME Disks or GParted), then retry"
        )

    def mkpart(fs_hint, p_start, p_end, name):
        before = list_partitions(disk)
        if label == "gpt":
            run(
                [
                    "parted",
                    "-s",
                    disk,
                    "unit",
                    "MiB",
                    "mkpart",
                    name,
                    fs_hint,
                    f"{p_start:.0f}",
                    f"{p_end:.0f}",
                ]
            )
        else:
            run(
                [
                    "parted",
                    "-s",
                    disk,
                    "unit",
                    "MiB",
                    "mkpart",
                    "primary",
                    fs_hint,
                    f"{p_start:.0f}",
                    f"{p_end:.0f}",
                ]
            )
        settle(disk)
        new = list_partitions(disk) - before
        if len(new) != 1:
            raise RuntimeError(f"could not identify newly created partition ({name})")
        return new.pop()

    cursor = start
    if need_bios_grub:
        bios_part = mkpart("ext2", cursor, cursor + BIOS_GRUB_MIB, "biosgrub")
        num = re.search(r"(\d+)$", bios_part).group(1)
        run(["parted", "-s", disk, "set", num, "bios_grub", "on"], check=False)
        cursor += BIOS_GRUB_MIB

    esp_is_new = False
    if need_esp:
        esp = mkpart("fat32", cursor, cursor + ESP_MIB, "EFI")
        num = re.search(r"(\d+)$", esp).group(1)
        run(["parted", "-s", disk, "set", num, "esp", "on"])
        cursor += ESP_MIB
        esp_is_new = True

    root = mkpart("ext4", cursor, end, "AxonOS")
    return esp, root, esp_is_new


# ---------------------------------------------------------------------------
# Install steps
# ---------------------------------------------------------------------------


def format_and_mount(esp: str, root: str, esp_is_new: bool) -> str:
    """Create filesystems and mount the target. Returns the root fs type.

    Root is BTRFS with @ / @home subvolumes so the boot watchdog can roll
    back to a factory snapshot; falls back to ext4 when mkfs.btrfs is
    unavailable in the live environment.
    """
    fs_type = "btrfs" if shutil.which("mkfs.btrfs") else "ext4"
    os.makedirs(TARGET, exist_ok=True)

    if fs_type == "btrfs":
        run(["mkfs.btrfs", "-f", "-L", "AxonOS", root])
        # Create the subvolume layout from the filesystem toplevel
        run(["mount", root, TARGET])
        run(["btrfs", "subvolume", "create", f"{TARGET}/@"])
        run(["btrfs", "subvolume", "create", f"{TARGET}/@home"])
        run(["umount", TARGET])
        run(["mount", "-o", "subvol=@,compress=zstd:1", root, TARGET])
        os.makedirs(f"{TARGET}/home", exist_ok=True)
        run(["mount", "-o", "subvol=@home,compress=zstd:1", root, f"{TARGET}/home"])
    else:
        run(["mkfs.ext4", "-F", "-L", "AxonOS", root])
        run(["mount", root, TARGET])

    if esp and esp_is_new:
        # The AXONESP label lets GRUB find the watchdog boot counter
        run(["mkfs.fat", "-F", "32", "-n", "AXONESP", esp])
    if esp:
        os.makedirs(f"{TARGET}/boot/efi", exist_ok=True)
        run(["mount", esp, f"{TARGET}/boot/efi"])
    return fs_type


RSYNC_EXCLUDES = [
    "/dev/*",
    "/proc/*",
    "/sys/*",
    "/run/*",
    "/tmp/*",
    "/mnt/*",
    "/media/*",
    "/cdrom",
    "/target",
    "/swapfile",
    "/var/crash/*",
    "/var/tmp/*",
    "/lost+found",
    "/boot/efi/*",
    "/home/*/.cache/*",
]


def copy_system(progress_cb) -> None:
    """rsync the running live root onto the target, reporting percentage."""
    cmd = ["rsync", "-aHAX", "--one-file-system", "--info=progress2", "--no-inc-recursive"]
    for pattern in RSYNC_EXCLUDES:
        cmd += ["--exclude", pattern]
    cmd += ["/", f"{TARGET}/"]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    pct_re = re.compile(r"(\d+)%")
    buf = ""
    while True:
        ch = proc.stdout.read(256)
        if not ch:
            break
        buf += ch
        # rsync redraws its progress line with \r
        for chunk in re.split(r"[\r\n]", buf)[:-1]:
            match = pct_re.search(chunk)
            if match:
                progress_cb(int(match.group(1)))
        buf = re.split(r"[\r\n]", buf)[-1]
    proc.wait()
    if proc.returncode not in (0, 23, 24):  # 23/24: vanished files in a live session
        raise RuntimeError(f"rsync failed with exit code {proc.returncode}")


def mount_chroot_binds() -> None:
    for fs, target in (
        ("/dev", "dev"),
        ("/dev/pts", "dev/pts"),
        ("/proc", "proc"),
        ("/sys", "sys"),
    ):
        os.makedirs(f"{TARGET}/{target}", exist_ok=True)
        run(["mount", "--bind", fs, f"{TARGET}/{target}"])
    if os.path.exists("/etc/resolv.conf"):
        dest = f"{TARGET}/etc/resolv.conf"
        if os.path.lexists(dest):
            try:
                os.remove(dest)
            except OSError:
                pass
        shutil.copy("/etc/resolv.conf", dest)


def unmount_all() -> None:
    for mp in (
        f"{TARGET}/dev/pts",
        f"{TARGET}/dev",
        f"{TARGET}/proc",
        f"{TARGET}/sys",
        f"{TARGET}/boot/efi",
        f"{TARGET}/home",
        TARGET,
    ):
        run(["umount", "-lf", mp], check=False)


def fstab_lines(root_uuid: str, esp_uuid: str, fs_type: str, swap_ok: bool) -> list:
    """Pure fstab content builder (unit-tested)."""
    lines = ["# /etc/fstab — generated by the Axon OS installer"]
    if fs_type == "btrfs":
        lines.append(f"UUID={root_uuid} / btrfs subvol=@,compress=zstd:1 0 1")
        lines.append(f"UUID={root_uuid} /home btrfs subvol=@home,compress=zstd:1 0 2")
    else:
        lines.append(f"UUID={root_uuid} / ext4 errors=remount-ro 0 1")
    if esp_uuid:
        lines.append(f"UUID={esp_uuid} /boot/efi vfat umask=0077 0 1")
    if swap_ok:
        swap_path = "/swap/swapfile" if fs_type == "btrfs" else "/swapfile"
        lines.append(f"{swap_path} none swap sw 0 0")
    return lines


def write_fstab(esp: str, root: str, fs_type: str, swap_ok: bool) -> None:
    root_uuid = run(["blkid", "-s", "UUID", "-o", "value", root], capture=True).strip()
    esp_uuid = ""
    if esp:
        esp_uuid = run(["blkid", "-s", "UUID", "-o", "value", esp], capture=True).strip()
    with open(f"{TARGET}/etc/fstab", "w") as f:
        f.write("\n".join(fstab_lines(root_uuid, esp_uuid, fs_type, swap_ok)) + "\n")


def create_swapfile(fs_type: str) -> bool:
    """Create a 2 GiB swapfile; BTRFS needs the nocow mkswapfile dance."""
    try:
        if fs_type == "btrfs":
            os.makedirs(f"{TARGET}/swap", exist_ok=True)
            run(["btrfs", "filesystem", "mkswapfile", "--size", "2g", f"{TARGET}/swap/swapfile"])
        else:
            swap = f"{TARGET}/swapfile"
            run(["fallocate", "-l", "2G", swap])
            os.chmod(swap, 0o600)
            run(["mkswap", swap])
        return True
    except (RuntimeError, OSError):
        return False  # no swap is survivable; never abort the install for it


def configure_identity(user: dict) -> None:
    hostname = user["hostname"]
    with open(f"{TARGET}/etc/hostname", "w") as f:
        f.write(hostname + "\n")
    with open(f"{TARGET}/etc/hosts", "w") as f:
        f.write(
            "127.0.0.1\tlocalhost\n"
            f"127.0.1.1\t{hostname}\n\n"
            "::1\tip6-localhost ip6-loopback\n"
            "fe00::0\tip6-localnet\n"
            "ff02::1\tip6-allnodes\n"
            "ff02::2\tip6-allrouters\n"
        )

    username = user["username"]
    live_user = "axon"

    if username == live_user:
        run_chroot(["usermod", "-c", user["full_name"], username])
    else:
        run_chroot(["useradd", "-m", "-s", "/bin/bash", "-c", user["full_name"], username])
        # Remove the casper live user copied over with the filesystem
        run_chroot(["deluser", "--remove-home", live_user], check=False)
    for group in ("adm", "sudo", "cdrom", "dip", "plugdev", "video", "audio"):
        run_chroot(["usermod", "-aG", group, username], check=False)
    run_chroot(["chpasswd"], input_text=f"{username}:{user['password']}\n")

    # Disable the casper autologin copied from the live session
    gdm_conf = f"{TARGET}/etc/gdm3/custom.conf"
    os.makedirs(os.path.dirname(gdm_conf), exist_ok=True)
    with open(gdm_conf, "w") as f:
        f.write("[daemon]\nAutomaticLoginEnable=false\n")


def strip_live_artifacts() -> None:
    run_chroot(["apt-get", "-y", "purge", "casper"], check=False)
    run_chroot(["apt-get", "-y", "autoremove"], check=False)
    for path in (
        "etc/casper.conf",
        "etc/xdg/autostart/axon-installer-live.desktop",
        "usr/share/applications/install-axon-os.desktop",
        "etc/sudoers.d/casper",
    ):
        full = f"{TARGET}/{path}"
        if os.path.exists(full):
            os.remove(full)
    # Fresh machine identity on first boot
    Path(f"{TARGET}/etc/machine-id").write_text("")


def install_bootloader(disk: str, mode: str) -> None:
    default_grub = f"{TARGET}/etc/default/grub"
    if mode == "alongside" and os.path.exists(default_grub):
        with open(default_grub) as f:
            content = f.read()
        content = re.sub(r"^GRUB_DISABLE_OS_PROBER=.*$", "", content, flags=re.M)
        content += "\nGRUB_DISABLE_OS_PROBER=false\n"
        with open(default_grub, "w") as f:
            f.write(content)

    if is_uefi():
        run_chroot(
            [
                "grub-install",
                "--target=x86_64-efi",
                "--efi-directory=/boot/efi",
                "--bootloader-id=AxonOS",
                "--recheck",
            ]
        )
        # Fallback path for firmware that ignores NVRAM entries
        run_chroot(
            ["grub-install", "--target=x86_64-efi", "--efi-directory=/boot/efi", "--removable"],
            check=False,
        )
    else:
        run_chroot(["grub-install", "--target=i386-pc", "--recheck", disk])
    run_chroot(["update-grub"])
    run_chroot(["update-initramfs", "-u"])


def setup_boot_watchdog(root: str, fs_type: str) -> None:
    """Provision the self-healing boot watchdog (BTRFS installs only).

    1. Initialise the GRUB boot counter on the ESP (FAT — GRUB can save_env
       there; it cannot write to btrfs).
    2. Snapshot the freshly installed @ subvolume as @axon-fallback; the
       /etc/grub.d/42_axon_rollback entry boots it after repeated failures.
    """
    if fs_type != "btrfs":
        return
    try:
        if os.path.isdir(f"{TARGET}/boot/efi"):
            run_chroot(["mkdir", "-p", "/boot/efi/axon"], check=False)
            run_chroot(["grub-editenv", "/boot/efi/axon/grubenv", "create"], check=False)
            run_chroot(
                ["grub-editenv", "/boot/efi/axon/grubenv", "set", "boot_attempts=0"], check=False
            )
        toplevel = f"{TARGET}-btrfs-toplevel"
        os.makedirs(toplevel, exist_ok=True)
        run(["mount", "-o", "subvolid=5", root, toplevel])
        try:
            if not os.path.exists(f"{toplevel}/@axon-fallback"):
                run(
                    [
                        "btrfs",
                        "subvolume",
                        "snapshot",
                        f"{toplevel}/@",
                        f"{toplevel}/@axon-fallback",
                    ]
                )
        finally:
            run(["umount", toplevel], check=False)
    except (RuntimeError, OSError) as exc:
        # The watchdog is a safety net, not a prerequisite — never abort.
        print(f"AXON-PROGRESS:96:Boot watchdog skipped ({exc})", flush=True)  # noqa: T201


def setup_ai(cfg: dict) -> None:
    """Provision AI choices: firstboot Ollama install + provider keys."""
    ai = cfg.get("ai", {})
    user = cfg["user"]["username"]

    if ai.get("install_ollama"):
        os.makedirs(f"{TARGET}/etc/axon", exist_ok=True)
        with open(f"{TARGET}/etc/axon/ai-setup.json", "w") as f:
            json.dump(
                {
                    "install_ollama": True,
                    "ollama_model": ai.get("ollama_model", "llama3.2:3b"),
                },
                f,
                indent=2,
            )
        run_chroot(["systemctl", "enable", "axon-ai-firstboot.service"], check=False)

    # Per-user AI configuration (~/.axon/config.toml) — API keys live here,
    # readable only by the user, never in /etc.
    lines = ["# Axon OS — AI configuration (generated by the installer)", "", "[ai]"]
    if ai.get("install_ollama"):
        lines.append(f'default_model = "{ai.get("ollama_model", "llama3.2:3b")}"')
        lines += ["", "[providers.ollama]", "enabled = true", 'host = "http://127.0.0.1:11434"']
    for provider in ai.get("providers", []):
        pid = provider.get("id")
        if pid == "ollama" or pid not in PROVIDER_DEFAULTS:
            continue
        defaults = PROVIDER_DEFAULTS[pid]
        lines += [
            "",
            f"[providers.{pid}]",
            "enabled = true",
            f'api_key = "{provider.get("api_key", "").strip()}"',
            f'base_url = "{defaults["base_url"]}"',
            f'model = "{defaults["model"]}"',
        ]

    home = f"{TARGET}/home/{user}"
    axon_dir = f"{home}/.axon"
    os.makedirs(axon_dir, exist_ok=True)
    config_path = f"{axon_dir}/config.toml"
    with open(config_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(config_path, 0o600)
    run_chroot(["chown", "-R", f"{user}:{user}", f"/home/{user}/.axon"])


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def install(cfg: dict) -> None:
    disk = cfg["target_disk"]
    mode = cfg["install_mode"]

    emit(2, "Validating configuration")
    medium = live_medium_disk()
    if medium and os.path.realpath(disk) == os.path.realpath(medium):
        fail("cannot install onto the live boot medium itself")

    emit(5, "Partitioning disk")
    if mode == "erase":
        esp, root, esp_is_new = partition_erase(disk)
    else:
        esp, root, esp_is_new = partition_alongside(disk)

    emit(12, "Creating filesystems")
    fs_type = format_and_mount(esp, root, esp_is_new)

    emit(18, "Copying the system (this is the longest step)")
    copy_system(lambda pct: emit(18 + int(pct * 0.52), "Copying the system"))

    emit(72, "Writing filesystem table")
    swap_ok = create_swapfile(fs_type)
    write_fstab(esp, root, fs_type, swap_ok)

    emit(75, "Preparing target system")
    mount_chroot_binds()

    emit(78, f"Creating user account '{cfg['user']['username']}'")
    configure_identity(cfg["user"])

    emit(82, "Removing live-session components")
    strip_live_artifacts()

    emit(86, "Installing the GRUB bootloader")
    install_bootloader(disk, mode)

    emit(93, "Configuring your AI providers")
    setup_ai(cfg)

    emit(95, "Arming the self-healing boot watchdog")
    setup_boot_watchdog(root, fs_type)

    emit(97, "Finishing up")
    unmount_all()

    emit(100, "Installation complete")
    print("AXON-DONE", flush=True)  # noqa: T201


def main() -> int:
    if len(sys.argv) != 2:
        fail("usage: install_engine.py <config.json>")
    if os.geteuid() != 0:
        fail("the install engine must run as root")

    try:
        config_path = sys.argv[1]
        with open(config_path) as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"could not read config: {exc}")
        return 1

    try:
        os.unlink(config_path)
    except OSError:
        pass

    problems = validate_config(cfg)
    if problems:
        fail("invalid configuration: " + "; ".join(problems))

    try:
        install(cfg)
    except Exception as exc:
        unmount_all()
        fail(str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
