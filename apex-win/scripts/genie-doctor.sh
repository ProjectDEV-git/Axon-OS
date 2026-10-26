#!/bin/sh
# Diagnose GENIE USB cable <-> Circuit Wizard connectivity.
# Run with: sh scripts/genie-doctor.sh     (no root needed)

VID=0403
PID=a5d8
WINEPREFIX="${WINEPREFIX:-$HOME/.wine}"
FAIL=0

ok()   { echo "  [ OK ] $1"; }
bad()  { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
warn() { echo "  [WARN] $1"; }

echo "GENIE cable diagnostics"
echo "======================="

echo
echo "1. USB device"
if lsusb 2>/dev/null | grep -qi "$VID:$PID"; then
    ok "Cable present: $(lsusb | grep -i "$VID:$PID")"
    PLUGGED=1
else
    warn "Cable $VID:$PID not plugged in. Plug it in to test the full chain."
    PLUGGED=0
fi

echo
echo "2. Kernel driver"
if lsmod | grep -q '^ftdi_sio'; then
    ok "ftdi_sio module loaded"
else
    bad "ftdi_sio not loaded -> run: sudo modprobe ftdi_sio"
fi

echo
echo "3. Persistence files"
for f in /etc/modprobe.d/genie-ftdi.conf \
         /etc/modules-load.d/genie-ftdi.conf \
         /etc/udev/rules.d/99-genie-usb.rules; do
    if [ -f "$f" ]; then ok "$f"; else bad "missing $f -> run setup-genie-serial.sh"; fi
done

echo
echo "4. Serial device node"
if [ -e /dev/genie ]; then
    ok "/dev/genie -> $(readlink -f /dev/genie)"
    DEV=$(readlink -f /dev/genie)
elif ls /dev/ttyUSB* >/dev/null 2>&1; then
    DEV=$(ls /dev/ttyUSB* | head -1)
    warn "/dev/genie missing, but found $DEV (udev SYMLINK rule may not have fired)"
elif [ "$PLUGGED" = "1" ]; then
    bad "cable is plugged in but no serial node exists -> re-run setup-genie-serial.sh"
    DEV=""
else
    warn "no serial node (expected: cable unplugged)"
    DEV=""
fi

echo
echo "5. Permissions"
if id -nG | tr ' ' '\n' | grep -qx dialout; then
    ok "user $(id -un) is in the 'dialout' group"
else
    bad "user $(id -un) NOT in 'dialout' -> sudo usermod -aG dialout $(id -un), then log out/in"
fi
if [ -n "$DEV" ]; then
    if [ -r "$DEV" ] && [ -w "$DEV" ]; then
        ok "$DEV is readable and writable"
    else
        bad "$DEV is not accessible: $(ls -l "$DEV")"
    fi
fi

echo
echo "6. Wine COM1 mapping"
COM1="$WINEPREFIX/dosdevices/com1"
if [ -L "$COM1" ]; then
    TARGET=$(readlink "$COM1")
    case "$TARGET" in
        /dev/genie|/dev/ttyUSB*)
            ok "COM1 -> $TARGET" ;;
        /dev/ttyS*)
            bad "COM1 -> $TARGET (phantom motherboard port; wineboot reset it)"
            echo "         Fix: ln -sfn /dev/genie $COM1"
            echo "         Or launch via: scripts/circuit-wizard (re-links automatically)" ;;
        *)
            bad "COM1 -> $TARGET (unexpected)" ;;
    esac
else
    bad "no COM1 symlink in $WINEPREFIX/dosdevices"
fi

echo
echo "7. Circuit Wizard port preference"
PORT=$(grep -A8 'Circuit Wizard 3\\\\Preferences' "$WINEPREFIX/user.reg" 2>/dev/null \
       | grep '"PortName"' | cut -d'"' -f4)
if [ -n "$PORT" ]; then
    if [ "$PORT" = "COM1" ]; then
        ok "Circuit Wizard is set to $PORT"
    else
        warn "Circuit Wizard is set to $PORT but this setup wires COM1"
    fi
else
    warn "no PortName preference found (Circuit Wizard may not have run yet)"
fi

echo
echo "8. Port open test"
if [ -n "$DEV" ] && [ -r "$DEV" ]; then
    python3 - "$DEV" <<'PY' 2>/dev/null || warn "python3 unavailable, skipped"
import os, sys, termios
dev = sys.argv[1]
try:
    fd = os.open(dev, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    termios.tcgetattr(fd)
    os.close(fd)
    print(f"  [ OK ] {dev} opens cleanly")
except Exception as e:
    print(f"  [FAIL] {dev} will not open: {e}")
    sys.exit(1)
PY
else
    warn "skipped (no accessible device)"
fi

echo
echo "======================="
if [ "$FAIL" -eq 0 ]; then
    echo "All checks passed. Launch with: circuit-wizard"
else
    echo "$FAIL check(s) failed. See the Fix hints above."
fi
exit "$FAIL"
