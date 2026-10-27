# Axon OS on Ubuntu 26.04 LTS

Phase 7 moves the image to Ubuntu 26.04 LTS ("resolute") and gives installed 24.04 systems a way to
follow. Everything below was checked against the Ubuntu archive on **2026-10-27** (26.04.1 is out and
Ubuntu offers the LTS upgrade); what has not been run on hardware is marked.

## Building on either release

`build/build.sh` takes `AXON_UBUNTU_RELEASE`: `resolute` (26.04, the default) or `noble` (24.04).

```bash
sudo bash build/build.sh --edition standard                      # 26.04
sudo AXON_UBUNTU_RELEASE=noble bash build/build.sh --edition standard   # 24.04, as before
python3 scripts/verify-packages.py --ubuntu-release resolute     # every package name exists
python3 scripts/check-shell-extension.py                         # the shell extension's API still exists
```

`build/package-lists/common.list` is for both; `common.noble.list` and `common.resolute.list` hold what
differs. A host `debootstrap` without a `resolute` script (the CI runner's) falls back to the `gutsy`
script, which every Ubuntu release uses.

## What changed in the package lists

| Was | On 26.04 | What we did |
|---|---|---|
| `policykit-1` | gone; `polkitd` + `pkexec` | list `pkexec` (pulls `polkitd`); both releases have it |
| `wireless-tools` | gone (`iw` does the job, already listed) | dropped |
| `mesa-va-drivers`, `mesa-vdpau-drivers` | part of `mesa-libgallium` | `common.noble.list` only |
| `packagekit-tools` (`pkcon`) | gone; `pkgcli` is in `packagekit` | `common.noble.list` only; Game Center runs `pkgcli --yes` where `pkcon` is missing |
| `tlp` | conflicts with `power-profiles-daemon` (also on 24.04) | dropped everywhere: GNOME's power-mode switch needs the daemon |

The `tlp` conflict was found by an `apt-get -s install` of the whole list, which now resolves on both
releases. With the conflict the bulk install in `chroot-setup.sh` would have failed and fallen back to one package at a
time, ending with one of the two removed.

**PPAs:** none are used, on either release. **Backports:** Gamescope 3.16.20 is in 26.04's archive, so
`packaging/backports` (3.16.15 and its libraries) is for 24.04 only; the `wayland` backport that
replaces a desktop library never reaches a 26.04 system.

## Tweaks that are now Ubuntu defaults

| Tweak (`axon-gaming-tweaks`) | 26.04 default | Kept? |
|---|---|---|
| `vm.max_map_count = 2147483642` | `1048576` (`procps`, `55-map-count.conf`) | **Kept.** Ubuntu's value stops the crash-on-start; the larger one is what SteamOS uses and some titles (Star Citizen) need 16 million or more |
| `kernel.split_lock_mitigate = 0` | not set | kept |
| `DefaultLimitNOFILE=1024:1048576` | `1024:524288` | kept (twice the hard limit; harmless) |
| zram swap | not enabled by default; `systemd-zram-generator` still packaged | kept |
| `axon-ntsync.service` | kernel 7.0 has the `ntsync` module, nothing loads it | kept |

**Kernel.** 26.04 ships Linux 7.0 (NTSYNC, sched_ext) as the normal kernel, so the Game Center "gaming
kernel" item (Ubuntu's HWE kernel) is shown on 24.04 only. The signed NVIDIA modules package name
follows the release: `linux-modules-nvidia-580-open-generic` on every 26.04 generic kernel, `...-hwe-24.04`
on 24.04's HWE kernels. The GRUB fallback entry (`41_axon_kernel_fallback`) is now "the oldest installed
kernel series, if a newer one exists", which also gives an upgraded system its old 6.8 kernel to fall
back to until it is cleaned up.

**Driver branches (NVIDIA).** The NVIDIA ISO uses **595**; 580 is for older cards. 590 and 595 dropped
Maxwell, Pascal and Volta; 595 supports Turing (GTX 16-series) and newer
([sources](https://wiki.nobaraproject.org/graphics/nvidia/supported-gpus)); 580 keeps security
updates for the older cards. Checked in the archives on 2026-10-27:

| Package | 24.04 | 26.04 |
|---|---|---|
| `nvidia-driver-595[-open]`, `linux-modules-nvidia-595-open-generic`, `libnvidia-gl-595` (+ `:i386`) | 595.99 | 595.99 |
| `nvidia-driver-590[-open]` | **590.48 (real)** | transitional package for 595 |
| `nvidia-driver-580[-open]` | 580.178 | 580.178 |

So "590" is not one driver across both releases; **595 is the same on both**, and is what
`packaging/axon-nvidia` depends on. The Hardware Profiler (`gpu_drivers.choose_nvidia_driver`) picks the
branch from the card's generation (chip code such as `GP106` or `TU117` in the `lspci` name, then the
marketing name): Turing and newer get `nvidia-driver-595-open`, Maxwell/Pascal/Volta get
`nvidia-driver-580` (open modules do not support them), and a card it cannot place keeps what
`ubuntu-drivers` recommends. A machine with both kinds gets 580, which covers both. Both branches have
signed prebuilt modules on both releases.

## GNOME Shell 50

26.04 has GNOME Shell 50.1 and **no X11 session**: `gnome-session-xsession` is not in the archive, so
`WaylandEnable=false` in GDM would have nothing to fall back to. The live session therefore stops forcing
X11 on 26.04 (`chroot-setup.sh` still does on 24.04 images). VirtualBox's `virtualbox-guest-x11` helper is still
installed but has no X11 to work in; VM guest resizing on 26.04 depends on the hypervisor's Wayland
support and **has not been tried**.

The Axon Shell extension lists shells 45 to 50. `scripts/check-shell-extension.py` downloads the
release's `gnome-shell` and `libmutter-*-dev`, takes the shell's own JavaScript out of `libshell`, and checks
that every shell module the extension imports exists, every `Main`, `PanelMenu`, `PopupMenu`,
`MessageTray`, `AppFavorites` and `Config` member it uses is still exported, and every `Clutter` and
`Meta` name is still in the GIR. Result for shell 50.1: no problems. It cannot see `St` and `Shell` (their GIR is not
shipped), and checks the first name after `Main.` and the like, not what is called on it. It is a
static check: **the extension has not been run under a Shell 50 session.** `St.BoxLayout`'s `vertical`
property, which the extension uses throughout, is still defined in 26.04's `libst`, but the shell's
own code now uses `orientation`; move to it if `vertical` is ever removed.

The GSettings overrides in `chroot-setup.sh` were checked against 26.04's schemas: every key exists.
Python 3.14 (26.04) removed no module the code imports.

## Upgrading an installed 24.04 system

```bash
sudo axon-update --check-release      # is Ubuntu 26.04 offered for this machine?
sudo axon-update --release-upgrade    # checks, snapshot, upgrade, fix-ups
sudo axon-update --rollback-upgrade   # only if it went wrong, see below
```

`--release-upgrade` runs Ubuntu's own `do-release-upgrade` without questions and
**never starts by itself**: the daily timer does not touch the release. Before anything is changed it
checks that

- `axon-os` is at least 1.1.0 (the first version whose extension lists Shell 50),
- 10 GB are free on the system disk and 300 MB on `/boot`,
- the machine is on mains power, no restart is pending, nothing is on hold,
- the system is on BTRFS (otherwise `--no-snapshot` must be given, after a backup).

Then it brings 24.04 fully up to date, takes a **writable BTRFS snapshot of `@`** named
`@axon-pre-upgrade-<date>-<time>`, and only then upgrades. Afterwards it sets
`UBUNTU_CODENAME=resolute` in `/etc/os-release` (Axon's own file; the upgrade leaves it alone), makes
sure the Axon apt source is switched on (its suite is `stable`, not a codename, so it needs no change),
and runs `update-grub`. The Axon repository is kept during the upgrade (`--allow-third-party`).

**Going back.** Restart and choose **Axon OS — before the upgrade (snapshot)** in the GRUB menu; that boots the
snapshot. It is a normal system; to make it *the* system again, run `sudo axon-update --rollback-upgrade`
from there: the upgraded `@` is renamed `@axon-failed-upgrade-<time>` and a new `@` is made from the
snapshot. The factory rollback entry (`@axon-fallback`) stays as before.

Not done, on purpose: a button in the updater window. It waits until `--release-upgrade` and
`--rollback-upgrade` have passed on real BTRFS machines (the command can already be started with `pkexec`;
the polkit action covers `axon-update`). When it is added it must only appear if a snapshot can be taken,
and on a system that is not on BTRFS it must say why the upgrade is not offered there. The module and its decisions are covered by
`tests/test_release_upgrade.py`, including the GRUB entry; `do-release-upgrade` itself, BTRFS and
GRUB are faked or stubbed there.

**Outside software sources are turned off first**, like Ubuntu's own upgrader does, because repositories
built for 24.04 are the commonest reason an upgrade breaks. Ubuntu's archive (and its mirrors) and
the Axon repository stay on; the Axon one has to, or the upgrader would call the Axon packages obsolete.
Each entry that is turned off gets a marker (`Enabled: no` for `.sources`, a `#axon-off# ` prefix for
`.list` lines), so it can be undone from the file alone. At the end the upgrade lists how many were turned
off. Flatpak apps are not touched.

```bash
sudo axon-update --list-sources            # what is off, and what the upgrade would turn off
sudo axon-update --enable-sources          # turn all back on
sudo axon-update --enable-sources foo.list # or one file
```

Turn a source back on only once its maker supports 26.04; otherwise `apt update` will complain.

## Size check (estimate)

No ISO was built (the build needs hours and a root machine with ~20 GB). Instead `apt-get install -s` of
the full package set, plus the edition's, was run in clean 24.04 and 26.04 root filesystems, and the
sizes of the packages it would install were added up from the Packages indexes.

| | 24.04 (noble) | 26.04 (resolute) | Change |
|---|---|---|---|
| Standard: packages | 1846 | 1898 | |
| Standard: installed | 5678 MB | 6648 MB | +970 MB |
| Standard: download (compressed) | 2022 MB | 2323 MB | **+301 MB** |
| NVIDIA: installed | 6856 MB | 7827 MB | +971 MB |
| NVIDIA: download (compressed) | 2390 MB | 2690 MB | **+300 MB** |

The compressed download is a rough guide to the squashfs (both are xz/zstd). On that basis the ISOs would
be around **2.5 GB (Standard)** and **2.9 GB (NVIDIA)**, inside the 3.5 and 4.3 GB targets. That is an
estimate; the first CI ISO build gives the real number (`check-iso-size.sh` enforces the limits).

Both editions grow by more than 50 MB. **Rule C (APEX-WIN/Wine works out of the box):** `libwine` for Wine 10
is 206 MB compressed against 105 MB for Wine 9 (+501 MB installed). **Rule A (boot/install):** the 7.0
kernel modules (+160 MB installed) and LLVM 21 for Mesa (+280 MB installed, both architectures). The rest
is spread thin; `snapd` (-113 MB) and the old kernel modules (-145 MB) are gone.

## Benchmarks

The Phase 6 benchmarks (5 games, 3 runs, AMD and NVIDIA) have **not been run**: there is no GPU in the
build environment. `scripts/bench-mangohud.py` and `docs/benchmarks/README.md` are ready; 26.04 is
the first thing to measure, and 24.04 numbers on the same hardware are the baseline.
