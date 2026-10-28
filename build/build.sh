#!/usr/bin/env bash
# Axon OS — Master ISO Build Script
#
# Builds a bootable hybrid (BIOS + UEFI) live ISO of Axon OS on top of
# Ubuntu 26.04 (resolute; 24.04 noble still selectable) using debootstrap + casper + GRUB + xorriso.
# No live-build dependency; works on any Ubuntu/Debian host and on
# GitHub Actions ubuntu-24.04 runners.
#
# Usage:
#   sudo bash build/build.sh [--edition standard|nvidia] [--ci] [--compression xz|gzip]
#                            [--keep-chroot] [--quick] [--chroot]
#
# Flags:
#   --edition=X       standard (default; AMD, Intel, older NVIDIA) or nvidia (GTX 16-series,
#                     RTX and newer: adds the NVIDIA driver). Each edition has its own work
#                     directory, so both can be built from one checkout.
#   --ci              Non-interactive (default)
#   --compression=X   Squashfs compression (default: xz)
#   --keep-chroot     Reuse existing chroot without re-bootstrapping (AUTO if chroot exists)
#   --quick           Fast rebuild: skips debootstrap + runs chroot-setup (already-installed pkgs are no-ops)
#   --fast            Ultra-fast rebuild: quick mode + gzip compression (3-5x faster, but the ISO
#                     is much bigger and NOT release quality; the size budget is not enforced)
#   --fresh           Force full rebuild: deletes chroot + base image cache, starts from scratch
#   --chroot          Drop into the existing build chroot shell
#   --cmd 'CMD'       Run a command inside the chroot (combine with --chroot)
#
# Environment:
#   AXON_UBUNTU_RELEASE   Ubuntu codename to build on: resolute (26.04, default) or noble (24.04)
#   AXON_BUILD_DIR   Work directory (default: /var/lib/axon-build; must be root-owned)
#   AXON_WINABI_BUILD     1 = build the untested axon-winabi kernel module into the image (default: 0)
#   AXON_WINABI_AUTOLOAD  1 = also auto-load it at boot (needs AXON_WINABI_BUILD=1; default: 0)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Single source of truth: the version field in pyproject.toml.
VERSION="$(sed -n 's/^version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "${BASE_DIR}/pyproject.toml" | head -1)"
VERSION="${VERSION:-0.3.0}"
ARCH="amd64"
# Ubuntu release to build on: resolute = 26.04 LTS (default), noble = 24.04 LTS.
DIST="${AXON_UBUNTU_RELEASE:-resolute}"
MIRROR="https://us.archive.ubuntu.com/ubuntu/"
EDITION="standard"
VOLID="AXON_OS"

BUILD_ROOT="${AXON_BUILD_DIR:-/var/lib/axon-build}"

# Reproducible builds: set SOURCE_DATE_EPOCH for deterministic timestamps
# If not set externally, use the last git commit timestamp
if [[ -z "${SOURCE_DATE_EPOCH:-}" ]]; then
    if command -v git &>/dev/null && git -C "${BASE_DIR}" rev-parse --git-dir &>/dev/null; then
        SOURCE_DATE_EPOCH="$(git -C "${BASE_DIR}" log -1 --format=%ct)"
    else
        SOURCE_DATE_EPOCH="$(date +%s)"
    fi
fi
export SOURCE_DATE_EPOCH
DIST_DIR="${BASE_DIR}/dist"

COMPRESSION="xz"
QUICK=false
KEEP_CHROOT=false
CHROOT_SHELL=false
CHROOT_CMD=""
FRESH=false
FAST=false

BUILD_START=$(date +%s)
log() { echo "[axon-build] $*"; }
die() { echo "[axon-build] ERROR: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ci) shift ;; # reserved; everything is already non-interactive
        --compression=*) COMPRESSION="${1#*=}"; shift ;;
        --compression) [[ $# -ge 2 ]] || die "--compression requires an argument"; COMPRESSION="$2"; shift 2 ;;
        --edition=*) EDITION="${1#*=}"; shift ;;
        --edition) [[ $# -ge 2 ]] || die "--edition requires an argument"; EDITION="$2"; shift 2 ;;
        --keep-chroot) KEEP_CHROOT=true; shift ;;
        --quick) QUICK=true; KEEP_CHROOT=true; shift ;; # --quick implies --keep-chroot
        --fast) QUICK=true; KEEP_CHROOT=true; COMPRESSION=gzip; FAST=true; shift ;;
        --chroot) CHROOT_SHELL=true; shift ;;
        --fresh) FRESH=true; shift ;; # force full rebuild: delete chroot + base image
        --cmd) [[ $# -ge 2 ]] || die "--cmd requires an argument"; CHROOT_CMD="$2"; shift 2 ;;
        *) echo "[axon-build] Unknown option: $1" >&2; exit 2 ;;
    esac
done

case "${EDITION}" in
    standard | nvidia) ;;
    *) echo "[axon-build] Unknown edition '${EDITION}' (use standard or nvidia)" >&2; exit 2 ;;
esac
[[ -f "${BASE_DIR}/build/package-lists/${EDITION}.list" ]] || die "no package list for edition ${EDITION}"

ISO_NAME="axon-os-${VERSION}-${EDITION}-${ARCH}.iso"
# The standard edition keeps the original work directory (so existing caches still work);
# any other edition gets its own, so editions never share a chroot.
if [[ "${EDITION}" == "standard" ]]; then
    WORK_DIR="${BUILD_ROOT}"
else
    WORK_DIR="${BUILD_ROOT}/edition-${EDITION}"
fi
CHROOT="${WORK_DIR}/chroot"
IMAGE="${WORK_DIR}/image"
APT_CACHE="${BUILD_ROOT}/apt-cache"  # persistent .deb cache, shared by every edition
DOWNLOAD_CACHE="${BUILD_ROOT}/download-cache"  # big pinned downloads (Ollama), shared too
BASE_IMAGE="${WORK_DIR}/base-${DIST}-${ARCH}.tar.xz"  # cached chroot tarball
STAGING="${WORK_DIR}/staging"

timer() {
    local elapsed=$(($(date +%s) - BUILD_START))
    local mins=$((elapsed / 60))
    local secs=$((elapsed % 60))
    printf "[%dm%ds] %s\n" "$mins" "$secs" "$*"
}

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
[[ ${EUID} -eq 0 ]] || die "This script must run as root (try: sudo bash build/build.sh)"

# The build runs chroot/bash as root inside WORK_DIR and reuses any chroot or
# base tarball it finds there, so the directory must not be writable by anyone
# but root (a predictable path in /tmp could be pre-seeded by another user).
secure_work_dir() {
    local dir owner perms
    for dir in "${BUILD_ROOT}" "${WORK_DIR}"; do
        mkdir -p -m 0755 "${dir}"
        owner="$(stat -c %u "${dir}")"
        perms="$(stat -c %a "${dir}")"
        [[ "${owner}" == "0" ]] || die "Work dir ${dir} is not owned by root; refusing to use it"
        (( (8#${perms} & 8#022) == 0 )) || die "Work dir ${dir} is group/world-writable; refusing to use it"
    done
}

check_deps() {
    local deps=(debootstrap mksquashfs xorriso grub-mkstandalone mkfs.vfat mmd mcopy rsync)
    local missing=()
    for dep in "${deps[@]}"; do
        command -v "${dep}" &>/dev/null || missing+=("${dep}")
    done
    [[ -f /usr/lib/grub/i386-pc/cdboot.img ]] || missing+=("grub-pc-bin")
    if [[ ${#missing[@]} -gt 0 ]]; then
        log "Missing build dependencies on host: ${missing[*]}"
        log "Attempting to install missing dependencies automatically..."
        local pkgs=()
        for m in "${missing[@]}"; do
            case "${m}" in
                debootstrap) pkgs+=("debootstrap") ;;
                mksquashfs) pkgs+=("squashfs-tools") ;;
                xorriso) pkgs+=("xorriso") ;;
                grub-mkstandalone) pkgs+=("grub-pc-bin" "grub-efi-amd64-bin") ;;
                mkfs.vfat) pkgs+=("dosfstools") ;;
                mmd|mcopy) pkgs+=("mtools") ;;
                rsync) pkgs+=("rsync") ;;
                grub-pc-bin) pkgs+=("grub-pc-bin") ;;
            esac
        done
        local unique_pkgs=()
        mapfile -t unique_pkgs < <(printf "%s\n" "${pkgs[@]}" | sort -u)
        log "Installing packages: ${unique_pkgs[*]}"
        apt-get update
        apt-get install -y "${unique_pkgs[@]}"
        
        # Verify again
        local still_missing=()
        for dep in "${deps[@]}"; do
            command -v "${dep}" &>/dev/null || still_missing+=("${dep}")
        done
        [[ -f /usr/lib/grub/i386-pc/cdboot.img ]] || still_missing+=("grub-pc-bin")
        if [[ ${#still_missing[@]} -gt 0 ]]; then
            die "Failed to install required dependencies: ${still_missing[*]}"
        fi
        log "All build dependencies successfully installed."
    else
        log "All build dependencies satisfied."
    fi
}

# ---------------------------------------------------------------------------
# Chroot mount management
# ---------------------------------------------------------------------------
MOUNTED=false

mount_chroot() {
    mount --bind /dev "${CHROOT}/dev"
    mount --bind /dev/pts "${CHROOT}/dev/pts"
    mount -t proc proc "${CHROOT}/proc"
    mount -t sysfs sysfs "${CHROOT}/sys"
    # Persistent APT cache: avoids re-downloading packages across builds
    mkdir -p "${APT_CACHE}"
    mkdir -p "${CHROOT}/var/cache/apt/archives"
    mount --bind "${APT_CACHE}" "${CHROOT}/var/cache/apt/archives"
    # Big pinned downloads (the 2 GB Ollama tarball) survive the chroot and a failed build here;
    # install-ollama.sh looks for this directory (AXON_DOWNLOAD_CACHE default)
    mkdir -p "${DOWNLOAD_CACHE}" "${CHROOT}/var/cache/axon-downloads"
    mount --bind "${DOWNLOAD_CACHE}" "${CHROOT}/var/cache/axon-downloads"
    if grep -q "127.0.0.53" /etc/resolv.conf; then
        log "Host uses systemd-resolved. Writing fallback DNS to chroot resolv.conf..."
        printf "nameserver 8.8.8.8\nnameserver 1.1.1.1\n" > "${CHROOT}/etc/resolv.conf"
    else
        cp /etc/resolv.conf "${CHROOT}/etc/resolv.conf"
    fi
    MOUNTED=true
}

umount_chroot() {
    [[ "${MOUNTED}" == "true" ]] || return 0
    umount -lf "${CHROOT}/var/cache/axon-downloads" 2>/dev/null || true
    rmdir "${CHROOT}/var/cache/axon-downloads" 2>/dev/null || true  # the image gets no empty mount point
    umount -lf "${CHROOT}/var/cache/apt/archives" 2>/dev/null || true
    umount -lf "${CHROOT}/dev/pts" 2>/dev/null || true
    umount -lf "${CHROOT}/dev" 2>/dev/null || true
    umount -lf "${CHROOT}/proc" 2>/dev/null || true
    umount -lf "${CHROOT}/sys" 2>/dev/null || true
    MOUNTED=false
}
trap umount_chroot EXIT

# ---------------------------------------------------------------------------
# Quick chroot entry (--chroot / --chroot --cmd)
# ---------------------------------------------------------------------------
chroot_shell() {
    if [[ ! -d "${CHROOT}" ]]; then
        die "No chroot found at ${CHROOT}. Run a full build first."
    fi
    mount_chroot
    if [[ -n "${CHROOT_CMD}" ]]; then
        log "Running command in chroot: ${CHROOT_CMD}"
        chroot "${CHROOT}" /usr/bin/env -i \
            HOME=/root \
            TERM="${TERM:-xterm-256color}" \
            PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
            /bin/bash -c "${CHROOT_CMD}"
    else
        log "Entering chroot shell (exit to leave)..."
        chroot "${CHROOT}" /usr/bin/env -i \
            HOME=/root \
            TERM="${TERM:-xterm-256color}" \
            PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
            /bin/bash --login
    fi
    umount_chroot
    exit 0
}

# ---------------------------------------------------------------------------
# Phase 1: bootstrap base system (from tarball cache or debootstrap)
# ---------------------------------------------------------------------------
bootstrap() {
    # --fresh: nuke everything and start over
    if [[ "${FRESH}" == "true" ]]; then
        log "FRESH mode: removing existing chroot + base image..."
        rm -rf "${CHROOT}"
        rm -f "${BASE_IMAGE}"
    fi

    # Reuse existing chroot if it's already there
    if [[ -d "${CHROOT}" ]]; then
        log "Reusing existing chroot at ${CHROOT}."
        return 0
    fi

    # FAST PATH: restore from cached base image tarball (skips debootstrap + all apt)
    if [[ -f "${BASE_IMAGE}" ]]; then
        log "Restoring chroot from cached base image: ${BASE_IMAGE}"
        log "  ($(du -sh "${BASE_IMAGE}" | cut -f1), extracting...)"
        local restore_start=$(date +%s)
        mkdir -p "${CHROOT}"
        tar -xJf "${BASE_IMAGE}" -C "${WORK_DIR}"
        local restore_secs=$(($(date +%s) - restore_start))
        log "  Chroot restored in ${restore_secs}s — skipping debootstrap."
        return 0
    fi

    # SLOW PATH: full debootstrap (first build only)
    rm -rf "${CHROOT}"
    mkdir -p "${CHROOT}"
    log "Bootstrapping Ubuntu ${DIST} (${ARCH})... (first time — downloads ~100 MB)"
    # An older host debootstrap has no script for a newer release; every Ubuntu release since
    # gutsy bootstraps the same way
    debootstrap_script=()
    [[ -e "/usr/share/debootstrap/scripts/${DIST}" ]] || debootstrap_script=(/usr/share/debootstrap/scripts/gutsy)
    debootstrap --arch="${ARCH}" "${DIST}" "${CHROOT}" "${MIRROR}" "${debootstrap_script[@]}"

    # Fix IPv6 unreachable + hash mismatch errors inside the chroot
    cat > "${CHROOT}/etc/apt/apt.conf.d/99force-ipv4" <<'APTEOF'
Acquire::ForceIPv4 "true";
Acquire::Retries "3";
Acquire::http::Pipeline-Depth "0";
APTEOF
    sed -i 's|http://archive.ubuntu.com/ubuntu/|https://us.archive.ubuntu.com/ubuntu/|g' "${CHROOT}/etc/apt/sources.list"
}

# ---------------------------------------------------------------------------
# Save chroot as cached base image (called after configure_chroot)
# ---------------------------------------------------------------------------
save_base_image() {
    if [[ -f "${BASE_IMAGE}" ]]; then
        log "Base image already cached: ${BASE_IMAGE}"
        return 0
    fi
    log "Saving base image for fast rebuilds: ${BASE_IMAGE}"
    log "  (this is a one-time cost — future builds restore from this)"
    # Temporarily remove resolv.conf symlink (it points to host path)
    rm -f "${CHROOT}/etc/resolv.conf"
    tar -cJf "${BASE_IMAGE}" -C "${WORK_DIR}" chroot
    # Restore resolv.conf
    ln -fs ../run/systemd/resolve/stub-resolv.conf "${CHROOT}/etc/resolv.conf"
    log "  Base image saved: $(du -sh "${BASE_IMAGE}" | cut -f1)"
}

# ---------------------------------------------------------------------------
# Phase 2: configure the chroot (packages + Axon components)
# ---------------------------------------------------------------------------
configure_chroot() {
    # APEX-WIN (Windows programs via Wine) is Rust: build it here, where the
    # host's toolchain lives, and let chroot-setup.sh install the binary.
    log "Building APEX-WIN runner..."
    bash "${BASE_DIR}/scripts/build-apex-win.sh" || \
        log "WARNING: APEX-WIN build failed — .exe files will open with plain Wine"

    log "Copying project sources into chroot..."
    mkdir -p "${CHROOT}/opt/axon-src"
    rsync -a --delete \
        --exclude='.git' --exclude='.idea' --exclude='/dist' \
        --exclude='apex-win/target' \
        --exclude='__pycache__' --exclude='*.pyc' --exclude='*.iso' \
        "${BASE_DIR}/" "${CHROOT}/opt/axon-src/"

    mount_chroot

    if [[ "${QUICK}" == "true" ]]; then
        log "QUICK mode: re-running component install (apt is idempotent, fast)..."
    else
        log "Entering chroot to install system (this takes a while)..."
    fi
    chroot "${CHROOT}" /usr/bin/env \
        AXON_VERSION="${VERSION}" \
        AXON_QUICK="${QUICK}" \
        AXON_EDITION="${EDITION}" \
        AXON_RELEASE="${DIST}" \
        AXON_WINABI_BUILD="${AXON_WINABI_BUILD:-0}" \
        AXON_WINABI_AUTOLOAD="${AXON_WINABI_AUTOLOAD:-0}" \
        /bin/bash /opt/axon-src/build/config/chroot-setup.sh
    umount_chroot
    rm -f "${CHROOT}/etc/resolv.conf"
    ln -fs ../run/systemd/resolve/stub-resolv.conf "${CHROOT}/etc/resolv.conf"
}

# ---------------------------------------------------------------------------
# Phase 3: live image tree (kernel, initrd, squashfs, manifests)
# ---------------------------------------------------------------------------
build_image_tree() {
    log "Assembling live image tree..."
    rm -rf "${IMAGE}" "${STAGING}"
    mkdir -p "${IMAGE}/casper" "${IMAGE}/boot/grub" "${IMAGE}/.disk" "${STAGING}"

    # Kernel + initrd (newest installed version)
    local kernel initrd
    kernel="$(find "${CHROOT}/boot" -maxdepth 1 -name 'vmlinuz-*' | sort -V | tail -1)"
    initrd="$(find "${CHROOT}/boot" -maxdepth 1 -name 'initrd.img-*' | sort -V | tail -1)"
    [[ -n "${kernel}" && -n "${initrd}" ]] || die "Kernel or initrd not found in chroot /boot"
    cp "${kernel}" "${IMAGE}/casper/vmlinuz"
    cp "${initrd}" "${IMAGE}/casper/initrd"

    # Package manifest (used by installers and for reproducibility)
    chroot "${CHROOT}" dpkg-query -W --showformat='${Package} ${Version}\n' \
        > "${IMAGE}/casper/filesystem.manifest"
    cp "${IMAGE}/casper/filesystem.manifest" "${IMAGE}/casper/filesystem.manifest-desktop"

    log "Compressing root filesystem (squashfs, ${COMPRESSION}, $(nproc) cores)..."
    local comp_args=(-comp "${COMPRESSION}" -processors "$(nproc)")
    [[ "${COMPRESSION}" == "xz" ]] && comp_args+=(-b 1M -Xbcj x86)
    mksquashfs "${CHROOT}" "${IMAGE}/casper/filesystem.squashfs" \
        -noappend -wildcards "${comp_args[@]}" \
        -e 'proc/*' 'sys/*' 'dev/*' 'run/*' 'tmp/*' 'opt/axon-src' \
           'boot/grub/grub.cfg' 'var/cache/apt/archives/*.deb' \
           'root/.bash_history' 'home/*'

    du -sx --block-size=1 "${CHROOT}" | cut -f1 > "${IMAGE}/casper/filesystem.size"

    echo "Axon OS ${VERSION} \"Pulse\" ${EDITION} - Release ${ARCH} ($(date +%Y%m%d))" > "${IMAGE}/.disk/info"
    touch "${IMAGE}/.disk/base_installable"
    echo 'full_cd/single' > "${IMAGE}/.disk/cd_type"

    # GRUB search marker so the bootloader finds this volume on any device
    touch "${IMAGE}/${VOLID}"

    # Copy custom GRUB theme into image
    mkdir -p "${IMAGE}/boot/grub/themes"
    cp -r "${BASE_DIR}/theme/grub/axon" "${IMAGE}/boot/grub/themes/"
    cp "${CHROOT}/usr/share/grub/unicode.pf2" "${IMAGE}/boot/grub/themes/axon/unicode.pf2" || \
    cp /usr/share/grub/unicode.pf2 "${IMAGE}/boot/grub/themes/axon/unicode.pf2" || true

    # The NVIDIA edition boots with the NVIDIA driver's kernel mode setting by default
    local default_params="quiet splash"
    local nvidia_params="quiet splash nouveau.modeset=0 nvidia-drm.modeset=1 console=tty0"
    [[ "${EDITION}" == "nvidia" ]] && default_params="${nvidia_params}"

    cat > "${IMAGE}/boot/grub/grub.cfg" <<EOF
set default="0"
set timeout=5
set timeout_style=menu

insmod all_video
insmod gfxterm
insmod png
insmod font

if loadfont /boot/grub/themes/axon/unicode.pf2 ; then
    set theme=/boot/grub/themes/axon/theme.txt
fi

menuentry "Try or Install Axon OS ${VERSION}" --class axonos {
    linux /casper/vmlinuz boot=casper ${default_params} ---
    initrd /casper/initrd
}

menuentry "Try or Install Axon OS ${VERSION} (safe graphics)" --class safe {
    linux /casper/vmlinuz boot=casper nomodeset vga=normal ---
    initrd /casper/initrd
}

menuentry "Try or Install Axon OS ${VERSION} (modern NVIDIA drivers)" --class nvidia {
    linux /casper/vmlinuz boot=casper ${nvidia_params} ---
    initrd /casper/initrd
}

menuentry "Power Off" --class power {
    halt
}
EOF
}

# ---------------------------------------------------------------------------
# Phase 4: BIOS + UEFI bootloaders, hybrid ISO via xorriso
# ---------------------------------------------------------------------------
build_iso() {
    log "Building bootloader images..."

    # Tiny embedded config: locate the live volume, chain to the real grub.cfg
    cat > "${STAGING}/grub-embed.cfg" <<EOF
search --set=root --file /${VOLID}
set prefix=(\$root)/boot/grub
configfile \$prefix/grub.cfg
EOF

    # UEFI: standalone GRUB EFI binary inside a FAT image
    grub-mkstandalone -O x86_64-efi \
        --modules="part_gpt part_msdos fat iso9660 search configfile normal linux all_video gfxterm png font" \
        --locales="" --themes="" --fonts="" \
        -o "${STAGING}/bootx64.efi" \
        "/boot/grub/grub.cfg=${STAGING}/grub-embed.cfg"

    dd if=/dev/zero of="${STAGING}/efiboot.img" bs=1M count=16 status=none
    mkfs.vfat -F 16 "${STAGING}/efiboot.img" >/dev/null
    mmd -i "${STAGING}/efiboot.img" ::EFI
    mmd -i "${STAGING}/efiboot.img" ::EFI/BOOT
    mcopy -i "${STAGING}/efiboot.img" "${STAGING}/bootx64.efi" ::EFI/BOOT/BOOTX64.EFI

    # BIOS: standalone GRUB core prefixed with El Torito CD boot image
    grub-mkstandalone -O i386-pc \
        --modules="linux16 linux normal iso9660 biosdisk memdisk search configfile all_video gfxterm png font" \
        --locales="" --fonts="" --install-modules="linux16 linux normal iso9660 biosdisk search configfile all_video gfxterm png font" \
        -o "${STAGING}/core.img" \
        "/boot/grub/grub.cfg=${STAGING}/grub-embed.cfg"
    cat /usr/lib/grub/i386-pc/cdboot.img "${STAGING}/core.img" > "${STAGING}/bios.img"

    # Integrity checksums for the "Check disc" boot entry
    (cd "${IMAGE}" && find . -type f -print0 | xargs -0 md5sum | \
        grep -v -e 'md5sum.txt' > md5sum.txt)

    log "Creating hybrid ISO with xorriso..."
    mkdir -p "${DIST_DIR}"
    xorriso -as mkisofs \
        -iso-level 3 \
        -full-iso9660-filenames \
        -joliet -joliet-long -rational-rock \
        -volid "${VOLID}" \
        -output "${DIST_DIR}/${ISO_NAME}" \
        -eltorito-boot boot/grub/bios.img \
            -no-emul-boot \
            -boot-load-size 4 \
            -boot-info-table \
            --eltorito-catalog boot/grub/boot.cat \
            --grub2-boot-info \
            --grub2-mbr /usr/lib/grub/i386-pc/boot_hybrid.img \
        -eltorito-alt-boot \
            -e EFI/efiboot.img \
            -no-emul-boot \
        -append_partition 2 0xef "${STAGING}/efiboot.img" \
        -graft-points \
            "/EFI/efiboot.img=${STAGING}/efiboot.img" \
            "/boot/grub/bios.img=${STAGING}/bios.img" \
            "${IMAGE}"

    (cd "${DIST_DIR}" && sha256sum "${ISO_NAME}" > "${ISO_NAME}.sha256")

    # Generate SBOM (Software Bill of Materials)
    generate_sbom

    # Hand the artifacts back to the invoking (non-root) user where possible
    if [[ -n "${SUDO_UID:-}" ]]; then
        chown "${SUDO_UID}:${SUDO_GID:-${SUDO_UID}}" \
            "${DIST_DIR}" "${DIST_DIR}/${ISO_NAME}" "${DIST_DIR}/${ISO_NAME}.sha256"
    fi

    # Also copy to the user's requested ISO files directory if it exists
    local iso_dir="${BASE_DIR}/../ISO files"
    if [[ -d "${iso_dir}" ]]; then
        log "Copying ISO to workspace ${iso_dir}..."
        cp "${DIST_DIR}/${ISO_NAME}" "${iso_dir}/${ISO_NAME}"
        sha256sum "${iso_dir}/${ISO_NAME}" > "${iso_dir}/${ISO_NAME}.sha256"
        if [[ -n "${SUDO_UID:-}" ]]; then
            chown "${SUDO_UID}:${SUDO_GID:-${SUDO_UID}}" \
                "${iso_dir}/${ISO_NAME}" "${iso_dir}/${ISO_NAME}.sha256"
        fi
        log "ISO copied to: ${iso_dir}/${ISO_NAME}"
    fi

    log "ISO produced: ${DIST_DIR}/${ISO_NAME} ($(du -sh "${DIST_DIR}/${ISO_NAME}" | cut -f1))"
    log "SHA-256:      ${DIST_DIR}/${ISO_NAME}.sha256"
}

# ---------------------------------------------------------------------------
# Size budget (see build/size-budget.conf)
# ---------------------------------------------------------------------------
# Writes the installed size of every package next to the ISO, so the next build (and CI)
# can say what grew, then checks the ISO against its edition's limit.
check_size_budget() {
    local list="${DIST_DIR}/${ISO_NAME%.iso}.packages.tsv"
    {
        echo "# iso_bytes=$(stat -c %s "${DIST_DIR}/${ISO_NAME}")"
        chroot "${CHROOT}" dpkg-query -W -f='${Installed-Size}\t${Package}\t${Version}\n'
    } > "${list}"
    [[ -n "${SUDO_UID:-}" ]] && chown "${SUDO_UID}:${SUDO_GID:-${SUDO_UID}}" "${list}"

    if [[ "${FAST}" == "true" ]]; then
        log "WARNING: --fast ISO (gzip) is NOT release quality; size budget not enforced"
        return 0
    fi
    local previous=()
    [[ -n "${AXON_PREVIOUS_PACKAGES:-}" && -f "${AXON_PREVIOUS_PACKAGES}" ]] \
        && previous=(--previous "${AXON_PREVIOUS_PACKAGES}")
    local rc=0
    bash "${BASE_DIR}/scripts/check-iso-size.sh" --edition "${EDITION}" \
        --packages "${list}" "${previous[@]}" "${DIST_DIR}/${ISO_NAME}" || rc=$?
    # 1 is "over the hard limit"; anything else is the check itself breaking, which must not be
    # reported as a size problem
    case "${rc}" in
        0) ;;
        1) die "ISO is over its size limit (see build/size-budget.conf)" ;;
        *) die "size check failed (exit ${rc}); the ISO was built: ${DIST_DIR}/${ISO_NAME}" ;;
    esac
}

# ---------------------------------------------------------------------------
# SBOM Generation
# ---------------------------------------------------------------------------
generate_sbom() {
    log "Generating Software Bill of Materials (SBOM)..."

    local sbom_file="${DIST_DIR}/${ISO_NAME%.iso}.sbom.spdx.json"

    # Collect package information from the chroot
    local packages=""
    if [[ -f "${CHROOT}/var/log/apt/history.log" ]]; then
        packages=$(grep "Install:" "${CHROOT}/var/log/apt/history.log" | \
            sed 's/Install://' | tr ',' '\n' | \
            sed 's/([^)]*)//g' | awk '{print $1}' | sort -u | tr '\n' ',')
    fi

    # Collect Python package versions
    local python_packages=""
    if [[ -d "${CHROOT}/usr/lib/python3/dist-packages" ]]; then
        python_packages=$(ls "${CHROOT}/usr/lib/python3/dist-packages/"*.dist-info/ 2>/dev/null | \
            grep -oP '[^/]+(?=-\d)' | sort -u | tr '\n' ',')
    fi

    # Generate minimal SPDX SBOM
    cat > "${sbom_file}" << EOF
{
  "spdxVersion": "SPDX-2.3",
  "dataLicense": "CC0-1.0",
  "SPDXID": "SPDXRef-DOCUMENT",
  "name": "Axon-OS-${VERSION}-${EDITION}",
  "documentNamespace": "https://axon-os.github.io/sbom/${VERSION}",
  "creationInfo": {
    "created": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
    "creators": ["Tool: axon-build-${VERSION}"]
  },
  "packages": [
    {
      "SPDXID": "SPDXRef-RootPackage",
      "name": "axon-os",
      "versionInfo": "${VERSION}",
      "downloadLocation": "https://github.com/ProjectDEV-git/Axon-OS",
      "filesAnalyzed": false,
      "primaryPackagePurpose": "OPERATING-SYSTEM"
    }
  ],
  "relationships": [
    {
      "spdxElementId": "SPDXRef-DOCUMENT",
      "relationshipType": "DESCRIBES",
      "relatedSpdxElement": "SPDXRef-RootPackage"
    }
  ]
}
EOF

    # Embed SBOM in the ISO's squashfs
    if [[ -d "${STAGING}/live" ]]; then
        mkdir -p "${STAGING}/live/sbom"
        cp "${sbom_file}" "${STAGING}/live/sbom/"
    fi

    log "SBOM generated: ${sbom_file}"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    # Quick chroot entry — bypass build entirely
    if [[ "${CHROOT_SHELL}" == "true" ]]; then
        chroot_shell
    fi

    log "============================================"
    log " Axon OS Build System v${VERSION}"
    log " Edition: ${EDITION}"
    log " Base:    Ubuntu ${DIST} (${ARCH})"
    log " Workdir: ${WORK_DIR}"
    log " Output:  ${DIST_DIR}/${ISO_NAME}"
    [[ -f "${BASE_IMAGE}" ]] && log " Cache:   ${BASE_IMAGE} ($(du -sh "${BASE_IMAGE}" | cut -f1))" || log " Cache:   (none — first build will create it)"
    [[ "${QUICK}" == "true" && "${COMPRESSION}" == "gzip" ]] && log " Mode:    FAST (quick rebuild + gzip compression)"
    [[ "${QUICK}" == "true" && "${COMPRESSION}" != "gzip" ]] && log " Mode:    QUICK (fast rebuild)"
    [[ "${FRESH}" == "true" ]] && log " Mode:    FRESH (full rebuild from scratch)"
    [[ "${FAST}" == "true" ]] && log " WARNING: --fast makes a gzip ISO for local testing only; it is NOT release quality"
    log "============================================"

    timer "Phase 1/4: Dependencies"
    check_deps
    secure_work_dir

    timer "Phase 2/4: Bootstrap + configure root filesystem"
    bootstrap
    configure_chroot
    save_base_image

    timer "Phase 3/4: Live image tree"
    build_image_tree

    timer "Phase 4/4: Bootable hybrid ISO"
    build_iso
    check_size_budget

    local total=$(($(date +%s) - BUILD_START))
    local mins=$((total / 60))
    local secs=$((total % 60))
    log "============================================"
    log " Build complete in ${mins}m${secs}s: ${DIST_DIR}/${ISO_NAME}"
    log " Size:    $(du -sh "${DIST_DIR}/${ISO_NAME}" | cut -f1)"
    log " Test it: qemu-system-x86_64 -enable-kvm -m 4G -cdrom '${DIST_DIR}/${ISO_NAME}'"
    log " Chroot:  sudo bash build/build.sh --chroot"
    log "============================================"
}

main
