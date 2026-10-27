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

## Console session (once Gamescope is packaged)

- [ ] "Axon Console" appears on the login screen and starts Steam Big Picture; the controller works.
- [ ] `ollama ps` stays empty and the AI answers "Game mode is on…" for the whole session.
- [ ] Steam > Power > Exit (or Switch to Desktop) returns to the login screen; game mode is off again afterwards.
- [ ] `~/.local/state/axon-console.log` shows the session starting and ending without errors.

## Gaming kernel

- [ ] Game Center > Performance > Kernel > Install; restart; the boot menu starts the newer kernel (`uname -r` shows 7.x or newer).
- [ ] `ls /dev/ntsync` exists and `systemctl status axon-ntsync` is active; a Proton game starts.
- [ ] NVIDIA machine: `nvidia-smi` still works on the new kernel (the signed HWE modules came with it).
- [ ] Boot menu > Advanced options lists the 6.8 kernel, and **Axon OS — previous kernel** is there.
- [ ] Simulate a broken kernel (add `init=/nonexistent` to the newest entry, boot it twice): the third start comes up on the 6.8 kernel, not the factory rollback.
- [ ] Remove the gaming kernel while running the 6.8 kernel: it goes away. Try it while running the gaming kernel: Game Center asks for a restart first.

## Secure Boot

- [ ] NVIDIA edition with Secure Boot on: the desktop starts on the NVIDIA driver (`nvidia-smi` works), no key enrollment screen.
- [ ] Standard edition, GTX 10-series, Secure Boot on: Game Center > Drivers installs the driver, and after a restart `nvidia-smi` works.
- [ ] Game Center > Performance: turn the FPS overlay on, sign in again, start a game: the overlay shows, Right Shift + F12 hides it.

## Size

- [ ] The Build ISO run summary shows both editions under their limits, and every package that grew by more than 50 MB is explained in its pull request.
