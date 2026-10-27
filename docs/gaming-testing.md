# Gaming test checklist

Automated tests cover the logic (`pytest tests/`). Games need real hardware, so each release is
checked by hand with this list. Copy it into the release pull request and tick it off.

## Hardware matrix

| Machine | Edition | Must pass |
|---|---|---|
| AMD GPU desktop | Standard | Install, Steam, a Proton game, game mode frees VRAM, ROCm libraries install from Game Center |
| NVIDIA RTX desktop | NVIDIA | Install with Secure Boot on, Steam, a Proton game, game mode frees VRAM, CUDA libraries install, `nvidia-smi` works |
| Intel iGPU laptop | Standard | Install, Steam, a light game, battery and power behaviour |
| Hybrid laptop (Intel or AMD + NVIDIA) | NVIDIA | Games use the NVIDIA card (`switcherooctl`, `nvidia-smi` shows the game) |
| Older NVIDIA (GTX 10-series) | Standard | Install, Game Center > Drivers offers the NVIDIA driver, Secure Boot warning shown if it is on |

## Games per release

- [ ] one native Linux game
- [ ] one Proton DX11 game
- [ ] one Proton DX12 game
- [ ] one Heroic (Epic or GOG) game
- [ ] one Windows `.exe` through Axon APEX
- [ ] one emulator

## Every machine

- [ ] The ISO boots to the live desktop; the installer finishes; the first boot reaches the welcome app.
- [ ] Welcome app > Gaming setup: the Starter Pack installs with no terminal.
- [ ] Game Center: Discover, Installed, Compatibility, Performance and Drivers open without errors.
- [ ] Launch a game with `gamemoderun`: the taskbar controller icon appears, `ollama ps` is empty within 5 s, the AI answers "Game mode is on…" with a **Run anyway** button. Close the game: it is back to normal within about 15 s.
- [ ] `kill -9` a game: game mode ends within about 25 s.
- [ ] `sysctl vm.max_map_count` is 2147483642; `ulimit -Hn` is at least 1048576.
- [ ] Offline: Game Center says to connect to the internet and nothing hangs.

## Size

- [ ] The Build ISO run summary shows both editions under their limits, and every package that grew by more than 50 MB is explained in its pull request.
