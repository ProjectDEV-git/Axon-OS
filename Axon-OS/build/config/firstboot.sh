#!/usr/bin/env bash
# Axon OS — First Boot Configuration Script
# Runs once on the user's first login. Subsequent logins are no-ops.
set -euo pipefail

DONE="${HOME}/.config/axon-os/.firstboot-done"

# Guard: skip if already completed
[[ -f "${DONE}" ]] && exit 0

# Create axon user config directory
mkdir -p "${HOME}/.config/axon-os"
mkdir -p "${HOME}/.local/share"
mkdir -p "${HOME}/.local/share/applications"

# Source directory containing Axon components inside chroot image
AXON_SYS_DIR="/usr/lib/axon"
APPS_DEST="${HOME}/.local/share/axon-os"
SERVICES_DEST="${APPS_DEST}/services"
EXTENSION_DEST="${HOME}/.local/share/gnome-shell/extensions/axon-shell@axon-os"

# 1. Copy Apps and Services if present in system paths
if [[ -d "${AXON_SYS_DIR}" ]]; then
    echo "[axon-firstboot] Copying apps and services..."
    mkdir -p "${APPS_DEST}"
    if [[ -d "${AXON_SYS_DIR}/apps" ]]; then
        cp -r "${AXON_SYS_DIR}/apps/"* "${APPS_DEST}/"
    fi
    
    if [[ -d "${AXON_SYS_DIR}/services" ]]; then
        mkdir -p "${SERVICES_DEST}"
        cp -r "${AXON_SYS_DIR}/services/"* "${SERVICES_DEST}/"
    fi
    
    # 2. Copy GNOME Shell extension
    if [[ -d "${AXON_SYS_DIR}/shell/axon-shell" ]]; then
        echo "[axon-firstboot] Copying GNOME extension..."
        mkdir -p "${EXTENSION_DEST}"
        cp -r "${AXON_SYS_DIR}/shell/axon-shell/." "${EXTENSION_DEST}/"
        
        # Compile GSettings schemas
        if command -v glib-compile-schemas &>/dev/null; then
            glib-compile-schemas "${EXTENSION_DEST}/schemas/" 2>/dev/null || true
        fi
        
        # Enable extension
        gnome-extensions enable axon-shell@axon-os 2>/dev/null || true
    fi
fi

# 3. Configure D-Bus activation and systemd user services
if [[ -d "${SERVICES_DEST}" ]]; then
    echo "[axon-firstboot] Registering D-Bus session services..."
    DBUS_DIR="${HOME}/.local/share/dbus-1/services"
    mkdir -p "${DBUS_DIR}"
    
    if [[ -f "${SERVICES_DEST}/axon-brain/org.axonos.Brain.service" ]]; then
        sed "s|AXON_SERVICES_DIR|${SERVICES_DEST}|g" "${SERVICES_DEST}/axon-brain/org.axonos.Brain.service" > "${DBUS_DIR}/org.axonos.Brain.service"
    fi
    if [[ -f "${SERVICES_DEST}/axon-context/org.axonos.Context.service" ]]; then
        sed "s|AXON_SERVICES_DIR|${SERVICES_DEST}|g" "${SERVICES_DEST}/axon-context/org.axonos.Context.service" > "${DBUS_DIR}/org.axonos.Context.service"
    fi
    
    # Systemd user services
    echo "[axon-firstboot] Registering Systemd user services..."
    SYSTEMD_DIR="${HOME}/.config/systemd/user"
    mkdir -p "${SYSTEMD_DIR}"
    
    if [[ -f "${SERVICES_DEST}/axon-brain/axon-brain.service" ]]; then
        sed "s|AXON_SERVICES_DIR|${SERVICES_DEST}|g" "${SERVICES_DEST}/axon-brain/axon-brain.service" > "${SYSTEMD_DIR}/axon-brain.service"
    fi
    if [[ -f "${SERVICES_DEST}/axon-context/axon-context.service" ]]; then
        sed "s|AXON_SERVICES_DIR|${SERVICES_DEST}|g" "${SERVICES_DEST}/axon-context/axon-context.service" > "${SYSTEMD_DIR}/axon-context.service"
    fi
    
    # Reload and enable user units
    systemctl --user daemon-reload || true
    systemctl --user enable axon-brain.service axon-context.service || true
    systemctl --user restart axon-brain.service axon-context.service 2>/dev/null || true
fi

# 4. Generate .desktop launchers for applications
# Since .desktop files live in /usr/lib/axon/apps/ or we can generate them from system templates
# Let's search if they exist in apps/ or copy from data/applications inside repository
# Actually, the build.sh does not copy data/applications into /usr/lib/axon. Let's make sure it does or we copy them.
# Wait, let's check if the installer copies them or we copy them. If we place them in /usr/share/applications during ISO build, that is system-wide and clean!
# But since they need path replacements, we can do it here if we copy them to /usr/lib/axon/data/applications first.
# Let's check if we can copy data/applications/ during build.sh.
# In build.sh, we will add:
# _rsync_if_exists "${BASE_DIR}/data/applications" "${CHROOT_OVERLAY}/usr/lib/axon/data/applications"
# Then here we can replace and install them:
DATA_APPS_DIR="/usr/lib/axon/data/applications"
if [[ -d "${DATA_APPS_DIR}" ]]; then
    echo "[axon-firstboot] Installing desktop application entry files..."
    for f in "${DATA_APPS_DIR}"/*.desktop; do
        DEST_FILE="${HOME}/.local/share/applications/$(basename "${f}")"
        sed "s|AXON_APPS_DIR|${APPS_DEST}|g" "${f}" > "${DEST_FILE}"
        chmod +x "${DEST_FILE}" || true
    done
fi

# 5. Configure workspace names
echo "[axon-firstboot] Applying workspace configurations..."
gsettings set org.gnome.desktop.wm.preferences workspace-names \
    "[Code,Web,Chat,Files,Media,Work,Personal,Terminal,Notes]"
gsettings set org.gnome.desktop.wm.preferences num-workspaces 9
gsettings set org.gnome.mutter dynamic-workspaces false
gsettings set org.gnome.desktop.interface enable-animations true

# Apply theme preferences
gsettings set org.gnome.desktop.interface gtk-theme axon-gtk || true
gsettings set org.gnome.desktop.interface color-scheme prefer-dark || true

# 6. Start Ollama service (if installed and not already running)
if command -v ollama &>/dev/null; then
    if ! pgrep -x ollama &>/dev/null; then
        ollama serve &>/dev/null &
    fi
fi

# 7. Launch welcome app
if [[ -f "${APPS_DEST}/axon-welcome/main.py" ]]; then
    echo "[axon-firstboot] Launching Axon Onboarding wizard..."
    python3 "${APPS_DEST}/axon-welcome/main.py" &
fi

# Mark first-boot complete
touch "${DONE}"
echo "[axon-firstboot] Axon OS post-installation configuration finished."
exit 0
