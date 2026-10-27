# Gaming on Axon OS

Status: Phase 1 of `docs/gaming-masterplan.md` (gaming core and system tweaks).
Audit and background: `docs/gaming-audit.md`.

## What ships in the ISO

Two small packages, built from `packaging/` by `packaging/build-pkg.sh` and
published to the Axon apt repo next to `axon-os`, so `axon-update` keeps them current:

| Package | What it does |
|---|---|
| `axon-gaming-core` | Metapackage: `steam-installer` (the Steam runtime downloads on first start), `steam-devices` (controller udev rules), `gamemode`, `mangohud`, `vulkan-tools`, the Vulkan loader and Mesa Vulkan driver, and the 32-bit Mesa/GL/Vulkan/GameMode libraries 32-bit Proton games need. |
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
