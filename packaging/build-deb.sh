#!/usr/bin/env bash
# Axon OS — build the axon-os .deb package.
#
# The package carries every Axon component that changes between releases:
# apps, services, the shell extension, theme, helper scripts and their
# systemd units. The ISO build installs it, and installed systems upgrade it
# from the Axon apt repository through the normal updater (axon-update).
#
# The live-session installer (apps/axon-installer, install-axon-os.desktop)
# is NOT packaged: the install engine deletes it from installed systems, and
# a package upgrade must not put it back.
#
# Usage: packaging/build-deb.sh [OUTPUT_DIR]   (default: dist/)
# Needs: dpkg-deb, glib-compile-schemas, gpg (only if a keyring is committed)
set -euo pipefail
umask 022

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-${SRC}/dist}"
VERSION="$(sed -n 's/^version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "${SRC}/pyproject.toml" | head -1)"
[[ -n "${VERSION}" ]] || { echo "build-deb: no version in pyproject.toml" >&2; exit 1; }

PKG_NAME="axon-os"
APT_URL="https://projectdev-git.github.io/Axon-OS/apt"
KEYRING_ASC="${SRC}/packaging/axon-os-archive-keyring.asc"

AXON_LIB="/usr/lib/axon"
APPS_DIR="${AXON_LIB}/apps"
SERVICES_DIR="${AXON_LIB}/services"

log() { echo "[build-deb] $*"; }

STAGE="$(mktemp -d)"
trap 'rm -rf "${STAGE}"' EXIT
R="${STAGE}/root"
mkdir -p "${R}" "${R}/DEBIAN"

# ── Apps, services, shell, desktop templates ─────────────────────────────────
mkdir -p "${R}${APPS_DIR}" "${R}${SERVICES_DIR}" "${R}${AXON_LIB}/shell" \
    "${R}${AXON_LIB}/data/applications"
cp -r "${SRC}/apps/." "${R}${APPS_DIR}/"
rm -rf "${R}${APPS_DIR}/axon-installer"
cp -r "${SRC}/services/." "${R}${SERVICES_DIR}/"
cp -r "${SRC}/shell/." "${R}${AXON_LIB}/shell/"
cp -r "${SRC}/data/applications/." "${R}${AXON_LIB}/data/applications/"
rm -f "${R}${AXON_LIB}/data/applications/install-axon-os.desktop"
find "${R}${AXON_LIB}" \( -name '__pycache__' -o -name '*.pyc' \) -prune -exec rm -rf {} +
chmod 755 "${R}${APPS_DIR}/axon-voice-overlay/main.py"

# Desktop entries (resolve AXON_APPS_DIR)
mkdir -p "${R}/usr/share/applications"
for f in "${SRC}/data/applications/"*.desktop; do
    [[ "$(basename "${f}")" == "install-axon-os.desktop" ]] && continue
    sed "s|AXON_APPS_DIR|${APPS_DIR}|g" "${f}" > "${R}/usr/share/applications/$(basename "${f}")"
done

# D-Bus session activation files (resolve AXON_SERVICES_DIR)
mkdir -p "${R}/usr/share/dbus-1/services"
for activation in "${SRC}/services"/*/org.axonos.*.service; do
    [[ -f "${activation}" ]] || continue
    sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" "${activation}" \
        > "${R}/usr/share/dbus-1/services/$(basename "${activation}")"
done

# systemd user units, enabled globally by postinst
mkdir -p "${R}/usr/lib/systemd/user"
USER_UNITS=()
for unit in "${SRC}/services"/*/axon-*.service; do
    [[ -f "${unit}" ]] || continue
    sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" "${unit}" \
        > "${R}/usr/lib/systemd/user/$(basename "${unit}")"
    USER_UNITS+=("$(basename "${unit}")")
done
[[ ${#USER_UNITS[@]} -gt 0 ]] || { echo "build-deb: no systemd user units found" >&2; exit 1; }

# GNOME Shell extension, system-wide
EXT_DIR="${R}/usr/share/gnome-shell/extensions/axon-shell@axon-os"
mkdir -p "${EXT_DIR}"
cp -r "${SRC}/shell/axon-shell/." "${EXT_DIR}/"
glib-compile-schemas "${EXT_DIR}/schemas/"

# GTK theme and wallpaper
install -Dm644 "${SRC}/theme/axon-gtk/gtk-dark.css" "${R}/usr/share/themes/axon-gtk/gtk-4.0/gtk.css"
install -Dm644 "${SRC}/theme/axon-gtk/index.theme" "${R}/usr/share/themes/axon-gtk/index.theme"
if [[ -f "${SRC}/theme/wallpapers/axon-aurora.png" ]]; then
    install -Dm644 "${SRC}/theme/wallpapers/axon-aurora.png" \
        "${R}/usr/share/backgrounds/axon/axon-aurora.png"
fi

# ── Helper scripts ───────────────────────────────────────────────────────────
B="${R}/usr/local/bin"
install -Dm755 "${SRC}/build/config/firstboot.sh" "${B}/axon-firstboot"
install -Dm755 "${SRC}/build/config/ollama-setup.sh" "${B}/axon-ollama-setup"
install -Dm755 "${SRC}/system/axon-updater.py" "${B}/axon-update"
install -Dm755 "${SRC}/services/axon-sandbox/axon-run" "${B}/axon-run"
install -Dm755 "${SRC}/system/boot_watchdog.py" "${B}/axon-boot-watchdog"
install -Dm755 "${SRC}/build/config/axon-voice-toggle" "${B}/axon-voice-toggle"
install -Dm755 "${SRC}/build/config/axon-boot-ok.sh" "${B}/axon-boot-ok"
install -Dm755 "${SRC}/build/config/ai-firstboot.sh" "${B}/axon-ai-firstboot"
cat > "${B}/axon-shield" <<EOF
#!/bin/sh
exec /usr/bin/python3 ${SERVICES_DIR}/axon-sandbox/shield.py "\$@"
EOF
chmod 755 "${B}/axon-shield"

# Lets the Updater window run the update as root after one password prompt
install -Dm644 "${SRC}/data/polkit/org.axonos.update.policy" \
    "${R}/usr/share/polkit-1/actions/org.axonos.update.policy"

# Hash-pinned Ollama installer used by first boot and axon-ollama-setup
install -Dm755 "${SRC}/build/config/install-ollama.sh" "${R}${AXON_LIB}/ollama/install-ollama.sh"
install -Dm644 "${SRC}/build/config/ollama-release.env" "${R}${AXON_LIB}/ollama/ollama-release.env"

# Shell environment interceptor and the shared Python logger
install -Dm644 "${SRC}/services/axon-sandbox/axon-sandbox-env.sh" "${R}/etc/profile.d/axon-sandbox.sh"
install -Dm644 "${SRC}/axon_logger.py" "${R}/usr/lib/python3/dist-packages/axon_logger.py"

# Self-healing boot watchdog: GRUB counter + rollback entry
install -Dm755 "${SRC}/build/config/grub.d-06_axon_watchdog" "${R}/etc/grub.d/06_axon_watchdog"
install -Dm755 "${SRC}/build/config/grub.d-42_axon_rollback" "${R}/etc/grub.d/42_axon_rollback"

# ── System units ─────────────────────────────────────────────────────────────
U="${R}/usr/lib/systemd/system"
install -Dm644 "${SRC}/system/axon-boot-watchdog.service" "${U}/axon-boot-watchdog.service"
install -Dm644 "${SRC}/build/config/axon-boot-ok.service" "${U}/axon-boot-ok.service"

cat > "${U}/axon-update-auto.service" <<'EOF'
[Unit]
Description=Axon OS automatic update check and apply
Wants=network-online.target
After=network-online.target NetworkManager-wait-online.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/axon-update --auto
EOF

cat > "${U}/axon-update-auto.timer" <<'EOF'
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

# Stays disabled; the install engine enables it when the user opts into Ollama.
cat > "${U}/axon-ai-firstboot.service" <<'EOF'
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

cat > "${R}/usr/share/applications/axon-update.desktop" <<'EOF'
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

# ── Axon apt repository (where this package's own updates come from) ─────────
if [[ -f "${KEYRING_ASC}" ]]; then
    install -d "${R}/usr/share/keyrings"
    gpg --batch --yes --dearmor -o "${R}/usr/share/keyrings/axon-os-archive-keyring.gpg" "${KEYRING_ASC}"
    chmod 644 "${R}/usr/share/keyrings/axon-os-archive-keyring.gpg"
    install -d "${R}/etc/apt/sources.list.d"
    cat > "${R}/etc/apt/sources.list.d/axon-os.sources" <<EOF
Types: deb
URIs: ${APT_URL}
Suites: stable
Components: main
Signed-By: /usr/share/keyrings/axon-os-archive-keyring.gpg
EOF
else
    log "WARNING: ${KEYRING_ASC#"${SRC}/"} missing; package will not add the Axon apt repository"
fi

# ── Maintainer scripts ───────────────────────────────────────────────────────
SYSTEM_UNITS="axon-boot-watchdog.service axon-boot-ok.service axon-update-auto.timer"

cat > "${R}/DEBIAN/control" <<EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Architecture: all
Maintainer: Axon OS <axon-os@users.noreply.github.com>
Depends: python3 (>= 3.10), python3-gi, init-system-helpers (>= 1.60)
Section: misc
Priority: optional
Homepage: https://github.com/ProjectDEV-git/Axon-OS
Description: Axon OS desktop components
 The Axon AI services, apps, GNOME Shell extension, theme and system
 helpers. Upgrading this package updates Axon OS itself.
EOF

cat > "${R}/DEBIAN/postinst" <<EOF
#!/bin/sh
set -e

if [ "\$1" = "configure" ]; then
    # Systems installed before Axon was packaged have an unpackaged copy of
    # this unit in /etc, which would shadow the packaged one.
    if [ -f /etc/systemd/system/axon-boot-ok.service ] && [ ! -L /etc/systemd/system/axon-boot-ok.service ]; then
        rm -f /etc/systemd/system/axon-boot-ok.service
    fi
    # Old session-bus policy files blocked Brain's own methods; drop leftovers.
    rm -f /usr/share/dbus-1/session.d/org.axonos.*.conf

    # was-enabled is true for units the helper has never seen, so a fresh
    # install enables them; a unit the admin disabled stays disabled.
    for unit in ${SYSTEM_UNITS}; do
        deb-systemd-helper unmask "\${unit}" >/dev/null || true
        if deb-systemd-helper --quiet was-enabled "\${unit}"; then
            deb-systemd-helper enable "\${unit}" >/dev/null || true
        else
            deb-systemd-helper update-state "\${unit}" >/dev/null || true
        fi
    done
    for unit in ${USER_UNITS[*]}; do
        deb-systemd-helper --user unmask "\${unit}" >/dev/null || true
        if deb-systemd-helper --user --quiet was-enabled "\${unit}"; then
            deb-systemd-helper --user enable "\${unit}" >/dev/null || true
        else
            deb-systemd-helper --user update-state "\${unit}" >/dev/null || true
        fi
    done

    if [ -d /run/systemd/system ]; then
        systemctl daemon-reload || true
        # Upgrades only: the boot entries come from /etc/grub.d.
        if [ -n "\$2" ] && [ -e /boot/grub/grub.cfg ] && command -v update-grub >/dev/null; then
            update-grub || true
        fi
        # Upgrades only: restart running Axon services so they pick up new code.
        if [ -n "\$2" ]; then
            for dir in /run/user/*; do
                [ -d "\${dir}" ] || continue
                name="\$(id -nu "\${dir##*/}" 2>/dev/null)" || continue
                systemctl --user -M "\${name}@" daemon-reload >/dev/null 2>&1 || true
                systemctl --user -M "\${name}@" try-restart ${USER_UNITS[*]} >/dev/null 2>&1 || true
            done
        fi
    fi
fi
exit 0
EOF

cat > "${R}/DEBIAN/postrm" <<EOF
#!/bin/sh
set -e

if [ "\$1" = "remove" ] && [ -d /run/systemd/system ]; then
    systemctl daemon-reload || true
fi
if [ "\$1" = "purge" ]; then
    for unit in ${SYSTEM_UNITS}; do
        deb-systemd-helper purge "\${unit}" >/dev/null || true
    done
    for unit in ${USER_UNITS[*]}; do
        deb-systemd-helper --user purge "\${unit}" >/dev/null || true
    done
fi
exit 0
EOF
chmod 755 "${R}/DEBIAN/postinst" "${R}/DEBIAN/postrm"

# Every file under /etc is a conffile, so local edits survive upgrades.
(cd "${R}" && find etc -type f -printf '/%p\n' | sort) > "${R}/DEBIAN/conffiles"
[[ -s "${R}/DEBIAN/conffiles" ]] || rm -f "${R}/DEBIAN/conffiles"

mkdir -p "${OUT_DIR}"
DEB="${OUT_DIR}/${PKG_NAME}_${VERSION}_all.deb"
dpkg-deb --root-owner-group -Zxz --build "${R}" "${DEB}" >/dev/null
log "Built ${DEB}"
