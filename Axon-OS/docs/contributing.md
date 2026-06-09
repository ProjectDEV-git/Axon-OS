# Contributing to Axon OS

## Code of Conduct

Be respectful, constructive, and welcoming — we do not tolerate harassment or discrimination of any kind.

## Fork / Branch / PR Workflow

1. **Fork** the repository on GitHub and clone your fork locally.
2. **Create a branch** from `main` with a short, descriptive name:
   ```bash
   git checkout -b feat/intent-bar-history
   # or
   git checkout -b fix/plymouth-fade-timing
   ```
3. **Make your changes** in small, focused commits. Each commit should build and pass tests on its own.
4. **Push** your branch to your fork:
   ```bash
   git push -u origin feat/intent-bar-history
   ```
5. **Open a Pull Request** against `axonos/axon-os` `main`. Fill in the PR template — include a summary of what changed and why, steps to reproduce any bug being fixed, and screenshots for UI changes.
6. Address review feedback with additional commits (do not force-push to an open PR unless asked).
7. A maintainer will squash-merge once the PR is approved and CI is green.

## Code Style

### Python

All Python code targets **Python 3.11+** and must pass [`ruff`](https://docs.astral.sh/ruff/) with the project configuration:

```bash
ruff check .
ruff format .
```

**Type hints are required** on all function signatures and class attributes. Use `from __future__ import annotations` at the top of every module for forward-compatible annotation evaluation.

```python
# Good
def list_disks(self) -> list[DiskInfo]:
    ...

# Bad — missing return type
def list_disks(self):
    ...
```

### Shell Scripts

All shell scripts must pass [`shellcheck`](https://www.shellcheck.net/) with no warnings:

```bash
shellcheck build/build.sh build/firstboot.sh installer/*.sh
```

Use `#!/usr/bin/env bash` shebangs, quote all variable expansions, and prefer `[[ ]]` over `[ ]` for conditionals.

### GNOME Shell Extensions (JavaScript)

Follow standard **GNOME Shell JS idioms**:

- Use ES module syntax (`import`/`export`) — GNOME Shell 45+ requires it.
- Prefer `GObject.registerClass` for all GObject subclasses.
- Clean up signal connections and allocated resources in `disable()` / `destroy()`.
- Do not block the main loop — offload slow work with `Gio.Subprocess` or `GLib.idle_add`.
- Avoid `log()` in production paths; use `console.debug()` guarded by a `DEBUG` flag.

## Testing

### Python

Run the unit test suite with `pytest`:

```bash
pytest tests/ -v
```

New Python modules require accompanying tests in `tests/`. Aim for meaningful coverage of public methods — 100 % line coverage is not required, but all error paths should be exercised.

### Shell Scripts

Re-run `shellcheck` after every shell change:

```bash
shellcheck $(git diff --name-only HEAD | grep '\.sh$')
```

Integration tests for `build.sh` run inside the Docker build environment to avoid polluting the host.

### GNOME Shell Extension

Test the extension in a **nested GNOME session** to avoid crashing your primary desktop:

```bash
# Start a nested Wayland compositor
dbus-run-session -- gnome-shell --nested --wayland &

# In a second terminal, enable the extension inside the nested session
DISPLAY= WAYLAND_DISPLAY=wayland-1 gnome-extensions enable axon-spaces@axonos
```

Use `journalctl -f /usr/bin/gnome-shell` to watch for JS errors in real time.

## Architecture

See [architecture.md](architecture.md) before making structural changes.
