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
git clone https://github.com/ProjectDEV-git/Axon-OS.git
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

The finished ISO is written to `dist/axon-os-<version>-<edition>-amd64.iso` with a `.sha256` checksum, an SBOM and a `.packages.tsv` (installed size of every package) alongside.

If you want to upload the ISO to GitHub automatically from a local build, use the helper script:

```bash
./scripts/upload-iso.sh [release-tag]
```

If no tag is supplied, the script infers one from the ISO filename or the current git tag.

Useful flags / environment:

| Option | Effect |
|--------|--------|
| `--edition standard\|nvidia` | Which ISO to build (default `standard`). Each edition has its own work directory (`edition-nvidia/` under `AXON_BUILD_DIR`), so both can be built from one checkout |
| `--compression=gzip` | Faster squashfs (larger ISO); default is `xz`, which release builds must use |
| `--fast` | `--quick` plus gzip. For local testing only: prints a warning and does not enforce the size budget |
| `--keep-chroot` | Reuse the existing chroot for iterative rebuilds |
| `AXON_BUILD_DIR=/path` | Work directory (default `/var/lib/axon-build`, needs ~15 GB) |

### Editions and package lists

| Edition | For | Size target / hard limit |
|---|---|---|
| `standard` | AMD, Intel, older NVIDIA (Mesa graphics) | 3.5 GB / 3.9 GB |
| `nvidia` | GTX 16-series, RTX and newer | 4.3 GB / 4.7 GB |

Packages come from `build/package-lists/`: `common.list` (both editions), plus `standard.list` or
`nvidia.list`. Edition packages must install; a missing one fails the build. Packages in
`common.list` that fail are skipped with a warning, so check names first:

```bash
python3 scripts/verify-packages.py     # every name in the lists and metapackages exists in noble
```

The NVIDIA edition installs `axon-nvidia` (`packaging/axon-nvidia`): Ubuntu's prebuilt and signed
`linux-modules-nvidia-580-open-generic` (no DKMS, so no compiler on the image and Secure Boot works),
the 580 userspace libraries and the 32-bit OpenGL libraries Steam needs. The open kernel modules
need a GTX 16-series or newer card; older cards use the Standard edition and the driver Game Center
offers. **Driver branch:** 580, checked against the noble archive on 2026-10-27. To move to another
branch, change every `580` in `packaging/axon-nvidia/control` together (a test checks they match) and
run `scripts/verify-packages.py`.

The image is slimmed at the end of `chroot-setup.sh`: Snap, extra language packs and `*-doc`
packages are removed (`AXON_SLIM=0` turns it off). `linux-firmware` is never trimmed.

### Local AI in the image

The image contains Ollama itself and its CPU libraries (about 60 MB on disk, installed by
`build/config/install-ollama.sh --cpu-only` from the release pinned in `ollama-release.env`). The
service is disabled until the user opts into local AI. The GPU libraries are never in the image:
`build/config/install-ollama-gpu.sh cuda|rocm` adds one backend (run through `pkexec`, polkit action
`org.axonos.ollama-gpu`) from the same pinned release, after checking its SHA-256. This replaces the
`axon-ollama-cuda` / `axon-ollama-rocm` apt packages the plan suggested: the Axon apt repository is
on GitHub Pages, which rejects files over 100 MB, and the CUDA libraries alone are over 1 GB.
Update the pin with `scripts/pin-ollama.sh <version>`.

### Size budget

`build/size-budget.conf` holds the limits. `build.sh` checks the finished ISO with
`scripts/check-iso-size.sh`, which fails above the hard limit, warns above the target, prints the 30
biggest packages and, given the package list of the previous build, what was added, removed or grew:

```bash
scripts/check-iso-size.sh --edition standard \
    --packages dist/axon-os-1.1.0-standard-amd64.packages.tsv \
    --previous previous.packages.tsv dist/axon-os-1.1.0-standard-amd64.iso
```

A pull request that adds more than 50 MB to either ISO must say why in its description and which
rule (A, B or C) of the ISO policy in `AGENTS.md` allows it.

### Keep Chroot (Interactive Development)

For iterating on the chroot configuration without rebuilding the full ISO, use the standalone `keep-chroot` command:

```bash
# Enter an interactive shell inside the persistent chroot
sudo bash scripts/keep-chroot.sh

# Run chroot-setup.sh to install all Axon components, then enter the shell
sudo bash scripts/keep-chroot.sh --setup

# Run a single command inside the chroot without entering interactively
sudo bash scripts/keep-chroot.sh --cmd "apt list --installed"

# Destroy the existing chroot and start fresh
sudo bash scripts/keep-chroot.sh --reset
```

The chroot is created via `debootstrap` on first run and persisted at `/var/lib/axon-build/chroot`. Subsequent runs reuse the same chroot, so you can install packages, edit configs, and test changes without re-downloading everything. Use `--setup` to apply the full Axon component installation from `chroot-setup.sh`.

### Persistent APT Cache

Downloaded `.deb` packages are cached in `${AXON_BUILD_DIR}/apt-cache/` and persist across builds. This means the first build downloads everything from the Ubuntu mirror, but subsequent builds (even with a fresh chroot) reuse cached packages — cutting the download phase from minutes to seconds.

To clear the cache and start fresh:

```bash
sudo rm -rf /var/lib/axon-build/apt-cache
```

## CI Builds (GitHub Actions)

Every push to `main` that touches `build/`, `apps/`, `services/`, `shell/`, `theme/`, `data/`, `installer/`, or `plymouth/` runs the **Build ISO** workflow (`.github/workflows/build-iso.yml`). It builds both editions in parallel (one failing does not stop the other). For each edition it checks the package names, builds the ISO, enforces the size limit, verifies the boot catalog, and uploads two artifacts: `axon-os-iso-<edition>` (the ISO, its `.sha256` and SBOM) and `axon-os-packages-<edition>` (package sizes, which the next build compares against; the size report is in the run summary).

## Releasing

Release hosting: **GitHub Releases, with ISOs over 2 GiB split into `.partNN` files** plus a `.sha256`
per ISO. Both editions are over 2 GiB, so both are split. This needs no extra infrastructure; a
mirror or BitTorrent can be added later without changing the build. The README explains how to join
the parts.

Release checklist:

1. `python3 scripts/verify-packages.py` and `python3 scripts/verify-catalog.py --write` on a machine with internet; commit the result.
2. Actions → **Release** → enter the version. It bumps `pyproject.toml`, tags, publishes the apt packages and starts **Build ISO** for the tag.
3. Both Build ISO jobs are green and neither size report shows an unexpected jump.
4. Boot each ISO in QEMU or a VM (`docs/gaming-testing.md` lists the hardware checks): live session, installer, welcome app, Game Center opens.
5. The release has both editions' parts, `.sha256` files and SBOMs. Download one edition, join the parts, run `sha256sum -c`.
6. Update the release notes with anything users must know (driver branch, new default settings).

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

Add or remove packages from the installed system by editing `build/package-lists/*.list`. Each non-empty, non-comment line is passed to `apt-get install` inside the chroot.

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
