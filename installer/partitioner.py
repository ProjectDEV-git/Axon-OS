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

try:
    from axon_logger import configure_app_logger
except ImportError:  # running standalone — repo root / installed shim not on sys.path
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    try:
        from axon_logger import configure_app_logger
    except ImportError:
        import logging as _logging

        def configure_app_logger(name, level=_logging.INFO, log_file=None):
            _logging.basicConfig(level=level)
            return _logging.getLogger(name)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
        
class PartitionInfo:
    num:    int
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
        logger = configure_app_logger(__name__)
        if self.dry_run:
            logger.info("DRY-RUN: %s", " ".join(cmd))
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
            ["lsblk", "-J", "-o", "NAME,SIZE,MODEL,TYPE,FSTYPE,RO"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
        disks: list[DiskInfo] = []

        for device in data.get("blockdevices", []):
            if device.get("type") != "disk":
                continue
            # Skip read-only devices (e.g. some live USBs or mounted ISOs)
            if device.get("ro") is True or device.get("ro") == "1":
                continue

            partitions: list[PartitionInfo] = []
            for i, child in enumerate(device.get("children", [])):
                # Deduce partition number from name (e.g. sda1 -> 1, nvme0n1p12 -> 12)
                p_name = child.get("name", "")
                import re
                m = re.search(r'(\\d+)$', p_name)
                p_num = int(m.group(1)) if m else (i + 1)
                
                partitions.append(
                    PartitionInfo(
                        num=p_num,
                        name=f"/dev/{p_name}",
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

    def get_partition_map(self, device: str) -> list[dict]:
        """Returns detailed partition map with start/end in MiB."""
        try:
            if self.dry_run:
                # Return dummy partition map for dry run
                return [
                    {"num": 1, "start": 1.0, "end": 513.0, "size": 512.0, "fstype": "vfat", "name": "EFI"},
                    {"num": 2, "start": 513.0, "end": 102400.0, "size": 101887.0, "fstype": "ext4", "name": "root"}
                ]
            result = self._run(["parted", "-m", device, "unit", "MiB", "print"])
            lines = result.stdout.strip().split('\n')
            partitions = []
            for line in lines:
                if not line or line.startswith("BYT;") or line.startswith("/dev/"):
                    continue
                parts = line.split(':')
                if len(parts) >= 6:
                    num = int(parts[0])
                    start = float(parts[1].replace("MiB", ""))
                    end = float(parts[2].replace("MiB", ""))
                    size = float(parts[3].replace("MiB", ""))
                    fstype = parts[4]
                    name = parts[5]
                    partitions.append({
                        "num": num,
                        "start": start,
                        "end": end,
                        "size": size,
                        "fstype": fstype,
                        "name": name
                    })
            return partitions
        except Exception:
            return []

    def partition_disk(self, device: str, mode: str = "erase") -> bool:
        """Partition *device* using the given *mode*.

        Currently only ``"erase"`` is supported: wipes the disk and
        creates a fresh GPT layout.

        Returns True on success, False on failure.
        """
        if mode != "erase":
            raise ValueError(f"Unsupported partitioning mode: {mode!r}")

        logger = configure_app_logger(__name__)
        try:
            self.create_partitions(device)
            return True
        except subprocess.CalledProcessError as exc:
            logger.error("Partitioning failed: %s", exc.stderr)
            return False

    def partition_alongside(self, device: str, partition_num: int, shrink_size_gb: int) -> int | None:
        """Shrinks partition_num by shrink_size_gb and creates a new root partition in the freed space.
        
        Returns the partition number of the newly created root partition on success, or None on failure.
        """
        try:
            pmap = self.get_partition_map(device)
            target = next((p for p in pmap if p["num"] == partition_num), None)
            logger = configure_app_logger(__name__)
            if not target:
                logger.error("Partition %s not found on %s", partition_num, device)
                return None
            
            # Calculate new size in MiB
            shrink_size_mib = shrink_size_gb * 1024
            original_size_mib = target["size"]
            new_size_mib = original_size_mib - shrink_size_mib
            if new_size_mib < 10240: # Leave at least 10 GB
                logger.warning("Cannot shrink partition %s below 10GB", partition_num)
                return None
                
            # Resolve partition device path (e.g. /dev/sda2 or /dev/nvme0n1p2)
            part_suffix = f"p{partition_num}" if device[-1].isdigit() else f"{partition_num}"
            part_path = f"{device}{part_suffix}"
            
            # Step 1: Shrink filesystem first
            if target["fstype"] in ("ext4", "ext3"):
                self._run(["e2fsck", "-f", "-y", part_path])
                self._run(["resize2fs", part_path, f"{int(new_size_mib)}M"])
            elif target["fstype"] in ("ntfs", "fuseblk"):
                self._run(["ntfsresize", "-y", "--size", f"{int(new_size_mib)}M", part_path])
            else:
                logger.error("Unsupported filesystem type: %s", target['fstype'])
                return None
                
            # Step 2: Shrink the partition itself
            new_end_mib = target["start"] + new_size_mib
            self._run(["parted", "-s", device, "resizepart", str(partition_num), f"{new_end_mib}MiB"])
            
            # Step 3: Create new root partition in the freed space
            # Start from the new end of the shrunk partition, end at 100% of the disk
            self._run(["parted", "-s", device, "mkpart", "root", "btrfs", f"{new_end_mib}MiB", "100%"])
            
            # Find new partition number (typically pmap count + 1)
            # Fetch the updated partition map to confirm number
            updated_map = self.get_partition_map(device)
            new_part = max(p["num"] for p in updated_map) if updated_map else partition_num + 1
            return new_part
        except Exception as e:
            logger.exception("Partition alongside failed: %s", e)
            return None

    def create_partitions(self, device: str) -> None:
        """Write a GPT label and two partitions onto *device*.

        Partition table:
          1. EFI  FAT32  1 MiB – 513 MiB  (with esp + boot flags)
          2. Root btrfs  513 MiB – 100%
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
            "mkpart", "root", "btrfs", "513MiB", "100%",
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

        # Root — btrfs
        self._run(["mkfs.btrfs", "-f", root_part])

    def format_partitions_alongside(self, device: str, root_partition_num: int) -> None:
        """Format only the newly created alongside root partition."""
        root_part = f"{device}p{root_partition_num}" if device[-1].isdigit() else f"{device}{root_partition_num}"
        self._run(["mkfs.btrfs", "-f", root_part])

    def mount_partitions(self, device: str, mount_point: str) -> None:
        """Mount root then EFI under *mount_point*.

        Creates ``<mount_point>/boot/efi`` if it does not exist.
        """
        efi_part  = f"{device}p1" if device[-1].isdigit() else f"{device}1"
        root_part = f"{device}p2" if device[-1].isdigit() else f"{device}2"

        import os
        import tempfile

        # BTRFS subvolumes: create @ and @home
        with tempfile.TemporaryDirectory() as tmp_mnt:
            self._run(["mount", root_part, tmp_mnt])
            self._run(["btrfs", "subvolume", "create", f"{tmp_mnt}/@"])
            self._run(["btrfs", "subvolume", "create", f"{tmp_mnt}/@home"])
            self._run(["umount", tmp_mnt])

        # Mount the @ subvolume to the target mount_point
        self._run(["mount", "-o", "subvol=@", root_part, mount_point])

        # Mount @home
        os.makedirs(f"{mount_point}/home", exist_ok=True)
        self._run(["mount", "-o", "subvol=@home", root_part, f"{mount_point}/home"])

        # Create EFI mount point and mount
        efi_mount = f"{mount_point}/boot/efi"
        self._run(["mkdir", "-p", efi_mount])
        self._run(["mount", efi_part, efi_mount])

    def mount_partitions_alongside(self, device: str, efi_partition_num: int, root_partition_num: int, mount_point: str) -> None:
        """Mount root partition and existing EFI partition for dual-boot environment."""
        efi_part  = f"{device}p{efi_partition_num}" if device[-1].isdigit() else f"{device}{efi_partition_num}"
        root_part = f"{device}p{root_partition_num}" if device[-1].isdigit() else f"{device}{root_partition_num}"

        import os
        import tempfile

        # BTRFS subvolumes: create @ and @home
        with tempfile.TemporaryDirectory() as tmp_mnt:
            self._run(["mount", root_part, tmp_mnt])
            self._run(["btrfs", "subvolume", "create", f"{tmp_mnt}/@"])
            self._run(["btrfs", "subvolume", "create", f"{tmp_mnt}/@home"])
            self._run(["umount", tmp_mnt])

        # Mount the @ subvolume to the target mount_point
        self._run(["mount", "-o", "subvol=@", root_part, mount_point])

        # Mount @home
        os.makedirs(f"{mount_point}/home", exist_ok=True)
        self._run(["mount", "-o", "subvol=@home", root_part, f"{mount_point}/home"])

        # Mount existing EFI partition
        efi_mount = f"{mount_point}/boot/efi"
        self._run(["mkdir", "-p", efi_mount])
        self._run(["mount", efi_part, efi_mount])
