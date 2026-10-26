#!/bin/sh
# Enable the New Wave Concepts "GENIE USB Cable" (FTDI chip, custom PID 0403:a5d8)
# so it appears as /dev/genie and Circuit Wizard (Wine) can talk to it on COM1.
#
# The stock ftdi_sio driver ignores this custom product ID, so we:
#   1. Load ftdi_sio and register the custom vendor/product ID
#   2. Persist across reboots (modprobe option + modules-load + udev rule)
#   3. Create a stable /dev/genie symlink via udev
#   4. Point Wine's COM1 at /dev/genie
#
# Run with: sudo sh setup-genie-serial.sh
set -e

VID=0403
PID=a5d8

# Resolve the invoking user's home even under sudo, so the Wine prefix is correct.
if [ -n "$SUDO_USER" ]; then
    REAL_USER="$SUDO_USER"
    REAL_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
    REAL_USER="$(id -un)"
    REAL_HOME="$HOME"
fi
WINEPREFIX="${WINEPREFIX:-$REAL_HOME/.wine}"

echo "[genie] Loading ftdi_sio kernel module..."
modprobe ftdi_sio

echo "[genie] Registering custom FTDI product ID $VID:$PID with ftdi_sio..."
if [ -w /sys/bus/usb-serial/drivers/ftdi_sio/new_id ]; then
    echo "$VID $PID" > /sys/bus/usb-serial/drivers/ftdi_sio/new_id 2>/dev/null || true
fi

# --- Persistence #1: modprobe option so the PID is known every boot ---
echo "[genie] Writing /etc/modprobe.d/genie-ftdi.conf ..."
cat > /etc/modprobe.d/genie-ftdi.conf <<EOF
# GENIE USB Cable (New Wave Concepts) uses a custom FTDI product ID.
options ftdi_sio vendor=0x$VID product=0x$PID
EOF

# --- Persistence #2: load module at boot ---
echo "ftdi_sio" > /etc/modules-load.d/genie-ftdi.conf

# --- Persistence #3: udev rule creates the stable /dev/genie symlink ---
echo "[genie] Writing /etc/udev/rules.d/99-genie-usb.rules ..."
cat > /etc/udev/rules.d/99-genie-usb.rules <<EOF
# GENIE USB Cable: force ftdi_sio to bind the custom product ID, then expose a
# stable /dev/genie node owned by the dialout group.
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="$VID", ATTR{idProduct}=="$PID", \\
  RUN+="/bin/sh -c 'modprobe ftdi_sio; echo $VID $PID > /sys/bus/usb-serial/drivers/ftdi_sio/new_id'"
ACTION=="add", SUBSYSTEM=="tty", ATTRS{idVendor}=="$VID", ATTRS{idProduct}=="$PID", \\
  GROUP="dialout", MODE="0660", SYMLINK+="genie"
EOF

udevadm control --reload-rules 2>/dev/null || true
udevadm trigger 2>/dev/null || true
sleep 1

# --- Make sure the user can actually open the port ---
if ! id -nG "$REAL_USER" | tr ' ' '\n' | grep -qx dialout; then
    echo "[genie] Adding $REAL_USER to the 'dialout' group..."
    usermod -aG dialout "$REAL_USER"
    echo "[genie] NOTE: log out and back in for the new group to take effect."
fi

# --- Point Wine COM1 at the GENIE cable ---
# We always link COM1 -> /dev/genie (not /dev/ttyUSBx). The udev SYMLINK above
# guarantees /dev/genie appears whenever the cable is plugged in, so the Wine
# link stays valid across replugs and reboots.
DOSDEV="$WINEPREFIX/dosdevices"
if [ -d "$DOSDEV" ]; then
    ln -sfn /dev/genie "$DOSDEV/com1"
    chown -h "$REAL_USER":"$REAL_USER" "$DOSDEV/com1" 2>/dev/null || true
    echo "[genie] Linked Wine COM1 -> /dev/genie in $DOSDEV"
else
    echo "[genie] WARNING: Wine prefix '$WINEPREFIX' has no dosdevices directory."
fi

# --- Report status ---
if [ -e /dev/genie ]; then
    echo "[genie] Cable detected: /dev/genie -> $(readlink -f /dev/genie)"
else
    echo "[genie] Cable not currently plugged in (that's fine - the rules are installed)."
    echo "[genie] Plug the GENIE cable in and /dev/genie will appear automatically."
fi

cat <<'EOF'

[genie] Done.

IMPORTANT: Wine's `wineboot` regenerates the COM port symlinks and will reset
COM1 back to /dev/ttyS0. Launch Circuit Wizard with the wrapper script
(scripts/circuit-wizard) which re-applies the COM1 link every time, or re-run
this script after any wineboot / winecfg.

In Circuit Wizard the port is COM1 (Settings are stored under
HKCU\Software\New Wave Concepts\Circuit Wizard 3\Preferences\PortName).
EOF
