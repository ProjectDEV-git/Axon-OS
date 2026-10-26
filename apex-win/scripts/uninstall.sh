#!/bin/bash
set -euo pipefail

# win-sandbox-runner uninstaller
# Usage: sudo ./scripts/uninstall.sh

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }

if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}[ERROR]${NC} This script must be run as root (use sudo)"
    exit 1
fi

PREFIX="${1:-/usr/local}"
BINDIR="${PREFIX}/bin"
LIBDIR="${PREFIX}/lib"
WINE_DLLDIR="/usr/lib/wine/x86_64-windows"
SYSTEMD_DIR="/etc/systemd/system"
BINFMET_DIR="/etc/binfmt.d"

info "Stopping services..."
systemctl stop win_tap_filter.service 2>/dev/null || true
systemctl stop win-tap-bridge.service 2>/dev/null || true
systemctl stop win-sandbox-runner.service 2>/dev/null || true

info "Disabling services..."
systemctl disable win_tap_filter.service 2>/dev/null || true
systemctl disable win-tap-bridge.service 2>/dev/null || true
systemctl disable win-sandbox-runner.service 2>/dev/null || true

info "Unregistering binfmt handler..."
# APEX-WIN is the current handler name. Windows_PE is the historical one and is
# cleaned up too, so upgrading from an old install does not leave a stale
# handler competing for the same MZ magic.
for handler in APEX-WIN Windows_PE; do
    if [[ -f "/proc/sys/fs/binfmt_misc/${handler}" ]]; then
        echo -1 > "/proc/sys/fs/binfmt_misc/${handler}" 2>/dev/null \
            && info "  unregistered ${handler}"
    fi
done
# Remove the persistent definitions so they do not come back on reboot
rm -f /etc/binfmt.d/apex-win.conf /etc/binfmt.d/windows-pe.conf

info "Removing binaries..."
rm -f "${BINDIR}/win-sandbox-runner"
rm -f "${BINDIR}/win-sandbox-gui"
rm -f "${BINDIR}/win-tap-bridge"
rm -f "${BINDIR}/win_tap_filter-loader"

info "Removing Wine DLL..."
rm -f "${WINE_DLLDIR}/sys_netmp.dll"

info "Removing eBPF object..."
rm -f "${LIBDIR}/win_tap_filter/win_tap_filter.bpf.o"
rmdir "${LIBDIR}/win_tap_filter" 2>/dev/null || true

info "Removing systemd units..."
rm -f "${SYSTEMD_DIR}/win-sandbox-runner.service"
rm -f "${SYSTEMD_DIR}/win-tap-bridge.service"
rm -f "${SYSTEMD_DIR}/win_tap_filter.service"
systemctl daemon-reload

info "Removing config files..."
rm -f "${BINFMET_DIR}/windows-pe.conf"
# Keep /etc/win-sandbox-runner/ and user config — ask user
if [[ -d /etc/win-sandbox-runner ]]; then
    warn "Keeping /etc/win-sandbox-runner/ (rules, schema, config)"
    warn "To remove: sudo rm -rf /etc/win-sandbox-runner"
fi

info "Removing TAP device if present..."
ip link delete winrunner-tap0 2>/dev/null || true

info "Uninstallation complete!"
echo ""
echo "User config at ~/.config/win-sandbox/ was preserved."
echo "To remove: rm -rf ~/.config/win-sandbox"
