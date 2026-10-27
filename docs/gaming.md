# Gaming on Axon OS

Status: Phases 1 (gaming core and system tweaks) and 2 (game mode) of `docs/gaming-masterplan.md`.
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

- **Gamescope is not packaged in noble.** It is not in `axon-gaming-core` and will come from
  the Axon-built packages (plan Phase 6) before the Console session (Phase 5).
- **MangoHud has no i386 build in noble**, so the overlay works for 64-bit games only.
- **ntsync:** the stock noble 6.8 kernel lacks it (mainline since 6.14). `axon-gaming-tweaks`
  adds the module to `modules-load.d` only when `modinfo ntsync` succeeds at install time.
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
