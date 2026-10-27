# Gaming on Axon OS

Status: Phases 1 (gaming core and system tweaks), 2 (game mode), 3 (catalog and Game Center), 4 (editions and size enforcement) and 5 (console mode and polish) and 6 (kernel and graphics stack) of `docs/gaming-masterplan.md`.
Audit and background: `docs/gaming-audit.md`.

## What ships in the ISO

Two small packages, built from `packaging/` by `packaging/build-pkg.sh` and
published to the Axon apt repo next to `axon-os`, so `axon-update` keeps them current:

| Package | What it does |
|---|---|
| `axon-gaming-core` | Metapackage: `steam-installer` (the Steam runtime downloads on first start), `steam-devices` (controller udev rules), `gamemode`, `mangohud`, `vulkan-tools`, the Vulkan loader and Mesa Vulkan driver, and the 32-bit Mesa/GL/Vulkan/GameMode libraries 32-bit Proton games need. |
| `axon-game-mode` | The `axon-game-mode` command and `/etc/gamemode.ini`, which makes GameMode run it when a game starts and ends. |
| `axon-gaming-tweaks` | `vm.max_map_count`, `kernel.split_lock_mitigate`, a 1048576 open-file hard limit for system and user services, zram swap (`min(RAM/2, 8 GiB)`, zstd), and `/dev/ntsync` access. |

Not shipped, on purpose (see the ISO policy in `AGENTS.md`): other launchers, Proton-GE,
emulators, chat/recording tools, optional media codecs, Node/Go/Docker, LibreOffice,
Mission Center. They arrive through Game Center in a later phase.

## Windows games (APEX-WIN)

`axon-apex-run` starts every Windows program through `gamemoderun` (turn off for one launch
with `AXON_GAMEMODE=0`). With Tier 0 (the default, full access) it can use a Proton or Wine
build you installed with Steam, ProtonPlus or ProtonUp-Qt:

```bash
axon-proton-runners list            # builds found in compatibilitytools.d and Steam
axon-proton-runners set GE-Proton9-27
axon-proton-runners clear           # back to the system Wine
```

The sandboxed tiers always use the system Wine. Wine Mono and Gecko download the first time a
program needs them; they are not in the ISO.

## Known gaps in Ubuntu 24.04 (checked 2026-10-27 against the noble archive)

Ubuntu 26.04 closes most of these (Gamescope, ntsync, newer Mesa are in the archive); see
[ubuntu-26.04.md](ubuntu-26.04.md). The list below is for 24.04 images and installs.

- **Gamescope is not packaged in noble.** It is not in `axon-gaming-core` and will come from
  the Axon-built packages (plan Phase 6) before the Console session (Phase 5).
- **MangoHud has no i386 build in noble**, so the overlay works for 64-bit games only.
- **ntsync:** the stock noble 6.8 kernel lacks it (mainline since 6.14). `axon-gaming-tweaks` ships
  `axon-ntsync.service`, which loads the module at boot on any kernel that has it (the gaming kernel below
  does) and does nothing on the 6.8 kernel.
- **Mesa** in `noble-updates` is already 25.2.x (amd64 and i386), so no third-party Mesa PPA is used.
- `steam-installer` asks to accept the Steam license through debconf. The ISO build preseeds
  the answer; Steam shows the Steam Subscriber Agreement again on its first start.

## Game mode (AI steps aside while you play)

GameMode (`gamemoderun`, Lutris, Heroic, `axon-apex-run`) runs `axon-game-mode on` when the
first game starts and `axon-game-mode off` after the last one ends. That calls
`org.axonos.Gaming` (service `axon-gaming`, session bus), which:

| Service | On | Off |
|---|---|---|
| Brain | `SetGameModeCloud`, `Suspend`: unloads every Ollama model (`keep_alive: 0`), stops streams, refuses GPU work | `Resume`: nothing is reloaded until the next request |
| Search | `Pause`: the indexer stops after the current file | `Resume` |
| Context | `Throttle(true)`: X11 clipboard polling every 10 s instead of 2 s | `Throttle(false)` |

`org.axonos.Gaming` API: `EnterGameMode(app_id, pid) → token`, `ExitGameMode(token)`,
`ForceExit()`, properties `Active` and `ActiveGames`, signal `GameModeChanged(active, app_id)`.
It is reference counted: two games keep game mode on until both close. After the last one it
waits **10 seconds** before resuming, so a launcher restarting its game does not make the AI
unload and reload. Every 15 s it checks that each game's process (when a pid was given) still
exists, so a `kill -9` ends game mode in at most about 25 s. It always starts with game mode
**off** and resumes every service it can reach; a Brain, Search or Context that restarts during
a game is paused again.

While Brain is suspended a request for a local model fails with *"Game mode is on. AI is paused
to keep your game smooth."* Instead:

- with **Use cloud provider during games** on, requests go to the first cloud model set as a
  tier model (or `game_mode_cloud_model` in the Brain config) and your prompts leave the computer;
- **Run anyway** (`Brain.AllowCpuOnly`) allows the Speed model on the CPU only (`num_gpu: 0`,
  unloaded right after) for two minutes.

Nothing loads a model onto the GPU during game mode.

Settings (`~/.config/axon/gaming.json`, Axon Preferences → Gaming): *Pause AI during games*
(on), *Use cloud provider during games* (off), *Pause file indexing during games* (on).
In the Intent Bar: "game mode on", "game mode off", "is game mode on". A controller icon appears
in the taskbar while game mode is on; its menu lists the games and has **Exit game mode**.

```bash
axon-game-mode status     # Active + running games
axon-game-mode reset      # force game mode off
```

### Limits

- GameMode only knows about a game if the launcher uses it. Lutris does by default; for Steam
  add `gamemoderun %command%` to a game's launch options; Heroic has a GameMode toggle. Windows
  programs started with `axon-apex-run` always use it.
- GameMode's hook does not say which game started, so those games show as "A game" and are not
  tracked by pid (GameMode itself reports when the last one exits).
- GPU optimisations in `gamemode.ini` (`apply_gpu_optimisations`, `amd_performance_level`) are
  not enabled; turn them on in `~/.config/gamemode.ini` if you want them.

## Game Center and the catalog

Everything optional installs after the system is installed, never from the ISO (see the ISO
policy in `AGENTS.md`). `data/gaming/catalog.json` lists it, validated by
`data/gaming/catalog.schema.json` in CI. The `axon-gaming-catalog` package puts both in
`/usr/share/axon/gaming/`, so **a new list reaches installed systems through the normal update,
without a new ISO**. Game Center, the welcome app and the Intent Bar all read that file.

Each item has a source, a size estimate, a category and a `verified` date:

| `source.type` | Installed with |
|---|---|
| `flatpak` | `flatpak install --user` from Flathub: no password. The first app also downloads a shared runtime (about 800 MB). |
| `apt`, `axon-apt` | PackageKit (`pkcon`), which asks for authorisation with polkit |
| `component` | Other Axon services (AI models, Ollama GPU libraries). Game Center only shows them. |

`pinned` (URL + SHA-256) is allowed by the plan but not implemented: no item needs it.
`scripts/verify-catalog.py --write` checks Flatpak refs on Flathub and apt names in the Ubuntu
archive and stamps `verified`. An item that has never been checked has `verified: null`.
Re-run it on a machine that can reach Flathub whenever the list changes.

**Axon Game Center** (`axon-game-center`, app id `org.axonos.GameCenter`):

- *Discover*: the Starter Pack (Heroic, Lutris, ProtonPlus, GOverlay, Discord) with its total size,
  then every category. Emulators carry the "bring your own games" note.
- *Installed*: Open, Update and Remove (with an option to remove runtimes nothing uses).
- *Compatibility*: Proton and Wine versions found in Steam's folders, the one Axon APEX uses,
  ProtonPlus, and links to ProtonDB and Are We Anti-Cheat Yet.
- *Performance*: game mode status and the game mode switches, an FPS overlay switch
  (`MANGOHUD=1` in `~/.config/environment.d`, from the next sign-in), and GOverlay.
- *Drivers*: graphics cards, VRAM, driver in use, the NVIDIA driver `ubuntu-drivers` recommends
  (with a Secure Boot warning) and the Ollama GPU libraries.

Installs queue per backend (one Flatpak and one apt job at a time), show progress, and send a
notification when done. Offline, the buttons are disabled with "Connect to the internet to install
apps." and a failed install offers Retry. Every action is logged through `axon-game-center`.
`axon-game-center --background --install heroic lutris` installs with no window, which is how the
welcome app keeps installing after it is closed.

**Welcome app**: a *Gaming setup* step after the AI model step offers Starter Pack (default),
Streamer, Retro or nothing, a *Customize* list, an opt-in *media codecs* box, the NVIDIA driver
when one is needed (on by default) and the total download size. It installs in the background.
Offline, it skips itself with "You can install these later from Game Center."

**Intent Bar**: "install heroic", "install epic games" (aliases come from the catalog),
"install the gaming starter pack", "open game center", "update proton".

### Not done yet

- **Microsoft core fonts** are left out: the package asks for a licence click-through that
  PackageKit cannot answer without a root helper.
- **Ollama CUDA/ROCm libraries and Wine Mono/Gecko** are in the catalog as components, but nothing
  installs them yet; they need the packages planned for Phase 4. Their Install button is disabled.
- **OpenRGB** may need udev rules that a Flatpak cannot add.

## Editions and size

Two ISOs are built from the same sources (`build.sh --edition standard|nvidia`): **Standard** for AMD,
Intel and older NVIDIA cards, and **NVIDIA** for GTX 16-series and newer, with the signed 580 driver and
the 32-bit libraries Steam needs. Both stay inside the budgets in `build/size-budget.conf` (3.5 / 3.9 GB
and 4.3 / 4.7 GB); the build fails above the second number. Details, the release checklist and where
the ISOs are hosted are in [docs/building.md](building.md).

**Local AI.** The ISO has Ollama and its CPU libraries only. When the welcome app or Game Center finds a
graphics card it offers the matching GPU libraries (NVIDIA CUDA, about 1.4 GB to download; AMD ROCm, about
1 GB), installed by `install-ollama-gpu.sh` after a password prompt. The AMD option is off by default because
only recent cards are supported. See [docs/building.md](building.md#local-ai-in-the-image).

## Console mode (TV and handheld)

`axon-console` (package, `packaging/axon-console`) adds an **Axon Console** entry to the login screen. It
runs `gamescope -e -f -- steam -gamepadui`: Steam's Big Picture interface as the whole session. Game mode
is turned on for the session (`axon-game-mode on --app-id console --pid <session>`), so Brain stays
suspended and file indexing stays paused until it ends, whatever games start and stop inside it. Leaving
Steam ends Gamescope and the session and brings back the login screen; `steamos-session-select desktop`
(what Steam's "Switch to Desktop" calls) ends the session the same way. Options for a particular screen
go in `AXON_GAMESCOPE_ARGS` or `~/.config/axon-os/console.conf` (for example `-W 1920 -H 1080 -r 60`).
There is no desktop to show errors, so `~/.local/state/axon-console.log` records what the session did.

**On 24.04 images, not in the ISO yet.** (On 26.04 Gamescope is in the archive, so `axon-console` is included.) The package depends on Gamescope, which Ubuntu 24.04 does not have. The image build
leaves `axon-console` out until `apt-cache policy gamescope` finds one (plan phase 6 builds it for the Axon
repository); until then it is published to the Axon repository but not installable. The session and its helper
are tested with stub programs; nothing has run in a real login session yet.

ChimeraOS's `gamescope-session` and `gamescope-session-steam` were considered and not used: the repositories
could not be fetched from the build sandbox (so their licences were not checked), and they depend on
SteamOS-specific parts (`steamos-polkit-helpers`, Deck-only Steam flags) that could not be tested here. The Axon
session is about 50 lines. Revisit when Gamescope is packaged and real hardware is available.

## FPS overlay and shortcuts

Ubuntu 24.04's MangoHud (0.6.9.1) reads only `~/.config/MangoHud/MangoHud.conf`; it does **not** read the
`/etc/MangoHud/MangoHud.conf` the plan assumed (checked in the shipped library, 2026-10-27). So the default
layout, one line in the top-left corner with FPS, frame time and processor and graphics card load and
temperature, is copied from `/etc/skel` for new users and by Game Center when it turns the overlay on, and
never overwrites a layout the user has (GOverlay edits that one). The overlay is off until Game Center >
Performance sets `MANGOHUD=1`. **Right Shift + F12** shows or hides it in a game and **Right Shift + F11**
moves it (MangoHud's own defaults; nothing in the Axon shell uses F11 or F12). They are listed under *Gaming* in
the shortcut overlay (Super + /).

## Secure Boot and the NVIDIA driver

- **NVIDIA edition:** the driver is Ubuntu's prebuilt, signed `linux-modules-nvidia-580-open-generic`.
  Secure Boot trusts Canonical's signature already, so nothing needs enrolling.
- **Standard edition, driver from Game Center:** on the 6.8 kernel Axon ships, Game Center installs the matching
  signed `linux-modules-nvidia-…-generic` package first, so apt does not choose the DKMS version. Signed this
  way, the driver also loads with Secure Boot on. On any other kernel the driver would be built with DKMS and
  unsigned; Game Center then says so and tells the user to turn Secure Boot off in the firmware settings.
- **MOK enrollment is not implemented.** Ubuntu's DKMS flow asks for a one-time password in a terminal dialog
  and a blue enrollment screen on the next boot. That cannot be done from a background PackageKit install, and
  a half-finished enrollment leaves the driver unusable. Using Canonical's signed modules avoids the need.
  A custom kernel (plan phase 6) will need its own signing key and this decision revisited.

## Kernel and graphics stack (phase 6)

### Gaming kernel: Ubuntu's own HWE kernel

The plan asked for an Axon-built `linux-axon` with NTSYNC and sched_ext. **Not built, on purpose.** Ubuntu's
noble HWE kernel (`linux-image-generic-hwe-24.04`, 7.0.0-38 on 2026-10-27) was downloaded and its config read:
`CONFIG_NTSYNC=m`, `CONFIG_SCHED_CLASS_EXT=y`, `CONFIG_HZ_1000`, `PREEMPT_DYNAMIC` with lazy preemption, MGLRU on,
BTF on, `X86_AMD_PSTATE`. Everything the plan needed is already there, in a kernel Canonical builds, signs for
Secure Boot, and ships signed NVIDIA modules for (`linux-modules-nvidia-<branch>-generic-hwe-24.04`). Building our
own would add a signing key to manage, no benefit anyone has measured, and a package whose modules alone are
166 MB (GitHub Pages rejects files over 100 MB, so the Axon repository could not even hold it). XanMod was
compared and rejected for the same reasons (own signing key, no signed NVIDIA modules, same hosting problem).

So the "gaming kernel" is an optional install in **Game Center > Performance**. It installs the HWE kernel, and
the matching signed NVIDIA modules when an NVIDIA driver is installed (without them a restart would lose the
driver). Remove takes it out again, but not while it is the running kernel. The 6.8 kernel stays installed:

- GRUB lists it under Advanced options; `41_axon_kernel_fallback` adds an **Axon OS — previous kernel** entry.
- The boot watchdog sets `axon_try_ga` on the second boot attempt in a row, so a gaming kernel that does not
  start falls back to the 6.8 kernel on the same system, and only a third failure goes to the factory rollback
  snapshot (which already contained the 6.8 kernel). Both fallbacks need the root file system to be btrfs, like
  the watchdog itself. The generated GRUB script is tested with stub tools and `grub-script-check`; it has not
  run in a real boot.

`sched_ext` schedulers (`scx_lavd` and friends) are **not offered**: Ubuntu 24.04 to 26.04 do not package them,
so they would have to be built from upstream Rust sources. The default stays the kernel's own scheduler, as the
plan says, until a benchmark (docs/benchmarks) shows a gain that justifies maintaining that package.

### Gamescope: rebuilt for 24.04

Gamescope is in Ubuntu 25.04 and newer (multiverse) but not in 24.04. `packaging/backports/backports.list` rebuilds
version 3.16.15 and the libraries it needs that 24.04 lacks, unchanged except for the version number
(`~24.04.axon1`, so any later official Ubuntu version wins), by `scripts/build-backports.sh` in a clean 24.04
chroot with `sbuild`:

| Source | Version | Why |
|---|---|---|
| libliftoff | 0.5.0 | Gamescope needs 0.5, noble has 0.4.1 |
| vkroots | 0~202404300106 | noble's is older |
| xcb-util-errors | 1.0.1 | not in noble |
| wayland | 1.24.0 | wlroots 0.18 needs 1.23, noble has 1.22 |
| wlroots | 0.18.2 (one patch) | not in noble; the patch keeps Meson 1.3 away from `c_std=c23` |
| gamescope | 3.16.15 | the point |

The workflow `publish-apt.yml` builds them when started with **backports** ticked and puts the packages in the
Axon apt repository. The `wayland` rebuild is the one entry that replaces something the whole desktop uses; the
repository's apt pin (`/etc/apt/preferences.d/axon-os`) gives everything in it priority 100, so these libraries
are installed only when a package asks for them and never replace Ubuntu's by themselves. Once published, the
image build finds Gamescope (`axon-gaming-core` recommends it), and the Axon Console session is included.
`scripts/verify-packages.py` checks that every listed source still exists at its pinned version.

### Mesa: not rebuilt

`noble-updates` has Mesa 25.2.8 for amd64 and i386; Ubuntu 25.10 has an older 25.2.3 and only 26.04 has a newer
one (26.0), whose build needs a toolchain (LLVM) 24.04 does not have. Rebuilding it would be the largest and
riskiest package in the repository for an unmeasured gain, and the plan says to add packages only when there is
a measured benefit. Adding it later is one line in `backports.list` once `docs/benchmarks` shows a difference.

### Benchmarks

`scripts/bench-mangohud.py` summarises MangoHud logs (average and 1% low) and compares two result folders, with
three runs per game and a warning for noisy ones. The method, the five-game list and the decision rule are in
[docs/benchmarks/README.md](benchmarks/README.md). **No results have been recorded:** they need real AMD and
NVIDIA machines with the games installed.
