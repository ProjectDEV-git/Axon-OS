# Building Axon OS

## Prerequisites

- **Host OS:** Ubuntu 24.04 LTS (other Debian-based distros may work but are untested)
- **Disk space:** 20 GB free in the build directory
- **RAM:** 8 GB minimum (16 GB recommended for parallel builds)
- **CPU:** x86-64, 4+ cores recommended
- **Network:** required for initial package fetch; subsequent builds are offline-capable

## Install Build Dependencies

```bash
sudo apt-get update
sudo apt-get install -y \
    debootstrap squashfs-tools xorriso \
    grub-pc-bin grub-efi-amd64-bin \
    mtools dosfstools rsync
```

(Optionally add `qemu-system-x86 ovmf` for testing the ISO in a VM.)

## Build Steps

```bash
# 1. Clone the repository
git clone https://github.com/kaorii-ako/Axon-OS.git
cd Axon-OS

# 2. Run the main build script (root required for debootstrap/chroot)
sudo bash build/build.sh
```

The script will:
1. Bootstrap a minimal Ubuntu 24.04 (noble) root filesystem with `debootstrap`.
2. Install the kernel, `casper` (Ubuntu live-boot), GNOME, and all Axon OS components into the chroot (`build/config/chroot-setup.sh`).
3. Apply Axon branding: GTK theme, gschema defaults, Plymouth splash, wallpaper, and the native Axon Installer.
4. Build a SquashFS image of the root filesystem.
5. Wrap everything in a hybrid BIOS + UEFI bootable ISO with GRUB and `xorriso`.

The finished ISO is written to `dist/axon-os-<version>-amd64.iso` with a `.sha256` checksum alongside.

If you want to upload the ISO to GitHub automatically from a local build, use the helper script:

```bash
./scripts/upload-iso.sh [release-tag]
```

If no tag is supplied, the script infers one from the ISO filename or the current git tag.

Useful flags / environment:

| Option | Effect |
|--------|--------|
| `--compression=gzip` | Faster squashfs (larger ISO); default is `xz` |
| `--keep-chroot` | Reuse the existing chroot for iterative rebuilds |
| `AXON_BUILD_DIR=/path` | Work directory (default `/tmp/axon-build`, needs ~15 GB) |

### Persistent APT Cache

Downloaded `.deb` packages are cached in `${AXON_BUILD_DIR}/apt-cache/` and persist across builds. This means the first build downloads everything from the Ubuntu mirror, but subsequent builds (even with a fresh chroot) reuse cached packages — cutting the download phase from minutes to seconds.

To clear the cache and start fresh:

```bash
sudo rm -rf /tmp/axon-build/apt-cache
```

## CI Builds (GitHub Actions)

Every push to `main` that touches `build/`, `apps/`, `services/`, `shell/`, `theme/`, `data/`, `installer/`, or `plymouth/` runs the **Build ISO** workflow (`.github/workflows/build-iso.yml`). Download the finished ISO from the workflow run's **Artifacts** section (`axon-os-iso`). Tagged pushes (`v*`) also attach the ISO to a GitHub Release when it fits the 2 GiB asset limit.

## Testing in QEMU

Boot the ISO directly without burning it to media:

```bash
qemu-system-x86_64 \
    -enable-kvm \
    -m 4G \
    -smp 4 \
    -cpu host \
    -drive file=dist/axon-os-0.3.0-amd64.iso,format=raw,if=virtio \
    -bios /usr/share/ovmf/OVMF.fd \
    -vga virtio \
    -display gtk,gl=on \
    -net nic,model=virtio \
    -net user \
    -boot d
```

For a persistent install test, create a virtual disk first:

```bash
qemu-img create -f qcow2 axon-test.qcow2 20G

qemu-system-x86_64 \
    -enable-kvm \
    -m 4G \
    -smp 4 \
    -cpu host \
    -drive file=axon-test.qcow2,format=qcow2,if=virtio \
    -cdrom dist/axon-os-0.3.0-amd64.iso \
    -bios /usr/share/ovmf/OVMF.fd \
    -vga virtio \
    -display gtk,gl=on \
    -net nic,model=virtio \
    -net user \
    -boot d
```

## Docker Build (Reproducible)

A `Dockerfile` in the repository root provides a fully reproducible build environment:

```bash
# Build the image
docker build -t axonos-builder .

# Run the build inside the container
docker run --rm \
    --privileged \
    -v "$(pwd)/build/output:/output" \
    axonos-builder \
    bash build.sh

# The ISO is placed in ./build/output/
```

> Note: `--privileged` is required for `debootstrap` and loop-device operations inside the container. Rootless Docker is not supported for the build stage.

## Graphical Installer (Axon Installer)

Axon OS ships its own native GTK4/libadwaita welcome and installation wizard (`apps/axon-installer/`). It autostarts when the ISO boots into the live session (`boot=casper` on the kernel command line) and walks the user through the full install — including AI setup, because Axon is an AI-centered OS.

### Install sequence

| Step | Page | Description |
|------|------|-------------|
| 1 | Welcome | Try the live session, or start the install |
| 2 | Internet | Wired status + Wi-Fi picker (`nmcli`); offline installs are allowed |
| 3 | About You | Full name, username (auto-suggested), password, computer name |
| 4 | Disk Setup | **Erase disk** (full wipe) or **install alongside another OS** (dual boot, uses the largest unallocated region ≥ 16 GiB) |
| 5 | AI Setup | Install Ollama + pick a default local model, and/or add cloud providers (Anthropic, OpenAI, Google Gemini, OpenRouter) with API keys |
| 6 | Summary | Review everything — nothing is written until Install is pressed |
| 7 | Installing | Live progress from the root engine, with AI feature tips |
| 8 | Finished | Reboot into the installed system or keep exploring live |

### Architecture

- **UI** — `apps/axon-installer/ui/wizard.py`, runs unprivileged in the live session.
- **Engine** — `apps/axon-installer/install_engine.py`, pure-stdlib Python run as root (`sudo -n` on the live session, `pkexec` via `/usr/local/bin/axon-install-engine` otherwise). It partitions (GPT via `sgdisk`/`parted`, BIOS + UEFI), formats the root as **BTRFS with `@`/`@home` subvolumes** (ext4 fallback), rsyncs the running live root to the target, writes fstab/swapfile, creates the user, removes live-session artifacts (casper, autologin, live user), installs GRUB (with `os-prober` enabled for dual boot), provisions AI choices, and arms the **self-healing boot watchdog**: a factory `@axon-fallback` snapshot plus a GRUB boot counter stored on the ESP (label `AXONESP`). After two consecutive failed boots GRUB auto-selects the rollback snapshot; `axon-boot-ok.service` resets the counter once a boot reaches the display manager.
- **Progress protocol** — the engine prints `AXON-PROGRESS:<pct>:<msg>` / `AXON-ERROR:<msg>` / `AXON-DONE` lines that the UI renders live.
- **AI provisioning** — provider API keys are written to the new user's `~/.axon/config.toml` (mode 600, never under `/etc`). Ollama install + model pull happens on the installed system's first online boot via `axon-ai-firstboot.service` (`build/config/ai-firstboot.sh`), so offline installs work too.

### Testing the installer in QEMU

Boot the ISO with a blank virtual disk attached (see the QEMU section below) — the wizard appears automatically in the live session. To relaunch it manually:

```bash
python3 /usr/lib/axon/apps/axon-installer/main.py
```

---

## Customization Guide

### Package List

Add or remove packages from the installed system by editing `build/packages.list`. Each non-empty, non-comment line is passed to `apt-get install` inside the chroot.

```
# Extra packages example
neovim
htop
ollama-model-mistral
```

### GTK Theme

The default dark stylesheet is at `theme/gtk-dark.css`. Override variables at the top of the file — all colors use CSS custom properties:

```css
:root {
  --accent-color: #a78bfa;   /* violet — change to taste */
  --bg-color:     #0d0d10;
}
```

### Default AI Model

Change the model that loads on first boot by setting `DEFAULT_MODEL` in `build/firstboot.sh`:

```bash
DEFAULT_MODEL="llama3"        # change to e.g. mistral, phi3, gemma2
```

The variable is written into `~/.axon/config.toml` for the default user during first-boot setup.

### First-Boot Script

`build/firstboot.sh` runs once as root on the installed system's first boot (via a systemd one-shot unit). Use it to seed configuration, pull additional Ollama models, or run post-install hooks:

```bash
# Pull an additional model on first boot
ollama pull nomic-embed-text
```
