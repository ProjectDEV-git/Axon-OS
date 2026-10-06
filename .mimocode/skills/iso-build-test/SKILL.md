---
name: iso-build-test
description: Build a bootable Axon OS ISO, test it in a VM, and debug boot failures systematically
---

# ISO Build, Test & Debug

Build the Axon OS ISO from source, test it in a VM, and diagnose boot failures using a structured approach.

## When to Use

- User says "build the ISO", "rebuild", "test the ISO", "won't boot"
- After making changes to `build/`, `installer/`, `shell/`, `services/`, `apps/`, `theme/`, `plymouth/`
- When investigating boot failures (black screen, kernel panic, GRUB issues)

## Prerequisites

- Must run on Ubuntu/Debian host with root access
- Required packages: `debootstrap squashfs-tools xorriso grub-pc-bin grub-efi-amd64-bin mtools dosfstools rsync`
- VM software installed (VirtualBox, QEMU/KVM, or VMware)

## Workflow

### Phase 1: Build

```bash
cd /home/gamingrf/Documents/Axons-OS/Axon-OS
sudo bash build/build.sh --ci
```

Build takes 15-30 minutes. Output: `dist/axon-os-<VERSION>-amd64.iso`

**If build fails**: Check error output. Common issues:
- Missing dependencies → script auto-installs them
- debootstrap network failure → check internet connection
- squashfs errors → check disk space (`df -h /tmp`)

### Phase 2: Verify ISO Structure

Before booting, verify the ISO is well-formed:

```bash
ISO=$(ls dist/*.iso | tail -1)

# Check El Torito boot entries (must have both BIOS and UEFI)
xorriso -indev "$ISO" -report_el_torito plain 2>/dev/null

# Verify live payload files exist
xorriso -indev "$ISO" -find / -type f 2>/dev/null | grep -E "(vmlinuz|initrd|filesystem.squashfs|grub.cfg)"

# Check ISO size (should be >700MB for a complete build)
ls -lh "$ISO"
```

### Phase 3: Boot Test in VM

1. Create a VM with:
   - 4GB+ RAM
   - 20GB+ disk (for installed system)
   - Boot from ISO (CD-ROM)
   - For VirtualBox: VMSVGA display, 128 MB video memory, 3D acceleration off,
     View → Auto-resize Guest Display on
   - For QEMU: `-enable-kvm -m 4G -cdrom "$ISO"`

2. Boot entries (in order of safety):
   - **Default**: `quiet splash` (kernel modesetting; VirtualBox auto-resize works)
   - **Safe graphics**: `nomodeset vga=normal` (Xorg `vesa`, fixed ~1024x768, no resize)
   - **NVIDIA**: `quiet splash nouveau.modeset=0 nvidia-drm.modeset=1 console=tty0`

### Phase 4: Debug Boot Failures

If the ISO doesn't boot, follow this diagnostic tree. Whenever a console is
reachable (Ctrl+Alt+F3, user `axon`), run `sudo axon-display-diag` first — it
collects the GPU driver, Xorg driver choice/errors, GDM, gnome-shell and
VBoxClient logs into one report.

#### Symptom: GRUB doesn't appear
- Check VM boot order (CD-ROM must be first)
- Verify ISO is properly attached
- Try UEFI vs BIOS mode

#### Symptom: GRUB appears but black screen after selection
1. In GRUB, press `e` to edit the boot entry
2. Remove `quiet splash` from the kernel line
3. Add `console=tty0 console=ttyS0,115200n8` for serial output
4. Press `Ctrl+X` to boot
5. If still black: the kernel is panicking before console init

**Common causes**:
- Splash script parse error: Plymouth rejects the whole script (black screen)
  if `axon.script` uses syntax it does not support, e.g. hex literals or
  non-existent functions. `pytest tests/test_boot_display.py` checks this.
- An Xorg `Driver` pinned for VMs (Ubuntu 24.04 has no `vboxvideo` X driver;
  leave driver selection to Xorg autodetection)
- VirtualBox 3D acceleration enabled (Mesa `svga` path)
- Corrupted squashfs (rebuild ISO)
- Missing casper hooks in initramfs

#### Symptom: Kernel panic messages visible
- **"VFS: Unable to mount root"**: Casper can't find squashfs. Check `boot=casper` parameter.
- **"Kernel panic - not syncing"**: Initramfs failure. Regenerate with `update-initramfs -u -k all` in chroot.

#### Symptom: Boot succeeds but GDM/login fails
- Check GDM config: `/etc/gdm3/custom.conf` should have `AutomaticLoginEnable=true`
- For live session: `WaylandEnable=false` forces X11 (needed for some VMs)
- Check `casper.conf` has correct `USERNAME="axon"`

#### Symptom: Plymouth splash appears then black screen
- Plymouth crash during early boot. Remove `splash` parameter to bypass.
- Check Plymouth theme: `/usr/share/plymouth/themes/axon/axon.plymouth`
- Test the splash without booting (needs `plymouth-x11`):
  `sudo plymouthd --debug --debug-file=/tmp/ply.log --no-daemon --tty=$(tty) --kernel-command-line="splash plymouth.ignore-serial-consoles" &`
  then `sudo plymouth show-splash`, `sudo plymouth quit`, and look for
  `Parser error` in `/tmp/ply.log`.

### Phase 5: Install & Test Installed System

If live session works, test installation:

1. Run the installer from the live desktop
2. After installation, reboot from the installed disk
3. If installed system fails to boot:
   - Check GRUB config has `rootflags=subvol=@` for BTRFS
   - Check fstab has correct UUID and subvolume mounts
   - Verify GRUB can find the BTRFS root partition

## Key Files

- `build/build.sh` — Master ISO build script
- `build/config/chroot-setup.sh` — Chroot configuration (kernel, GRUB, GDM, Plymouth)
- `build/config/packages.list` — Package manifest
- `build/config/grub.d-06_axon_watchdog` — Boot failure counter
- `build/config/grub.d-42_axon_rollback` — Recovery rollback entry
- `installer/axon-installer.py` — GTK4 installer wizard
- `plymouth/axon-splash/axon.script` — Boot splash animation

## Verification

After fixing boot issues, always:
1. Rebuild the ISO: `sudo bash build/build.sh --ci`
2. Verify ISO structure (Phase 2)
3. Test in VM (Phase 3)
4. Test installation if live session works (Phase 5)
