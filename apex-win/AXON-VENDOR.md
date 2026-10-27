# APEX-WIN in Axon OS

This directory is a copy of [Gaming-RF/APEX-WIN](https://github.com/Gaming-RF/APEX-WIN)
(MIT, see `LICENSE`), vendored so the ISO build doesn't need network access to
GitHub for it.

- Upstream commit: `efb65ac3fdd6c8eed7756e628a3974823c533a06` (2026-09-25)
- Re-sync: `bash scripts/update-apex-win.sh [ref]`, then re-apply the patch below.

## Axon patches

1. **Programs installed into a prefix reuse it** (`prefix.rs`
   `PrefixManager::owning_prefix`, used in `dispatch.rs`). APEX-WIN gives every
   `.exe` its own Wine prefix, keyed by its hash. An app that an installer put
   in `prefixes/<key>/prefix/drive_c/...` has a different hash, so it started in
   an empty prefix without its registry keys and DLLs. It now runs in the prefix
   it was installed into. Worth sending upstream.

2. **User-chosen Proton/Wine build** (`tier0.rs` `runner_wine`). Tier 0 runs
   `$AXON_WINE_DIR/bin/wine` instead of the system `wine` when that variable
   (set by `scripts/axon-apex-run` from `axon-proton-runners`) names a runner
   whose `bin/wine` is an executable file nobody else can write. It is read from
   the runner's own environment, never from a daemon client. Tier 0 only.

## How Axon uses it

See `docs/windows-apps.md`. In short: only the `win-sandbox-runner` binary is
built (`scripts/build-apex-win.sh`), and every launch goes through
`scripts/axon-apex-run`, which forces Tier 0 (Wine, no sandbox) without the
first-launch prompt. The root daemon, GTK GUI, TAP bridge and eBPF parts are
not installed.
