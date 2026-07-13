#!/usr/bin/env python3
import logging
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from axon_logger import configure_app_logger

log = configure_app_logger("boot-watchdog", level=logging.INFO)


def reset_boot_counter() -> int:
    """Reset the GRUB boot attempts counter.

    Returns:
        0 on success, 1 on failure.
    """
    if os.geteuid() != 0:
        log.error("boot_watchdog: must be run as root")
        return 1

    # The GRUB watchdog (/etc/grub.d/06_axon_watchdog) counts boot attempts in
    # a grubenv file on the ESP — GRUB cannot write to btrfs, so /boot/grub/
    # grubenv is never used for the counter. Reset the ESP copy.
    grubenv = "/boot/efi/axon/grubenv"
    try:
        if not os.path.exists(grubenv):
            # Live sessions, ext4 installs, and BIOS systems have no ESP
            # counter — the watchdog is inactive, nothing to reset.
            log.info("No watchdog grubenv at %s; nothing to do.", grubenv)
            return 0
        res = subprocess.run(
            ["grub-editenv", grubenv, "set", "boot_attempts=0"], capture_output=True, text=True
        )
        if res.returncode == 0:
            log.info("Boot attempts counter successfully reset to 0.")
            return 0
        else:
            log.error("Failed to reset boot attempts: %s", res.stderr)
            return 1
    except FileNotFoundError:
        log.error("grub-editenv not found — is grub2-common installed?")
        return 1
    except Exception as e:
        log.exception("Exception during watchdog run: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(reset_boot_counter())
