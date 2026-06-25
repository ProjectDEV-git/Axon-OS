#!/usr/bin/env python3
import logging
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from axon_logger import configure_app_logger

log = configure_app_logger("boot-watchdog", level=logging.INFO)


def reset_boot_counter():
    if os.geteuid() != 0:
        print("boot_watchdog: must be run as root", file=sys.stderr)  # noqa: T201
        sys.exit(1)

    grubenv = "/boot/grub/grubenv"
    try:
        if not os.path.exists(grubenv):
            log.error("grubenv file not found at %s", grubenv)
            sys.exit(1)
        res = subprocess.run(
            ["grub-editenv", grubenv, "set", "boot_attempts=0"], capture_output=True, text=True
        )
        if res.returncode == 0:
            log.info("Boot attempts counter successfully reset to 0.")
            sys.exit(0)
        else:
            log.error("Failed to reset boot attempts: %s", res.stderr)
            sys.exit(1)
    except Exception as e:
        log.exception("Exception during watchdog run: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    reset_boot_counter()
