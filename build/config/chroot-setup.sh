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
VERSION="${AXON_VERSION:-0.2.1}"
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
if ! apt-get install -y "${PACKAGES[@]}"; then
    log "Bulk install failed — retrying packages one at a time..."
    for p in "${PACKAGES[@]}"; do
        apt-get install -y "${p}" || log "WARNING: package ${p} failed to install"
    done
fi

log "Adding flathub remote..."
flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo || true

log "Installing Python AI libraries inside chroot..."
pip3 install faster-whisper sqlite-vec --break-system-packages || log "WARNING: Python AI libraries failed to install"


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

# D-Bus session activation files (resolve AXON_SERVICES_DIR)
mkdir -p /usr/share/dbus-1/services /usr/share/dbus-1/session.d
sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" \
    "${SERVICES_DIR}/axon-brain/org.axonos.Brain.service" \
    > /usr/share/dbus-1/services/org.axonos.Brain.service
sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" \
    "${SERVICES_DIR}/axon-context/org.axonos.Context.service" \
    > /usr/share/dbus-1/services/org.axonos.Context.service
sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" \
    "${SERVICES_DIR}/axon-voice/org.axonos.Voice.service" \
    > /usr/share/dbus-1/services/org.axonos.Voice.service
sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" \
    "${SERVICES_DIR}/axon-sandbox/org.axonos.Sandbox.service" \
    > /usr/share/dbus-1/services/org.axonos.Sandbox.service
sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" \
    "${SERVICES_DIR}/axon-gui-agent/org.axonos.GuiAgent.service" \
    > /usr/share/dbus-1/services/org.axonos.GuiAgent.service

cp "${SERVICES_DIR}/axon-brain/org.axonos.Brain.conf" /usr/share/dbus-1/session.d/
cp "${SERVICES_DIR}/axon-context/org.axonos.Context.conf" /usr/share/dbus-1/session.d/
cp "${SERVICES_DIR}/axon-voice/org.axonos.Voice.conf" /usr/share/dbus-1/session.d/
cp "${SERVICES_DIR}/axon-sandbox/org.axonos.Sandbox.conf" /usr/share/dbus-1/session.d/
cp "${SERVICES_DIR}/axon-gui-agent/org.axonos.GuiAgent.conf" /usr/share/dbus-1/session.d/

# systemd user units, enabled globally for every user
mkdir -p /usr/lib/systemd/user
sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" \
    "${SERVICES_DIR}/axon-brain/axon-brain.service" \
    > /usr/lib/systemd/user/axon-brain.service
sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" \
    "${SERVICES_DIR}/axon-context/axon-context.service" \
    > /usr/lib/systemd/user/axon-context.service
sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" \
    "${SERVICES_DIR}/axon-context/axon-file-indexer.service" \
    > /usr/lib/systemd/user/axon-file-indexer.service
sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" \
    "${SERVICES_DIR}/axon-voice/axon-voice.service" \
    > /usr/lib/systemd/user/axon-voice.service
sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" \
    "${SERVICES_DIR}/axon-sandbox/axon-sandbox.service" \
    > /usr/lib/systemd/user/axon-sandbox.service
sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" \
    "${SERVICES_DIR}/axon-gui-agent/axon-gui-agent.service" \
    > /usr/lib/systemd/user/axon-gui-agent.service

systemctl --global enable axon-brain.service axon-context.service axon-file-indexer.service axon-voice.service axon-sandbox.service axon-gui-agent.service

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

# Append GRUB self-healing boot attempts watchdog logic to 00_header
if [[ -f /etc/grub.d/00_header ]]; then
    log "Injecting GRUB boot-attempts rollback watchdog logic..."
    cat << 'EOF' >> /etc/grub.d/00_header

# Axon OS Self-Healing Watchdog
if [ -s \$prefix/grubenv ]; then
  load_env boot_attempts
fi
if [ "\${boot_attempts}" -gt 2 ]; then
  # Fallback to recovery, live session, or previous entry
  set default="1"
  echo "⬡ Axon Rogue Shield: Boot loop detected. Rolling back to fallback..."
else
  if [ -z "\${boot_attempts}" ]; then
    set boot_attempts=1
  elif [ "\${boot_attempts}" = "1" ]; then
    set boot_attempts=2
  elif [ "\${boot_attempts}" = "2" ]; then
    set boot_attempts=3
  fi
  save_env boot_attempts
fi
EOF
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
# 6. GNOME defaults (gschema overrides apply to every user, incl. live)
# ---------------------------------------------------------------------------
# macOS-style look: WhiteSur GTK + Shell + icon themes (built from source at
# image-build time; falls back to the Axon dark theme if anything fails).
log "Installing WhiteSur (macOS-style) themes..."
apt-get install -y sassc libglib2.0-dev-bin || log "WARNING: theme build deps failed"
GTK_THEME_NAME='axon-gtk'
ICON_THEME_NAME='Papirus-Dark'
SHELL_THEME_NAME=''
if git clone --depth=1 https://github.com/vinceliuice/WhiteSur-gtk-theme.git /tmp/wsg \
   && /tmp/wsg/install.sh -d /usr/share/themes -c Dark -t purple -N glassy; then
    GTK_THEME_NAME='WhiteSur-Dark-purple'
    SHELL_THEME_NAME='WhiteSur-Dark-purple'
else
    log "WARNING: WhiteSur GTK theme install failed — keeping axon-gtk"
fi
if git clone --depth=1 https://github.com/vinceliuice/WhiteSur-icon-theme.git /tmp/wsi \
   && /tmp/wsi/install.sh -d /usr/share/icons; then
    ICON_THEME_NAME='WhiteSur-dark'
else
    log "WARNING: WhiteSur icon theme install failed — keeping Papirus-Dark"
fi
rm -rf /tmp/wsg /tmp/wsi

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

[org.gnome.desktop.peripherals.touchpad]
tap-to-click=true

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
