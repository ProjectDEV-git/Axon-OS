#!/usr/bin/env bash
# Axon OS — keep-chroot: interactive chroot development tool.
#
# Creates, configures, and maintains a persistent chroot environment for
# iterative development without rebuilding the full ISO each time.
#
# Usage:
#   sudo bash scripts/keep-chroot.sh [--setup] [--reset] [--cmd '...']
#
# Examples:
#   sudo bash scripts/keep-chroot.sh              # enter interactive shell
#   sudo bash scripts/keep-chroot.sh --setup      # run chroot-setup.sh then enter
#   sudo bash scripts/keep-chroot.sh --reset      # destroy and recreate chroot
#   sudo bash scripts/keep-chroot.sh --cmd "apt list --installed"  # run one command
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DIST="noble"
ARCH="amd64"
MIRROR="http://us.archive.ubuntu.com/ubuntu/"

WORK_DIR="${AXON_BUILD_DIR:-/tmp/axon-build}"
CHROOT="${WORK_DIR}/chroot"
APT_CACHE="${WORK_DIR}/apt-cache"

SETUP=false
RESET=false
RUN_CMD=""

log() { echo "[keep-chroot] $*"; }
die() { echo "[keep-chroot] ERROR: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "${1}" in
        --setup)  SETUP=true; shift ;;
        --reset)  RESET=true; shift ;;
        --cmd)
            [[ $# -lt 2 ]] && die "--cmd requires an argument"
            RUN_CMD="${2}"; shift 2 ;;
        *)
            echo "[keep-chroot] Unknown option: ${1}" >&2
            echo "Usage: sudo bash scripts/keep-chroot.sh [--setup] [--reset] [--cmd '...']" >&2
            exit 2 ;;
    esac
done

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
[[ ${EUID} -eq 0 ]] || die "This script must run as root (try: sudo bash scripts/keep-chroot.sh)"

check_deps() {
    local deps=(debootstrap rsync)
    local missing=()
    for dep in "${deps[@]}"; do
        command -v "${dep}" &>/dev/null || missing+=("${dep}")
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        log "Installing missing dependencies: ${missing[*]}"
        apt-get update -qq
        apt-get install -y -qq "${missing[@]}"
    fi
}

# ---------------------------------------------------------------------------
# Chroot mount management
# ---------------------------------------------------------------------------
MOUNTED=false

mount_chroot() {
    [[ "${MOUNTED}" == "true" ]] && return 0
    log "Mounting chroot filesystems..."
    mount --bind /dev "${CHROOT}/dev"
    mount --bind /dev/pts "${CHROOT}/dev/pts"
    mount -t proc proc "${CHROOT}/proc"
    mount -t sysfs sysfs "${CHROOT}/sys"
    # Persistent APT cache
    mkdir -p "${APT_CACHE}"
    mkdir -p "${CHROOT}/var/cache/apt/archives"
    mount --bind "${APT_CACHE}" "${CHROOT}/var/cache/apt/archives"
    # DNS resolution
    if grep -q "127.0.0.53" /etc/resolv.conf 2>/dev/null; then
        log "Host uses systemd-resolved. Writing fallback DNS..."
        printf "nameserver 8.8.8.8\nnameserver 1.1.1.1\n" > "${CHROOT}/etc/resolv.conf"
    else
        cp /etc/resolv.conf "${CHROOT}/etc/resolv.conf"
    fi
    MOUNTED=true
}

umount_chroot() {
    [[ "${MOUNTED}" == "true" ]] || return 0
    log "Unmounting chroot filesystems..."
    umount -lf "${CHROOT}/var/cache/apt/archives" 2>/dev/null || true
    umount -lf "${CHROOT}/dev/pts" 2>/dev/null || true
    umount -lf "${CHROOT}/dev" 2>/dev/null || true
    umount -lf "${CHROOT}/proc" 2>/dev/null || true
    umount -lf "${CHROOT}/sys" 2>/dev/null || true
    MOUNTED=false
}
trap umount_chroot EXIT

# ---------------------------------------------------------------------------
# Bootstrap: create chroot via debootstrap if missing
# ---------------------------------------------------------------------------
bootstrap() {
    if [[ -d "${CHROOT}" && "${RESET}" != "true" ]]; then
        log "Reusing existing chroot at ${CHROOT}"
        return 0
    fi
    if [[ "${RESET}" == "true" && -d "${CHROOT}" ]]; then
        log "Resetting chroot (removing ${CHROOT})..."
        rm -rf "${CHROOT}"
    fi
    log "Bootstrapping Ubuntu ${DIST} (${ARCH})... (downloads ~100 MB)"
    mkdir -p "${WORK_DIR}" "${CHROOT}"
    debootstrap --arch="${ARCH}" "${DIST}" "${CHROOT}" "${MIRROR}"
    log "Bootstrap complete."
}

# ---------------------------------------------------------------------------
# Setup: copy project sources and run chroot-setup.sh
# ---------------------------------------------------------------------------
setup_chroot() {
    log "Copying project sources into chroot..."
    mkdir -p "${CHROOT}/opt/axon-src"
    rsync -a --delete \
        --exclude='.git' --exclude='.idea' --exclude='dist' \
        --exclude='__pycache__' --exclude='*.pyc' --exclude='*.iso' \
        "${BASE_DIR}/" "${CHROOT}/opt/axon-src/"

    # Read version from pyproject.toml
    local version
    version="$(sed -n 's/^version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "${BASE_DIR}/pyproject.toml" | head -1)"
    version="${version:-0.3.0}"

    log "Running chroot-setup.sh (version ${version})..."
    mount_chroot
    chroot "${CHROOT}" /usr/bin/env \
        AXON_VERSION="${version}" \
        /bin/bash /opt/axon-src/build/config/chroot-setup.sh
    log "Chroot setup complete."
}

# ---------------------------------------------------------------------------
# Enter: drop into interactive shell (or run a single command)
# ---------------------------------------------------------------------------
enter_chroot() {
    mount_chroot
    if [[ -n "${RUN_CMD}" ]]; then
        log "Running command inside chroot: ${RUN_CMD}"
        chroot "${CHROOT}" /usr/bin/env -i \
            HOME=/root \
            TERM="${TERM:-xterm-256color}" \
            PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
            /bin/bash -c "${RUN_CMD}"
    else
        log "Entering chroot shell (exit to leave)..."
        log "Tip: run 'sudo bash scripts/keep-chroot.sh --setup' to configure the chroot first."
        chroot "${CHROOT}" /usr/bin/env -i \
            HOME=/root \
            TERM="${TERM:-xterm-256color}" \
            PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
            /bin/bash --login
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    log "============================================"
    log " Axon OS keep-chroot"
    log " Work dir: ${WORK_DIR}"
    log " Chroot:   ${CHROOT}"
    log "============================================"

    check_deps
    bootstrap

    if [[ "${SETUP}" == "true" ]]; then
        setup_chroot
    fi

    enter_chroot
}

main
