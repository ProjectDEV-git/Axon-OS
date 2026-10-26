#!/bin/bash
set -euo pipefail

# win-sandbox-runner installer
# Usage: sudo ./scripts/install.sh [--prefix /usr/local]

PREFIX="${1:-/usr/local}"
BINDIR="${PREFIX}/bin"
LIBDIR="${PREFIX}/lib"
WINE_DLLDIR="/usr/lib/wine/x86_64-windows"
SYSTEMD_DIR="/etc/systemd/system"
BINFMET_DIR="/etc/binfmt.d"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# --- Pre-flight checks ---

check_root() {
    if [[ $EUID -ne 0 ]]; then
        error "This script must be run as root (use sudo)"
    fi
}

check_deps() {
    local missing=()
    for cmd in cargo wine bwrap; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        error "Missing dependencies: ${missing[*]}"
    fi
}

check_binfmt_conflict() {
    if [[ -d /proc/sys/fs/binfmt_misc ]] && [[ -f /proc/sys/fs/binfmt_misc/Windows_PE ]]; then
        error "An existing MZ binfmt handler (Windows_PE) is already registered.
Please remove it first: echo -1 > /proc/sys/fs/binfmt_misc/Windows_PE"
    fi
    # Check for Wine's own binfmt
    if command -v update-binfmts &>/dev/null; then
        if update-binfmts --list 2>/dev/null | grep -q "MZ"; then
            error "An existing MZ binfmt handler is registered via update-binfmts.
Please remove it first: sudo update-binfmts --disable wine"
        fi
    fi
}

# --- Build ---

build_rust() {
    info "Building Rust workspace..."
    cargo build --workspace --release
    info "Running clippy..."
    cargo clippy --workspace -- -D warnings 2>/dev/null || warn "Clippy warnings found (non-fatal)"
}

build_c() {
    if command -v x86_64-w64-mingw32-gcc &>/dev/null; then
        info "Building C components..."
        make all
    else
        warn "MinGW not found, skipping Wine DLL build"
        warn "Install: apt install gcc-mingw-w64-x86-64"
        # Build only native components
        make -C csrc/win-tap-bridge 2>/dev/null || true
    fi
}

# --- Install ---

install_rust_binaries() {
    info "Installing Rust binaries to ${BINDIR}..."
    install -Dm755 target/release/win-sandbox-runner "${DESTDIR:-}${BINDIR}/win-sandbox-runner"
    install -Dm755 target/release/win-sandbox-gui "${DESTDIR:-}${BINDIR}/win-sandbox-gui"
}

install_c_components() {
    # TAP bridge
    if [[ -f csrc/win-tap-bridge/win-tap-bridge ]]; then
        info "Installing win-tap-bridge to ${BINDIR}..."
        install -Dm755 csrc/win-tap-bridge/win-tap-bridge "${DESTDIR:-}${BINDIR}/win-tap-bridge"
    fi

    # Wine DLL
    if [[ -f csrc/sys_netmp/sys_netmp.dll ]]; then
        info "Installing sys_netmp.dll to ${WINE_DLLDIR}..."
        install -Dm755 csrc/sys_netmp/sys_netmp.dll "${DESTDIR:-}${WINE_DLLDIR}/sys_netmp.dll"
    fi

    # eBPF
    if [[ -f csrc/win_tap_filter/loader ]]; then
        info "Installing eBPF loader to ${BINDIR}..."
        install -Dm755 csrc/win_tap_filter/loader "${DESTDIR:-}${BINDIR}/win_tap_filter-loader"
    fi
    if [[ -f csrc/win_tap_filter/win_tap_filter.bpf.o ]]; then
        info "Installing eBPF object to ${LIBDIR}/win_tap_filter/..."
        install -Dm644 csrc/win_tap_filter/win_tap_filter.bpf.o "${DESTDIR:-}${LIBDIR}/win_tap_filter/win_tap_filter.bpf.o"
    fi
}

install_config() {
    info "Installing configuration files..."
    install -Dm644 config/rules.json "${DESTDIR:-}/etc/win-sandbox-runner/rules.json"
    install -Dm644 config/rules.schema.json "${DESTDIR:-}/etc/win-sandbox-runner/rules.schema.json"
    install -Dm644 config/win-sandbox-runner.conf "${DESTDIR:-}/etc/win-sandbox-runner.conf"

    # User config directory
    local user_home="${SUDO_HOME:-$HOME}"
    if [[ -n "$user_home" && "$user_home" != "/" ]]; then
        mkdir -p "${user_home}/.config/win-sandbox"
        if [[ ! -f "${user_home}/.config/win-sandbox/rules.json" ]]; then
            cp config/rules.json "${user_home}/.config/win-sandbox/rules.json"
            info "User rules created at ${user_home}/.config/win-sandbox/rules.json"
        fi
    fi
}

install_binfmt() {
    info "Installing binfmt handler..."
    # The definition comes from register-binfmt.sh --print: one place knows the
    # format. It was previously duplicated and the same missing-mask bug (which
    # makes the kernel return EINVAL) had to be fixed three separate times.
    install -d "${DESTDIR:-}${BINFMET_DIR}"
    BINDIR="${BINDIR}" sh scripts/register-binfmt.sh --print \
        > "${DESTDIR:-}${BINFMET_DIR}/apex-win.conf"
    # Also register immediately (more reliable than waiting for systemd-binfmt)
    if [[ -d /proc/sys/fs/binfmt_misc ]]; then
        BINDIR="${BINDIR}" sh scripts/register-binfmt.sh
    fi
}

install_systemd() {
    info "Installing systemd units..."
    install -Dm644 scripts/win-sandbox-runner.service "${DESTDIR:-}${SYSTEMD_DIR}/"
    # Also install legacy service files if they exist
    [[ -f systemd/win-tap-bridge.service ]] && install -Dm644 systemd/win-tap-bridge.service "${DESTDIR:-}${SYSTEMD_DIR}/"
    [[ -f systemd/win_tap_filter.service ]] && install -Dm644 systemd/win_tap_filter.service "${DESTDIR:-}${SYSTEMD_DIR}/"
    # Reload systemd to pick up new service
    systemctl daemon-reload 2>/dev/null || true
}

# Desktop MIME handler — this is what actually makes double-click work.
#
# Nautilus (and every other file manager) resolves a MIME handler and never
# exec()s the .exe, so binfmt_misc alone does NOT cover the double-click path.
# This was previously only done by `make quick-install`, which meant anyone
# following the documented `sudo ./scripts/install.sh` route got a working
# daemon and a non-working double-click, with nothing reporting a problem.
install_desktop_handler() {
    info "Installing double-click MIME handler..."
    install -Dm644 scripts/apex-win.desktop \
        "${DESTDIR:-}/usr/share/applications/apex-win.desktop"
    update-desktop-database /usr/share/applications 2>/dev/null || true

    # Register as default for every MIME type a PE binary may be detected as.
    # xdg-mime writes PER-USER preferences, so it must run as the invoking
    # user: under sudo it would set root's associations and leave the actual
    # desktop session unchanged.
    local mimes="application/vnd.microsoft.portable-executable \
application/x-ms-dos-executable application/x-msdownload application/x-msi"
    for m in $mimes; do
        if [[ -n "${SUDO_USER:-}" ]]; then
            sudo -u "$SUDO_USER" xdg-mime default apex-win.desktop "$m" 2>/dev/null || true
        else
            xdg-mime default apex-win.desktop "$m" 2>/dev/null || true
        fi
    done
    info "Double-click handler registered for .exe files"
}

# --- Main ---

main() {
    echo "win-sandbox-runner installer"
    echo "==========================="
    echo ""

    check_root
    check_deps
    check_binfmt_conflict

    build_rust
    build_c

    install_rust_binaries
    install_c_components
    install_config
    install_binfmt
    install_systemd
    install_desktop_handler

    echo ""
    info "Installation complete!"
    echo ""
    echo "Quick start (background mode):"
    echo "  sudo systemctl enable --now win-sandbox-runner   # Start daemon + binfmt"
    echo "  /path/to/app.exe                                 # Run any .exe transparently"
    echo ""
    echo "Manual mode (no daemon):"
    echo "  win-sandbox-runner --exe app.exe                 # Run an app directly"
    echo "  sudo win-sandbox-runner --optimize-net            # Optimize network for gaming"
    echo ""
    echo "Daemon management:"
    echo "  win-sandbox-runner --status                       # Check daemon status"
    echo "  win-sandbox-runner --reload                       # Reload rules"
    echo "  sudo systemctl stop win-sandbox-runner            # Stop daemon"
    echo ""
    echo "Configuration:"
    echo "  /etc/win-sandbox-runner/rules.json                # Sandbox rules"
    echo "  ~/.config/win-sandbox/rules.json                  # User rules"
    echo "  ~/.config/win-sandbox/net-optimizer.json           # Network tuning"
    echo ""
}

main "$@"
