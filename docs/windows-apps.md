# Running Windows programs

Axon OS runs Windows programs (`.exe`, `.msi`) with Wine through
[APEX-WIN](https://github.com/Gaming-RF/APEX-WIN), vendored in `apex-win/`.

## Using it

- **Double-click** any `.exe` or `.msi` in Files. It opens like a native app.
- **From a terminal**, run it directly: `./setup.exe` or `~/Downloads/game.exe --windowed`.
  The kernel recognises the Windows `MZ` header (`binfmt_misc`) and hands the
  file to APEX-WIN, so no `wine` prefix is needed.
- Or call the launcher yourself: `axon-apex-run program.exe [args...]`.

Programs run **without a sandbox**: APEX-WIN Tier 0 runs Wine directly, so a
program can read and write your files, use the network, GPU, audio, gamepads and
USB devices, the same as a native Linux app. They always run as your user,
never as root (`axon-apex-run` refuses to run as root).

Only run programs you trust. To sandbox one launch, pick a stricter APEX-WIN tier:

```bash
AXON_APEX_TIER=2 axon-apex-run untrusted.exe   # namespaces, isolated home
AXON_APEX_TIER=1 axon-apex-run tool.exe        # Landlock filesystem allowlist
```

## Where things live

| What | Where |
|---|---|
| Wine prefix per program | `~/.local/share/win-sandbox/prefixes/<sha256>/prefix` |
| Program installed by an installer | runs in its installer's prefix (Axon patch) |
| Rules and app database | `/etc/win-sandbox-runner/` (override in `~/.config/win-sandbox/`) |
| Launcher | `/usr/bin/axon-apex-run` |
| Kernel rule | `/usr/lib/binfmt.d/axon-apex-win.conf` |

The first launch of a program creates its prefix (`wineboot`) and, for known
apps in the app database, installs DXVK or winetricks components it needs, so
it takes longer than later launches.

## Limits

- Wine runs Windows user-mode programs. Kernel-mode drivers do not run, so
  games whose anti-cheat needs a Windows driver (Vanguard, some EAC/BattlEye
  setups) will not start.
- The experimental `kernel/axon-winabi` module is separate and not used by APEX-WIN.

## Troubleshooting

```bash
# Is the kernel rule loaded?
cat /proc/sys/fs/binfmt_misc/APEX-WIN
sudo systemctl restart systemd-binfmt     # reload it

# Which app opens .exe files?
xdg-mime query default application/vnd.microsoft.portable-executable   # axon-apex-win.desktop

# See what APEX-WIN would do, without running anything
win-sandbox-runner --dry-run --tier 0 --no-gui --exe program.exe
```

## Building

`build/build.sh` runs `scripts/build-apex-win.sh` on the build host, which needs
a Rust toolchain (`cargo`). If it is missing, the ISO still builds and `.exe` files
open with plain Wine. To update APEX-WIN, see `apex-win/AXON-VENDOR.md`.
