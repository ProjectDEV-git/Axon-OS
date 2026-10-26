# APEX-WIN Handoff Document

**Last updated**: 2026-09-25 — launch socket with peer credentials, daemon fs-identity model, overlay lifecycle, csrc hardening
**Repository**: https://github.com/Gaming-RF/APEX-WIN
**Branch**: main (base HEAD `15c0e49`; this session's work remains uncommitted)

## 2026-09-25 session — residual security items closed

All six residuals from the 2026-09-17 review are fixed (see "How the Daemon
Works" below for the design): FIFO replaced by a peer-credentialed launch
socket carrying the exe fd; overlay registry + RAII + startup sweep (no more
single `static mut`); display/audio/runtime vars come from the validated
session env instead of root's process env; hash-then-exec identity check;
app-database name matches can no longer grant trust; `csrc/` audited.

Also fixed while there: root `chown_recursive` symlink escalation, tiers
binding the wrong WINEPREFIX (process env instead of the resolved per-app
prefix), raw client env merged over the sanitized Tier 0 env, binfmt `C`
flag (setuid .exe credential gain), `/dev/shm` and `/tmp` squatting,
IPC `trust` cross-user pollution, unbounded IPC reads, root-run Xephyr
without DISPLAY and Xvfb with `-ac`, missing X11 socket inside bwrap. In
`csrc/`: TAP session hijack by the last connecting user, stalled-client
freeze, SIGPIPE crash, TAP busy loop, DLL framing desync, IPv4 checksum not
updated after DSCP rewrite, and service units that could not start.

Verified: 177 Rust tests, clippy `-D warnings`, fmt; bridge adversarial
tests under ASan/UBSan (`make -C csrc/win-tap-bridge test`); eBPF object
compiles (helper calls emitted); DLL sources syntax-checked against Wine's
headers. **Not verified here** (no root/mingw/libbpf on the dev box): a live
root daemon end to end, a real TAP session, loading the eBPF program into
the verifier, and a MinGW build of sys_netmp.dll.

Known residuals: the TAP bridge is single-session (a second user is refused
while one is attached, not multiplexed); `trust` via IPC is root-only by
design; Tier 3's mount is still `mount(8)` as root.

## 2026-09-17 session — current status and verification boundaries

This section supersedes conflicting historical statements below. See
[UPGRADE_PLAN.md](UPGRADE_PLAN.md) for M0 baseline plus six implementation
milestones, estimates, dependencies and release gates.

- **Trust/tier precedence — code-complete and locally unit-verified (not CI).**
  Source inspection of `dispatch.rs::resolve_tier` and `execute` confirms that a
  trusted `rules.json` or app-database entry defaults to Tier 0 only. A current
  `--tier` flag wins, is parsed/validated, and follows the explicit Tier 3 gate.
  Three new unit tests cover trusted rules, CLI override and trusted appdb
  provenance. An appdb match is heuristic; a hash rule or CLI tier is explicit.
- **T1 dry-run — documented-intent until final test evidence is recorded.**
  The launch-preview contract is: report the matched entry, tier and provenance,
  network permission and would-be `WINEPREFIX`, without creating a prefix,
  running Wine initialization/DXVK/winetricks, prompting or saving wizard
  decisions. T1 also makes untrusted-path matching component-aware (`/tmp/x.exe`
  matches; `/tmpfoo/x.exe` does not). At initial inspection prefix setup preceded
  dry-run handling. `wizard.rs` itself returns a decision without saving it.
  Separately, `main.rs` saved `--trust` and auto-applied game network tuning before
  dispatch; the lead was notified that these need gating for a full CLI promise.
  Standalone management flags are not launch previews.
- **T2 explicit Tier 3 refusal — documented-intent until final test evidence.**
  Explicit requests must refuse both at capability preflight and on runtime
  overlay privilege failure. Heuristic requests may retain a loud Tier 2
  fallback. The baseline had a preflight gate but still downgraded explicit
  requests at the runtime mount failure; do not mistake that for fail-secure.
- **T2 probe alignment — documented-intent until final test evidence.**
  Require both bubblewrap >= 0.10 and advertised `--overlay` support rather than
  version alone. This is a feature-presence check, not a successful mount test.
  The backend still calls `mount(8)` and direct Wine, not `bwrap --overlay`;
  unprivileged disposable containment remains roadmap work.
- **Docs — source-reviewed, not a runtime isolation certification.** README now
  distinguishes Linux/macOS mechanisms, host-network sharing in Linux Tier 2,
  prefix-only overlay semantics, and the Rust 1.96.x build requirement. No Rust
  or config files are owned by the documentation task; `config/appdb.json` is a
  pre-existing unrelated change and must remain untouched.

### Verification ledger

No fresh remote CI run or macOS Wine launch is claimed for this uncommitted
session. Existing workflow definitions are coverage intent, not proof that a
particular revision passed. Historical CI claims below refer to the prior
session's report, not independently retrieved run artifacts.

- Locally checked toolchain: `rustc 1.96.1`, `cargo 1.96.1` under
  `/home/gamingrf/.rustup/toolchains/stable-x86_64-unknown-linux-gnu/bin`.
  System Cargo 1.75 cannot parse the locked dependency manifests. Use Rust
  1.96.x; do not rewrite the lockfile to accommodate the old system toolchain.
- Local docs verification: `cargo test --locked -p win-sandbox-runner dispatch
  -- --test-threads=1` passed **27 tests** (0 failed, 113 filtered), including the
  three trust-precedence regressions. This run was against the initial trust
  change, before T1/T2 landed; it does not verify their documented intent.
  `cargo test --locked --workspace` also passed locally (exit 0; runner: 140
  tests). Logs: `/tmp/apex-docs-dispatch-test.log` and
  `/tmp/apex-docs-workspace-test.log`. Markdown fences, local links, all seven
  milestone sections and the 22–35 week estimate sum were checked.
  `git diff --check` passed. No new change is labelled CI-verified.
- macOS: Seatbelt is kernel-enforced, but the profile permits broad reads and
  prefix/scratch/device writes. It is not Linux namespace isolation. CI defines
  smoke and generated-profile checks; end-to-end Wine compatibility remains
  unverified. A failed outbound curl alone needs a reachable unsandboxed
  control before it proves sandbox network denial.
- Linux: Tier 2 currently passes `--share-net` unconditionally and binds the
  executable directory read-write. Tier 3 directly launches Wine over a prefix
  overlay; it is not Tier 2 plus overlay. Neither establishes that all host
  writes are blocked/discarded. These are M1/M2 gaps, not fixes in T1/T2.

---

## What APEX-WIN Is

Transparent tiered sandbox for running Windows `.exe` files via Wine on Linux.
Double-click uses the desktop MIME handler and direct CLI dispatch, not
binfmt_misc. Bare terminal execution uses the separate binfmt_misc/daemon path.
Both hash the binary, resolve policy and select a Wine launch tier.

A macOS port of the CLI core (`win-sandbox-runner`) also exists — see "macOS Port" below. It reuses the same dispatch/rules/hashing logic. Tier 1/2 are backed by Apple's Seatbelt sandbox (real kernel-enforced isolation, weaker than Linux's Landlock/bubblewrap on some axes — see "Seatbelt-backed Tier 1/2"); Tier 3 has no macOS equivalent and is refused.

**Target**: Zorin OS 18.1, kernel 7.0, Rust 1.96, Wine 10.0 (primary). macOS 11+ (Big Sur or later, both Intel and Apple Silicon) for the CLI-only port.

---

## Architecture

There are **two independent launch paths**. Both are required; neither
substitutes for the other.

**Path A — file manager double-click (the primary user experience)**

```
User double-clicks .exe in Nautilus
  → GNOME resolves the MIME type (application/vnd.microsoft.portable-executable)
  → Launches the registered handler: apex-win.desktop
  → Exec=win-sandbox-runner --exe %f
  → dispatch → tier → Wine
```

Nautilus **never `exec()`s the file**, so binfmt_misc is not involved here.
Without `apex-win.desktop` installed and registered, double-click does nothing
useful, no matter how correct the daemon is. This was the actual reason
double-click appeared broken.

### Which path runs as whom (important)

Path A (`apex-win.desktop` → `--exe`) runs **entirely as the invoking user**.
Because `--exe` is set, `main()` never takes the FIFO branch, so the root daemon
is not involved at all. Verified: prefixes under `~/.local/share/win-sandbox`
are owned `user:user`. This is the safer path — arbitrary Windows binaries never
execute as root.

Path B (bare `./game.exe`) is the only one that reaches the root daemon, and it
depends on env forwarded over the FIFO.

**Path B — terminal / script execution**

```
User runs ./game.exe (or a script does)
  → Kernel sees MZ header → binfmt_misc triggers /usr/bin/win-sandbox-runner
  → main() detects .exe as a positional arg → writes to daemon FIFO with env
  → Daemon reads FIFO, hashes binary, looks up app DB + rules
  → Dispatches to tier 0/1/2/3
  → Wine runs the .exe
```

### Four Isolation Tiers (current mechanisms, not equivalent guarantees)
- **Tier 0**: No sandboxing (direct Wine execution); trust defaults here unless overridden by `--tier`.
- **Tier 1**: Linux Landlock allowlist; macOS Seatbelt scoped writes with broad reads.
- **Tier 2**: Linux bubblewrap namespaces with host binds and currently shared host networking; macOS Seatbelt denies networking (network-enabled mode refused).
- **Tier 3**: Linux RAM-backed Wine-prefix overlay via `mount(8)` then direct Wine, not a namespace sandbox. Explicit requests must refuse when unavailable; runtime refusal hardening is tracked in this session's ledger.

### Key Components
| File | Purpose |
|------|---------|
| `crates/win-sandbox-runner/src/main.rs` | CLI, binfmt detection, FIFO write with env |
| `crates/win-sandbox-runner/src/daemon.rs` | Background daemon, FIFO reader, IPC, binfmt registration |
| `crates/win-sandbox-runner/src/dispatch.rs` | Tier selection and execution |
| `crates/win-sandbox-runner/src/tier0-3.rs` | Tier implementations |
| `crates/win-sandbox-runner/src/appdb.rs` | App database (35+ profiles) |
| `crates/win-sandbox-runner/src/rules.rs` | Rules file loading |
| `crates/win-sandbox-runner/src/prefix.rs` | Per-app Wine prefix management |
| `crates/win-sandbox-runner/src/netopt.rs` | Linux network optimizer (BBR, fq_codel, DSCP) |
| `crates/win-sandbox-runner/src/wizard.rs` | First-launch wizard |
| `crates/win-sandbox-runner/src/config.rs` | Config file discovery |
| `crates/win-sandbox-runner/src/hasher.rs` | Binary hashing |
| `scripts/win-sandbox-runner.service` | systemd unit file |
| `scripts/apex-win.desktop` | MIME handler — makes double-click work. Installed by BOTH `make quick-install` and `scripts/install.sh`. It was missing from `install.sh` until 2026-08-15, so anyone following the documented install route got a working daemon and a silently non-working double-click. |
| `config/appdb.json` | Embedded app database |
| `config/rules.json` | Sandbox rules |
| `config/net-optimizer.json` | Network optimization config |
| `Makefile` | Build + install + quick-install |

---

## How the Daemon Works

### Startup Flow (systemd → `--daemon`)
1. Check root permissions; sweep stale `/dev/shm/win-run-<pid>-*` overlays
   whose owning process is gone (covers SIGKILL / OOM)
2. Load state: app DB, rules, config, net-optimizer config
3. Ensure `/run/win-sandbox-runner/` exists, is ours, mode 0755 (not 1777)
4. Register binfmt_misc handler (MZ header → `/usr/bin/win-sandbox-runner`, flags `F`)
5. Bind `launch.sock` and `ipc.sock` (0666; every peer authorized by `SO_PEERCRED`)
6. Apply network optimizations (BBR, fq_codel, DSCP, socket buffers)
7. One thread per connection; at most 32 concurrent launches per uid

### Launch protocol (binfmt handler → daemon), `src/launch.rs`
Replaced the old world-writable FIFO, whose `UID:` line was self-reported.
- Client opens the .exe with its own credentials and passes the **fd**
  (`SCM_RIGHTS`) with a 4-byte length + JSON `{exe, args, env}`.
- Daemon takes the uid from `SO_PEERCRED` (root peers are refused), hashes
  the passed fd, and checks that `exe` names that same file (dev/inode/size/
  mtime/ctime) under the user's own fs identity.
- `env` is re-validated against the passwd entry (`env_sanitize::validate_daemon_env`):
  `XDG_RUNTIME_DIR` must be `/run/user/<uid>` and owned by the uid,
  `XAUTHORITY` must be user-owned, `DISPLAY`/`WAYLAND_DISPLAY` syntax-checked,
  `HOME`/`USER` always from passwd.
- Replies `ACCEPTED`, then `EXIT <code>` or `ERROR: <text>`: the shell that
  ran `./app.exe` now gets a real status. Program arguments are forwarded.

### Privilege model inside the daemon, `src/privs.rs`
- Each launch thread switches its **fsuid/fsgid** to the user for all file
  work (prefix creation, state.json, per-user rules, overlay dirs). The old
  recursive root `chown` followed user-planted symlinks.
- Children drop to the user with the user's full supplementary group list,
  resolved before fork (the earlier `setgroups(0)` broke `dialout` etc.).
- The file identity recorded at hash time is re-checked immediately before
  every tier spawns Wine (hash-then-swap TOCTOU).

### IPC Commands (via `win-sandbox-runner --status/--reload/--stop`)
- `status` → JSON (anyone)
- `reload`, `quit`, `trust <path>` → root (or the daemon's own uid) only.
  `trust` writes rules every user's launches read, so it is not delegated;
  users run `--exe <file> --trust`, which writes their own
  `~/.config/win-sandbox/rules.json`, honored for their launches only.
- Command lines are bounded (8 KiB) and time out after 5 s.

### Graceful Shutdown
- IPC "quit" sets the shared shutdown flag; both accept loops poll it
- On exit: unregisters binfmt, removes runtime files

### Trust
An app-database match is by **file name** only, so it can choose sandbox
settings but never grants trust or Tier 0 (Tier 0 profiles are raised to
Tier 1). Trust comes only from a hash in rules.json (`--trust`).

---

## Installed File Locations

After `make quick-install`:
| Path | Content |
|------|---------|
| `/usr/bin/win-sandbox-runner` | Main binary (CLI + daemon) |
| `/usr/bin/win-sandbox-gui` | GUI companion |
| `/etc/systemd/system/win-sandbox-runner.service` | systemd unit |
| `/usr/share/applications/apex-win.desktop` | double-click MIME handler |
| `/etc/win-sandbox-runner/appdb.json` | App database |
| `/etc/win-sandbox-runner/rules.json` | Sandbox rules |
| `/etc/win-sandbox-runner/net-optimizer.json` | Network config |
| `/proc/sys/fs/binfmt_misc/APEX-WIN` | binfmt handler |

---

## Build & Install

Requires Rust **1.96.x** for the locked dependencies. On the current Linux
checkout, select the working rustup toolchain before running these commands:

```bash
export PATH="/home/gamingrf/.rustup/toolchains/stable-x86_64-unknown-linux-gnu/bin:$PATH"
rustc --version
cargo --version
```

Build as the invoking user, not root; elevate only installation steps.

```bash
# Build only (no sudo)
cargo build --release --workspace

# Full install (builds as user, installs with sudo)
make quick-install

# Start the daemon
sudo systemctl enable --now win-sandbox-runner

# Check status
win-sandbox-runner --status

# View logs
journalctl -u win-sandbox-runner -f
```

---

## Tests / CI

```bash
cargo test --workspace                          # record the actual count per revision
cargo clippy --workspace --all-targets -- -D warnings
cargo fmt --all --check
```

CI runs all three on every push (`.github/workflows/ci.yml`), plus a
skip-safe integration job, plus a macOS job that builds/tests/lints
`win-sandbox-runner`+`win-sandbox-common` only (see "macOS Port" below for
why `win-sandbox-gui` is excluded there). Adding CI immediately caught that
the workspace did not actually build: `nix` was missing the `user` feature,
and stale release artifacts had been masking it.

Note `--all-targets` on clippy: several real bugs here were only visible in
test code.

---

## Session History (Bugs Found & Fixed)

### Phases 1-6 (v0.1.0-v0.2.0) — Pre-daemon
- Trusted flag, app profiles, per-app Wine prefixes
- App database (35+ profiles), first-launch wizard
- Installation infrastructure (install.sh, .deb builder, Makefile)
- Native Linux network optimizer (BBR, fq_codel, DSCP, socket buffers)
- `--configure-net` diagnostics, `--optimize-net`/`--cleanup-net` standalone

### Phase 7 (v0.3.0) — Background Daemon
- Created `daemon.rs` (500+ lines): FIFO-based dispatch, Unix socket IPC
- New CLI flags: `--daemon`, `--status`, `--reload`, `--stop`, `--unregister`
- systemd service file, register-binfmt.sh script

### Session Bugs (12 fixed)

| # | Bug | Fix |
|---|-----|-----|
| 1 | Makefile service file path pointed to `systemd/` instead of `scripts/` | Fixed path to `scripts/win-sandbox-runner.service` |
| 2 | Daemon had no graceful shutdown (IPC wrote file main loop never read) | Shared `Arc<Mutex<bool>>` shutdown flag, non-blocking FIFO |
| 3 | Service file missing `RuntimeDirectory` under `ProtectSystem=strict` | Added `RuntimeDirectory=win-sandbox-runner` |
| 4 | Cargo.lock v4 format incompatible with stable Rust | Committed v3 lockfile, Makefile auto-downgrades v4→v3 |
| 5 | Cargo.lock was gitignored (should be committed for binary apps) | Removed from `.gitignore`, committed lockfile |
| 6 | Root's cargo was 1.75.0 (too old), `sudo make` failed | Split: cargo builds as user, install uses `sudo` internally |
| 7 | binfmt_misc passed .exe as positional arg, not `--exe` | main() detects `.exe` positional arg, routes via FIFO |
| 8 | PREFIX=/usr/local installed to wrong path | Changed to `PREFIX=/usr` |
| 9 | binfmt registration missing mask field (EINVAL) | Added `\xff\xff` mask to registration format |
| 10 | binfmt registration failure was fatal (killed daemon) | Made non-fatal (warn instead of error) |
| 11 | Config files not installed to `/etc/win-sandbox-runner/` | Makefile now copies appdb.json, rules.json, net-optimizer.json |
| 12 | Wine couldn't create windows (no display env from daemon) | New FIFO protocol passes user's DISPLAY, XDG_RUNTIME_DIR, etc. |

---

## macOS Port

CLI-only port of `win-sandbox-runner`. `win-sandbox-gui` is Linux-only (hard
dependency on GTK4/libadwaita, no macOS build story) and is excluded from
macOS builds by package selection (`-p win-sandbox-runner -p
win-sandbox-common`, not `--workspace`) rather than by any Cargo
platform-conditional membership, since Cargo workspaces don't support that.

### What ported directly (no changes needed)

`dispatch.rs`, `rules.rs`, `appdb.rs`, `wizard.rs`, `prefix.rs`, `hasher.rs`,
`config.rs`, `env_sanitize.rs`, `tier0.rs`, `netopt.rs`, `nvidia.rs` have no
hard Linux syscall dependencies. `netopt.rs` and `nvidia.rs` in particular
were audited function-by-function: every OS-specific operation is already
wrapped in `Result`/`Option` with graceful degradation to "feature
unavailable" rather than a hard failure, so they needed zero code changes,
only verification that this was actually true rather than assumed.

### What's Linux-only and cfg-gated out entirely

`tier1.rs` (Landlock), `tier2.rs` (bubblewrap), `tier3.rs` (OverlayFS+mount),
and the modules that exist purely to serve them (`amd.rs`, `audio.rs`,
`display.rs`, `net.rs`, `cleanup.rs`) are behind `#[cfg(target_os =
"linux")]` module declarations in `main.rs`. They have zero non-Linux
callers (verified by grep against every other module before gating). The
`landlock` crate and `nix`'s `mount` feature are declared as
Linux-only `[target.'cfg(target_os = "linux")'.dependencies]` in
`crates/win-sandbox-runner/Cargo.toml`, not `workspace.dependencies`, so
they don't even get fetched on other platforms — confirmed via `cargo check
--target x86_64-apple-darwin`, where `landlock` doesn't compile at all and
`nix` never pulls in the `mount` feature.

**Consequence**: Tier 3 does not exist as a *mechanism* on macOS at all (no
unprivileged ephemeral overlay filesystem). `dispatch.rs`'s
`check_tier_implemented()` is the gate that enforces this at runtime: an
explicit `--tier 3` request (or a hash pinned to it in `rules.json`) refuses
outright with a clear reason; a heuristic suggestion (app-database match,
wizard guess) degrades to Tier 0 with a loud warning. This mirrors the
fail-secure-on-explicit-request / degrade-on-heuristic split already
established for Tier 3's overlay availability check on Linux
(`check_tier3_available`), kept as a separate function rather than merged
into it so the already-tested Linux logic stays untouched.

**Tier 1/2 DO exist on macOS**, backed by Apple's Seatbelt sandbox — see
"Seatbelt-backed Tier 1/2" below. `check_tier_implemented_for_os()` admits
them specifically when `caps.seatbelt_available == Some(true)`; when
Seatbelt is confirmed absent (`Some(false)`) or the platform has no
capability info at all (`None`), Tier 1/2 refuse the same way Tier 3 always
does. `Capabilities::seatbelt_available` is only ever `Some(_)` on macOS
(`None` elsewhere), so this check alone is equivalent to "is this macOS
with sandbox-exec present" without a separate `is_macos` parameter.

### Seatbelt-backed Tier 1/2

`crates/win-sandbox-runner/src/seatbelt.rs` is the macOS analogue of
`tier1.rs`/`tier2.rs`: `sandbox-exec` plus a generated SBPL (Sandbox
Profile Language) `.sb` profile, `(deny default)` with a minimal allowlist,
mirroring `tier1.rs`'s own Landlock ruleset shape (`file-read*` allowed
broadly, matching Landlock Tier 1's read-only system-dir grants;
`file-write*` scoped to the resolved Wine prefix and a per-launch scratch
dir only; `process-exec`/`process-fork` so `wineserver` and its children,
which inherit the parent's Seatbelt profile, can run). Tier 1 allows
network (matching Landlock Tier 1's own documented inability to fully
block it); Tier 2 denies it outright, which Seatbelt CAN do completely
unlike Landlock — Tier 2 on macOS is therefore strictly more capable than
Linux's own Tier 1 on the network axis, while lacking Tier 2's namespace
isolation and GPU/audio/display passthrough plumbing.

This is a real kernel-enforced MAC (mandatory access control) boundary,
not a cosmetic wrapper — verified multiple ways rather than assumed:
  - Fetched and read a current production Seatbelt profile
    (`sandbox-macos-permissive-open.sb` from google-gemini/gemini-cli) to
    confirm the exact SBPL syntax and the "children inherit the profile"
    property this design depends on, before writing any of it.
  - `seatbelt.rs`'s own unit tests assert the generated profile's shape
    directly (deny-default present, write rule is subpath-scoped not
    unconditional, devices are `literal` not `subpath` so a write to
    `/dev/null` can't accidentally also match a hypothetical
    `/dev/nullish-thing`, etc.) — run natively via a throwaway crate
    extracted from `seatbelt.rs`'s pure functions, since this module only
    compiles under `target_os = "macos"` and this session has no Mac.
  - CI's `check-macos` job now runs a **hand-written** Seatbelt smoke test
    first (a `(deny default)` profile asserting a write inside an allowed
    dir succeeds and a write outside it fails) as a gate before anything
    else in that job runs — if `sandbox-exec` doesn't actually enforce on
    the runner, everything built on top of it is moot, so this is checked
    before relying on it, not assumed.
  - CI then runs a **generated-profile** enforcement test via a new
    `--print-seatbelt-profile --tier 1/2 --wine-prefix <path>` CLI flag
    (exists for this test, not for real dispatch — real dispatch never
    needs to print a profile, only apply one): asserts a write inside the
    printed Tier 1 profile's prefix succeeds, a write outside it fails, and
    the Tier 2 profile additionally blocks a real outbound HTTPS
    connection. This tests the *actual* profile-generation code path, not
    a hand-copied approximation of it that could silently drift.

`sandbox-exec` is deprecated by Apple with no public replacement for
confining arbitrary third-party binaries (App Sandbox requires the binary
itself opt in via entitlements, which a Windows `.exe` running under Wine
cannot do). It remains in active production use for exactly this kind of
untrusted-subprocess confinement by Chrome, OpenAI Codex, and Gemini CLI —
checked before relying on it here, not assumed to still work despite the
deprecation notice.

### Runtime architecture differences

The daemon (`daemon.rs`) runs as **root** on Linux because binfmt_misc
registration (`/proc/sys/fs/binfmt_misc/register`) requires it, and the
runtime dir (`/run/win-sandbox-runner`, matching the systemd unit's
`RuntimeDirectory=`) is root-owned. Neither justification exists on macOS:
there is no binfmt_misc equivalent, so `run_daemon()`'s root check is now
`#[cfg(target_os = "linux")]`, and the runtime dir is
`$TMPDIR/win-sandbox-runner` (a real per-user private tmp, not a
root-owned shared path) via a new `runtime_dir_base()` function instead of
the Linux-only `RUNTIME_DIR_BASE` const.

The daemon **refuses to start on macOS if `$TMPDIR` is unset** rather than
falling back to `/tmp`. This was a real bug caught in review of the first
version of this port: `$TMPDIR` is per-user and mode 0700, which is exactly
what makes it safe to host a FIFO and IPC socket that control process
launches, while `/tmp` is mode 1777 and shared by every local user. The
directory mode and FIFO mode are also now least-privilege per platform
(Linux 1777/0666, because the root daemon must accept writes from
unprivileged users via binfmt_misc; macOS 0700/0600, because the daemon and
its only writer are the same unprivileged user). Three tests pin this down,
including one asserting the runtime dir never resolves under any of
`/tmp`, `/var/tmp`, `/private/tmp`, `/var/run`, `/private/var/run`.

**Path A (double-click) does NOT depend on the daemon at all, on either
platform.** On macOS it's Launch Services resolving the
`com.microsoft.windows-executable` UTI to `macos/APEX-WIN.app`
(`Info.plist`'s `CFBundleDocumentTypes`), which forwards the opened file's
path as `argv[1]` to a shell shim
(`Contents/MacOS/apex-win-launcher`) that execs `win-sandbox-runner --exe
"$1"`. Same shape as Linux's `apex-win.desktop`, just a different OS
mechanism for the type association (UTI + app bundle vs. MIME type +
`.desktop` file). Confirmed the argv-forwarding behavior is real (not
assumed) via Apple's own `CFBundleExecutable` docs plus Platypus, a widely
used tool built on exactly this: a non-Cocoa script as a bundle's
executable receiving the opened file's path as a command-line argument.

The daemon on macOS is a **per-user `launchd` LaunchAgent**
(`macos/com.apex-win.daemon.plist`, `~/Library/LaunchAgents`), not a
system-wide `LaunchDaemon`, and it exists only as an optional CLI
convenience (pre-loaded rules/app-db for repeated `win-sandbox-runner
some.exe` invocations from Terminal) — there is no macOS equivalent of
binfmt_misc's kernel-level interception, so unlike Linux's Path B, nothing
transparently routes a bare `./game.exe` through the daemon there.

### New/changed files

| File | Purpose |
|------|---------|
| `macos/APEX-WIN.app/Contents/Info.plist` | UTI document-type claim (`com.microsoft.windows-executable`), `LSUIElement=true` (no Dock icon) |
| `macos/APEX-WIN.app/Contents/MacOS/apex-win-launcher` | Shell shim: forwards Launch Services' opened-file argv to `win-sandbox-runner --exe` |
| `macos/com.apex-win.daemon.plist` | launchd LaunchAgent (per-user, not root); `@BINDIR@` placeholder substituted by the installer |
| `scripts/install-macos.sh` | macOS installer: detects Homebrew prefix (Apple Silicon vs Intel differ), builds `-p win-sandbox-runner -p win-sandbox-common` only, installs the app bundle, registers it via `lsregister`, writes per-user config (no macOS equivalent of `/etc/win-sandbox-runner`, and none is needed — `config.rs`'s search paths already fall back to `None` gracefully when a path doesn't exist). Privilege model is per-target, unlike Linux's `install.sh` which simply requires root throughout: this script **refuses to run under sudo** (it writes `~/.config` and `~/Library/LaunchAgents`, which must belong to the user, not root) and elevates only the specific steps whose target directory is not writable, tested with `-w` against the real filesystem rather than assumed (`/opt/homebrew/bin` is usually user-owned; `/usr/local/bin` and `/Applications` usually are not). `lsregister` is deliberately never elevated, since Launch Services registrations are per-user. |
| `crates/win-sandbox-runner/src/capabilities.rs` | Added `seatbelt_available: Option<bool>` (macOS-only, `None` elsewhere — deliberately not `Some(false)`, so callers can distinguish "not applicable" from "checked and absent"), `tier12_available()` |
| `crates/win-sandbox-runner/src/seatbelt.rs` | New: macOS Tier 1/2 via `sandbox-exec` + generated SBPL profile. `build_profile()` is pure (tested directly, 9 tests); `run()`/`run_with_network()` wrap the profile-write + `sandbox-exec` exec, mirroring `tier1.rs`/`tier2.rs`'s shape |
| `crates/win-sandbox-runner/src/dispatch.rs` | `check_tier_implemented_for_os()` now admits Tier 1/2 on non-Linux when `caps.seatbelt_available == Some(true)`; Tier 3 stays refused unconditionally off Linux (no capability flag can unlock it, unlike Tier 1/2 — pinned by `tier3_stays_refused_even_with_seatbelt_available`). The `execute()` match dispatches Tier 1/2 to `seatbelt::run`/`run_with_network` under `#[cfg(target_os = "macos")]`. 15 tests total on this gate (6 rewritten for the new semantics, 9 new) |
| `crates/win-sandbox-runner/src/wizard.rs` | Implemented the interactive first-launch prompt that was previously a stub (`"Interactive wizard not yet implemented"` — true on Linux too, not just macOS; `win-sandbox-gui`'s IPC was never actually called from this binary on either platform). A real TTY prompt (stderr/stdin) behind a `Prompt` trait seam so both the "prompt asked and honored" and "prompt correctly skipped" paths are unit-testable. Gated on `!no_gui && !from_daemon`, where `from_daemon` must be `args.uid.is_some()` at the call site — see `daemon.rs` entry below for why that specific signal |
| `crates/win-sandbox-runner/src/daemon.rs` | Platform-gated root check in `run_daemon()`; `runtime_dir_base()`/`runtime_dir_base_checked()` replace the Linux-only `RUNTIME_DIR_BASE` const; `fifo_path()` made `pub(crate)`. **Found and fixed a real pre-existing bug while wiring the new wizard prompt**: `handle_launch()` (extracted into `args_for_launch_request()`) hardcoded `no_gui: false` for every daemon-dispatched request, harmless only because the prompt was unimplemented — now that it does something, that would have hung the daemon's background FIFO thread on every unknown `.exe`. Fixed to `no_gui: true` unconditionally (not keyed off `req.uid`, which can legitimately be `None` for a malformed FIFO message and would have been an unsafe proxy). 3 pinning tests, mutation-tested. `--status` JSON gained `seatbelt_available` and `tier1_2_available` fields |
| `crates/win-sandbox-runner/src/main.rs` | Added `--print-seatbelt-profile`/`--wine-prefix` (macOS-only; exists so CI can assert real enforcement against the exact profile text this binary generates, not a hand-copied approximation) |
| `scripts/apex-win-shell-hook.sh` | New: opt-in zsh/bash hook catching `.exe` invocations typed in Terminal (`./game.exe` and bare `game.exe`). NOT built on `command_not_found_handler`/`command_not_found_handle` — verified directly (real MZ-header test files, both shells, with and without +x) that those hooks never fire for a slash-containing path at all (EACCES/126, a different failure class than "not found"/127 entirely). Uses zsh's `accept-line` ZLE widget override and bash's `DEBUG` trap (with `extdebug`, needed for a non-zero return to actually veto the command) instead, both verified end-to-end with a real pty (`script(1)`), including catching and fixing a zsh-specific bug along the way: unquoted `$var` does not word-split in zsh by default (unlike bash/POSIX sh), which silently collapsed a multi-arg command line into one argv element until fixed with `${=1}` |
| `.github/workflows/ci.yml` | `check-macos` job gained two enforcement steps that run before anything else: a hand-written Seatbelt smoke test (proves `sandbox-exec` actually confines a process on the runner at all — the load-bearing check everything else depends on) and a generated-profile test using `--print-seatbelt-profile` (proves the real profile-generation code enforces its documented Tier 1/2 guarantees, not just that the profile *text* looks right) |

### What was NOT done (out of scope for this port)

- **Tier 3 on macOS.** No unprivileged ephemeral overlay filesystem exists;
  refused rather than approximated (e.g. copy-prefix-to-temp-dir-and-discard
  would be a real but much slower and more complex substitute, not
  attempted here).
- **Tier 2 networking on macOS.** Linux Tier 2 has an optional
  `network=true` mode via the TAP bridge; macOS Tier 2 is filesystem
  isolation + no network only — `seatbelt::run_with_network(args, true)`
  fails loudly with a clear message rather than silently granting full
  network access, which would make "Tier 2, network=true" mean something
  weaker on macOS than the same combination means on Linux.
- Seatbelt's own profile allowlist (sysctl-read names, mach-lookup service
  names) was built by cross-referencing what a production profile
  (Chrome's, via the Gemini CLI profile that was fetched and read) grants
  for a comparable GUI-adjacent process, not by tracing every syscall Wine
  itself makes on macOS. If a real Wine launch needs something not on that
  list, Seatbelt will deny it and Wine will fail to start — the profile may
  need iteration against a real Wine install, which this session doesn't
  have.
- No macOS icon (`.icns`) for `APEX-WIN.app` — `Info.plist` deliberately
  omits `CFBundleIconFile` rather than reference a file that doesn't exist;
  Launch Services falls back to a generic bundle icon.
- No code signing / notarization for `APEX-WIN.app`, which Gatekeeper will
  likely require before a downloaded (not locally built) copy runs without
  a manual override.
- Universal binary vs. Apple-Silicon-only was left unresolved; `cargo build
  --release` on a given machine only produces that machine's architecture.
  A universal build needs an explicit `lipo`-based build step, not added
  here.
- **A real Wine launch under Seatbelt was never performed** in the port session
  (no Mac/Wine installation was available locally). The historical report claimed
  macOS CI enforcement results, but no run artifact has been retrieved in this
  documentation session. The workflow does define smoke and generated-profile
  checks for scoped writes and network denial. These are not end-to-end Wine
  compatibility tests; the outbound curl check also lacks an unsandboxed
  reachability control. The `wineserver`/mach-lookup allowlist still needs
  validation against real Wine, regardless of profile syntax tests.

---

## Known Remaining Issues

**Verified working** (2026-08-13): full chain launches a real Windows GUI app.
Evidence: `xwininfo -root -tree` showed `"7-Zip 24.09 (x64) Setup"` 300x182 owned
by the exe and framed by `mutter-x11-frames`, with zero display errors, prefix
created under `~/.local/share/win-sandbox/prefixes/<hash>/prefix`.

Resolved since the first draft of this document:
- ~~Environment set globally in daemon threads~~ → per-child `Command::env()` (`ad2cbd6`)
- ~~UID not switched in child process~~ → `configure_child_uid` via `pre_exec` (`ad2cbd6`)
- ~~Wine prefix created as root~~ → prefix ownership fixed (`ad2cbd6`)
- ~~`XAUTHORITY` stripped by sanitizer~~ → added to allowlist (`237780d`)
- ~~`WINEPREFIX` clobbered by config default~~ → config is now fallback only (`237780d`)
- ~~No .deb package~~ → `scripts/build-deb.sh` updated for v0.3.0 (correct paths, service file, MIME handler, net-optimizer.json)
- ~~`appdb.json` not truly embedded~~ → `load_embedded()` now uses `include_str!` fallback, always available even if /etc is missing
- ~~`rules.json` not embedded~~ → `load_rules()` now uses `include_str!` fallback
- ~~Wayland warning too weak~~ → Now warns about experimental Wine Wayland support

Still open:

1. ~~binfmt definition duplicated across 5 sites~~ — **fixed.**
   `scripts/register-binfmt.sh` is now the single source (`--print` emits the
   definition, `BINDIR=` overrides the prefix). Makefile, install.sh and the
   .deb postinst all call it. daemon.rs keeps its own copy on purpose, since it
   registers at startup and cannot assume the script is installed; two tests
   (`binfmt_definition_matches_script`, `binfmt_registration_carries_mask`)
   assert the copies stay identical. The guard was verified by proving it
   fails when the script diverges.

2. ~~Daemon FIFO path (Path B) broken~~ — **fixed and verified.** Both launch
   paths now map a real window.

   Worth reading if you hit something similar. The failure was
   `wine: unable to create wineserver tmpdir`, and it survived two wrong
   diagnoses before the real cause was found:

   - *Wrong #1:* the `/tmp` prefix. Real bug, fixed by `PrefixManager::for_user`,
     but the wineserver error persisted afterwards.
   - *Wrong #2:* `ProtectHome=read-only` in the unit. A `systemd-run` probe
     proved `/home` really was read-only, which looked conclusive. Removing it
     changed nothing.
   - *Actual cause:* `tier1::build_rw_paths()` read `XDG_RUNTIME_DIR` from
     `std::env`. The daemon is a systemd service with a near-empty environment
     (verify with `cat /proc/<pid>/environ`), so the value was absent,
     `/run/user/<uid>` was silently dropped from the Landlock allowlist, and
     wineserver was denied its socket dir. The prefix was also only granted
     read, never write.

   The lesson worth keeping: a plausible mechanism that reproduces in isolation
   is not proof of causation. Both wrong theories were independently true and
   neither was the bug. What settled it was reading the daemon's actual
   environment instead of reasoning about what it should contain.

2. **Historical Tier 3 fallback observation — not current explicit-request policy.**
   The earlier X11 acceptance session observed Tier 2's Xephyr window and a
   Tier 3 mount privilege failure on bubblewrap 0.9.0. A successful fallback
   tested Tier 2, **not ephemeral Tier 3 isolation**. Explicit Tier 3 requests
   now have a preflight refusal; this session's T2 work closes the remaining
   runtime downgrade path. Heuristic requests may still warn and degrade.
   Newer bubblewrap alone does not fix this backend: it still calls `mount(8)`
   and direct Wine, not `bwrap --overlay`. Implementing a genuinely unprivileged
   disposable sandbox and verifying cleanup are roadmap work; a setuid helper
   is not the recommended shortcut.

3. **Wayland path untested** — verification was on X11 (`XDG_SESSION_TYPE=x11`).
   A Wayland session takes a different Wine driver path. A warning is now emitted
   when `--wayland` is used.

### Debugging note

When testing Wine directly, do **not** put the prefix under `/tmp`. Wine refuses
with `'/tmp' is not owned by you, refusing to create a configuration directory there`
and exits immediately. This produces a misleading "no errors" result. Always use a
user-owned path and confirm a window with `xwininfo -root -tree`, rather than
inferring success from absent error messages.

---

## CLI Reference

```bash
# Run a .exe directly (bypasses daemon)
win-sandbox-runner --exe game.exe

# Force specific tier
win-sandbox-runner --exe game.exe --tier 2

# Trust an app (runs without sandboxing next time)
win-sandbox-runner --exe game.exe --trust

# Gamepad access in sandbox
win-sandbox-runner --exe game.exe --gamepad

# Display modes
win-sandbox-runner --exe game.exe --nested-x11   # Xephyr (default tier 2/3)
win-sandbox-runner --exe game.exe --xvfb          # Virtual framebuffer
win-sandbox-runner --exe game.exe --host-x11      # Direct host X11 (DANGEROUS)
win-sandbox-runner --exe game.exe --wayland       # Wayland

# Daemon management
win-sandbox-runner --status     # Query daemon
win-sandbox-runner --reload     # Reload rules
win-sandbox-runner --stop       # Stop daemon
win-sandbox-runner --unregister # Remove binfmt handler

# Network optimization (standalone)
win-sandbox-runner --optimize-net    # Apply BBR, fq_codel, DSCP
win-sandbox-runner --cleanup-net     # Remove optimizations
win-sandbox-runner --configure-net   # Diagnostics
```

---

## Git Log (Recent)

```
9ec8bde fix(daemon): pass user display env to Wine via FIFO protocol
0b4388f fix(binfmt): add missing mask field to registration format
0391c23 fix(makefile): install config files to /etc/win-sandbox-runner
19c1348 fix(makefile): auto-cleanup old installs in quick-install
c377214 fix(makefile): PREFIX=/usr (was /usr/local)
726e0f0 fix(service): remove ProtectSystem=strict causing 203/EXEC
55d91e9 fix(binfmt): detect .exe positional arg from kernel, route via FIFO
206cb21 fix(makefile): quick-install builds as user, installs with sudo
8c5ab18 fix(makefile): simplify lockfile v4 safeguard
048f6f9 fix: commit Cargo.lock for reproducible builds (v3)
eeab319 fix(makefile): auto-detect and fix Cargo.lock v4 on stable Rust
3e01c7d fix(daemon): graceful shutdown + service file RuntimeDirectory
2a03011 feat(makefile): add quick-install target
4414d77 fix(makefile): service file path + binfmt registration name
2ca86c2 feat(daemon): background daemon mode with binfmt_misc interception
```
