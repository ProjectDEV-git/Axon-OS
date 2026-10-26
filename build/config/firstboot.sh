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
    
    # Register every service shipping a D-Bus activation file
    for activation in "${SERVICES_DEST}"/*/org.axonos.*.service; do
        [[ -f "${activation}" ]] || continue
        sed "s|AXON_SERVICES_DIR|${SERVICES_DEST}|g" "${activation}" > "${DBUS_DIR}/$(basename "${activation}")"
    done

    # Systemd user services
    echo "[axon-firstboot] Registering Systemd user services..."
    SYSTEMD_DIR="${HOME}/.config/systemd/user"
    mkdir -p "${SYSTEMD_DIR}"

    AXON_UNITS=()
    for unit in "${SERVICES_DEST}"/*/axon-*.service; do
        [[ -f "${unit}" ]] || continue
        sed "s|AXON_SERVICES_DIR|${SERVICES_DEST}|g" "${unit}" > "${SYSTEMD_DIR}/$(basename "${unit}")"
        AXON_UNITS+=("$(basename "${unit}")")
    done

    # Reload and enable user units
    systemctl --user daemon-reload || true
    if [[ ${#AXON_UNITS[@]} -gt 0 ]]; then
        systemctl --user enable "${AXON_UNITS[@]}" || true
        systemctl --user restart "${AXON_UNITS[@]}" 2>/dev/null || true
    fi
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
        # The installer only belongs in the live session
        [[ "$(basename "${f}")" == "install-axon-os.desktop" ]] && continue
        DEST_FILE="${HOME}/.local/share/applications/$(basename "${f}")"
        sed "s|AXON_APPS_DIR|${APPS_DEST}|g" "${f}" > "${DEST_FILE}"
        chmod +x "${DEST_FILE}" || true
    done
fi

# 5. Configure workspace names
# GVariant string arrays need quoted elements; and none of these are worth
# aborting first-boot over (set -e) — a failure here used to kill the script
# before the .firstboot-done marker, so every login re-ran firstboot and
# restarted all AI services mid-session.
echo "[axon-firstboot] Applying workspace configurations..."
gsettings set org.gnome.desktop.wm.preferences workspace-names \
    "['Code','Web','Chat','Files','Media','Work','Personal','Terminal','Notes']" || true
gsettings set org.gnome.desktop.wm.preferences num-workspaces 9 || true
gsettings set org.gnome.mutter dynamic-workspaces false || true
gsettings set org.gnome.desktop.interface enable-animations true || true

# Apply theme preferences
gsettings set org.gnome.desktop.interface gtk-theme axon-gtk || true
gsettings set org.gnome.desktop.interface color-scheme prefer-dark || true

# Windows muscle-memory keybindings: Super+D shows desktop, Super+E opens
# the file manager (GNOME's "home" media key launches the default one).
gsettings set org.gnome.desktop.wm.keybindings show-desktop "['<Super>d']" || true
gsettings set org.gnome.settings-daemon.plugins.media-keys home "['<Super>e']" || true

# 6. Make sure Ollama is running.
# install-ollama.sh ships a system ollama.service (user `ollama`, models in
# /usr/share/ollama/.ollama). Never start a second `ollama serve` as the
# desktop user next to it: that one grabs port 11434 first and serves an
# empty ~/.ollama, so every model the system service pulled "disappears" and
# the system unit crash-loops on "address already in use".
if command -v ollama &>/dev/null; then
    if systemctl cat ollama.service &>/dev/null; then
        if ! systemctl is-active --quiet ollama.service; then
            # Polkit may refuse this without a password; ollama.service is
            # enabled at boot anyway, so a failure here is not fatal.
            systemctl start ollama.service &>/dev/null || true
        fi
    elif ! pgrep -x ollama &>/dev/null; then
        # No system unit (e.g. a manual install): run it for this session.
        ollama serve &>/dev/null &
    fi
fi

# 7. Launch welcome app (skipped in the live session, where the Axon
#    Installer wizard autostarts instead via /etc/xdg/autostart)
if ! grep -q boot=casper /proc/cmdline && [[ -f "${APPS_DEST}/axon-welcome/main.py" ]]; then
    echo "[axon-firstboot] Launching Axon Onboarding wizard..."
    python3 "${APPS_DEST}/axon-welcome/main.py" &
fi

# Mark first-boot complete
touch "${DONE}"

# Clean up installer shortcut from Desktop if it still exists
if [[ -f "${HOME}/Desktop/install-axon-os.desktop" ]]; then
    rm -f "${HOME}/Desktop/install-axon-os.desktop"
fi

echo "[axon-firstboot] Axon OS post-installation configuration finished."
exit 0
