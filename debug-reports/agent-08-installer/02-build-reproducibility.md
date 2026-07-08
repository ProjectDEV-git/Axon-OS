# Build Script Reproducibility Analysis

**Agent:** Debug Agent 8 — Installer & Build System
**Date:** 2026-07-02
**Files Analyzed:**
- `build/build.sh` (master ISO build script)
- `build/config/chroot-setup.sh` (chroot configuration)
- `build/config/packages.list` (package manifest)
- `build/config/packages-winabi.list` (Windows ABI packages)
- `scripts/qa.sh` (quality assurance)
- `scripts/check-reproducibility.sh` (reproducibility check)
- `scripts/verify-iso.sh` (ISO verification)

---

## CRITICAL REPRODUCIBILITY ISSUES

### CRIT-1: Package Versions Not Pinned — Builds Will Drift
**File:** `build/config/packages.list`
**Severity:** CRITICAL for reproducibility

`packages.list` lists packages without version constraints:
```
gnome-shell
python3
curl
...
```

When `apt-get install -y` runs in `chroot-setup.sh`, it installs whatever version is currently in the Ubuntu repository. Two builds on different days will produce different ISOs because:
- Package versions change daily in Ubuntu repos
- Transitive dependencies shift
- New security patches are applied

The `check-reproducibility.sh` script exists and correctly tests reproducibility, but **it will fail** until package versions are pinned.

**Recommendation:** Use `apt-mark hold` or create a snapshot apt repository, or pin versions like `gnome-shell=46.0-0ubuntu4`.

### CRIT-2: WhiteSur Theme Cloned from `master` Branch
**File:** `build/config/chroot-setup.sh` lines 426-442
**Severity:** HIGH

```bash
WHITESUR_GTK_COMMIT="${WHITESUR_GTK_COMMIT:-master}"
WHITESUR_ICON_COMMIT="${WHITESUR_ICON_COMMIT:-master}"
```

The themes are cloned from GitHub and checked out to `${WHITESUR_GTK_COMMIT}` which defaults to `master`. This means:
- Every build gets the latest commit from `master`
- Theme appearance changes without notice
- Builds are non-reproducible

The comment says "Pinned commit hashes for reproducible builds — update these when bumping themes" but the actual defaults are `master`.

**Recommendation:** Set default values to specific commit SHAs, e.g.:
```bash
WHITESUR_GTK_COMMIT="${WHITESUR_GTK_COMMIT:-a1b2c3d4e5f6...}"
```

### CRIT-3: Timestamps Embedded in ISO Artifacts
**File:** `build/build.sh` line 222
**Severity:** MEDIUM

```bash
echo "Axon OS ${VERSION} \"Pulse\" - Release ${ARCH} ($(date +%Y%m%d))" > "${IMAGE}/.disk/info"
```

The build date is embedded in `.disk/info` using the current date. This is non-deterministic.

**File:** `build/build.sh` line 384 (SBOM generation)
```bash
"created": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
```

The SBOM also contains a build timestamp.

**Mitigation:** The `SOURCE_DATE_EPOCH` variable is correctly set and exported, but `date` is not called with `--date=@${SOURCE_DATE_EPOCH}` — it uses the real system time. `SOURCE_DATE_EPOCH` should be used consistently:
```bash
date -u -d "@${SOURCE_DATE_EPOCH}" +%Y%m%d
```

---

## HIGH SEVERITY ISSUES

### HIGH-1: dbus-uuidgen Creates Random Machine-ID
**File:** `build/config/chroot-setup.sh` line 54
**Severity:** LOW (mitigated)

```bash
dbus-uuidgen > /etc/machine-id
```

This generates a random machine-ID during the build. However, `chroot-setup.sh` line 638 truncates it at cleanup:
```bash
truncate -s 0 /etc/machine-id
```

And `install_engine.py` line 535 also truncates:
```python
Path(f"{TARGET}/etc/machine-id").write_text("")
```

So the machine-ID is properly cleaned. **No issue here** — correctly handled.

### HIGH-2: `pip3 install` Without Version Pins
**File:** `build/config/chroot-setup.sh` line 103
**Severity:** MEDIUM

```bash
pip3 install faster-whisper sqlite-vec --break-system-packages
```

These Python packages are installed without version pins. Different builds will get different versions. The `--break-system-packages` flag is a code smell (PEP 668 violation) but necessary for chroot installs.

### HIGH-3: Flatpak Requires Network During Build
**File:** `build/config/chroot-setup.sh` line 100
**Severity:** MEDIUM

```bash
flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo || true
```

This runs inside the chroot and requires network access. If the build host has no network at this point, it silently fails (due to `|| true`). This is acceptable since it's just registering the remote, not installing anything.

---

## CHROOT SETUP ANALYSIS

### Chroot Completeness
The `chroot-setup.sh` is well-structured with 10 phases:

1. **APT sources** — Correctly configured with main/universe/multiverse + security
2. **Base system** — systemd, dbus, locales properly installed
3. **Kernel + casper** — Standard Ubuntu live-boot infrastructure
4. **Desktop packages** — From `packages.list` with DKMS fallback handling
5. **Axon components** — Apps, services, shell extension, D-Bus, systemd units
6. **GNOME defaults** — GSchema overrides, themes, wallpaper
7. **Plymouth** — Boot splash configured
8. **Installer** — Native Axon installer + Calamares polkit policy
9. **Identity** — hostname, casper.conf, GDM autologin, os-release
10. **Cleanup** — initramfs regeneration, apt clean, machine-id truncation

**Missing dependencies (minor):**
- `python3-gi-cairo` is in `packages.list` but the GTK4 bindings might need additional GIR packages for the installer
- `gir1.2-adw-1` (libadwaita GIR) is listed — good

### Service Guards
```bash
printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
```
Correctly prevents services from starting during the build. Removed at cleanup.

### Error Handling
DKMS packages (bcmwl, broadcom-sta) are installed separately with `|| log "WARNING..."` — good pattern for kernel-module packages that may fail on non-matching host kernels.

Bulk package install falls back to one-at-a-time:
```bash
if ! apt-get install -y "${NON_DKMS_PACKAGES[@]}"; then
    for p in "${NON_DKMS_PACKAGES[@]}"; do
        apt-get install -y "${p}" || log "WARNING: package ${p} failed to install"
    done
fi
```
This is a good resilience pattern.

---

## APTEOF HEREDOC ANALYSIS

### build.sh APTEOF (line 159-163)
```bash
cat > "${CHROOT}/etc/apt/apt.conf.d/99force-ipv4" <<'APTEOF'
Acquire::ForceIPv4 "true";
Acquire::Retries "3";
Acquire::http::Pipeline-Depth "0";
APTEOF
```
**Correct.** Single-quoted `<<'APTEOF'` prevents variable expansion. Content has no variables to escape.

### chroot-setup.sh APTEOF (line 37-41)
```bash
cat > /etc/apt/apt.conf.d/99force-ipv4 <<'APTEOF'
Acquire::ForceIPv4 "true";
Acquire::Retries "3";
Acquire::http::Pipeline-Depth "0";
APTEOF
```
**Correct.** Same pattern, no issues.

### GRUB header injection (line 193-215)
```bash
cat << 'EOF' >> /etc/grub.d/00_header
# Axon OS Self-Healing Watchdog
if [ -s \$prefix/grubenv ]; then
  load_env boot_attempts
fi
```
**Correct.** Single-quoted `<< 'EOF'` prevents expansion, and GRUB `$` is escaped as `\$`.

### Other heredocs
- `axon-shield` (line 261-264): Uses `<<EOF` (unquoted) with `\$@` — **Correct**, the `\$` prevents shell expansion of `$@`.
- `axon-install-engine` (line 524-527): Uses `<<EOF` (unquoted) with `\$@` — **Correct**.
- GNOME gschema override (line 454-493): Uses `<<EOF` (unquoted) to allow `${GTK_THEME_NAME}` etc. expansion — **Correct**, these are intentional.
- Netplan config (line 398-403): Uses `<<'EOF'` — **Correct**.

**No heredoc escaping issues found.**

---

## QA PIPELINE ANALYSIS

### scripts/qa.sh
The QA script runs:
1. Ruff lint on apps/services/tests/installer
2. Python syntax checks on specific files
3. ShellCheck on `install.sh`
4. JSON validation
5. Pre-commit hooks
6. Pytest

**Observations:**
- ShellCheck is only run on `install.sh`, not on `build/build.sh`, `chroot-setup.sh`, or the GRUB scripts. Should expand coverage.
- Pytest runs with `--timeout=30` which is good for preventing hangs.
- The QA script does NOT run `mypy` or `bandit` (mentioned in AGENTS.md CI order as part of the standard pipeline).

### scripts/check-reproducibility.sh
Well-designed script that:
1. Sets fixed `SOURCE_DATE_EPOCH=1704067200` (2024-01-01)
2. Builds ISO twice in separate directories
3. Compares SHA-256 checksums

**Issue:** Uses `sudo -E` which preserves environment variables, but the environment might differ between builds (e.g., locale, timezone). Consider also setting `LANG=C`, `TZ=UTC`.

### scripts/verify-iso.sh
Good verification tool that checks:
1. SHA-256 checksum (auto-discovers `.sha256` file)
2. GPG signature (auto-discovers `.sig` file)
3. ISO structure (ISO 9660 format, El Torito boot catalog)

**Missing:** No GPG public key is distributed with the project for signature verification. The `gpg --verify` step will always fail without importing the signing key first.

---

## BUILD SCRIPT ROBUSTNESS

### Positive Findings
- `set -euo pipefail` used consistently — good.
- Trap handler for unmounting chroot on exit — prevents mount leaks.
- `--keep-chroot` flag for iterative development — good DX.
- Persistent apt cache (`/tmp/axon-build/apt-cache/`) avoids re-downloading across builds.
- Automatic dependency installation with verification loop.
- SHA-256 checksum generated for ISO.
- SBOM (Software Bill of Materials) generated.

### Issues
- The build script runs as root (`sudo bash build/build.sh`) but the `check_deps()` function installs packages with `apt-get install -y` which may prompt in some environments. The `DEBIAN_FRONTEND=noninteractive` is not set in `build.sh` itself (only in `chroot-setup.sh`).
- The `configure_chroot()` function calls `umount_chroot()` then immediately `mount_chroot()` again via `chroot-setup.sh`. This is because `chroot-setup.sh` does its own bind mounts internally. Wait — actually `configure_chroot()` calls `mount_chroot()`, then runs `chroot-setup.sh` which doesn't do its own mounts, then calls `umount_chroot()`. This is correct.

---

## SUMMARY TABLE

| ID | Severity | Description | Impact |
|----|----------|-------------|--------|
| CRIT-1 | CRITICAL | Package versions not pinned | ISO differs between builds |
| CRIT-2 | HIGH | WhiteSur theme defaults to `master` | Theme changes unpredictably |
| CRIT-3 | MEDIUM | Timestamps in .disk/info and SBOM | Non-deterministic artifacts |
| HIGH-2 | MEDIUM | pip3 packages not version-pinned | Python deps drift |
| HIGH-3 | MEDIUM | Flatpak remote-add requires network | Silent failure offline |
| -- | LOW | ShellCheck not run on build scripts | Lint coverage gap |
| -- | LOW | No GPG key distributed for verify-iso.sh | Signature check always fails |
| -- | LOW | DEBIAN_FRONTEND not set in build.sh | Possible apt prompts |
