# Gaming Edition — Phase 0 Audit

Read-only audit for `docs/gaming-masterplan.md` Phase 0. Date: 2026-10-27, `main` at `2c26de1`.
No code was changed. Items marked **NOT MEASURED** need a machine with root and a full build;
the sandbox this was written in has neither, so the commands to collect them are given instead.

## 1. Headline findings

1. **The ISO is already at the plan's size limit.** The latest successful Build ISO run
   ([#25](https://github.com/ProjectDEV-git/Axon-OS/actions/runs/40425947509)) uploaded an
   `axon-os-iso` artifact of 3,829,402,002 bytes (3.57 GiB / 3.83 GB, zipped ISO). The plan's
   Standard hard limit is 3.9 GB and its target is 3.5 GB. Adding Phase 1 gaming packages
   (32-bit Mesa, Gamescope, ...) on top will break the limit unless existing content is removed
   first. **Phase 4 size work has to move earlier, or Phase 1 must ship with a package diet.**
2. **Much of the "download later" list is already in the ISO** (see §6), which contradicts the
   Standing Rule as written.
3. **Ollama is not in the ISO at all.** It is installed on first boot by a hash-pinned
   installer (`build/config/install-ollama.sh`, pinned in `ollama-release.env`, v0.35.1). Plan
   §8.2 ("ISO contains the Ollama binary, CPU only") describes a change, not the current state.
4. **Brain is not the only Ollama client.** The AI Panel (`apps/axon-ai-panel/ui/panel.py:90,127`)
   and the Intent Bar (`apps/intent-bar/ollama_client.py`) call Ollama directly on `:11434`.
   `Brain.Suspend` alone will not stop them from loading a model onto the GPU during a game.
5. **`packaging/` does not contain multiple Debian packages.** It holds only `build-deb.sh`,
   which builds the single `axon-os` package, and the archive keyring. The plan's
   `packaging/axon-gaming-*` directories are new infrastructure, not additions to an existing layout.

## 2. Build pipeline (`build/build.sh`, `build/config/`)

- Host script, root only. debootstrap Ubuntu `noble` into `$AXON_BUILD_DIR/chroot`
  (default `/var/lib/axon-build`), runs `build/config/chroot-setup.sh` inside, then builds a
  casper squashfs + hybrid BIOS/UEFI ISO with `grub-mkstandalone` + `xorriso`.
- **Flags** (`build.sh:66-78`): `--ci`, `--compression=xz|gzip`, `--keep-chroot`, `--quick`
  (implies keep-chroot), `--fast` (quick + gzip), `--fresh`, `--chroot`, `--cmd`.
  Default compression is **xz** (`-b 1M -Xbcj x86`). There is no `--edition`.
  Bug noticed in passing: `--cmd) [[ ... $((i+1)) ]]` uses `i` before it is set and
  `die` before it is defined (lines 75, 89).
- **Package selection:** a single flat list, `build/config/packages.list` (plus
  `packages-winabi.list`). Plan paths `build/package-lists/{common,standard,nvidia}.list` do not
  exist; the edition split means turning this one file into three.
  `chroot-setup.sh` installs via apt and tolerates optional-package failures.
- **Sources / multiverse / i386:** `chroot-setup.sh:44-65` already enables
  main+restricted+universe+multiverse (+updates, security) and runs
  `dpkg --add-architecture i386`. Plan §5.1 "enable multiverse / add i386" is already done.
- **Flathub** remote is added at `chroot-setup.sh:125`. Mission Center is installed as a
  **system Flatpak into the ISO** (`:195`), which violates §2.2.
- **Brave** is added from its own apt repo (`:130-178`), with `epiphany` as fallback.
- **Axon itself** reaches the image as the `axon-os` .deb built by `packaging/build-deb.sh`
  and installed at `chroot-setup.sh:226`. `/opt/axon-src` is rsynced in for the build and excluded
  from squashfs.
- **Kernel:** `linux-image-generic` (`:92`). Version is whatever noble ships at build time.
- **APEX-WIN** is built on the host by `scripts/build-apex-win.sh` (Rust, optional) and installed
  by chroot-setup.
- **Output:** `dist/axon-os-<version>-amd64.iso` + `.sha256` + a stub SPDX SBOM. Version comes from
  `pyproject.toml`. Plan filename `axon-os-<version>-<edition>-amd64.iso` needs a rename step
  and a change in `build-iso.yml`, `release.yml`, `verify-iso.sh`, `upload-iso.sh`.
- **No size checks** exist beyond `build-iso.yml:93` (fails if under 700 MB).

## 3. Packaging and updates

- One package, `axon-os` (`Architecture: all`), contains apps, services, shell extension, theme,
  helper scripts, systemd user units, D-Bus activation files, the apt source and keyring.
  `Depends:` is only python3, python3-gi, gir1.2-secret-1, init-system-helpers.
- `build-deb.sh` copies **all** of `services/*` and `apps/*` into `/usr/lib/axon/` and generates
  user units from `services/*/axon-*.service` and D-Bus files from `services/*/org.axonos.*.service`.
  A new `services/axon-gaming/` directory with those two files is picked up automatically
  (the `axon-` unit-name prefix matters).
- Publishing: Actions → Release → `publish-apt.yml` builds the .deb, signs the repo with
  `APT_SIGNING_KEY`, pushes `gh-pages` (`https://projectdev-git.github.io/Axon-OS/apt stable main`,
  newest five versions kept). Only **one** package is published today; publishing several
  needs `publish-apt.yml` changes. Signing key fingerprint `20D7784A...7517`. Plan says to ask
  before changing the key — nothing here requires that.
- `axon-update` (`system/axon-updater.py`) runs daily via `axon-update-auto.timer` and does a
  normal apt upgrade, so any package in that repo is picked up with no extra work.
- Release assets: `build-iso.yml:131-142` already splits ISOs >2 GiB into `.partNN` files
  (`split -b 1900M`). Plan §8.4 treats hosting as undecided; the repo currently solves it with parts.

## 4. Plugin system and `ServiceBase`

- `ServiceBase` (`services/service_base.py`): subclass sets `BUS_NAME`, `OBJECT_PATH`,
  `SERVICE_NAME`, implements `_setup()` / `_cleanup()`; `Subclass.main()` runs the GLib loop and
  handles SIGTERM. Claims the name on the **session bus** and exits(1) if it is already taken.
  Provides `org.axonos.Service.GetStatus/GetServiceName`.
- Plugins: `services/plugins/<name>/manifest.toml` with `[service]` (`name`, `description`,
  `bus_name`, `object_path`, `entry_point`, `dependencies`) and `[systemd]`
  (`description`, `after`, `restart_sec`). `services/plugin_deploy.py` generates the user unit and
  D-Bus file; `services/plugin_registry.py` (`ServiceRegistry`) discovers, topologically sorts
  and starts them.
- **Decision needed:** first-party services (Brain, Search, Context) are *not* plugins; they are
  top-level `services/axon-*/` directories with hand-written unit and D-Bus files.
  The plan says "plugin using ServiceBase and a TOML manifest". `org.axonos.Gaming` must be
  always-on to recover from crashes, so I recommend a top-level `services/axon-gaming/` like
  its peers (which `build-deb.sh` already handles) rather than a plugin. Needs your call.
- Tests need Linux with `dbus-python`/GTK; they cannot run in the authoring sandbox
  (`import dbus` fails here), so unit tests for the Gaming service should keep D-Bus out of the
  core logic (pure-Python token manager) so they run anywhere.

## 5. Brain, Ollama and the Hardware Profiler

- Brain: `services/axon-brain/brain_service.py`. All Ollama traffic goes through
  `OLLAMA_BASE_URL` (`services/constants.py`, default `http://localhost:11434`).
- **`keep_alive` is never set anywhere** in the repo, so Ollama's default (5 minutes) applies.
  Exception: `_warm_up_ollama` (`brain_service.py:266`) sends a prompt-less `/api/generate` at
  startup to preload the **speed** model; Suspend must stop this and must not let a service
  restart (Brain restarts on upgrade) re-run it while a game is active.
- Local calls: `/api/generate`, `/api/chat`, `/api/embed(dings)`, `/api/tags`, `/api/pull`.
  Embeddings use `nomic-embed-text` (also used by Search) — it loads onto the GPU too, so
  Suspend must unload *all* `/api/ps` models, including the embed model, and Search must be paused.
- No `Suspend/Resume` exist. Cloud routing exists (`providers.py`, `docs/ai-providers.md`),
  so "Use cloud during games" is feasible. `num_gpu: 0` for CPU-only fallback is not used today.
- **Bypass paths** (must be handled in Phase 2): AI Panel `panel.py`, Intent Bar
  `ollama_client.py`, `apps/axon-welcome/ui/welcome.py:46`, `services/diagnostics.py`,
  `model_marketplace.py` (tags/pull/delete only — harmless). Recommend routing the panel and
  Intent Bar through Brain, or having them check `org.axonos.Gaming.Active`.
- **Hardware Profiler** (`hardware_profiler.py`): `get_gpu_info()` → `{vendor, model, vram, status}`;
  tries `nvidia-smi`, then `rocm-smi`, then `lspci`-based fallbacks (NVIDIA/AMD fixed at 4 GB
  guess, Intel by name). **It only recognises AMD when `rocm-smi` exists**, so an AMD GPU on a
  fresh install (no ROCm) is reported through the fallback with a guessed VRAM. The Drivers page
  and "install ROCm" flow need a better source (sysfs `mem_info_vram_total`, `lspci -nn`).
  It also reports nothing about the installed driver or hybrid (iGPU+dGPU) setups.
- **Ollama install:** `install-ollama.sh` extracts the official `ollama-linux-amd64.tar.zst`
  after a SHA-256 check. That tarball includes the GPU runner libraries, so today GPU support is
  not separable. The §8.2 split (`axon-ollama-cuda/rocm`) means repackaging the upstream tarball
  contents; check the layout of the pinned release before promising it.

## 6. What the ISO contains today that the Standing Rule says to download later

From `build/config/packages.list` and `chroot-setup.sh`. Sizes **NOT MEASURED**.

| Package(s) | Rule status |
|---|---|
| `libreoffice`, `vlc`, `transmission-gtk`, `gedit` | Not A/B/C — candidates to drop (libreoffice is large) |
| `docker.io`, `docker-compose-v2`, `golang`, `nodejs`, `npm`, `build-essential`, `cmake`, `podman`, `distrobox` ("App Development" block) | Not A/B/C |
| `lutris` | §2.2: launchers other than Steam are download-later |
| `dxvk`, `vkd3d-proton`, `wine64`, `wine32:i386` | Rule C (APEX-WIN/Wine) — keep, but `wine32:i386` is large; verify |
| `gstreamer1.0-plugins-bad/ugly`, `gstreamer1.0-libav`, `ffmpeg`, `libavcodec-extra` | §2.2: optional codecs are a wizard checkbox |
| Mission Center system Flatpak | §2.2: no Flatpak apps in ISO |
| `virtualbox-guest-*`, `open-vm-tools*`, `spice-vdagent`, `qemu-guest-agent` | Debatable; useful for VM testing, not for gamers |
| `printer-driver-*`, `foomatic-db-compressed-ppds`, `cups*`, `sane*`, `simple-scan` | Large; consider on-demand |
| `xserver-xorg-video-*` (all, intel, nouveau, ...) | Legacy X11 drivers on a Wayland GNOME 46 system |
| `steam-installer`, `steam-devices`, `gamemode`, `mangohud` | Rule C, already present |
| `libvulkan-dev`, `libevdev-dev`, `libudev-dev`, `libpipewire-0.3-dev`, `libspa-0.2-dev` | `-dev` packages in a runtime image |

Not present yet, needed by the plan: `gamescope`, `libgamemode0:i386`, `mangohud:i386`,
`mesa-vulkan-drivers:i386`, `libgl1-mesa-dri:i386`, `libglx-mesa0:i386`, `libvulkan1:i386`,
`systemd-zram-generator`, `ubuntu-drivers` is present via `ubuntu-drivers-common`.

Takeaway: "gaming works out of the box" is already roughly half-delivered, and the heavy
removals (libreoffice, dev toolchains, docker, Lutris, codecs) are what make room.
Removing any of these changes the product for non-gamers, so it needs your sign-off (AGENTS rule 7).

## 7. ISO size and package breakdown — NOT MEASURED

- Latest CI artifact: 3,829,402,002 bytes (see §1). Squashfs compression: **xz, 1 MiB blocks, BCJ x86**
  (`build.sh:340-342`); `--fast` uses gzip.
- To get the top-30 packages: `sudo bash build/build.sh --chroot --cmd "dpkg-query -Wf '\${Installed-Size}\t\${Package}\n' | sort -n | tail -30"`,
  or download the CI artifact and `unsquashfs` it. Left for a machine with root + 20 GB.
- Kernel: whatever `linux-image-generic` resolved to on 2026-10-27; read it from
  `casper/filesystem.manifest` in the CI ISO. `modinfo ntsync` also NOT MEASURED. Note noble's
  stock 6.8 kernel does **not** include ntsync (merged upstream in 6.14); the HWE kernel
  (6.14+) might. Verify in the chroot. If missing, the ntsync tweak is skipped (as the plan says)
  and Wine falls back to esync/fsync.

## 8. Other relevant facts

- **Swap/zram:** none configured in `build/`, `installer/` or `system/` (grep found no match).
  No existing swap setup to preserve.
- **sysctl / limits / ulimit:** nothing shipped today.
- **GameMode / Steam / Lutris** are installed but nothing configures them; no references in
  apps or services.
- **APEX-WIN:** `apex-win/` (Rust) with a `win-sandbox-runner`; entry point is
  `scripts/axon-apex-run` plus a binfmt rule. Tier 2 (bubblewrap) is a sandbox: GameMode
  (`gamemoderun`) and Proton lookups need to work with Tier 0 first; Tier 2 would need socket
  access to `gamemoded`. Welcome app already has a "Windows Programs" page to extend.
- **Shortcuts in use** (`shell/axon-shell/schemas`): Intent Bar, AI Panel, Spaces 1-9, space
  switcher, plus those in the plan. Check the schema before assigning an overlay shortcut.
- **Tests:** 66 files under `tests/`; `tests/conftest.py` aliases hyphenated service dirs.
  CI order per AGENTS.md: ruff → mypy → pytest (cov ≥ 40%) → bandit. A `.shellcheckrc` and
  pre-commit (ruff, mypy, bandit) are present.
- **Docs:** no `docs/gaming*.md` existed before this file. `README.md` needs updating in Phase 4.

## 9. Conflicts and deviations between the plan and the repo

| # | Plan says | Repo reality | Proposed handling |
|---|---|---|---|
| 1 | Standard ISO target ≤ 3.5 GB, hard 3.9 GB | Already ~3.83 GB | Do the package diet in Phase 1 or reorder Phase 4 first; ask before removing non-gaming apps |
| 2 | ISO ships Ollama CPU binary | Ollama installed at first boot, GPU libs included in the tarball | Keep first-boot install; model GPU libs as components |
| 3 | Gaming service is a plugin w/ TOML | Core services are top-level dirs; plugin loader is for third parties | Top-level `services/axon-gaming/` (needs your call) |
| 4 | Brain Suspend is enough | AI Panel and Intent Bar hit Ollama directly | Route via Brain or gate on `Active` |
| 5 | `packaging/axon-gaming-*` debs | Only `build-deb.sh` for one package; `publish-apt.yml` publishes one | Generalise `build-deb.sh`/`publish-apt.yml` in Phase 1 |
| 6 | `build/package-lists/*.list` | `build/config/packages.list` | Split during Phase 4; for Phase 1 add a gaming block via the metapackage |
| 7 | Enable multiverse, add i386 | Already done | Skip |
| 8 | Lutris, Mission Center, codecs not in ISO | All present | Remove, with your approval |
| 9 | Hardware Profiler reports vendor/VRAM | AMD only via `rocm-smi`; guessed VRAM otherwise | Improve detection (sysfs/lspci) in Phase 3 |
| 10 | Release hosting undecided (>2 GiB) | `.partNN` splitting already implemented | Keep unless you want a different host |
| 11 | "Axon Settings → Gaming", "Shell top-bar indicator" | Settings app is `apps/axon-settings`, extension `shell/axon-shell` | No conflict; both extensible |
| 12 | kisak-mesa PPA in Phase 1 | Third-party PPA vs. Standing Rule 5/Shield posture | Needs explicit approval + pinned key fingerprint; noble's Mesa may suffice for Phase 1 |
| 13 | `AGENTS.md` conflicts | AGENTS.md is a multi-project file; Axon section matches plan (ruff, mypy, 100 cols, `ServiceBase`) | No conflict. Standing Rule (§2) is **not yet in AGENTS.md** — add when you approve |

## 10. Where each phase lands

| Phase | Files touched |
|---|---|
| 1 | `packaging/` (new debs + generalised `build-deb.sh`, `publish-apt.yml`), `build/config/packages.list` + `chroot-setup.sh` (remove/add), `apex-win/` + `scripts/axon-apex-run` (Proton runners, gamemoderun) |
| 2 | new `services/axon-gaming/`, `brain_service.py`, `search_service.py`, `context_service.py`, `apps/axon-ai-panel`, `apps/intent-bar`, `shell/axon-shell` (indicator), `apps/axon-settings`, new `axon-game-mode` CLI |
| 3 | `data/gaming/`, new `apps/axon-game-center/`, `apps/axon-welcome/ui/welcome.py`, `hardware_profiler.py`, `data/applications/` |
| 4 | `build/build.sh`, `build/config/packages.list` split, `scripts/check-iso-size.sh`, `build/size-budget.conf`, `build-iso.yml`, `release.yml`, `docs/building.md`, `README.md` |
| 5-7 | as in the plan |

## 11. Questions for you before Phase 1

1. May I remove non-gaming bulk from the ISO (libreoffice, docker/golang/node, Lutris, codecs, Mission Center Flatpak) to make room? Alternative: raise the size budget.
2. `org.axonos.Gaming` as a top-level service rather than a plugin?
3. Is a third-party Mesa PPA acceptable for Phase 1, or should we rely on noble's Mesa until Phase 6?
4. Should I add the Standing Rule (§2) to `AGENTS.md` now?
