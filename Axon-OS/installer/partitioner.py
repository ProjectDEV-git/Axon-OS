"""
partitioner.py — Disk partitioning helpers for the Axon OS installer.

Layout produced by create_partitions():
  p1  EFI System Partition  FAT32   512 MiB  (1 MiB – 513 MiB)
  p2  Root                  ext4    rest of disk  (513 MiB – 100%)
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PartitionInfo:
    name:   str
    size:   str
    fstype: str | None = None


@dataclass
class DiskInfo:
    device:     str
    size:       str
    model:      str | None
    partitions: list[PartitionInfo] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Partitioner
# ---------------------------------------------------------------------------

class Partitioner:
    """Wrapper around parted / mkfs utilities for Axon OS installation."""

    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        """Run *cmd*, or just print it when dry_run is True."""
        if self.dry_run:
            print("DRY-RUN:", " ".join(cmd))
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")
        return subprocess.run(cmd, capture_output=True, text=True, check=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_disks(self) -> list[DiskInfo]:
        """Return information about all block devices on the system.

        Uses ``lsblk --json`` so no root privileges are required for
        read-only enumeration.
        """
        result = subprocess.run(
            ["lsblk", "-J", "-o", "NAME,SIZE,MODEL,TYPE,FSTYPE"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
        disks: list[DiskInfo] = []

        for device in data.get("blockdevices", []):
            if device.get("type") != "disk":
                continue

            partitions: list[PartitionInfo] = []
            for child in device.get("children", []):
                partitions.append(
                    PartitionInfo(
                        name=child.get("name", ""),
                        size=child.get("size", ""),
                        fstype=child.get("fstype"),
                    )
                )

            disks.append(
                DiskInfo(
                    device=f"/dev/{device['name']}",
                    size=device.get("size", ""),
                    model=device.get("model"),
                    partitions=partitions,
                )
            )

        return disks

    def partition_disk(self, device: str, mode: str = "erase") -> bool:
        """Partition *device* using the given *mode*.

        Currently only ``"erase"`` is supported: wipes the disk and
        creates a fresh GPT layout.

        Returns True on success, False on failure.
        """
        if mode != "erase":
            raise ValueError(f"Unsupported partitioning mode: {mode!r}")

        try:
            self.create_partitions(device)
            return True
        except subprocess.CalledProcessError as exc:
            print(f"Partitioning failed: {exc.stderr}")
            return False

    def create_partitions(self, device: str) -> None:
        """Write a GPT label and two partitions onto *device*.

        Partition table:
          1. EFI  FAT32  1 MiB – 513 MiB  (with esp + boot flags)
          2. Root ext4   513 MiB – 100%
        """
        self._run(["parted", "-s", device, "mklabel", "gpt"])

        # EFI System Partition
        self._run([
            "parted", "-s", device,
            "mkpart", "EFI", "fat32", "1MiB", "513MiB",
        ])
        self._run(["parted", "-s", device, "set", "1", "esp", "on"])
        self._run(["parted", "-s", device, "set", "1", "boot", "on"])

        # Root partition
        self._run([
            "parted", "-s", device,
            "mkpart", "root", "ext4", "513MiB", "100%",
        ])

    def format_partitions(self, device: str) -> None:
        """Format the two partitions created by :meth:`create_partitions`.

        Assumes the kernel has updated the partition table (or that
        ``partprobe`` / ``udevadm settle`` was called beforehand).
        """
        efi_part  = f"{device}p1" if device[-1].isdigit() else f"{device}1"
        root_part = f"{device}p2" if device[-1].isdigit() else f"{device}2"

        # EFI — FAT32
        self._run(["mkfs.fat", "-F32", efi_part])

        # Root — ext4  (-F forces even if already formatted)
        self._run(["mkfs.ext4", "-F", root_part])

    def mount_partitions(self, device: str, mount_point: str) -> None:
        """Mount root then EFI under *mount_point*.

        Creates ``<mount_point>/boot/efi`` if it does not exist.
        """
        efi_part  = f"{device}p1" if device[-1].isdigit() else f"{device}1"
        root_part = f"{device}p2" if device[-1].isdigit() else f"{device}2"

        # Mount root first
        self._run(["mount", root_part, mount_point])

        # Create EFI mount point and mount
        efi_mount = f"{mount_point}/boot/efi"
        self._run(["mkdir", "-p", efi_mount])
        self._run(["mount", efi_part, efi_mount])
