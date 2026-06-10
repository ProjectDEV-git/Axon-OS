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
VERSION="${AXON_VERSION:-0.1.0}"
CODENAME="Pulse"

log() { echo "[chroot-setup] $*"; }

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
deb http://archive.ubuntu.com/ubuntu/ noble main restricted universe multiverse
deb http://archive.ubuntu.com/ubuntu/ noble-updates main restricted universe multiverse
deb http://security.ubuntu.com/ubuntu/ noble-security main restricted universe multiverse
EOF
# debootstrap may have created the new deb822 file; sources.list wins, drop it
rm -f /etc/apt/sources.list.d/ubuntu.sources

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
if ! apt-get install -y "${PACKAGES[@]}"; then
    log "Bulk install failed — retrying packages one at a time..."
    for p in "${PACKAGES[@]}"; do
        apt-get install -y "${p}" || log "WARNING: package ${p} failed to install"
    done
fi

log "Installing Python AI libraries inside chroot..."
pip3 install faster-whisper sqlite-vec --break-system-packages || log "WARNING: Python AI libraries failed to install"


# ---------------------------------------------------------------------------
# 5. Axon OS components (system-wide)
# ---------------------------------------------------------------------------
log "Installing Axon OS components..."

AXON_LIB="/usr/lib/axon"
APPS_DIR="${AXON_LIB}/apps"
SERVICES_DIR="${AXON_LIB}/services"

mkdir -p "${AXON_LIB}"
cp -r "${SRC}/apps" "${APPS_DIR}"
cp -r "${SRC}/services" "${SERVICES_DIR}"
cp -r "${SRC}/shell" "${AXON_LIB}/shell"
mkdir -p "${AXON_LIB}/data"
cp -r "${SRC}/data/applications" "${AXON_LIB}/data/applications"
find "${AXON_LIB}" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# Desktop entries -> /usr/share/applications (resolve AXON_APPS_DIR)
for f in "${SRC}/data/applications/"*.desktop; do
    sed "s|AXON_APPS_DIR|${APPS_DIR}|g" "${f}" \
        > "/usr/share/applications/$(basename "${f}")"
done

# D-Bus session activation files (resolve AXON_SERVICES_DIR)
mkdir -p /usr/share/dbus-1/services /usr/share/dbus-1/session.d
sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" \
    "${SERVICES_DIR}/axon-brain/org.axonos.Brain.service" \
    > /usr/share/dbus-1/services/org.axonos.Brain.service
sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" \
    "${SERVICES_DIR}/axon-context/org.axonos.Context.service" \
    > /usr/share/dbus-1/services/org.axonos.Context.service
cp "${SERVICES_DIR}/axon-brain/org.axonos.Brain.conf" /usr/share/dbus-1/session.d/
cp "${SERVICES_DIR}/axon-context/org.axonos.Context.conf" /usr/share/dbus-1/session.d/

# systemd user units, enabled globally for every user
mkdir -p /usr/lib/systemd/user
sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" \
    "${SERVICES_DIR}/axon-brain/axon-brain.service" \
    > /usr/lib/systemd/user/axon-brain.service
sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" \
    "${SERVICES_DIR}/axon-context/axon-context.service" \
    > /usr/lib/systemd/user/axon-context.service
systemctl --global enable axon-brain.service axon-context.service

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

# ---------------------------------------------------------------------------
# 6. GNOME defaults (gschema overrides apply to every user, incl. live)
# ---------------------------------------------------------------------------
log "Applying GNOME defaults..."
cat > /usr/share/glib-2.0/schemas/90_axon-os.gschema.override <<'EOF'
[org.gnome.desktop.interface]
color-scheme='prefer-dark'
gtk-theme='axon-gtk'
icon-theme='Papirus-Dark'
font-name='Inter 11'
enable-animations=true

[org.gnome.desktop.background]
picture-uri='file:///usr/share/backgrounds/axon/axon-aurora.png'
picture-uri-dark='file:///usr/share/backgrounds/axon/axon-aurora.png'
picture-options='zoom'

[org.gnome.desktop.screensaver]
picture-uri='file:///usr/share/backgrounds/axon/axon-aurora.png'

[org.gnome.desktop.wm.preferences]
num-workspaces=9
workspace-names=['Code', 'Web', 'Chat', 'Files', 'Media', 'Work', 'Personal', 'Terminal', 'Notes']

[org.gnome.mutter]
dynamic-workspaces=false

[org.gnome.shell]
enabled-extensions=['axon-shell@axon-os']
favorite-apps=['axon-welcome.desktop', 'install-axon-os.desktop', 'org.gnome.Nautilus.desktop', 'org.gnome.Epiphany.desktop', 'axon-terminal.desktop', 'axon-files.desktop', 'axon-ai-panel.desktop', 'axon-settings.desktop']
EOF
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
# 8. Custom Axon OS installer configuration
# ---------------------------------------------------------------------------
log "Configuring custom Axon OS installer..."
mkdir -p "${AXON_LIB}/installer"
cp -r "${SRC}/installer/." "${AXON_LIB}/installer/"
chmod +x "${AXON_LIB}/installer/axon-installer.py"

cat > /usr/share/applications/install-axon-os.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Install Axon OS
Comment=Install Axon OS to your hard disk
Exec=sh -c "pkexec python3 /usr/lib/axon/installer/axon-installer.py || sudo -E python3 /usr/lib/axon/installer/axon-installer.py"
Icon=system-software-install
Terminal=false
Categories=System;
Keywords=installer;axon;system;
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
HOME_URL="https://github.com/kaorii-ako/Axon-OS"
SUPPORT_URL="https://github.com/kaorii-ako/Axon-OS/issues"
BUG_REPORT_URL="https://github.com/kaorii-ako/Axon-OS/issues"
LOGO=axon-os
EOF

cat > /etc/axon-release <<EOF
AXON_VERSION=${VERSION}
AXON_CODENAME=${CODENAME}
EOF

# ---------------------------------------------------------------------------
# 10. Regenerate initramfs (casper + plymouth hooks) and clean up
# ---------------------------------------------------------------------------
log "Regenerating initramfs..."
update-initramfs -u -k all

log "Cleaning up..."
apt-get autoremove -y
apt-get clean
rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
rm -f /usr/sbin/policy-rc.d /root/.bash_history /root/.wget-hsts
# Fresh machine-id is generated on first boot of each system
truncate -s 0 /etc/machine-id

log "Chroot configuration complete."
