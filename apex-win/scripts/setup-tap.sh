#!/bin/bash
set -euo pipefail

# TAP device setup helper for win-sandbox-runner
# Usage: sudo ./scripts/setup-tap.sh [--device winrunner-tap0]

TAP_DEVICE="${1:-winrunner-tap0}"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

if [[ $EUID -ne 0 ]]; then
    error "This script must be run as root (use sudo)"
fi

# Load tun module if not loaded
if ! lsmod | grep -q "^tun "; then
    info "Loading tun kernel module..."
    modprobe tun
fi

# Check if device already exists
if ip link show "$TAP_DEVICE" &>/dev/null; then
    info "TAP device $TAP_DEVICE already exists"
    ip link show "$TAP_DEVICE"
else
    info "Creating TAP device: $TAP_DEVICE"
    ip tuntap add dev "$TAP_DEVICE" mode tap
fi

# Bring the interface up
info "Bringing up $TAP_DEVICE..."
ip link set "$TAP_DEVICE" up

# Assign a link-local address for the bridge
info "Assigning link-local address..."
ip addr flush dev "$TAP_DEVICE" 2>/dev/null || true
ip addr add 169.254.169.1/24 dev "$TAP_DEVICE" 2>/dev/null || true

info "TAP device $TAP_DEVICE is ready"
echo ""
echo "Configuration:"
ip addr show "$TAP_DEVICE"
echo ""
echo "To start the bridge daemon: sudo systemctl start win-tap-bridge"
echo "To enable at boot:         sudo systemctl enable win-tap-bridge"
