# win-sandbox-runner

A transparent, tiered sandbox for running Windows executables via Wine.

Double-click any `.exe` and it runs inside an isolation tier chosen from a policy
file, with no terminal step. Each binary is hashed (SHA-256) and matched against
`rules.json` and a built-in app database to decide how much isolation it gets.

**Linux is the primary platform**, using Landlock and bubblewrap. macOS
Tier 1/2 use Apple's Seatbelt sandbox, with different guarantees and unresolved
Wine compatibility testing — see [Platform support](#platform-support).
This is a work in progress, not VM-level containment for arbitrary malware.
See [the upgrade roadmap](UPGRADE_PLAN.md) for the release gates and scope.

## Architecture

There are two independent launch paths on Linux, and both are needed. They are
not alternatives to each other.

**Path A — file manager double-click (the primary experience)**

```
Double-click .exe in the file manager
    │
    ▼
Desktop resolves the file type and launches the registered handler
  Linux: apex-win.desktop  (MIME: application/vnd.microsoft.portable-executable)
  macOS: APEX-WIN.app      (UTI:  com.microsoft.windows-executable)
    │
    ▼
win-sandbox-runner --exe <path>   →  hash → rules → tier → Wine
```

A file manager never `exec()`s the file, so `binfmt_misc` is not involved in
this path at all. Without the handler installed, double-click does nothing
useful no matter how correct the daemon is.

**Path B — terminal / script execution (Linux only)**

```
Run ./game.exe directly
    │
    ▼
Kernel sees the MZ header → binfmt_misc → win-sandbox-runner
    │
    ▼
launch.sock (exe fd + SO_PEERCRED uid) → hash → rules → tier → Wine
```

macOS has no `binfmt_misc` equivalent, so Path B does not exist there.

Both paths converge on the same dispatch:

```
    ├─ Hash binary (SHA-256)
    ├─ Lookup rules.json + built-in app database
    ├─ Unknown app: heuristics; optional interactive TTY prompt (not daemon)
    │
    ├─ Tier 0: Direct wine exec (sanitized env only)
    ├─ Tier 1: Landlock filesystem restrictions / macOS Seatbelt
    ├─ Tier 2: Bubblewrap namespaces / macOS Seatbelt with network denied
    └─ Tier 3: RAM-backed Wine-prefix overlay (Linux; limitations below)
```

### Which path runs as whom

Path A runs **entirely as the invoking user**; the root daemon is never
involved, so Windows binaries never execute as root. Path B is the only one
that reaches the root daemon, which then drops to the calling user's UID for
the actual Wine process.

## Platform support

| | Linux | macOS |
|---|---|---|
| Run `.exe` via Wine | yes | yes |
| Double-click handler | yes (`apex-win.desktop`) | yes (`APEX-WIN.app`) |
| Auto-intercept bare `./game.exe`/`game.exe` in Terminal | yes (`binfmt_misc`, no opt-in) | opt-in shell hook (`apex-win-shell-hook.sh`) |
| Tier 0 (no isolation) | yes | yes |
| Tier 1 (filesystem isolation) | yes (Landlock) | yes (Seatbelt) |
| Tier 2 | yes (bubblewrap; currently shares host network) | yes (Seatbelt; network denied) |
| Tier 3 (prefix overlay) | conditional; see limitations below | **no** |
| Background daemon | systemd, runs as root | launchd LaunchAgent, per-user |
| GUI (`win-sandbox-gui`) | yes | no (GTK4/libadwaita); a TTY prompt covers the same first-launch decision on both platforms instead |

**Tier 1/2 on macOS use Apple's Seatbelt sandbox (`sandbox-exec` + a
generated `.sb` profile), not Landlock/bubblewrap.** It is a real
kernel-enforced `(deny default)` boundary. CI contains tests for allowed and
blocked writes and (Tier 2 only) outbound network denial; these are not a
verified end-to-end Wine compatibility result. File reads remain broadly
allowed; writes are allowed in the Wine prefix, launch scratch directory and
selected devices. It is *not* equivalent to
Landlock/bubblewrap on every axis: no mount or PID namespace, no resource
limits, same visible process table. `--status` reports which backend is in
use. Tier 3 has no macOS equivalent (no unprivileged ephemeral overlay
filesystem) and stays refused: an explicit `--tier 3` request errors rather
than being silently served as something weaker; a heuristic suggestion
degrades to Tier 0 with a loud warning instead. See
`crates/win-sandbox-runner/src/seatbelt.rs` for the profile design and
`--tier 0`/`1`/`2` for what's actually available.

There is no macOS kernel equivalent of `binfmt_misc`: `execve()` there only
understands Mach-O and `#!` scripts, with no user-registerable table for
other formats, so a bare `./game.exe` typed in Terminal cannot be
transparently intercepted the way it is on Linux without a kext (deprecated,
blocked by default on Apple Silicon). `apex-win-shell-hook.sh` is the closest
userspace substitute: an opt-in zsh/bash hook (source it from `~/.zshrc`)
that catches `.exe` invocations before the shell tries to run them, verified
directly against real interactive-shell sessions. It only affects the shell
that sources it, not other programs or GUI launchers, and is not presented
as equivalent to kernel-level interception.

## Tiers

| Tier | Mechanism | Filesystem | Network | Platform |
|------|-----------|------------|---------|----------|
| 0 | Direct Wine | Host access | Host | Linux, macOS |
| 1 | Landlock / Seatbelt | Allowlisted access (Linux); broad reads, scoped writes (macOS) | Not a no-network tier | Linux, macOS |
| 2 | Bubblewrap / Seatbelt | Namespaces with host binds (Linux); scoped writes (macOS) | Currently host-shared (Linux); denied (macOS) | Linux, macOS |
| 3 | OverlayFS + direct Wine | RAM-backed prefix overlay, not whole-host containment | Host; optional bridge setup | Linux only |

Performance relative to direct Wine has not been established by a reproducible
benchmark suite; "native" or "near-native" is not a measured guarantee.

### Tier 0 — Direct
Runs Wine directly with a sanitized environment. All secrets, proxy configs, and sensitive variables are stripped. Suitable for trusted binaries.

### Tier 1 — Landlock (Linux) / Seatbelt (macOS)
Linux: uses Landlock LSM to restrict filesystem access to an allowlisted set of paths. Network isolation is partial (Landlock cannot block all TCP — additive-only model). macOS: uses Seatbelt (`sandbox-exec`) with a generated `(deny default)` profile — filesystem writes are scoped to the Wine prefix, network is allowed (matching Linux Tier 1's own inability to fully block it). Recommended for moderately trusted software.

### Tier 2 — Bubblewrap (Linux) / Seatbelt, no network (macOS)
Linux: namespace isolation via `bwrap` (not a verified no-network boundary).
The binary runs in its own mount/PID/IPC namespace with:
- tmpfs root, isolated /home and /tmp
- Read-only system directories (/usr, /lib, /etc)
- GPU passthrough (NVIDIA/AMD detection)
- Audio socket binding (PipeWire/PulseAudio)
- Display forwarding (X11/Wayland)
- TAP bridge setup when policy requests it

**Current Linux limitation:** `tier2.rs` passes `--share-net` unconditionally,
so `network=false` does not currently deny host networking. The executable's
parent directory is bound read-write, and configured prefix `drive_c` writes
persist. These gaps must be fixed and tested before claiming strict containment.

macOS: the same Seatbelt profile as Tier 1, but network is denied outright — Seatbelt can block this completely (unlike Landlock), so this axis is strictly more capable than Linux's own Tier 1. No mount/PID namespace or GPU/audio/display passthrough config the way Linux Tier 2 has (Wine talks to the host WindowServer directly); no TAP-bridge network mode yet, so Tier 2 on macOS is filesystem isolation + no network only.

### Tier 3 — OverlayFS Ephemeral (Linux only)
The current implementation mounts an OverlayFS over the resolved Wine prefix,
with upper/work directories under `/dev/shm`, then launches Wine directly. It
**does not compose Tier 2's bubblewrap namespaces with the overlay**. Writes
outside that prefix are not made disposable, and process-tree/cleanup behavior
still needs end-to-end verification. It is not a safe claim that all filesystem
changes disappear, or that this contains malware, DRM or kernel anti-cheat.

An **explicit** Tier 3 request (`--tier 3` or an untrusted hash rule pinned to
Tier 3) must be refused when unavailable, including a runtime overlay privilege
failure; only heuristic selections may warn and fall back to Tier 2. This is the
T2 end-state contract; [HANDOFF.md](HANDOFF.md) records verification status.
On macOS explicit Tier 3 is refused; a heuristic suggestion warns and uses Tier 0.

The intended T2 capability check requires bubblewrap >= 0.10 and advertised
`--overlay` support (rather than version alone), but the execution backend still
uses `mount(8)`.
Installing newer bubblewrap alone does **not** implement unprivileged Tier 3.
A successful capability probe is not proof that an overlay launch will work.

## Installation (Linux)

### From a clone (recommended)

```bash
git clone https://github.com/Gaming-RF/APEX-WIN.git
cd APEX-WIN
sudo ./scripts/install.sh
```

This script:
- Checks for required tools (cargo, wine, bwrap) and refuses early if missing
- Builds the workspace and the C components
- Installs binaries, config, the systemd unit and the double-click MIME handler
- Registers the binfmt_misc handler so bare `./app.exe` also works

Requires root, because binfmt_misc registration and `/etc` writes need it.

### .deb package (Ubuntu/Zorin/Debian)

```bash
# Build the .deb
make deb

# Install it
sudo dpkg -i target/deb/win-sandbox-runner_0.1.0_amd64.deb
sudo apt-get install -f  # fix any missing deps
```

### From source (any distro)

```bash
# Install Rust: https://rustup.rs
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Clone and build
git clone https://github.com/Gaming-RF/APEX-WIN.git
cd APEX-WIN
make cargo-release

# Install (requires root for binfmt_misc)
sudo make cargo-install
```

### cargo install (Rust only, no C components)

```bash
cargo install --path crates/win-sandbox-runner
cargo install --path crates/win-sandbox-gui
```

### Uninstall

```bash
sudo make cargo-uninstall
# or
sudo win-sandbox-uninstall  # if installed via install.sh
```

## Installation (macOS)

Requires macOS 11+ (Intel or Apple Silicon), Rust, and a macOS Wine build.

```bash
brew install --cask wine-stable     # or any Wine for macOS
git clone https://github.com/Gaming-RF/APEX-WIN.git
cd APEX-WIN
./scripts/install-macos.sh
```

Do **not** run it with `sudo`; the script refuses. It writes per-user files
(`~/.config/win-sandbox`, `~/Library/LaunchAgents`) that must belong to you,
and prompts for `sudo` only for the individual steps whose target directory
isn't writable (typically `/usr/local/bin` and `/Applications`; Homebrew's
`/opt/homebrew/bin` usually needs no elevation).

It installs `win-sandbox-runner`, copies `APEX-WIN.app` to `/Applications`,
registers it with Launch Services, and writes a launchd LaunchAgent.

To run an `.exe`, right-click it in Finder and pick **Open With → APEX-WIN**,
or set APEX-WIN as the default for `.exe` in Get Info. The app bundle is
registered as an *alternate* handler, so it never silently takes over the
file type on install.

The background daemon is optional on macOS and **not needed for double-click**;
it only pre-loads rules for repeated Terminal use:

```bash
launchctl load   ~/Library/LaunchAgents/com.apex-win.daemon.plist
launchctl unload ~/Library/LaunchAgents/com.apex-win.daemon.plist
```

Not done yet on macOS: code signing / notarization (Gatekeeper will warn on a
downloaded, non-locally-built bundle), an app icon, and universal binaries
(`cargo build --release` produces only the host architecture).

## Quick Start

### Background Mode (recommended)

After installation, enable the daemon for seamless .exe execution:

```bash
# Start the daemon (registers binfmt_misc + loads app database)
sudo systemctl enable --now win-sandbox-runner

# Now just run any .exe — it's intercepted automatically
/path/to/program.exe

# Check daemon status
win-sandbox-runner --status

# Reload rules after editing
win-sandbox-runner --reload

# Stop the daemon
sudo systemctl stop win-sandbox-runner
```

The daemon:
- Registers a **binfmt_misc** handler for .exe files (MZ header detection)
- Pre-loads the app database (35+ profiles) and rules into memory
- Applies network optimizations on startup (if configured)
- Exposes an IPC socket for runtime control (`--status`, `--reload`, `--stop`)
- Runs as a systemd service with auto-restart on failure

### Manual Mode

```bash
# Run an app (auto-detected tier)
win-sandbox-runner --exe MyApp.exe

# Trust an app (no sandboxing, remembered for future runs; per-user, by hash).
# App-database name matches never grant trust on their own.
win-sandbox-runner --exe Fusion360.exe --trust

# Force a specific tier
win-sandbox-runner --exe untrusted.exe --tier 3

# See what would happen
win-sandbox-runner --exe app.exe --dry-run

# Launch the GUI
win-sandbox-gui

# Optimize network for gaming (applied automatically for game profiles)
sudo win-sandbox-runner --optimize-net

# Clean up network optimizations
sudo win-sandbox-runner --cleanup-net
```

### Tier selection, trust and preview

Selection precedence is: current `--tier` flag, exact hash in `rules.json`,
app-database name match, then first-launch heuristics. A matched entry with
`trusted=true` defaults to **Tier 0**, regardless of its stored tier; it does
**not** override `--tier` on the current launch. For example, a trusted app
launched with `--tier 2` takes tier validation rather than the trusted Tier 0
shortcut; an invalid tier is rejected and `--tier 3` follows fail-secure checks.
Trust is a default, not permission to ignore an explicit launch request.

CLI tiers and hash rules carry explicit provenance; appdb and wizard suggestions
are heuristic. This distinction controls Tier 3 refusal versus warned fallback.
There is still a separate NVIDIA Tier 2-to-1 downgrade heuristic on real launches;
explicit-tier precedence is not a promise that every tier has fail-secure runtime
handling yet. Network permission is resolved separately from the hash rule or
`defaults.network_default`, not the matched appdb profile's network field.

`--exe app.exe --dry-run` is intended to be a **side-effect-free launch preview**:
it reports the matched entry, resolved tier and explicit/heuristic provenance,
network permission and would-be `WINEPREFIX`, without creating prefixes,
initializing Wine, installing DXVK/winetricks or prompting/saving wizard choices.
Unavailable explicit Tier 3 is reported as a would-refuse outcome (preview can
exit successfully); no Wine application is launched. Unknown apps use a
noninteractive suggestion. Read-only hashing and capability probes still run.
See [HANDOFF.md](HANDOFF.md) for which T1/T2 items have landed and been verified,
including pre-dispatch `--trust`/network-tuning guards. This preview contract does
not apply to separate daemon or network-management commands.

The built-in app database (`config/appdb.json`) contains 30+ known app profiles with recommended settings for Fusion 360, Steam, Office, Unity, Unreal, popular games, and more.

## Networking

On Linux, Tiers 2 and 3 contain optional TAP bridge plumbing with the following
intended architecture. This is not a verified network-isolation guarantee:
Tier 2 currently shares host networking, and Tier 3 launches Wine directly.

```
Wine process (in bwrap)
    │
    ├─ sys_netmp.dll (Wine DLL, MinGW cross-compiled)
    │       │  NDIS IOCTLs → named pipe IPC
    │       ▼
    └─ win-tap-bridge (Linux daemon)
            │  Unix socket ↔ /dev/net/tun
            ▼
        winrunner-tap0 (TAP device)
            │
        Host networking
```

The bridge daemon allocates a TAP device and bridges Ethernet frames between Wine (via named pipe) and the host network. An optional eBPF TC classifier marks UDP packets with DSCP EF for QoS.

### Network Optimizer (Gaming)

Replaces Windows tools like Gear Up Booster with native Linux network tuning. Automatically applied when launching game profiles from the app database.

```bash
# Apply all optimizations (requires root)
sudo win-sandbox-runner --optimize-net

# Remove all applied optimizations
sudo win-sandbox-runner --cleanup-net
```

What it does:
- **BBR congestion control** — Google's algorithm, reduces bufferbloat
- **fq_codel SQM** — 5ms target latency, smart packet scheduling
- **Socket buffers** — 16MB for high-PPS game traffic, 50μs busy poll
- **DSCP marking** — marks game packets (Steam, Xbox, PSN, Blizzard ports) as priority
- **TCP tweaks** — fast open, no slow start after idle, MTU probing

Configure via `~/.config/win-sandbox/net-optimizer.json`:

```json
{
  "bbr": true,
  "sqm": true,
  "socket_buffers": true,
  "dscp_marking": true,
  "tcp_tweaks": true,
  "game_ports": [27015, 3478, 3074, 6112],
  "download_mbps": 100,
  "upload_mbps": 50
}
```

Set `download_mbps` / `upload_mbps` to your actual connection speed for optimal SQM shaping. Leave at 0 for auto (1 Gbit/s default).

## Rules Configuration

Rules are stored in `config/rules.json` (system) or `~/.config/win-sandbox/rules.json` (user).

Tier values are strings (`"tier0"`..`"tier3"`), not integers, and `hash` must be
exactly 64 hex characters or the file is rejected at load time.

```json
{
  "version": 1,
  "entries": [
    {
      "hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "name": "notepad.exe",
      "tier": "tier0",
      "network": false,
      "gpu": false,
      "trusted": false
    }
  ],
  "defaults": {
    "unmapped_tier": "tier0",
    "untrusted_path_tier": "tier2",
    "network_default": false,
    "gpu_default": false
  }
}
```

Check a rules file without running anything:

```bash
win-sandbox-runner --exe /path/to/app.exe --rules ./rules.json --dry-run
```

## Dependencies

`scripts/install.sh` checks for the essential ones and stops early if any are
missing, but installing them up front avoids a failed run.

```bash
# Ubuntu / Zorin / Debian
sudo apt install wine bubblewrap libgtk-4-dev libadwaita-1-dev \
    gcc-mingw-w64-x86-64 clang libbpf-dev linux-headers-generic

# Rust (any distro)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
rustup default stable
```

`gcc-mingw-w64-x86-64`, `clang` and `libbpf-dev` are only needed for the
optional C components (the Wine networking DLL and the eBPF DSCP filter). The
install script skips those and warns if MinGW is absent, rather than failing.

Optional services, if you want isolated networking for Tier 2/3:

```bash
sudo systemctl start win-tap-bridge    # TAP bridge daemon
sudo systemctl start win_tap_filter    # eBPF DSCP classifier
```
## Project Structure

```
.
├── Cargo.toml                   # Workspace root
├── Makefile                     # C component builds
├── HANDOFF.md                   # Architecture, bug history, gotchas
├── config/
│   ├── apparmor/                # AppArmor profile
│   ├── rules.json               # Default sandbox rules
│   ├── rules.schema.json        # JSON Schema for rules
│   └── win-sandbox-runner.conf  # Runtime config
├── crates/
│   ├── win-sandbox-runner/      # Main CLI binary (Rust)
│   ├── win-sandbox-gui/         # GTK4 dialog GUI (Rust)
│   └── win-sandbox-common/      # Shared types & IPC messages
├── csrc/
│   ├── win-tap-bridge/          # TAP daemon (C, native Linux)
│   ├── sys_netmp/               # Wine DLL (C, MinGW cross-compiled)
│   └── win_tap_filter/          # eBPF TC classifier (C + clang)
├── macos/                       # macOS-only assets
│   ├── APEX-WIN.app/            # Launch Services handler (.exe double-click)
│   └── com.apex-win.daemon.plist # launchd LaunchAgent (per-user)
├── scripts/
│   ├── install.sh               # Linux build & install
│   ├── install-macos.sh         # macOS build & install
│   ├── uninstall.sh
│   ├── build-deb.sh             # .deb packaging
│   ├── register-binfmt.sh       # Single source of truth for the binfmt line
│   ├── setup-tap.sh
│   ├── apex-win.desktop         # MIME handler — makes double-click work
│   └── win-sandbox-runner.service
├── systemd/
│   ├── win-tap-bridge.service
│   └── win_tap_filter.service
└── tests/
    └── integration/             # End-to-end tests
```

## Security Model

### Threat Model
- **Trusted**: The Linux host kernel, Wine runtime, win-sandbox-runner binary
- **Untrusted**: Windows executables launched through Wine
- **Out of scope**: VM-level isolation, cgroups, seccomp, anti-malware scanning

### Hardening
- **Environment sanitization**: Allowlisted env vars only; secrets, proxy configs, and DBUS_SESSION_BUS_ADDRESS are stripped
- **Recursion guard**: `WIN_SANDBOX_ACTIVE` env var prevents re-entry
- **Landlock**: Kernel-enforced filesystem restrictions (additive-only, no escalation)
- **Bubblewrap**: Mount/PID/IPC/UTS namespaces, with selected host binds; currently shares host networking
- **OverlayFS**: RAM-backed upper layer for the Wine prefix, not all host writes
- **Isolation is the tiers' job, not the unit file's**: the systemd unit
  deliberately does *not* set `ProtectHome`, `ProtectSystem` or
  `NoNewPrivileges=yes`. systemd applies those to the whole cgroup, so they
  also apply to the Wine process the daemon spawns, and `ProtectHome` breaks
  Wine outright (`unable to create wineserver tmpdir`, because the per-app
  prefix lives under `~/.local/share/win-sandbox`). Confining the *Windows
  binary* is what Landlock/bubblewrap/OverlayFS do. See the comments in
  `scripts/win-sandbox-runner.service` for the verification behind this.

### Known Limitations
- Landlock cannot block all TCP connections (additive-only model)
- TAP bridge requires `CAP_NET_ADMIN` on the bridge daemon
- eBPF TC filter requires kernel 5.8+ with BTF
- Tier 3's current `mount(8)` backend requires mount privileges; the version/flag
  capability probe does not establish that those privileges are available.
  Explicit requests must refuse rather than silently become Tier 2, including
  runtime privilege failures (T2 hardening). Actual `bwrap --overlay` execution
  and verified disposable sessions are still roadmap work.
- **macOS has no Tier 3** (no unprivileged ephemeral overlay filesystem); Tier
  1/2 use Seatbelt, weaker than Landlock/bubblewrap on axes it doesn't cover
  (no mount/PID namespace, no resource limits) — see
  [Platform support](#platform-support)

## Development

Builds with the current locked dependencies require **Rust 1.96.x**. System
Cargo 1.75 cannot parse those dependencies; changing the lockfile format is not
a fix. Use rustup's toolchain for both `cargo` and `rustc`.

On this Linux development checkout, the working toolchain is:

```bash
export PATH="/home/gamingrf/.rustup/toolchains/stable-x86_64-unknown-linux-gnu/bin:$PATH"
rustc --version   # verified here: 1.96.1
cargo --version   # verified here: 1.96.1
```

Elsewhere, install/select Rust 1.96.x through rustup and verify both commands
before building. Ensure elevated installer steps do not accidentally select the
old system Cargo; prefer building as the user and elevating installation only.
The CI workflow currently selects floating `stable`, not a pinned 1.96 toolchain.

```bash
# Build
cargo build --workspace

# Test
cargo test --workspace

# Lint
cargo clippy --workspace -- -D warnings

# Build C components
make all
```

CI runs build/test/clippy/fmt on Linux and macOS, plus a skip-safe
integration-script job. Note `--all-targets` on clippy: several real bugs in
this project were only ever visible in test code.

```bash
# What CI runs (Linux)
cargo clippy --workspace --all-targets -- -D warnings
cargo fmt --all --check

# What CI runs (macOS) — by package, since win-sandbox-gui needs GTK4
cargo test  -p win-sandbox-runner -p win-sandbox-common
cargo clippy -p win-sandbox-runner -p win-sandbox-common --all-targets -- -D warnings

# Cross-check the macOS build from a Linux box (catches cfg mistakes early)
rustup target add x86_64-apple-darwin
cargo clippy --target x86_64-apple-darwin -p win-sandbox-runner --all-targets -- -D warnings
```

## Target Environment

**Linux (primary)**
- **OS**: Zorin OS 18.1 (Ubuntu 24.04 base)
- **Kernel**: 7.0+ (Landlock, eBPF)
- **Rust**: 1.96.x (current locked dependencies)
- **GTK4**: 4.14 / libadwaita 1.5
- **Wine**: 9.0+ (10.0 tested)
- **bubblewrap**: >= 0.10 plus `--overlay` feature detection for Tier 3 preflight;
  actual unprivileged overlay execution is not yet implemented

**macOS (CLI core only, Tier 1/2 via Seatbelt, no Tier 3)**
- **OS**: macOS 11+ (Intel and Apple Silicon)
- **Rust**: 1.96.x (current locked dependencies)
- **Wine**: any macOS build on `PATH`

## License

GPL-3.0-or-later
