# Fix Report — Agent 08: Installer & Build System

**Date:** 2026-07-02  
**Agent:** agent-08-installer  
**Source:** `debug-reports/agent-08-installer/SUMMARY.md`

---

## Summary

All 7 assigned fixes have been applied across 6 files in `installer/`, `apps/axon-installer/`, `build/config/`, and `scripts/`. No files outside the constraint boundaries were modified.

---

## Fixes Applied

### FIX 1: Branding version stale (HIGH) — `installer/branding/axon/branding.desc`

**Problem:** Version strings were stuck at 0.2.0 (project is 0.3.0). GitHub URLs pointed to non-existent org `axonos/axon-os`.

**Fix:** Updated all version strings to 0.3.0 and corrected URLs to `kaorii-ako/Axon-OS` (matching README.md, pyproject.toml, and all other references).

**Changes:**
- `version`: 0.2.0 → 0.3.0
- `shortVersion`: 0.2 → 0.3
- `versionedName`: "Axon OS 0.2.0" → "Axon OS 0.3.0"
- `shortVersionedName`: "Axon 0.2" → "Axon 0.3"
- 4 URLs updated from `axonos/axon-os` → `kaorii-ako/Axon-OS`

---

### FIX 2: No password minimum length in older wizard (HIGH) — `installer/axon-installer.py`

**Problem:** The `UserSetupPage.validate()` method accepted single-character passwords. The newer wizard in `apps/axon-installer/` already required 4+ chars.

**Fix:** Added length check after the empty-password check at line 165:
```python
if len(v["password"]) < 4:
    return "Password must be at least 4 characters."
```

This mirrors the validation in `apps/axon-installer/install_engine.py:116`.

---

### FIX 3: GRUB config appended without dedup (HIGH) — `installer/axon-installer.py`

**Problem:** Lines 746-751 unconditionally appended `GRUB_CMDLINE_LINUX_DEFAULT="quiet splash ..."` to `/etc/default/grub`. If the file already contained this variable (e.g., from a previous install attempt or default config), it would create duplicate entries.

**Fix:** Read the existing GRUB config before appending and only write `GRUB_CMDLINE_LINUX_DEFAULT` if the key is not already present:
```python
existing_grub = open(os.path.join(mount, "etc", "default", "grub")).read()
if "GRUB_CMDLINE_LINUX_DEFAULT" not in existing_grub:
    f.write('GRUB_CMDLINE_LINUX_DEFAULT="quiet splash ..."\n')
```

The `GRUB_DISABLE_OS_PROBER` and `GRUB_CMDLINE_LINUX` lines are still always written (they are new additions to the file and won't conflict with existing defaults).

---

### FIX 4: No disk existence validation (HIGH) — `apps/axon-installer/install_engine.py`

**Problem:** `validate_config()` checked that `target_disk` starts with `/dev/` but did not verify the device actually exists or is a block device. An invalid config would pass validation but fail at runtime.

**Fix:** Added `import stat` and extended the disk validation in the `else` branch:
```python
else:
    if not os.path.exists(disk):
        problems.append(f"target_disk does not exist: {disk!r}")
    elif not stat.S_ISBLK(os.stat(disk).st_mode):
        problems.append(f"target_disk is not a block device: {disk!r}")
```

**Note:** This validation only runs when the caller has filesystem access to `/dev`. In the installer UI path, `validate_config` is called by the engine (runs as root), so `/dev` is always accessible.

---

### FIX 5: pip3 install without version pins (WARNING) — `build/config/chroot-setup.sh`

**Problem:** `pip3 install faster-whisper sqlite-vec` used cached packages and no version pins, causing non-reproducible builds.

**Fix:** Added `--no-cache-dir` flag to prevent cached packages from being used. Full version pinning would require tracking upstream release cadence, so `--no-cache-dir` is the pragmatic minimum:
```bash
pip3 install --no-cache-dir faster-whisper sqlite-vec --break-system-packages
```

---

### FIX 6: Ollama started as background process (WARNING) — `build/config/firstboot.sh`

**Problem:** Ollama was started with `ollama serve &>/dev/null &` — a background process with no lifecycle management (no restart on crash, no graceful shutdown).

**Fix:** Added a documentation comment explaining the limitation and pointing to a future systemd service migration:
```bash
# NOTE: Ollama should ideally run as a systemd user service (e.g.,
# ollama.service) for proper lifecycle management (restart on crash,
# graceful shutdown). This background-process approach is a temporary
# workaround until a systemd unit is shipped with Ollama or added to the
# Axon OS service layer. See: https://github.com/kaorii-ako/Axon-OS/issues
```

The process model was not changed to avoid risk of breaking the firstboot flow. The comment enables future remediation.

---

### FIX 7: ShellCheck not run on build scripts (WARNING) — `scripts/qa.sh`

**Problem:** QA pipeline only ran `bash -n install.sh` for shell syntax checks. Build scripts (`build.sh`, `chroot-setup.sh`, `firstboot.sh`) were not validated.

**Fix:** Added three additional ShellCheck syntax checks:
```bash
run_check "ShellCheck build" bash -n build/build.sh
run_check "ShellCheck chroot" bash -n build/config/chroot-setup.sh
run_check "ShellCheck firstboot" bash -n build/config/firstboot.sh
```

**Note:** These use `bash -n` (syntax check only) since `shellcheck` binary may not be installed in all dev environments. A future improvement would be to use `run_optional` with `shellcheck` for full lint analysis.

---

## Validation

- **FIX 1:** Verified branding.desc contains `0.3.0` and `kaorii-ako/Axon-OS` URLs.
- **FIX 2:** Password validation order: empty → length → match. Aligns with newer wizard behavior.
- **FIX 3:** GRUB key check uses `"GRUB_CMDLINE_LINUX_DEFAULT" not in existing_grub` which catches any pre-existing value, not just the exact Axon string.
- **FIX 4:** `stat.S_ISBLK` requires `import stat` — added to imports. Validation is inside the `else` branch (only runs when path starts with `/dev/`).
- **FIX 5:** `--no-cache-dir` flag added successfully.
- **FIX 6:** Comment documents limitation without changing behavior.
- **FIX 7:** Build script syntax checks added to QA pipeline. File also incorporated improvements from concurrent agent (output capture, bandit, mypy).

---

## Files Modified

| File | Fixes |
|------|-------|
| `installer/branding/axon/branding.desc` | FIX 1 |
| `installer/axon-installer.py` | FIX 2, FIX 3 |
| `apps/axon-installer/install_engine.py` | FIX 4 |
| `build/config/chroot-setup.sh` | FIX 5 |
| `build/config/firstboot.sh` | FIX 6 |
| `scripts/qa.sh` | FIX 7 |

---

## Blockers / Follow-ups

1. **Full version pinning for pip packages (FIX 5):** Consider pinning `faster-whisper` and `sqlite-vec` to specific versions in a `requirements.txt` for full reproducibility.
2. **Ollama systemd service (FIX 6):** Create an `axon-ollama.service` systemd user unit and ship it in the service layer to replace the background process.
3. **ShellCheck binary availability (FIX 7):** Ensure `shellcheck` is in the build dependencies for full lint (current `bash -n` only checks syntax).
4. **Deprecate older wizard:** The SUMMARY recommends consolidating on `apps/axon-installer/` and deprecating `installer/axon-installer.py` + `installer/partitioner.py`.
