# Installer Edge Cases Analysis

**Agent:** Debug Agent 8 — Installer & Build System
**Date:** 2026-07-02
**Files Analyzed:**
- `installer/axon-installer.py` (older 4-page GTK4 wizard)
- `apps/axon-installer/install_engine.py` (backend engine)
- `apps/axon-installer/ui/wizard.py` (newer 8-page GTK4 wizard)
- `installer/partitioner.py` (older partitioning backend)
- `installer/settings.conf` + `installer/modules/*.conf` (Calamares config)

---

## Architecture Overview

There are **three separate installer code paths** in this project:

| Path | Type | Status |
|------|------|--------|
| `installer/axon-installer.py` + `installer/partitioner.py` | Standalone GTK4 wizard + partitioner | Older, missing features |
| `apps/axon-installer/ui/wizard.py` + `apps/axon-installer/install_engine.py` | Full GTK4 wizard + root engine | Newer, more complete |
| `installer/settings.conf` + `installer/modules/` | Calamares modules | Legacy/fallback |

**Observation:** The three paths are inconsistent. The Calamares config (`settings.conf`) references modules that may not be maintained alongside the native installer. The older standalone wizard lacks features the newer one has. This creates a maintenance burden and potential for divergent behavior.

---

## CRITICAL BUGS

### BUG-1: Older Wizard Always Forces EFI — No BIOS Boot Support
**File:** `installer/partitioner.py` lines 265-303
**Severity:** CRITICAL

`create_partitions()` unconditionally creates GPT + EFI partitions:
```python
self._run(["parted", "-s", device, "mklabel", "gpt"])
# EFI only — no BIOS boot partition
self._run(["parted", "-s", device, "mkpart", "EFI", "fat32", "1MiB", "513MiB"])
self._run(["parted", "-s", device, "set", "1", "esp", "on"])
# Root
self._run(["parted", "-s", device, "mkpart", "root", "btrfs", "513MiB", "100%"])
```

On BIOS-only systems, GRUB needs a `bios_grub` partition (ef02) on GPT disks. Without it, `grub-install` will fail with "no available disk for embedding."

The newer `install_engine.py` correctly handles this with `partition_erase()` creating a `BIOS_GRUB` partition.

Additionally, in `axon-installer.py` line 816-827, `grub-install` is hardcoded to `--target=x86_64-efi`:
```python
subprocess.run(["chroot", mount, "grub-install",
    "--target=x86_64-efi", ...], check=True)
```
No fallback to `--target=i386-pc` for BIOS systems.

**Impact:** Installation fails on any BIOS-only machine using the older wizard.

### BUG-2: Older Wizard Password Has No Minimum Length
**File:** `installer/axon-installer.py` line 164
**Severity:** HIGH

```python
if not v["password"]:
    return "Password cannot be empty."
```

The older wizard only checks for empty password. A single-character password is accepted. The newer wizard and `install_engine.py` both enforce `>= 4 characters`.

The Calamares config (`installer/modules/users.conf`) also sets `minLength: 4`, making this inconsistency even more glaring.

---

## HIGH SEVERITY ISSUES

### HIGH-1: No Read-Only Disk Detection in Older Wizard
**File:** `installer/axon-installer.py` lines 272-299
**Severity:** HIGH

`DiskPage._populate()` does not filter read-only devices. The `Partitioner.list_disks()` in `partitioner.py` does check `device.get("ro")`, but the older wizard's `_populate()` calls `Partitioner().list_disks()` which includes the RO check. However, the UI itself doesn't disable or warn about read-only disks that `lsblk` might report differently.

The newer wizard (`wizard.py` line 775) explicitly checks `dev.get("ro")`:
```python
if dev.get("type") != "disk" or dev.get("ro"):
    continue
```

### HIGH-2: Alongside Partitioner Doesn't Validate Resize Safety
**File:** `installer/partitioner.py` lines 204-263
**Severity:** HIGH

`partition_alongside()` calls `resize2fs` and `ntfsresize` but:
- For ext4: trusts that `resize2fs` will handle used-space validation (comment on line 220 says this is intentional)
- For NTFS: same trust in `ntfsresize`
- **No user confirmation** that the partition has enough free space to shrink

However, the code comment explicitly says "No explicit used-space pre-check is needed here" because the tools handle it. This is **correct** but worth noting that errors from these tools may be cryptic to users.

### HIGH-3: install_engine.py Does Not Verify Disk Exists Before Partitioning
**File:** `apps/axon-installer/install_engine.py` lines 100-128
**Severity:** HIGH

`validate_config()` checks that `target_disk` starts with `/dev/` but does not verify:
- The device exists (`os.path.exists(disk)`)
- It's a block device
- It has sufficient size
- It's not a partition (e.g., `/dev/sda1` instead of `/dev/sda`)

The live-medium check (`live_medium_disk()`) prevents installing to the USB, but other invalid paths could slip through.

---

## MEDIUM SEVERITY ISSUES

### MED-1: fstab Hardcodes btrfs Options Without Checking Root Filesystem Type
**File:** `installer/axon-installer.py` lines 691-703
**Severity:** MEDIUM

The older wizard writes fstab assuming btrfs:
```python
fstab_content += f"UUID={root_uuid} / btrfs subvol=@,defaults,noatime,space_cache=v2 0 0\n"
```

But the `partitioner.py` creates btrfs partitions in `create_partitions()` (line 301), so this is consistent. However, if someone were to change the root filesystem type, the fstab would be wrong. The newer `install_engine.py` correctly passes `fs_type` to `fstab_lines()`.

### MED-2: GRUB Config Appended Without Checking Existing Content
**File:** `installer/axon-installer.py` lines 746-751
**Severity:** MEDIUM

```python
with open(os.path.join(mount, "etc", "default", "grub"), "a") as f:
    f.write("\nGRUB_DISABLE_OS_PROBER=false\n")
    f.write('GRUB_CMDLINE_LINUX_DEFAULT="quiet splash rootflags=subvol=@ console=tty0"\n")
```

This **appends** to `/etc/default/grub` without checking for existing values. If the file already has `GRUB_CMDLINE_LINUX_DEFAULT`, there will be duplicate entries and the last one wins. The newer `install_engine.py` uses `re.sub()` to properly replace existing values.

### MED-3: No Validation of Hostname Uniqueness on Network
**File:** Both wizards
**Severity:** LOW-MEDIUM

Neither installer checks if the hostname conflicts with other machines on the network. This isn't strictly an installer bug (it's a network admin concern), but the hostname validation only checks RFC 1123 format.

### MED-4: Timezone Not Validated Against System Database
**File:** Both wizards
**Severity:** LOW-MEDIUM

The timezone string is validated by regex (`^[A-Za-z0-9_/.-]+$`) but not checked against `/usr/share/zoneinfo/` to ensure it's a valid timezone. An invalid timezone would cause `ln -sf` to create a broken symlink.

---

## LOW SEVERITY ISSUES / OBSERVATIONS

### LOW-1: Dual Installer Code Paths Create Maintenance Burden
The project maintains both the older standalone wizard and the newer full wizard. The older one is missing: network setup, AI configuration, BIOS support, read-only disk filtering, password minimum length. Recommend deprecating the older path or consolidating.

### LOW-2: Calamares Config May Be Stale
`installer/settings.conf` and `installer/modules/` define a Calamares-based installer path. The version in `branding.desc` is hardcoded to `0.2.0` while the project is at `0.3.0`. It's unclear if Calamares is still used or has been replaced by the native installer.

### LOW-3: MockPartitioner in Older Wizard
`axon-installer.py` lines 559-587 define an inline `MockPartitioner` class that raises `RuntimeError` on every operation. If `Partitioner` is `None` (import fails), the install will always fail. The wizard could show a better error message upfront.

### LOW-4: Thread Safety of Progress Updates
Both wizards use `GLib.idle_add()` for thread-safe UI updates from the install worker thread. This is correct GTK practice. No issues found.

### LOW-5: Config File Deleted Before Validation
**File:** `apps/axon-installer/install_engine.py` lines 724-726
```python
try:
    os.unlink(config_path)
except OSError:
    pass
```
The config JSON file is deleted before installation begins. This is a good security practice (prevents password from lingering on disk), but means if installation fails, the config is lost and cannot be inspected for debugging.

### LOW-6: Swap Creation Failures Are Non-Fatal
**File:** `apps/axon-installer/install_engine.py` lines 472-485
This is actually good design — swap failure doesn't abort the install. The older wizard is less graceful (uses `check=False` on each swap command but doesn't have the same clean error handling).

---

## SUMMARY TABLE

| ID | Severity | Description | Location |
|----|----------|-------------|----------|
| BUG-1 | CRITICAL | No BIOS boot support in older wizard | `partitioner.py`, `axon-installer.py` |
| BUG-2 | HIGH | No password minimum length in older wizard | `axon-installer.py` |
| HIGH-1 | HIGH | No read-only disk detection in older wizard | `axon-installer.py` |
| HIGH-2 | HIGH | Alongside resize relies on tool validation only | `partitioner.py` |
| HIGH-3 | HIGH | No disk existence/type validation in engine | `install_engine.py` |
| MED-1 | MEDIUM | Hardcoded btrfs fstab options | `axon-installer.py` |
| MED-2 | MEDIUM | GRUB config appended without dedup | `axon-installer.py` |
| MED-3 | LOW-MED | No hostname network uniqueness check | Both wizards |
| MED-4 | LOW-MED | Timezone not validated against zoneinfo | Both wizards |
| LOW-1 | LOW | Dual installer paths create maintenance burden | Architecture |
| LOW-2 | LOW | Calamares branding version stale (0.2.0) | `branding.desc` |
| LOW-3 | LOW | MockPartitioner gives poor error UX | `axon-installer.py` |
