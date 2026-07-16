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

# QUICK mode: passed from build.sh via env var. Skips expensive non-essential
# steps (theme rebuilds, kernel module rebuild, initramfs regen) to speed up
# iterative development rebuilds. Set AXON_QUICK=1 to enable.
QUICK="${AXON_QUICK:-0}"
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

log "Installing Python AI libraries inside chroot..."
pip3 install --no-cache-dir faster-whisper sqlite-vec --break-system-packages || log "WARNING: Python AI libraries failed to install"


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
for buspolicy in "${SERVICES_DIR}"/*/org.axonos.*.conf; do
    [[ -f "${buspolicy}" ]] && cp "${buspolicy}" /usr/share/dbus-1/session.d/
done

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
systemctl enable axon-boot-watchdog.service

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
    # Ensure timeout style is menu and timeout is 5 seconds
    sed -i 's/^GRUB_TIMEOUT_STYLE=.*/GRUB_TIMEOUT_STYLE=menu/' /etc/default/grub
    sed -i 's/^GRUB_TIMEOUT=.*/GRUB_TIMEOUT=5/' /etc/default/grub
    
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
log "Building Axon Windows ABI kernel module..."
MODULE_BUILT=false
if [[ -d "${SRC}/kernel/axon-winabi" ]]; then
    # Check if module is already built (for --quick mode)
    KMOD_DIR="/lib/modules/$(uname -r)/extra"
    if [[ "${QUICK}" == "1" ]] && [[ -f "${KMOD_DIR}/axon-winabi.ko" ]]; then
        log "Quick mode: Windows ABI module already built — skipping rebuild"
        MODULE_BUILT=true
    fi

    if [[ "${MODULE_BUILT}" == "false" ]]; then
        # Install kernel headers if not already present
        apt-get install -y linux-headers-$(uname -r) || \
            apt-get install -y linux-headers-generic || \
            log "WARNING: could not install kernel headers — Windows ABI module skipped"

        if [[ -d /usr/src/linux-headers-$(uname -r) ]]; then
            (cd "${SRC}/kernel/axon-winabi" && \
             make KDIR=/usr/src/linux-headers-$(uname -r) && \
             make KDIR=/usr/src/linux-headers-$(uname -r) install) || \
                log "WARNING: Windows ABI kernel module build failed"

            # Auto-load the module on boot
            echo "axon-winabi" >> /etc/modules-load.d/axon-winabi.conf 2>/dev/null || \
                echo "axon-winabi" > /etc/modules-load.d/axon-winabi.conf

            # Configure binfmt_misc support
            echo "binfmt_misc" >> /etc/modules-load.d/binfmt.conf 2>/dev/null || \
                echo "binfmt_misc" > /etc/modules-load.d/binfmt.conf
        fi
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

# Polkit policy
cp "${SRC}/data/polkit/org.axonos.winabi.policy" /usr/share/polkit-1/actions/

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

# ---------------------------------------------------------------------------
# 6a. VM guest integration (auto-resize display in VirtualBox/VMware/QEMU)
# ---------------------------------------------------------------------------
log "Configuring VM guest tools..."
install -Dm755 /dev/stdin /usr/local/bin/axon-vm-guest-init <<'VMEOF'
#!/bin/sh
# Detect the virtualisation platform and start the appropriate guest tools
# so the display auto-resizes to match the host window.
case "$(systemd-detect-virt 2>/dev/null || echo none)" in
    oracle)   # VirtualBox
        VBoxClient --display    2>/dev/null || true
        VBoxClient --vmsvga     2>/dev/null || true
        VBoxClient --clipboard  2>/dev/null || true
        VBoxClient --draganddrop 2>/dev/null || true
        ;;
    vmware)
        /usr/bin/vmware-user-suid-wrapper 2>/dev/null || true
        ;;
    qemu|kvm)
        spice-vdagent 2>/dev/null || true
        ;;
esac
VMEOF

# XDG autostart (works for installed systems with a normal GNOME session)
cat > /etc/xdg/autostart/axon-vm-guest.desktop <<'VMDESK'
[Desktop Entry]
Type=Application
Name=Axon VM Guest Integration
Comment=Starts guest display tools for VirtualBox/VMware/QEMU auto-resize
Exec=/usr/local/bin/axon-vm-guest-init
Terminal=false
StartupNotify=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Phase=Initialization
VMDESK

# systemd service (more reliable, especially in live sessions)
cat > /etc/systemd/system/axon-vm-guest.service <<'VMUNIT'
[Unit]
Description=Axon VM guest display auto-resize (VirtualBox/VMware/QEMU)
After=display-manager.service
Wants=display-manager.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/axon-vm-guest-init
RemainAfterExit=yes

[Install]
WantedBy=graphical.target
VMUNIT

# Enable the vboxservice system service if present (VirtualBox's own
# systemd unit that runs VBoxClient --vmsvga + clipboard + drag-and-drop).
# This is the most reliable way to get auto-resize in VirtualBox.
systemctl enable vboxservice 2>/dev/null || true

# Also enable our custom unit as a safety net
systemctl enable axon-vm-guest.service 2>/dev/null || true

# ---------------------------------------------------------------------------
# 6b. GNOME defaults (gschema overrides apply to every user, incl. live)
# ---------------------------------------------------------------------------
# macOS-style look: WhiteSur GTK + Shell + icon themes (built from source at
# image-build time; falls back to the Axon dark theme if anything fails).
log "Installing WhiteSur (macOS-style) themes..."
GTK_THEME_NAME='axon-gtk'
ICON_THEME_NAME='Papirus-Dark'
SHELL_THEME_NAME=''

# In quick mode, skip theme rebuild if themes are already installed
WHITESUR_SKIP=false
if [[ "${QUICK}" == "1" ]] && [[ -d /usr/share/themes/WhiteSur-Dark ]] && [[ -d /usr/share/icons/WhiteSur-dark ]]; then
    log "Quick mode: WhiteSur themes already installed — skipping rebuild"
    GTK_THEME_NAME='WhiteSur-Dark'
    ICON_THEME_NAME='WhiteSur-dark'
    SHELL_THEME_NAME='WhiteSur-Dark'
    WHITESUR_SKIP=true
fi

if [[ "${WHITESUR_SKIP}" == "false" ]]; then
    apt-get install -y sassc libglib2.0-dev-bin || log "WARNING: theme build deps failed"
    # Pinned commit hashes for reproducible builds — update these when bumping themes.
    WHITESUR_GTK_COMMIT="${WHITESUR_GTK_COMMIT:-master}"
    WHITESUR_ICON_COMMIT="${WHITESUR_ICON_COMMIT:-master}"
    if git clone https://github.com/vinceliuice/WhiteSur-gtk-theme.git /tmp/wsg \
       && git -C /tmp/wsg checkout "${WHITESUR_GTK_COMMIT}" \
       && /tmp/wsg/install.sh -d /usr/share/themes -c Dark -N glassy; then
        GTK_THEME_NAME='WhiteSur-Dark'
        SHELL_THEME_NAME='WhiteSur-Dark'
    else
        log "WARNING: WhiteSur GTK theme install failed — keeping axon-gtk"
    fi
    if git clone https://github.com/vinceliuice/WhiteSur-icon-theme.git /tmp/wsi \
       && git -C /tmp/wsi checkout "${WHITESUR_ICON_COMMIT}" \
       && /tmp/wsi/install.sh -d /usr/share/icons; then
        ICON_THEME_NAME='WhiteSur-dark'
    else
        log "WARNING: WhiteSur icon theme install failed — keeping Papirus-Dark"
    fi
    rm -rf /tmp/wsg /tmp/wsi
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
favorite-apps=['axon-welcome.desktop', 'install-axon-os.desktop', 'org.gnome.Nautilus.desktop', 'org.gnome.Epiphany.desktop', 'axon-terminal.desktop', 'axon-ai-panel.desktop', 'axon-settings.desktop']
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
cat > /usr/local/bin/axon-install-engine <<EOF
#!/bin/sh
exec /usr/bin/python3 ${APPS_DIR}/axon-installer/install_engine.py "\$@"
EOF
chmod 755 /usr/local/bin/axon-install-engine

mkdir -p /usr/share/polkit-1/actions
cp "${SRC}/data/polkit/org.axonos.install-engine.policy" /usr/share/polkit-1/actions/

# AI first-boot provisioner: installs Ollama + pulls the chosen model on the
# installed system's first online boot. The unit stays disabled in the image;
# the install engine enables it on the target when the user opts in.
install -Dm755 "${SRC}/build/config/ai-firstboot.sh" /usr/local/bin/axon-ai-firstboot
cat > /usr/lib/systemd/system/axon-ai-firstboot.service <<'EOF'
[Unit]
Description=Axon OS AI first-boot setup (Ollama install + model pull)
Wants=network-online.target
After=network-online.target NetworkManager-wait-online.service
ConditionPathExists=/etc/axon/ai-setup.json

[Service]
Type=oneshot
ExecStart=/usr/local/bin/axon-ai-firstboot
TimeoutStartSec=0

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
    log "Quick mode: skipping initramfs regeneration"
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
