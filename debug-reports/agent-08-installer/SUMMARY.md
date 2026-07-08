# Debug Agent 8 — SUMMARY: Installer & Build System

**Date:** 2026-07-02
**Project:** Axon OS
**Scope:** Installer edge cases, build reproducibility, GRUB/boot configuration

---

## CRITICAL BUGS (Must Fix)

| # | Issue | File(s) | Impact |
|---|-------|---------|--------|
| C1 | **No BIOS boot support in older wizard** — `partitioner.py` always creates EFI-only GPT; `axon-installer.py` hardcodes `grub-install --target=x86_64-efi`. BIOS-only machines cannot install. | `installer/partitioner.py:265-303`, `installer/axon-installer.py:816-827` | Installation fails on BIOS systems |
| C2 | **Package versions not pinned** in `packages.list` — every build gets different Ubuntu package versions, making ISOs non-reproducible. | `build/config/packages.list`, `build/config/chroot-setup.sh:76-97` | Builds differ between runs |
| C3 | **Three inconsistent installer paths** exist (older GTK wizard, newer GTK wizard, Calamares) with different feature sets, validation rules, and bug states. | `installer/axon-installer.py`, `apps/axon-installer/ui/wizard.py`, `installer/settings.conf` | Maintenance burden, user confusion |

---

## HIGH SEVERITY (Should Fix)

| # | Issue | File(s) | Impact |
|---|-------|---------|--------|
| H1 | **No password minimum length** in older wizard — accepts single-character passwords. Newer wizard requires 4+ chars. | `installer/axon-installer.py:164` | Weak passwords possible |
| H2 | **WhiteSur theme defaults to `master` branch** — non-reproducible theme appearance across builds. | `build/config/chroot-setup.sh:426-442` | Theme drift |
| H3 | **Branding version stale** — `branding.desc` says 0.2.0, project is 0.3.0. Also URLs point to wrong GitHub org. | `installer/branding/axon/branding.desc` | Wrong version in Calamares UI |
| H4 | **No disk existence validation** in `install_engine.py` — validates path format but not that the device exists or is a block device. | `apps/axon-installer/install_engine.py:100-128` | Invalid config causes runtime error |
| H5 | **GRUB config appended without dedup** in older wizard — duplicate `GRUB_CMDLINE_LINUX_DEFAULT` entries. | `installer/axon-installer.py:746-751` | Wrong GRUB parameters |

---

## WARNINGS (Recommended Fixes)

| # | Issue | File(s) | Impact |
|---|-------|---------|--------|
| W1 | Timestamps in `.disk/info` and SBOM not using `SOURCE_DATE_EPOCH` | `build/build.sh:222,384` | Minor non-determinism |
| W2 | `pip3 install` without version pins in chroot setup | `build/config/chroot-setup.sh:103` | Python deps drift |
| W3 | Ollama started as background process instead of systemd user service | `build/config/firstboot.sh:116-119` | Fragile process management |
| W4 | `curl \| sh` pattern for Ollama install (standard but risky) | `build/config/ai-firstboot.sh:42` | Supply chain risk |
| W5 | No mechanism to refresh `@axon-fallback` snapshot after updates | `build/config/grub.d-42_axon_rollback` | Rollback always goes to initial install |
| W6 | ShellCheck not run on build/boot scripts in QA pipeline | `scripts/qa.sh` | Lint coverage gap |
| W7 | GPG public key not distributed for ISO signature verification | `scripts/verify-iso.sh` | Signature check always fails |

---

## POSITIVE FINDINGS

- **Boot watchdog logic is correct and safe.** The counter (1→2→3→recovery) works as designed, `boot-ok.sh` never causes boot failure (no `set -e`), and the recovery entry properly boots the `@axon-fallback` subvolume.
- **User data is preserved during rollback.** The `@home` subvolume is separate from `@` and `@axon-fallback`, so rollback only affects system files.
- **Firstboot is idempotent** via guard file (`~/.config/axon-os/.firstboot-done`). All operations are idempotent.
- **AI firstboot retry logic is well-designed.** Uses a marker file as state machine; retries on next boot if offline or if operations fail.
- **NVMe/virtio disk naming is handled correctly** in both installers via `device[-1].isdigit()` checks.
- **APTEOF heredocs are correctly escaped** — no unexpanded variables in any heredoc across the build system.
- **`install_engine.py` is well-structured** — proper BTRFS fallback to ext4, clean error handling, swap failure is non-fatal, and the config is deleted from disk after reading.
- **SOURCE_DATE_EPOCH is set from git commit timestamp** for reproducibility, even if not fully utilized.

---

## RECOMMENDATIONS

1. **Deprecate the older wizard** (`installer/axon-installer.py` + `installer/partitioner.py`) and consolidate on the newer `apps/axon-installer/` path. The older path is missing BIOS support, password validation, read-only disk detection, network setup, and AI configuration.

2. **Pin package versions** in `packages.list` for reproducible builds. Consider using `apt-mark hold` or building from a snapshot repository.

3. **Pin WhiteSur theme commits** to specific SHAs instead of `master`.

4. **Update `branding.desc`** to version 0.3.0 and correct GitHub URLs.

5. **Add disk existence validation** to `validate_config()` in `install_engine.py`.

6. **Expand ShellCheck** to cover `build.sh`, `chroot-setup.sh`, `firstboot.sh`, and GRUB scripts in the QA pipeline.
