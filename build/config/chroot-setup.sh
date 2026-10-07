#!/usr/bin/env bash
# Axon OS — chroot configuration script.
# Executed by build/build.sh *inside* the debootstrapped root filesystem.
# Expects the repository to be available at /opt/axon-src and the
# AXON_VERSION environment variable to be set.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C
export HOME=/root

SRC="/opt/axon-src"
VERSION="${AXON_VERSION:-0.3.0}"
CODENAME="Pulse"

log() { echo "[chroot-setup] $*"; }

# Version of the kernel the ISO boots: the newest /boot/vmlinuz-*, which is
# the one build.sh copies into casper/. Empty if no kernel is installed.
iso_kernel_version() {
    local k
    k="$(find /boot -maxdepth 1 -name 'vmlinuz-*' -printf '%f\n' | sort -V | tail -1 || true)"
    echo "${k#vmlinuz-}"
}

# QUICK mode: passed from build.sh via env var (build.sh --quick/--fast sets
# AXON_QUICK=true; AXON_QUICK=1 also works). Skips expensive steps whose
# results a reused chroot already has (WhiteSur theme build, unchanged kernel
# module rebuild) and regenerates only the ISO kernel's initramfs, to speed up
# iterative development rebuilds.
case "${AXON_QUICK:-0}" in
    1 | true) QUICK=1 ;;
    *) QUICK=0 ;;
esac
[[ "${QUICK}" == "1" ]] && log "QUICK MODE enabled — skipping expensive non-essential steps"

# ---------------------------------------------------------------------------
# 0. Guards against services starting inside the chroot
# ---------------------------------------------------------------------------
printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
chmod +x /usr/sbin/policy-rc.d

# ---------------------------------------------------------------------------
# 1. APT sources (main + universe + multiverse, with updates and security)
# ---------------------------------------------------------------------------
log "Writing APT sources..."
cat > /etc/apt/sources.list <<'EOF'
deb https://us.archive.ubuntu.com/ubuntu/ noble main restricted universe multiverse
deb https://us.archive.ubuntu.com/ubuntu/ noble-updates main restricted universe multiverse
deb https://security.ubuntu.com/ubuntu/ noble-security main restricted universe multiverse
EOF
# debootstrap may have created the new deb822 file; sources.list wins, drop it
rm -f /etc/apt/sources.list.d/ubuntu.sources

# Force IPv4 and retries to avoid CDN hash-mismatch errors
# Parallel downloads for speed (16 concurrent connections)
cat > /etc/apt/apt.conf.d/99force-ipv4 <<'APTEOF'
Acquire::ForceIPv4 "true";
Acquire::Retries "3";
Acquire::http::Pipeline-Depth "0";
Acquire::Parallel::Downloads "16";
APT::Acquire::QueueMode "acquire";
APTEOF

dpkg --add-architecture i386
apt-get update

# ---------------------------------------------------------------------------
# 2. Base system, machine-id, locale
# ---------------------------------------------------------------------------
log "Installing core system..."
apt-get install -y systemd-sysv dbus libnss-systemd

# A machine-id must exist for systemd tooling during the build; it is
# truncated again at cleanup so every installed/live system gets its own.
dbus-uuidgen > /etc/machine-id
ln -fs /etc/machine-id /var/lib/dbus/machine-id

apt-get install -y locales
locale-gen en_US.UTF-8
update-locale LANG=en_US.UTF-8

ln -fs /usr/share/zoneinfo/UTC /etc/localtime

# ---------------------------------------------------------------------------
# 3. Kernel + casper (Ubuntu live-boot infrastructure)
# ---------------------------------------------------------------------------
log "Installing kernel and casper..."
apt-get install -y linux-image-generic initramfs-tools casper
for p in discover laptop-detect os-prober; do
    apt-get install -y "${p}" || log "Optional package ${p} unavailable — skipped"
done

# ---------------------------------------------------------------------------
# 4. Desktop + Axon dependencies from the package manifest
# ---------------------------------------------------------------------------
log "Installing desktop packages from packages.list..."
mapfile -t PACKAGES < <(grep -vE '^\s*(#|$)' "${SRC}/build/config/packages.list")
# DKMS/WiFi packages often fail on mismatched kernels — install them last
# and tolerate failures so the rest of the build continues.
DKMS_PACKAGES=(bcmwl-kernel-source broadcom-sta-dkms)
NON_DKMS_PACKAGES=()
for p in "${PACKAGES[@]}"; do
    skip=false
    for dk in "${DKMS_PACKAGES[@]}"; do
        [[ "${p}" == "${dk}" ]] && skip=true && break
    done
    ${skip} || NON_DKMS_PACKAGES+=("${p}")
done
if ! apt-get install -y "${NON_DKMS_PACKAGES[@]}"; then
    log "Bulk install failed — retrying packages one at a time..."
    for p in "${NON_DKMS_PACKAGES[@]}"; do
        apt-get install -y "${p}" || log "WARNING: package ${p} failed to install"
    done
fi
# Install DKMS packages separately, tolerate failure (host kernel may differ)
for p in "${DKMS_PACKAGES[@]}"; do
    apt-get install -y "${p}" 2>/dev/null || log "WARNING: DKMS package ${p} failed (expected if host kernel differs)"
done

log "Adding flathub remote..."
flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo || true

# ── Web browser: Brave (official apt repo, signing key pinned) ───────────────
# Falls back to GNOME Web (Epiphany) so the image always has a browser.
# Brave's release keyring holds several keys and Brave signs with one of them
# (DBF1A116... as of 2026-10). All three are listed on https://brave.com/signing-keys/.
# Every primary key in the downloaded keyring must be one of these.
BRAVE_KEY_FPRS=(
    "DBF1A116C220B8C7164F98230686B78420038257"
    "47D32A74E9A9E013A4B4926C68D513D36A73CD96"
    "B2A3DCA350E67256740DF904DE4EC67BE4B0DCA0"
)
BRAVE_KEYRING="/usr/share/keyrings/brave-browser-archive-keyring.gpg"
BROWSER_DESKTOP="brave-browser.desktop"
log "Installing Brave browser..."
install_brave() {
    apt-get install -y gnupg || return 1
    curl -fsSL --retry 3 -o "${BRAVE_KEYRING}.new" \
        https://brave-browser-apt-release.s3.brave.com/brave-browser-archive-keyring.gpg || return 1
    local gnupghome fprs
    gnupghome="$(mktemp -d)"
    fprs="$(GNUPGHOME="${gnupghome}" gpg --show-keys --with-colons "${BRAVE_KEYRING}.new" 2>/dev/null || true)"
    rm -rf "${gnupghome}"
    local pub_fprs fpr
    pub_fprs="$(awk -F: '/^pub:/{p=1;next} /^fpr:/&&p{print $10;p=0}' <<<"${fprs}")"
    if [ -z "${pub_fprs}" ]; then
        log "WARNING: Brave keyring has no keys; not adding its repository"
        rm -f "${BRAVE_KEYRING}.new"
        return 1
    fi
    for fpr in ${pub_fprs}; do
        if ! printf '%s\n' "${BRAVE_KEY_FPRS[@]}" | grep -qx "${fpr}"; then
            log "WARNING: Brave signing key fingerprint mismatch (${fpr}); not adding its repository"
            rm -f "${BRAVE_KEYRING}.new"
            return 1
        fi
    done
    mv "${BRAVE_KEYRING}.new" "${BRAVE_KEYRING}"
    cat > /etc/apt/sources.list.d/brave-browser-release.sources <<BRAVEEOF
Types: deb
URIs: https://brave-browser-apt-release.s3.brave.com/
Suites: stable
Components: main
Architectures: amd64
Signed-By: ${BRAVE_KEYRING}
BRAVEEOF
    apt-get update && apt-get install -y brave-browser
}
if install_brave; then
    apt-get purge -y epiphany-browser 2>/dev/null || true
else
    log "WARNING: Brave install failed — falling back to GNOME Web"
    rm -f /etc/apt/sources.list.d/brave-browser-release.sources
    apt-get install -y epiphany-browser || log "WARNING: epiphany-browser failed to install"
    BROWSER_DESKTOP="org.gnome.Epiphany.desktop"
fi
# Default browser for every user (GNOME reads /etc/xdg/mimeapps.list)
cat > /etc/xdg/mimeapps.list <<MIMEEOF
[Default Applications]
text/html=${BROWSER_DESKTOP}
x-scheme-handler/http=${BROWSER_DESKTOP}
x-scheme-handler/https=${BROWSER_DESKTOP}
x-scheme-handler/about=${BROWSER_DESKTOP}
x-scheme-handler/unknown=${BROWSER_DESKTOP}
MIMEEOF

# ── System monitor: Mission Center (Flathub, GPL-3.0) ───────────────────────
# Not packaged for Ubuntu 24.04, so it ships as a system-wide Flatpak. Keeps
# GNOME System Monitor only if the Flatpak cannot be installed.
log "Installing Mission Center..."
if flatpak install --system --noninteractive -y flathub io.missioncenter.MissionCenter; then
    apt-get purge -y gnome-system-monitor 2>/dev/null || true
else
    log "WARNING: Mission Center install failed — keeping GNOME System Monitor"
    apt-get install -y gnome-system-monitor || true
fi

log "Installing Python AI libraries inside chroot..."
# Pinned versions: unpinned installs pulled whatever PyPI served at build time
pip3 install --no-cache-dir "faster-whisper==1.2.1" "sqlite-vec==0.1.9" --break-system-packages || log "WARNING: Python AI libraries failed to install"


# ---------------------------------------------------------------------------
# 5. Axon OS components (system-wide)
# ---------------------------------------------------------------------------
log "Installing Axon OS components..."

AXON_LIB="/usr/lib/axon"
APPS_DIR="${AXON_LIB}/apps"
SERVICES_DIR="${AXON_LIB}/services"

mkdir -p "${APPS_DIR}"
mkdir -p "${SERVICES_DIR}"
mkdir -p "${AXON_LIB}/shell"
mkdir -p "${AXON_LIB}/data/applications"
cp -r "${SRC}/apps/." "${APPS_DIR}/"
cp -r "${SRC}/services/." "${SERVICES_DIR}/"
cp -r "${SRC}/shell/." "${AXON_LIB}/shell/"
cp -r "${SRC}/data/applications/." "${AXON_LIB}/data/applications/"
find "${AXON_LIB}" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# Desktop entries -> /usr/share/applications (resolve AXON_APPS_DIR)
for f in "${SRC}/data/applications/"*.desktop; do
    sed "s|AXON_APPS_DIR|${APPS_DIR}|g" "${f}" \
        > "/usr/share/applications/$(basename "${f}")"
done

# D-Bus session activation files (resolve AXON_SERVICES_DIR) — every
# service directory that ships org.axonos.*.service / *.conf is registered.
mkdir -p /usr/share/dbus-1/services /usr/share/dbus-1/session.d
for activation in "${SERVICES_DIR}"/*/org.axonos.*.service; do
    [[ -f "${activation}" ]] || continue
    sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" "${activation}" \
        > "/usr/share/dbus-1/services/$(basename "${activation}")"
done
# No session-bus policy files: every caller on a session bus runs as the same
# user, so <policy user=...> cannot tell clients apart, and the old files'
# literal user="${user}" plus default-context denies blocked Brain's own
# methods for the shell and apps. Remove copies left in reused chroots.
rm -f /usr/share/dbus-1/session.d/org.axonos.*.conf

# systemd user units, enabled globally for every user
mkdir -p /usr/lib/systemd/user
AXON_USER_UNITS=()
for unit in "${SERVICES_DIR}"/*/axon-*.service; do
    [[ -f "${unit}" ]] || continue
    sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" "${unit}" \
        > "/usr/lib/systemd/user/$(basename "${unit}")"
    AXON_USER_UNITS+=("$(basename "${unit}")")
done
if [[ ${#AXON_USER_UNITS[@]} -gt 0 ]]; then
    systemctl --global enable "${AXON_USER_UNITS[@]}"
else
    log "WARNING: no systemd user units found to enable"
fi

# GNOME Shell extension, system-wide
EXT_DIR="/usr/share/gnome-shell/extensions/axon-shell@axon-os"
mkdir -p "${EXT_DIR}"
cp -r "${SRC}/shell/axon-shell/." "${EXT_DIR}/"
glib-compile-schemas "${EXT_DIR}/schemas/"

# GTK theme
mkdir -p /usr/share/themes/axon-gtk/gtk-4.0
cp "${SRC}/theme/axon-gtk/gtk-dark.css" /usr/share/themes/axon-gtk/gtk-4.0/gtk.css
cp "${SRC}/theme/axon-gtk/index.theme" /usr/share/themes/axon-gtk/

# Wallpaper
mkdir -p /usr/share/backgrounds/axon
if [[ -f "${SRC}/theme/wallpapers/axon-aurora.png" ]]; then
    cp "${SRC}/theme/wallpapers/axon-aurora.png" /usr/share/backgrounds/axon/
fi

# First-boot + ollama helper scripts
install -Dm755 "${SRC}/build/config/firstboot.sh" /usr/local/bin/axon-firstboot
install -Dm755 "${SRC}/build/config/ollama-setup.sh" /usr/local/bin/axon-ollama-setup
install -Dm755 "${SRC}/system/axon-updater.py" /usr/local/bin/axon-update

# Install Axon Voice overlay & Sandbox / Watchdog helpers
mkdir -p /usr/lib/axon/apps/axon-voice-overlay
install -Dm755 "${SRC}/apps/axon-voice-overlay/main.py" /usr/lib/axon/apps/axon-voice-overlay/main.py
install -Dm755 "${SRC}/services/axon-sandbox/axon-run" /usr/local/bin/axon-run
install -Dm755 "${SRC}/system/boot_watchdog.py" /usr/local/bin/axon-boot-watchdog
install -Dm644 "${SRC}/system/axon-boot-watchdog.service" /lib/systemd/system/axon-boot-watchdog.service
systemctl enable axon-boot-watchdog.service || log "WARNING: could not enable axon-boot-watchdog"

# Shell environment interceptor for interactive shells
install -Dm644 "${SRC}/services/axon-sandbox/axon-sandbox-env.sh" /etc/profile.d/axon-sandbox.sh

# Python global logger path helper (copying to python standard dist-packages)
cp "${SRC}/axon_logger.py" /usr/lib/python3/dist-packages/ || true

# NOTE: the boot-attempts watchdog lives in /etc/grub.d/06_axon_watchdog
# (installed further below) and counts in a grubenv file on the ESP, which
# GRUB can actually write. Do NOT append watchdog logic to 00_header:
# appended lines execute as *bash* while update-grub runs (they are not
# emitted into grub.cfg), where save_env does not exist — under 00_header's
# `set -e` that aborts grub-mkconfig and leaves the system with a stale or
# missing grub.cfg (boot error / blank screen).

cat > /usr/share/applications/axon-update.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Axon OS Updater
Comment=Check for and apply the latest Axon OS updates
Exec=/usr/local/bin/axon-update
Icon=software-update-available
Terminal=false
StartupNotify=true
Categories=System;Settings;
EOF

cat > /usr/lib/systemd/system/axon-update-auto.service <<'EOF'
[Unit]
Description=Axon OS automatic update check and apply
Wants=network-online.target
After=network-online.target NetworkManager-wait-online.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/axon-update --auto
EOF

cat > /usr/lib/systemd/system/axon-update-auto.timer <<'EOF'
[Unit]
Description=Run Axon OS automatic updates daily

[Timer]
OnBootSec=30min
OnUnitActiveSec=1d
RandomizedDelaySec=2h
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl enable axon-update-auto.timer || log "WARNING: could not enable axon-update-auto.timer"

# Voice push-to-talk toggle (bound to Super+V via the gschema override)
install -Dm755 "${SRC}/build/config/axon-voice-toggle" /usr/local/bin/axon-voice-toggle

# Rogue Software Shield CLI wrapper
cat > /usr/local/bin/axon-shield <<EOF
#!/bin/sh
exec /usr/bin/python3 ${SERVICES_DIR}/axon-sandbox/shield.py "\$@"
EOF
chmod 755 /usr/local/bin/axon-shield

# Self-healing boot watchdog: GRUB counter + rollback entry + reset unit.
# The grub.d scripts only emit anything on installed btrfs systems.
install -Dm755 "${SRC}/build/config/axon-boot-ok.sh" /usr/local/bin/axon-boot-ok
install -Dm644 "${SRC}/build/config/axon-boot-ok.service" \
    /etc/systemd/system/axon-boot-ok.service
systemctl enable axon-boot-ok.service || log "WARNING: could not enable axon-boot-ok"
install -Dm755 "${SRC}/build/config/grub.d-06_axon_watchdog" /etc/grub.d/06_axon_watchdog
install -Dm755 "${SRC}/build/config/grub.d-42_axon_rollback" /etc/grub.d/42_axon_rollback

# Copy and configure polished GRUB theme for installed system
log "Installing polished GRUB theme..."
mkdir -p /boot/grub/themes
cp -r "${SRC}/theme/grub/axon" /boot/grub/themes/
cp /usr/share/grub/unicode.pf2 /boot/grub/themes/axon/unicode.pf2 || true

if [[ -f /etc/default/grub ]]; then
    log "Configuring system GRUB default settings..."
    # Boot straight through: the menu stays hidden for 1 second (hold Shift or
    # press Esc to show it). The installer switches back to a visible menu
    # when installing alongside another OS, and the boot watchdog shows it
    # itself when it picks the rollback entry. GRUB cannot write its env on
    # btrfs, so cap the recordfail timeout too (Ubuntu's default is 30 s).
    sed -i '/^GRUB_TIMEOUT_STYLE=/d; /^GRUB_TIMEOUT=/d; /^GRUB_RECORDFAIL_TIMEOUT=/d' /etc/default/grub
    {
        echo 'GRUB_TIMEOUT_STYLE=hidden'
        echo 'GRUB_TIMEOUT=1'
        echo 'GRUB_RECORDFAIL_TIMEOUT=5'
    } >> /etc/default/grub
    
    # Remove existing GRUB_THEME setting if any and append the custom one
    sed -i '/^GRUB_THEME=/d' /etc/default/grub
    echo 'GRUB_THEME="/boot/grub/themes/axon/theme.txt"' >> /etc/default/grub
fi

mkdir -p /etc/skel/.config/autostart
cat > /etc/skel/.config/autostart/axon-firstboot.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Axon OS First Boot Setup
Comment=Runs once on first login to complete Axon OS setup
Exec=/usr/local/bin/axon-firstboot
Terminal=false
StartupNotify=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Phase=Applications
EOF

# ── 5a. Axon Windows ABI kernel module ──────────────────────────────────────
# Built against the kernel the ISO boots: the newest /boot/vmlinuz-* in the
# chroot, which is the one build.sh copies into casper/. `uname -r` would give
# the build host's kernel, and /lib/modules may still hold host-kernel dirs
# left by older builds in a reused chroot.
#
# The module is an untested prototype that parses untrusted PE files in the
# kernel, so release images do not ship it. It is only built for development
# images that opt in with AXON_WINABI_BUILD=1, and only auto-loaded at boot
# when AXON_WINABI_AUTOLOAD=1 is also set. Failing to build it never fails the
# ISO build.
MODULE_BUILT=false
# Drop the auto-load entry and module that older builds left in reused chroots
rm -f /etc/modules-load.d/axon-winabi.conf
if [[ "${AXON_WINABI_BUILD:-0}" != "1" ]]; then
    log "Skipping Axon Windows ABI kernel module (set AXON_WINABI_BUILD=1 to build it)"
    find /lib/modules -path '*/extra/axon-winabi.ko*' -delete 2>/dev/null || true
elif [[ -d "${SRC}/kernel/axon-winabi" ]]; then
    log "Building Axon Windows ABI kernel module (AXON_WINABI_BUILD=1)..."
    KSRC="${SRC}/kernel/axon-winabi"
    KVER="$(iso_kernel_version)"
    KDIR="/usr/src/linux-headers-${KVER}"
    KMOD_DIR="/lib/modules/${KVER}/extra"
    # .ko or .ko.zst (noble kernels compress modules on modules_install)
    KMOD_FILE="$(compgen -G "${KMOD_DIR}/axon-winabi.ko*" | head -1 || true)"

    if [[ -z "${KVER}" ]]; then
        log "WARNING: no kernel found in /boot — Windows ABI module skipped"
    elif [[ "${QUICK}" == "1" ]] && [[ -n "${KMOD_FILE}" ]] && \
         [[ -z "$(find "${KSRC}" -type f -newer "${KMOD_FILE}" -print -quit)" ]]; then
        # Like make: rebuild only when a source file is newer than the module
        log "Quick mode: Windows ABI module up to date for ${KVER} — skipping rebuild"
        MODULE_BUILT=true
    else
        apt-get install -y "linux-headers-${KVER}" || \
            log "WARNING: could not install linux-headers-${KVER} — Windows ABI module skipped"

        if [[ -d "${KDIR}" ]]; then
            # Not `make install`: its bare `depmod -a` targets `uname -r`
            if (cd "${KSRC}" && \
                make KDIR="${KDIR}" && \
                make -C "${KDIR}" M="${KSRC}" modules_install && \
                depmod -a "${KVER}"); then
                MODULE_BUILT=true
                log "Windows ABI module installed to ${KMOD_DIR}"
            else
                log "WARNING: Windows ABI kernel module build failed for ${KVER}"
            fi

            # Configure binfmt_misc support
            echo "binfmt_misc" > /etc/modules-load.d/binfmt.conf
        fi
    fi

    if [[ "${MODULE_BUILT}" == "true" ]] && [[ "${AXON_WINABI_AUTOLOAD:-0}" == "1" ]]; then
        log "AXON_WINABI_AUTOLOAD=1 — auto-loading untested axon-winabi module at boot"
        echo "axon-winabi" > /etc/modules-load.d/axon-winabi.conf
    fi
else
    log "Windows ABI module source not found — skipping"
fi

# ── 5a2. DirectX / Gaming integration ──────────────────────────────────────
log "Configuring DirectX translation layers..."
# Register DXVK and vk3d-proton DLL overrides
mkdir -p /usr/lib/axon-winabi/dlls
# Symlink DXVK native libraries
for dll in d3d9 d3d10 d3d10_1 d3d10core d3d11 dxgi; do
    if [ -f "/usr/lib/dxvk/${dll}.dll.so" ]; then
        ln -sf "/usr/lib/dxvk/${dll}.dll.so" "/usr/lib/axon-winabi/dlls/${dll}.dll.so"
        log "Linked DXVK ${dll}"
    fi
done
# Symlink vkd3d-proton
for dll in d3d12; do
    if [ -f "/usr/lib/vkd3d-proton/${dll}.dll.so" ]; then
        ln -sf "/usr/lib/vkd3d-proton/${dll}.dll.so" "/usr/lib/axon-winabi/dlls/${dll}.dll.so"
        log "Linked vkd3d-proton ${dll}"
    fi
done

# ── 5a3. Windows ABI desktop integration ─────────────────────────────────────
log "Configuring Windows ABI desktop integration..."

# Install launcher script
install -Dm755 "${SRC}/scripts/axon-winabi-run" /usr/local/bin/axon-winabi-run
install -Dm755 "${SRC}/scripts/axon-winabi-sandbox" /usr/local/bin/axon-winabi-sandbox

# MIME type for .exe files
cp "${SRC}/data/mime/axon-winabi-exe.xml" /usr/share/mime/packages/
update-mime-database /usr/share/mime || true

# Desktop entries for file associations
cp "${SRC}/data/applications/axon-winabi-run-exe.desktop" /usr/share/applications/
cp "${SRC}/data/applications/axon-winabi-exe-handler.desktop" /usr/share/applications/
update-desktop-database /usr/share/applications || true

# No polkit policy: Windows apps run as the invoking user, never as root.
# Remove the one older builds installed (it let any user run files as root).
rm -f /usr/share/polkit-1/actions/org.axonos.winabi.policy

# Create registry directory
mkdir -p /var/lib/axon-winabi/registry

# Create default Windows C: drive structure
mkdir -p /usr/lib/axon-winabi/drive_c/windows/system32
mkdir -p /usr/lib/axon-winabi/drive_c/windows/temp
mkdir -p /usr/lib/axon-winabi/drive_c/Program\ Files
mkdir -p /usr/lib/axon-winabi/drive_c/users

# Set up default environment
cat > /etc/profile.d/axon-winabi.sh <<'EOF'
# Axon Windows ABI environment
export WINEDLLPATH=/usr/lib/axon-winabi/dlls
export WINEPREFIX=${HOME}/.axon-winabi/prefix
export AXON_WINABI=1
EOF

# GNOME desktop integration: set .exe as default handler for PE files
xdg-mime default axon-winabi-run-exe.desktop application/x-ms-dos-executable 2>/dev/null || true

# ---------------------------------------------------------------------------
# 5b. Networking — hand every interface to NetworkManager
# ---------------------------------------------------------------------------
# Ubuntu's network-manager package marks all non-wifi devices "unmanaged"
# unless a desktop netplan config exists. debootstrap provides neither, so
# without these two files the live system boots with no working ethernet.
log "Configuring networking (NetworkManager manages everything)..."
mkdir -p /etc/netplan
cat > /etc/netplan/01-network-manager-all.yaml <<'EOF'
# Axon OS: let NetworkManager manage all devices
network:
  version: 2
  renderer: NetworkManager
EOF
chmod 600 /etc/netplan/01-network-manager-all.yaml

# Override the package default that excludes ethernet from NM management
mkdir -p /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/10-globally-managed-devices.conf <<'EOF'
[keyfile]
unmanaged-devices=none
EOF

systemctl enable NetworkManager.service || log "WARNING: could not enable NetworkManager"

# Boot time: nothing on a desktop should hold boot until the network is up.
# docker.service Wants=network-online.target, which pulls in
# NetworkManager-wait-online (up to 30 s with no cable/Wi-Fi). Docker starts
# on first use through its socket instead, and the wait-online units are
# disabled; services that need the network (axon-ai-firstboot, Ollama) wait
# for it themselves.
systemctl disable NetworkManager-wait-online.service \
    systemd-networkd-wait-online.service 2>/dev/null || true
systemctl disable docker.service containerd.service 2>/dev/null || true
systemctl enable docker.socket 2>/dev/null || log "WARNING: could not enable docker.socket"

# ---------------------------------------------------------------------------
# 6a. VM guest display integration (VirtualBox / VMware / QEMU)
# ---------------------------------------------------------------------------
# Display auto-resize, clipboard and drag-and-drop come from the guest
# packages in packages.list — they start themselves in every X11 session:
#   * VirtualBox: virtualbox-guest-x11 runs VBoxClient from
#     /etc/X11/Xsession.d/98vboxadd-xclient. Its --vmsvga-session helper
#     falls back to the X11 RandR resize agent (Ubuntu does not ship
#     VBoxDRMClient). The service side is virtualbox-guest-utils.service.
#   * VMware: open-vm-tools-desktop's /etc/xdg/autostart/vmware-user.desktop
#   * QEMU/KVM: spice-vdagent's own autostart entry and system unit
#
# Do NOT pin an Xorg driver for VMs. Ubuntu 24.04 has no vboxvideo_drv.so,
# and Xorg's autodetection already picks the right one: `vmware` for
# VirtualBox's default VMSVGA adapter (VBoxClient needs its VMWARE_CTRL
# extension for multi-monitor resize) and `modesetting` for VBoxVGA.
log "Configuring VM guest display integration..."

# Axon OS 1.0.7 shipped hand-rolled replacements for the above: a
# 10-vboxvideo.conf that forced a non-existent X driver (pushing VMSVGA onto
# modesetting) and a root system unit running VBoxClient without an X
# display. build.sh reuses the chroot between builds, so remove them
# explicitly rather than just no longer creating them.
systemctl disable axon-vbox-xorg-setup.service axon-vm-guest.service 2>/dev/null || true
rm -f /etc/systemd/system/axon-vbox-xorg-setup.service \
      /etc/systemd/system/sysinit.target.wants/axon-vbox-xorg-setup.service \
      /etc/systemd/system/axon-vm-guest.service \
      /etc/systemd/system/graphical.target.wants/axon-vm-guest.service \
      /usr/local/bin/axon-vbox-xorg-setup \
      /usr/local/bin/axon-vm-guest-init \
      /etc/xdg/autostart/axon-vm-guest.desktop \
      /etc/X11/xorg.conf.d/10-vboxvideo.conf

# VirtualBox's service unit on Ubuntu (there is no "vboxservice" unit). The
# package already enables it; ConditionVirtualization=oracle keeps it inert
# outside VirtualBox.
systemctl enable virtualbox-guest-utils.service 2>/dev/null || \
    log "WARNING: could not enable virtualbox-guest-utils.service"

# VirtualBox's VMSVGA "3D acceleration" path (Mesa's svga driver) is a common
# cause of black screens and frozen GNOME Shell sessions on 24.04 guests,
# while booting with nomodeset (safe graphics) avoids it. Render with
# llvmpipe inside VirtualBox instead. With 3D acceleration off (the
# VirtualBox default) Mesa already uses llvmpipe, so this changes nothing.
# A systemd user environment generator is evaluated at login, so it also
# covers the GDM greeter, and does nothing on other hypervisors or hardware.
install -Dm755 "${SRC}/build/config/axon-vm-graphics-env" \
    /usr/lib/systemd/user-environment-generators/60-axon-vm-graphics

# One-shot log collector for black-screen / resolution bug reports.
install -Dm755 "${SRC}/build/config/axon-display-diag" /usr/local/bin/axon-display-diag

# ---------------------------------------------------------------------------
# 6b. GNOME defaults (gschema overrides apply to every user, incl. live)
# ---------------------------------------------------------------------------
# Window/shell look: WhiteSur GTK + Shell theme (MIT, built from source at
# image-build time; falls back to the Axon dark theme if anything fails).
# Icons: Papirus (GPL-3.0, from the Ubuntu archive). The WhiteSur icon theme
# redraws Apple's app icons, so it is no longer shipped. See
# docs/THIRD-PARTY.md for every bundled theme and app and its license.
log "Installing WhiteSur GTK/Shell theme..."
GTK_THEME_NAME='axon-gtk'
ICON_THEME_NAME='Papirus-Dark'
SHELL_THEME_NAME=''
# Drop the WhiteSur icon theme that older builds left in reused chroots
rm -rf /usr/share/icons/WhiteSur /usr/share/icons/WhiteSur-dark /usr/share/icons/WhiteSur-light

# In quick mode, skip theme rebuild if themes are already installed
WHITESUR_SKIP=false
if [[ "${QUICK}" == "1" ]] && [[ -d /usr/share/themes/WhiteSur-Dark ]]; then
    log "Quick mode: WhiteSur theme already installed — skipping rebuild"
    GTK_THEME_NAME='WhiteSur-Dark'
    SHELL_THEME_NAME='WhiteSur-Dark'
    WHITESUR_SKIP=true
fi

if [[ "${WHITESUR_SKIP}" == "false" ]]; then
    apt-get install -y sassc libglib2.0-dev-bin || log "WARNING: theme build deps failed"
    # Pinned commit hashes for reproducible builds — update these when bumping themes.
    # Their install.sh runs as root in the image, so never track a branch.
    WHITESUR_GTK_COMMIT="${WHITESUR_GTK_COMMIT:-d5782652d412137e26fb8ff55b55a5572e4c6995}"
    if git clone https://github.com/vinceliuice/WhiteSur-gtk-theme.git /tmp/wsg \
       && git -C /tmp/wsg checkout "${WHITESUR_GTK_COMMIT}" \
       && /tmp/wsg/install.sh -d /usr/share/themes -c Dark -N glassy; then
        GTK_THEME_NAME='WhiteSur-Dark'
        SHELL_THEME_NAME='WhiteSur-Dark'
    else
        log "WARNING: WhiteSur GTK theme install failed — keeping axon-gtk"
    fi
    rm -rf /tmp/wsg
fi

# The user-theme extension schema lives outside the default schema dir; copy
# it in so the gschema override below can reference it.
USER_THEME_EXT="user-theme@gnome-shell-extensions.gcampax.github.com"
USER_THEME_SCHEMA="/usr/share/gnome-shell/extensions/${USER_THEME_EXT}/schemas/org.gnome.shell.extensions.user-theme.gschema.xml"
if [[ -f "${USER_THEME_SCHEMA}" ]]; then
    cp "${USER_THEME_SCHEMA}" /usr/share/glib-2.0/schemas/
fi

log "Applying GNOME defaults..."
cat > /usr/share/glib-2.0/schemas/90_axon-os.gschema.override <<EOF
[org.gnome.desktop.interface]
color-scheme='prefer-dark'
gtk-theme='${GTK_THEME_NAME}'
icon-theme='${ICON_THEME_NAME}'
font-name='Inter 11'
enable-animations=true
cursor-size=24
text-scaling-factor=1.0

[org.gnome.desktop.background]
picture-uri='file:///usr/share/backgrounds/axon/axon-aurora.png'
picture-uri-dark='file:///usr/share/backgrounds/axon/axon-aurora.png'
picture-options='zoom'

[org.gnome.desktop.screensaver]
picture-uri='file:///usr/share/backgrounds/axon/axon-aurora.png'

[org.gnome.desktop.wm.preferences]
num-workspaces=9
workspace-names=['Code', 'Web', 'Chat', 'Files', 'Media', 'Work', 'Personal', 'Terminal', 'Notes']
button-layout='close,minimize,maximize:'

[org.gnome.mutter]
dynamic-workspaces=false
edge-tiling=true
experimental-features=['scale-monitor-framebuffer']

[org.gnome.desktop.peripherals.touchpad]
tap-to-click=true

[org.gnome.settings-daemon.plugins.media-keys]
custom-keybindings=['/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/axon-voice/']

[org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/axon-voice/]
name='Axon Voice (push-to-talk)'
command='/usr/local/bin/axon-voice-toggle'
binding='<Super>v'

[org.gnome.shell]
enabled-extensions=['axon-shell@axon-os', '${USER_THEME_EXT}']
favorite-apps=['axon-welcome.desktop', 'install-axon-os.desktop', 'org.gnome.Nautilus.desktop', '${BROWSER_DESKTOP}', 'axon-terminal.desktop', 'axon-ai-panel.desktop', 'axon-settings.desktop']
EOF

if [[ -n "${SHELL_THEME_NAME}" && -f /usr/share/glib-2.0/schemas/org.gnome.shell.extensions.user-theme.gschema.xml ]]; then
    cat >> /usr/share/glib-2.0/schemas/90_axon-os.gschema.override <<EOF

[org.gnome.shell.extensions.user-theme]
name='${SHELL_THEME_NAME}'
EOF
fi
glib-compile-schemas /usr/share/glib-2.0/schemas/

# ---------------------------------------------------------------------------
# 7. Plymouth boot splash
# ---------------------------------------------------------------------------
log "Installing Plymouth theme..."
mkdir -p /usr/share/plymouth/themes/axon
cp "${SRC}/plymouth/axon-splash/axon.plymouth" \
   "${SRC}/plymouth/axon-splash/axon.script" \
   "${SRC}/plymouth/axon-splash/axon.png" \
   "${SRC}/plymouth/axon-splash/progress-track.png" \
   "${SRC}/plymouth/axon-splash/progress-fill.png" \
   /usr/share/plymouth/themes/axon/
update-alternatives --install /usr/share/plymouth/themes/default.plymouth \
    default.plymouth /usr/share/plymouth/themes/axon/axon.plymouth 200
update-alternatives --set default.plymouth \
    /usr/share/plymouth/themes/axon/axon.plymouth

# ---------------------------------------------------------------------------
# 8. Axon Installer (native welcome + install wizard)
# ---------------------------------------------------------------------------
log "Configuring the Axon Installer..."

# Root-engine wrapper, referenced by the polkit policy so pkexec can grant it
# Refuses to run outside the live session: the polkit policy grants it root
# without a password, and the install engine strips both from the target.
cat > /usr/local/bin/axon-install-engine <<EOF
#!/bin/sh
grep -qw boot=casper /proc/cmdline || { echo "axon-install-engine: live session only" >&2; exit 1; }
exec /usr/bin/python3 ${APPS_DIR}/axon-installer/install_engine.py "\$@"
EOF
chmod 755 /usr/local/bin/axon-install-engine

mkdir -p /usr/share/polkit-1/actions
cp "${SRC}/data/polkit/org.axonos.install-engine.policy" /usr/share/polkit-1/actions/

# AI first-boot provisioner: installs Ollama + pulls the chosen model on the
# installed system's first online boot. The unit stays disabled in the image;
# the install engine enables it on the target when the user opts in.
install -Dm755 "${SRC}/build/config/ai-firstboot.sh" /usr/local/bin/axon-ai-firstboot
# Hash-pinned Ollama installer used by first boot and axon-ollama-setup
install -Dm755 "${SRC}/build/config/install-ollama.sh" /usr/lib/axon/ollama/install-ollama.sh
install -Dm644 "${SRC}/build/config/ollama-release.env" /usr/lib/axon/ollama/ollama-release.env
cat > /usr/lib/systemd/system/axon-ai-firstboot.service <<'EOF'
[Unit]
Description=Axon OS AI first-boot setup (Ollama install + model pull)
After=network.target NetworkManager.service
ConditionPathExists=/etc/axon/ai-setup.json
StartLimitIntervalSec=0

# Type=exec, not oneshot: a multi-GB download must not hold up boot. The
# script waits for the network itself and exits 75 to be retried.
[Service]
Type=exec
ExecStart=/usr/local/bin/axon-ai-firstboot
Environment=HOME=/root
Restart=on-failure
RestartSec=60
Nice=10
IOSchedulingClass=idle

[Install]
WantedBy=multi-user.target
EOF

# Auto-launch the installer wizard in the live session only (boot=casper)
mkdir -p /etc/xdg/autostart
cat > /etc/xdg/autostart/axon-installer-live.desktop <<EOF
[Desktop Entry]
Type=Application
Name=Welcome to Axon OS
Comment=Welcome and installation wizard for the live session
Exec=sh -c "grep -q boot=casper /proc/cmdline && exec /usr/bin/python3 ${APPS_DIR}/axon-installer/main.py"
Terminal=false
StartupNotify=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Phase=Applications
EOF

# ---------------------------------------------------------------------------
# 9. Identity: hostname, casper live user, os-release
# ---------------------------------------------------------------------------
log "Setting system identity..."
echo "axon-os" > /etc/hostname
cat > /etc/hosts <<'EOF'
127.0.0.1   localhost
127.0.1.1   axon-os

::1         ip6-localhost ip6-loopback
fe00::0     ip6-localnet
ff00::0     ip6-mcastprefix
ff02::1     ip6-allnodes
ff02::2     ip6-allrouters
EOF

cat > /etc/casper.conf <<'EOF'
export USERNAME="axon"
export USERFULLNAME="Axon Live"
export HOST="axon-os"
export BUILD_SYSTEM="Ubuntu"
export FLAVOUR="Axon"
EOF

# GDM autologin for the live session — casper's built-in autologin can fail on
# Ubuntu 24.04, leaving the user on a black screen after Plymouth quits.
log "Configuring GDM autologin for live session..."
mkdir -p /etc/gdm3
cat > /etc/gdm3/custom.conf <<'EOF'
[daemon]
AutomaticLoginEnable=true
AutomaticLogin=axon
WaylandEnable=false
EOF

# /etc/os-release is a symlink to /usr/lib/os-release on Ubuntu; replace the
# link with Axon identity while keeping ID_LIKE for tooling compatibility.
rm -f /etc/os-release
cat > /etc/os-release <<EOF
PRETTY_NAME="Axon OS ${VERSION} (${CODENAME})"
NAME="Axon OS"
VERSION_ID="${VERSION}"
VERSION="${VERSION} (${CODENAME})"
VERSION_CODENAME=${CODENAME,,}
ID=axonos
ID_LIKE="ubuntu debian"
UBUNTU_CODENAME=noble
HOME_URL="https://github.com/ProjectDEV-git/Axon-OS"
SUPPORT_URL="https://github.com/ProjectDEV-git/Axon-OS/issues"
BUG_REPORT_URL="https://github.com/ProjectDEV-git/Axon-OS/issues"
LOGO=axon-os
EOF

cat > /etc/axon-release <<EOF
AXON_VERSION=${VERSION}
AXON_CODENAME=${CODENAME}
EOF

# ---------------------------------------------------------------------------
# 10. Regenerate initramfs (casper + plymouth hooks) and clean up
# ---------------------------------------------------------------------------
if [[ "${QUICK}" == "1" ]]; then
    # Not skippable: the casper and plymouth hooks copy /etc/casper.conf and
    # the Plymouth theme written above into the initrd. Only the ISO kernel's
    # initrd is shipped, so regenerate just that one.
    ISO_KVER="$(iso_kernel_version)"
    log "Quick mode: regenerating initramfs only for the ISO kernel ${ISO_KVER}"
    update-initramfs -u -k "${ISO_KVER:-all}"
else
    log "Regenerating initramfs..."
    update-initramfs -u -k all
fi

log "Cleaning up..."
dpkg --configure -a 2>/dev/null || log "WARNING: dpkg configure had errors (DKMS-related, non-fatal)"
apt-get autoremove -y 2>/dev/null || log "WARNING: autoremove had errors (non-fatal)"
apt-get clean
rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
rm -f /usr/sbin/policy-rc.d /root/.bash_history /root/.wget-hsts
# Fresh machine-id is generated on first boot of each system
truncate -s 0 /etc/machine-id

log "Chroot configuration complete."
