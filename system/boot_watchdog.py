#!/usr/bin/env python3
import subprocess
import sys

def reset_boot_counter():
    try:
        # Reset the boot counter environment variable in GRUB
        res = subprocess.run(["grub-editenv", "-", "set", "boot_attempts=0"], capture_output=True, text=True)
        if res.returncode == 0:
            print("[boot-watchdog] Boot attempts counter successfully reset to 0.")
            sys.exit(0)
        else:
            print(f"[boot-watchdog] Failed to reset boot attempts: {res.stderr}")
            # Try resetting with default filepath fallback
            fallback = subprocess.run(["grub-editenv", "/boot/grub/grubenv", "set", "boot_attempts=0"], capture_output=True, text=True)
            if fallback.returncode == 0:
                print("[boot-watchdog] Boot attempts counter reset via fallback path.")
                sys.exit(0)
            sys.exit(1)
    except Exception as e:
        print(f"[boot-watchdog] Exception during watchdog run: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    reset_boot_counter()
