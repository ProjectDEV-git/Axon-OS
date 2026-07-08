# Branding, GRUB & Boot Configuration Analysis

**Agent:** Debug Agent 8 — Installer & Build System
**Date:** 2026-07-02
**Files Analyzed:**
- `build/config/axon-boot-ok.service` (systemd unit)
- `build/config/axon-boot-ok.sh` (boot success marker)
- `build/config/grub.d-06_axon_watchdog` (GRUB boot counter)
- `build/config/grub.d-42_axon_rollback` (GRUB recovery entry)
- `build/config/firstboot.sh` (user first-login setup)
- `build/config/ai-firstboot.sh` (AI/Ollama first-boot setup)
- `installer/branding/axon/branding.desc` (Calamares branding)
- `installer/branding/axon/AxonSlideshow.qml` (Calamares slideshow)

---

## GRUB BOOT WATCHDOG SYSTEM

### Architecture

The self-healing boot watchdog works as follows:

```
Boot attempt 1 → 06_axon_watchdog sets boot_attempts=1 → boot-ok.sh resets to 0
Boot attempt 2 → 06_axon_watchdog sets boot_attempts=2 → boot-ok.sh resets to 0
Boot attempt 3 → 06_axon_watchdog sets boot_attempts=3 → selects recovery entry
Recovery entry → boots @axon-fallback subvolume → boot-ok.sh resets to 0
```

### grub.d-06_axon_watchdog Analysis
**File:** `build/config/grub.d-06_axon_watchdog`
**Shell:** `#!/bin/sh` (POSIX sh) — Correct, GRUB scripts run in GRUB's shell, not bash.
**Bash vs sh compatibility:** N/A — this is a GRUB script generator, not a shell script.

```sh
if [ "$(grub-probe --target=fs / 2>/dev/null)" != "btrfs" ]; then
    exit 0
fi
```
**Correct:** Only activates on btrfs root filesystems. ext4 systems are unaffected.

**Counter logic:**
- Empty/undefined → 1
- 1 → 2
- 2 → 3
- 3 → stays at 3 (prevents overflow)
- Any other → 1 (reset)

**Issue (minor):** When `boot_attempts` is already 3, it stays at 3 and selects the recovery entry. But the recovery entry itself also goes through GRUB, which means the counter will be 3 again on the next boot. This creates a loop where:
1. System boots normally, fails
2. System boots normally, fails
3. System boots recovery entry
4. Recovery entry also triggers the watchdog counter check — counter is already 3
5. Counter stays at 3, recovery is selected again

**However**, this is mitigated because:
- The recovery entry (`42_axon_rollback`) boots from `@axon-fallback` which is a known-good snapshot
- `boot-ok.sh` runs after the display manager loads and resets counter to 0
- So step 3 (recovery boot) will succeed, `boot-ok.sh` resets to 0, and subsequent boots are normal

**Net assessment:** The logic is correct and self-resolving.

### grub.d-42_axon_rollback Analysis
**File:** `build/config/grub.d-42_axon_rollback`
**Shell:** `#!/bin/sh` (POSIX sh)

```sh
uuid="$(grub-probe --target=fs_uuid / 2>/dev/null)" || exit 0
```
**Correct:** Gets the root filesystem UUID for the kernel `root=` parameter.

```sh
linux /@axon-fallback/boot/${kbase} root=UUID=${uuid} rootflags=subvol=@axon-fallback rw quiet splash axon.rollback=1
```
**Correct points:**
- Boots from `@axon-fallback` subvolume
- Passes `rootflags=subvol=@axon-fallback` so the kernel mounts the correct subvolume
- Passes `axon.rollback=1` flag for detection by `boot-ok.sh`
- Uses `rw` to allow writes (important for recovery operations)

**Safety assessment:**
- **User data preserved:** The `@home` subvolume is separate from `@` and `@axon-fallback`. Rolling back the system subvolume does NOT affect `/home`.
- **Rollback is one-way:** There's no mechanism to "undo" a rollback. If the user wants to go back to the current (potentially broken) system, they'd need to manually snapshot.
- **Missing:** No mechanism to update `@axon-fallback` after the user has been running. The fallback always contains the initial install state. After a rollback, all system-level changes (package installs, updates) are lost.

### axon-boot-ok.sh Analysis
**File:** `build/config/axon-boot-ok.sh`
**Shell:** `#!/bin/bash` with `set -u` (but not `set -e`)

```bash
findmnt /boot/efi >/dev/null 2>&1 || exit 0
command -v grub-editenv >/dev/null 2>&1 || exit 0
```
**Correct:** Safely exits on live sessions or ext4 installs where ESP isn't mounted.

```bash
grub-editenv "${ENV_FILE}" set boot_attempts=0
```
**Correct:** Resets the counter, indicating a successful boot.

```bash
if grep -q 'axon\.rollback=1' /proc/cmdline; then
    mkdir -p /var/lib/axon
    date +%s > /var/lib/axon/last-rollback
    echo "Axon OS booted from the factory rollback snapshot." \
        > /etc/motd.d/axon-rollback 2>/dev/null || true
fi
```
**Correct:** Records rollback timestamp and notifies the user via MOTD.

**Note on `set -u`:** The script uses `set -u` (treat unset variables as errors) but not `set -e` (exit on error). This is intentional — the boot-ok script should NEVER cause boot to fail. If `grub-editenv` fails, we want to continue.

### axon-boot-ok.service Analysis
**File:** `build/config/axon-boot-ok.service`

```ini
[Unit]
After=display-manager.service
Wants=display-manager.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/axon-boot-ok

[Install]
WantedBy=graphical.target
```

**Correct:** Runs after the display manager loads (indicating a successful graphical boot). Uses `graphical.target` as the install target.

**Potential issue:** If the display manager takes a long time to start or fails silently, the boot-ok service might not run in time. However, `After=` ensures it runs after GDM starts, which is the right signal.

---

## FIRSTBOOT ANALYSIS

### firstboot.sh (User First-Login)
**File:** `build/config/firstboot.sh`
**Shell:** `#!/usr/bin/env bash` with `set -euo pipefail`

#### Idempotency
```bash
DONE="${HOME}/.config/axon-os/.firstboot-done"
[[ -f "${DONE}" ]] && exit 0
# ... work ...
touch "${DONE}"
```
**Correct:** Guard file at the end ensures the script only runs once. If the script crashes mid-execution, the guard file is NOT created, so the next login will re-attempt.

#### Partial Failure Recovery
If the script crashes halfway through:
- Files may be partially copied (e.g., apps copied but services not yet)
- GNOME extension may be copied but not compiled
- D-Bus services may be registered but systemd units not enabled
- The gsettings calls at step 5 may not have run

On the next login, the script re-runs from the beginning. This means:
- Files are re-copied (idempotent, overwrites existing)
- GNOME extension is re-compiled (idempotent)
- D-Bus and systemd are re-registered (idempotent)
- gsettings are re-applied (idempotent)

**Assessment:** Partial failure is handled correctly because all operations are idempotent. The only concern is wasted time on re-copy.

#### Issues

**Issue 1: Unconditional File Copying**
```bash
cp -r "${AXON_SYS_DIR}/apps/"* "${APPS_DEST}/"
```
This copies ALL apps from the system directory to the user directory on every first boot. If a user modifies a file in `~/.local/share/axon-os/`, the next firstboot (which shouldn't happen due to guard file) would overwrite it. But since the guard file prevents re-runs, this is fine.

**Issue 2: gsettings Schema Override**
```bash
gsettings set org.gnome.desktop.wm.preferences workspace-names \
    "[Code,Web,Chat,Files,Media,Work,Personal,Terminal,Notes]"
```
This hardcodes workspace names. Users who customize these will lose their settings if firstboot somehow re-runs. Again, the guard file prevents this.

**Issue 3: Ollama Started in Background**
```bash
if command -v ollama &>/dev/null; then
    if ! pgrep -x ollama &>/dev/null; then
        ollama serve &>/dev/null &
    fi
fi
```
Ollama is started as a background process from a desktop autostart script. This is fragile:
- No systemd integration for the Ollama service in the user session
- The process may be killed when the session ends
- No logging if Ollama fails to start

Better approach: Use a systemd user service for Ollama.

**Issue 4: Welcome App Launched on Every Non-Live Boot (if file exists)**
```bash
if ! grep -q boot=casper /proc/cmdline && [[ -f "${APPS_DEST}/axon-welcome/main.py" ]]; then
    python3 "${APPS_DEST}/axon-welcome/main.py" &
fi
```
This launches the welcome app only if:
1. Not in live session
2. The welcome app file exists

But wait — the guard file should prevent this from running on subsequent boots. The welcome app only launches on the FIRST boot. This is correct.

### ai-firstboot.sh (AI/Ollama Setup)
**File:** `build/config/ai-firstboot.sh`
**Shell:** `#!/usr/bin/env bash` with `set -uo pipefail` (no `-e`)

#### Retry Behavior
The script is designed for graceful retry:
- If no `ai-setup.json` exists: exits immediately
- If `install_ollama` is false: removes marker and exits
- If offline: exits 0 (service "succeeds" but marker stays, retries on next boot)
- If Ollama install fails: exits 0 (retries on next boot)
- If model pull fails: retries 3 times with 10s delay, then exits 0 (retries on next boot)

**This is well-designed for a systemd oneshot service.** The marker file (`/etc/axon/ai-setup.json`) acts as the state machine driver.

#### Security Concern
```bash
curl -fsSL https://ollama.com/install.sh | sh
```
Piping curl output to shell is inherently risky (MITM, compromised CDN). However, this is the **official Ollama installation method** and is standard practice in the Ollama ecosystem. Ollama's install script is well-maintained.

**Recommendation:** Verify the Ollama installer's checksum if possible, or use the official Ollama apt repository instead.

#### Network Wait
```bash
for _ in $(seq 1 60); do
    if curl -sf --max-time 5 https://ollama.com >/dev/null 2>&1; then
        break
    fi
    sleep 5
done
```
Waits up to 5 minutes (60 iterations * 5 seconds). This is reasonable for a first-boot provisioner. The `systemd` service has `TimeoutStartSec=0` (infinite timeout), so the wait is bounded by the script, not systemd.

---

## BRANDING ANALYSIS

### branding.desc
**File:** `installer/branding/axon/branding.desc`

**BUG: Stale Version String**
```yaml
version:             "0.2.0"
shortVersion:        "0.2"
versionedName:       "Axon OS 0.2.0"
shortVersionedName:  "Axon 0.2"
```

The project is at version 0.3.0 (per `pyproject.toml`). The branding descriptor still says 0.2.0. This would show the wrong version in the Calamares installer if it's used.

**BUG: Wrong GitHub URLs**
```yaml
productUrl:          "https://github.com/axonos/axon-os"
supportUrl:          "https://github.com/axonos/axon-os/issues"
```

The actual repository URL is `https://github.com/kaorii-ako/Axon-OS` (as seen in `chroot-setup.sh` line 614). The branding URLs point to a non-existent organization.

### AxonSlideshow.qml
**File:** `installer/branding/axon/AxonSlideshow.qml`

**Well-implemented.** Three slides:
1. Welcome to Axon OS
2. AI-Powered Desktop (Intent Bar, AI Panel, Ollama)
3. Zero Cloud Required

Uses Calamares Slideshow API v2. Auto-advances every 6 seconds. Dark theme consistent with the project's visual identity.

**No issues found.** The QML is clean, well-structured, and uses proper Qt Quick patterns.

---

## CROSS-CUTTING OBSERVATIONS

### GRUB Theme Installation
The GRUB theme is installed in two places:
1. **ISO build** (`build.sh` line 230-233): Copies to `${IMAGE}/boot/grub/themes/axon/`
2. **Chroot setup** (`chroot-setup.sh` line 277-280): Copies to `/boot/grub/themes/axon/` in the installed system

Both copy `unicode.pf2` with a fallback chain:
```bash
cp "${CHROOT}/usr/share/grub/unicode.pf2" "${IMAGE}/boot/grub/themes/axon/unicode.pf2" || \
cp /usr/share/grub/unicode.pf2 "${IMAGE}/boot/grub/themes/axon/unicode.pf2" || true
```
The `|| true` means if neither source exists, the build continues without the font. The GRUB theme would show without proper Unicode rendering, but would still function.

### Boot Watchdog vs initramfs
The boot watchdog relies on:
1. GRUB reading `grubenv` from the ESP — requires FAT filesystem support (built into GRUB)
2. `grub-editenv` command — installed via `grub-efi-amd64` package
3. BTRFS subvolume support — requires `btrfs-progs` and kernel module

All dependencies are properly declared in `packages.list`. The initramfs includes btrfs support because `btrfs-progs` is installed before `update-initramfs -u`.

### Security of Boot Watchdog
The GRUB environment file on the ESP is world-readable (FAT32 has no permissions). The `boot_attempts` counter is not secret, but an attacker could:
1. Reset the counter by editing the grubenv file
2. Modify the rollback entry

Mitigations:
- Secure Boot would prevent unauthorized GRUB modifications
- The ESP is typically not mounted in the running system (mounted only briefly by `boot-ok.sh`)
- Physical access is required for ESP manipulation

---

## SUMMARY TABLE

| ID | Severity | Description | Location |
|----|----------|-------------|----------|
| BRD-1 | HIGH | Branding version stale (0.2.0 vs 0.3.0) | `branding.desc` |
| BRD-2 | HIGH | Branding URLs point to wrong GitHub org | `branding.desc` |
| BOOT-1 | MEDIUM | Ollama started as background process, not systemd user service | `firstboot.sh` |
| BOOT-2 | MEDIUM | ai-firstboot uses `curl \| sh` pattern | `ai-firstboot.sh` |
| BOOT-3 | LOW | No mechanism to refresh @axon-fallback snapshot | `grub.d-42_axon_rollback` |
| BOOT-4 | LOW | GRUB font fallback silently ignores missing unicode.pf2 | `build.sh`, `chroot-setup.sh` |
| FB-1 | LOW | firstboot partial failure re-runs all steps (wasteful but safe) | `firstboot.sh` |
| -- | OK | Boot watchdog counter logic is correct | `grub.d-06_axon_watchdog` |
| -- | OK | Rollback preserves user data (@home separate) | `grub.d-42_axon_rollback` |
| -- | OK | boot-ok.sh never causes boot failure (no set -e) | `axon-boot-ok.sh` |
| -- | OK | Firstboot is idempotent via guard file | `firstboot.sh` |
| -- | OK | ai-firstboot retry logic is well-designed | `ai-firstboot.sh` |
