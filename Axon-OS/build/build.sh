#!/usr/bin/env bash
set -euo pipefail

# Axon OS — Master Build Script
# Produces a bootable ISO using live-build on Ubuntu Noble (24.04).

VERSION="0.1.0-alpha"
ISO_NAME="axon-os-${VERSION}.iso"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

WORK_DIR="/tmp/axon-build"
CHROOT_OVERLAY="${WORK_DIR}/config/includes.chroot"

# ---------------------------------------------------------------------------
# check_deps
# Verifies that all required build tools are available on the host.
# ---------------------------------------------------------------------------
check_deps() {
    echo "[axon-build] Checking build dependencies..."

    local deps=(live-build debootstrap xorriso mksquashfs wget rsync)
    local missing=()

    for dep in "${deps[@]}"; do
        if ! command -v "${dep}" &>/dev/null; then
            missing+=("${dep}")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        echo "[axon-build] ERROR: Missing required tools: ${missing[*]}" >&2
        echo "[axon-build] Install them with:" >&2
        echo "  sudo apt-get install -y ${missing[*]}" >&2
        exit 1
    fi

    echo "[axon-build] All dependencies satisfied."
}

# ---------------------------------------------------------------------------
# setup_workdir
# Creates and enters the temporary build working directory.
# ---------------------------------------------------------------------------
setup_workdir() {
    echo "[axon-build] Setting up work directory at ${WORK_DIR}..."

    if [[ -d "${WORK_DIR}" ]]; then
        sudo rm -rf "${WORK_DIR}"
    fi
    mkdir -p "${WORK_DIR}"
    cd "${WORK_DIR}"

    echo "[axon-build] Work directory ready: ${WORK_DIR}"
}

# ---------------------------------------------------------------------------
# configure_chroot
# Runs lb config to set up a live-build tree targeting Ubuntu Noble amd64.
# ---------------------------------------------------------------------------
configure_chroot() {
    echo "[axon-build] Configuring live-build (Ubuntu Noble / amd64)..."

    lb config \
        --mode ubuntu \
        --distribution noble \
        --architecture amd64 \
        --archive-areas "main restricted universe multiverse" \
        --mirror-bootstrap "http://archive.ubuntu.com/ubuntu/" \
        --mirror-chroot "http://archive.ubuntu.com/ubuntu/" \
        --mirror-chroot-security "http://security.ubuntu.com/ubuntu/" \
        --mirror-binary "http://archive.ubuntu.com/ubuntu/" \
        --mirror-binary-security "http://security.ubuntu.com/ubuntu/" \
        --apt-options "--yes --no-install-recommends" \
        --debian-installer none \
        --bootappend-live "boot=live components quiet splash" \
        --iso-application "Axon OS" \
        --iso-publisher "Axon OS Project" \
        --iso-volume "AXON_OS_${VERSION}" \
        --checksums sha256 \
        --compression xz

    # Copy package list into live-build config
    mkdir -p "${WORK_DIR}/config/package-lists"
    cp "${SCRIPT_DIR}/config/packages.list" \
       "${WORK_DIR}/config/package-lists/axon.list.chroot"

    # Copy first-boot and ollama setup scripts into the live system
    mkdir -p "${CHROOT_OVERLAY}/usr/local/bin"
    cp "${SCRIPT_DIR}/config/ollama-setup.sh" \
       "${CHROOT_OVERLAY}/usr/local/bin/axon-ollama-setup"
    cp "${SCRIPT_DIR}/config/firstboot.sh" \
       "${CHROOT_OVERLAY}/usr/local/bin/axon-firstboot"
    chmod +x \
        "${CHROOT_OVERLAY}/usr/local/bin/axon-ollama-setup" \
        "${CHROOT_OVERLAY}/usr/local/bin/axon-firstboot"

    echo "[axon-build] live-build configuration complete."
}

# ---------------------------------------------------------------------------
# copy_files
# Rsyncs project source trees into the chroot overlay at correct system paths.
# ---------------------------------------------------------------------------
copy_files() {
    echo "[axon-build] Copying project files into chroot overlay..."

    # Helper: only copy if source directory exists
    _rsync_if_exists() {
        local src="${1}"
        local dst="${2}"
        if [[ -d "${src}" ]]; then
            mkdir -p "${dst}"
            rsync -a --exclude='*.pyc' --exclude='__pycache__' \
                  "${src}/" "${dst}/"
            echo "[axon-build]   Copied: ${src} -> ${dst}"
        else
            echo "[axon-build]   Skipped (not found): ${src}"
        fi
    }

    # Shell / session scripts -> /usr/lib/axon/shell
    _rsync_if_exists \
        "${BASE_DIR}/shell" \
        "${CHROOT_OVERLAY}/usr/lib/axon/shell"

    # Theme assets -> /usr/share/themes/Axon
    _rsync_if_exists \
        "${BASE_DIR}/theme" \
        "${CHROOT_OVERLAY}/usr/share/themes/Axon"

    # Application source -> /usr/lib/axon/apps
    _rsync_if_exists \
        "${BASE_DIR}/apps" \
        "${CHROOT_OVERLAY}/usr/lib/axon/apps"

    # D-Bus Services -> /usr/lib/axon/services
    _rsync_if_exists \
        "${BASE_DIR}/services" \
        "${CHROOT_OVERLAY}/usr/lib/axon/services"

    # D-Bus session policies -> /usr/share/dbus-1/session.d
    mkdir -p "${CHROOT_OVERLAY}/usr/share/dbus-1/session.d"
    if [[ -f "${BASE_DIR}/services/axon-brain/org.axonos.Brain.conf" ]]; then
        cp "${BASE_DIR}/services/axon-brain/org.axonos.Brain.conf" \
           "${CHROOT_OVERLAY}/usr/share/dbus-1/session.d/org.axonos.Brain.conf"
    fi
    if [[ -f "${BASE_DIR}/services/axon-context/org.axonos.Context.conf" ]]; then
        cp "${BASE_DIR}/services/axon-context/org.axonos.Context.conf" \
           "${CHROOT_OVERLAY}/usr/share/dbus-1/session.d/org.axonos.Context.conf"
    fi

    # Application launch templates -> /usr/lib/axon/data/applications
    _rsync_if_exists \
        "${BASE_DIR}/data/applications" \
        "${CHROOT_OVERLAY}/usr/lib/axon/data/applications"

    # Plymouth theme -> /usr/share/plymouth/themes/axon
    _rsync_if_exists \
        "${BASE_DIR}/plymouth" \
        "${CHROOT_OVERLAY}/usr/share/plymouth/themes/axon"

    # Calamares main settings -> /etc/calamares/settings.conf
    if [[ -f "${BASE_DIR}/installer/settings.conf" ]]; then
        mkdir -p "${CHROOT_OVERLAY}/etc/calamares"
        cp "${BASE_DIR}/installer/settings.conf" \
           "${CHROOT_OVERLAY}/etc/calamares/settings.conf"
        echo "[axon-build]   Copied: installer/settings.conf -> /etc/calamares/settings.conf"
    fi

    # Calamares branding -> /usr/share/calamares/branding/axon
    _rsync_if_exists \
        "${BASE_DIR}/installer/branding/axon" \
        "${CHROOT_OVERLAY}/usr/share/calamares/branding/axon"

    # Calamares module overrides -> /etc/calamares/modules
    _rsync_if_exists \
        "${BASE_DIR}/installer/modules" \
        "${CHROOT_OVERLAY}/etc/calamares/modules"

    # Axon OS version file
    mkdir -p "${CHROOT_OVERLAY}/etc"
    cat > "${CHROOT_OVERLAY}/etc/axon-release" <<EOF
AXON_VERSION=${VERSION}
AXON_BUILD_DATE=$(date -Iseconds)
AXON_CODENAME=Pulse
EOF

    echo "[axon-build] File copy complete."
}

# ---------------------------------------------------------------------------
# build_iso
# Runs lb build and copies the resulting ISO to the project root.
# ---------------------------------------------------------------------------
build_iso() {
    echo "[axon-build] Starting lb build (this will take a while)..."

    sudo lb build 2>&1 | tee "${WORK_DIR}/build.log"

    # Locate the generated ISO (live-build outputs to the work dir root)
    local generated_iso
    generated_iso="$(find "${WORK_DIR}" -maxdepth 1 -name "*.iso" | head -1)"

    if [[ -z "${generated_iso}" ]]; then
        echo "[axon-build] ERROR: No ISO found after lb build." >&2
        echo "[axon-build] Check ${WORK_DIR}/build.log for details." >&2
        exit 1
    fi

    local output_path="${BASE_DIR}/${ISO_NAME}"
    cp "${generated_iso}" "${output_path}"

    local iso_size
    iso_size="$(du -sh "${output_path}" | cut -f1)"

    echo "[axon-build] ISO produced: ${output_path} (${iso_size})"

    # Generate checksum
    sha256sum "${output_path}" > "${output_path}.sha256"
    echo "[axon-build] SHA-256 checksum: ${output_path}.sha256"
}

# ---------------------------------------------------------------------------
# main
# Orchestrates all build phases in order.
# ---------------------------------------------------------------------------
main() {
    echo "[axon-build] ============================================"
    echo "[axon-build]  Axon OS Build System v${VERSION}"
    echo "[axon-build]  Base: ${BASE_DIR}"
    echo "[axon-build]  Target ISO: ${ISO_NAME}"
    echo "[axon-build] ============================================"

    echo "[axon-build] Phase 1/5: Checking dependencies..."
    check_deps

    echo "[axon-build] Phase 2/5: Setting up work directory..."
    setup_workdir

    echo "[axon-build] Phase 3/5: Configuring chroot..."
    configure_chroot

    echo "[axon-build] Phase 4/5: Copying project files..."
    copy_files

    echo "[axon-build] Phase 5/5: Building ISO..."
    build_iso

    echo "[axon-build] ============================================"
    echo "[axon-build]  Build complete."
    echo "[axon-build]  Output: ${BASE_DIR}/${ISO_NAME}"
    echo "[axon-build] ============================================"
}

main "$@"
