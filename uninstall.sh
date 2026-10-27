#!/usr/bin/env bash
# Axon OS — undo what ./install.sh did to a machine that is not Axon OS (Zorin, Ubuntu, ...).
#
# Usage: ./uninstall.sh [--dry-run] [--yes] [--also-ollama] [--also-pip] [--also-apt]
#
#   --dry-run      show what would be done, change nothing
#   --yes          do every step without asking
#   --also-ollama  also stop and remove Ollama and ALL downloaded models
#   --also-pip     also remove the Python extras install.sh added with pip (--user)
#   --also-apt     also remove the apt packages install.sh added that you may not want (sox, python3-httpx)
#
# What it undoes: the Axon user services and D-Bus activation files, the first-login autostart (which
# re-applies the Axon theme and shortcuts, so it goes first), the GNOME Shell extension, the GTK theme
# and its settings, the apps and launchers, the .exe handler (APEX-WIN, which needs sudo) and the GNOME
# settings install.sh changed (reset to your distribution's defaults, not to what you had before: it
# cannot know that). Your files, ~/.config/axon and Ollama's models are kept unless asked.
# Run it as your normal user, not with sudo; it asks for sudo only for the system files.
set -uo pipefail

DRY=0 ASSUME_YES=0 ALSO_OLLAMA=0 ALSO_PIP=0 ALSO_APT=0
for a in "$@"; do
    case "$a" in
        --dry-run) DRY=1 ;;
        --yes) ASSUME_YES=1 ;;
        --also-ollama) ALSO_OLLAMA=1 ;;
        --also-pip) ALSO_PIP=1 ;;
        --also-apt) ALSO_APT=1 ;;
        -h | --help) sed -n '2,17p' "$0"; exit 0 ;;
        *) echo "unknown option: $a" >&2; exit 2 ;;
    esac
done
if [[ ${EUID} -eq 0 ]]; then
    echo "Run this as your normal user, not root: it undoes things in your home folder." >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
say() { printf '  %s\n' "$*"; }
run() { # run a command, or only show it with --dry-run
    if ((DRY)); then printf '  [dry-run] %s\n' "$*"; else "$@"; fi
}
ask() { # ask "question" -> yes/no
    ((ASSUME_YES)) && return 0
    local r
    read -rp "  $1 [Y/n] " r
    [[ -z "$r" || "$r" =~ ^[Yy] ]]
}
step() { printf '\n== %s\n' "$*"; }

# -- 1. Autostart first: it re-applies the theme and shortcuts at every login ----------------------
step "First-login autostart"
if [[ -f "${HOME}/.config/autostart/axon-firstboot.desktop" ]]; then
    run rm -f "${HOME}/.config/autostart/axon-firstboot.desktop"
    say "removed"
else say "not there"; fi

# -- 2. Services -----------------------------------------------------------------------------------
step "Axon user services"
units=()
for f in "${SCRIPT_DIR}"/services/*/axon-*.service; do [[ -e "$f" ]] && units+=("$(basename "$f")"); done
if ((${#units[@]})); then
    run systemctl --user disable --now "${units[@]}" 2>/dev/null
    for u in "${units[@]}"; do run rm -f "${HOME}/.config/systemd/user/${u}"; done
    run systemctl --user daemon-reload
fi
for f in "${SCRIPT_DIR}"/services/*/org.axonos.*.service; do
    [[ -e "$f" ]] && run rm -f "${HOME}/.local/share/dbus-1/services/$(basename "$f")"
done
say "done"

# -- 3. Shell extension ----------------------------------------------------------------------------
step "GNOME Shell extension"
run gnome-extensions disable axon-shell@axon-os 2>/dev/null
run rm -rf "${HOME}/.local/share/gnome-shell/extensions/axon-shell@axon-os"
say "removed (log out and in for the shell to drop it)"

# -- 4. Theme --------------------------------------------------------------------------------------
step "GTK theme"
run rm -rf "${HOME}/.themes/axon-gtk"
css="${HOME}/.config/gtk-4.0/gtk.css"
if [[ -f "$css" ]] && grep -q "generated from theme/tokens/axon-tokens.json" "$css"; then
    run rm -f "$css"
    [[ -f "${css}.before-axon" ]] && run mv "${css}.before-axon" "$css" && say "restored your own gtk.css"
fi
say "done"

# -- 5. GNOME settings -----------------------------------------------------------------------------
step "GNOME settings install.sh changed"
say "gtk-theme, color-scheme, num-workspaces, dynamic-workspaces, workspace-names, enable-animations,"
say "Super+D (show desktop) and Super+E (home) go back to your distribution's defaults."
if ask "Reset them?"; then
    for kv in \
        "org.gnome.desktop.interface gtk-theme" \
        "org.gnome.desktop.interface color-scheme" \
        "org.gnome.desktop.interface enable-animations" \
        "org.gnome.desktop.wm.preferences num-workspaces" \
        "org.gnome.desktop.wm.preferences workspace-names" \
        "org.gnome.mutter dynamic-workspaces" \
        "org.gnome.desktop.wm.keybindings show-desktop" \
        "org.gnome.settings-daemon.plugins.media-keys home"; do
        # shellcheck disable=SC2086
        run gsettings reset $kv
    done
fi

# -- 6. Apps, launchers ----------------------------------------------------------------------------
step "Apps and launchers"
run rm -rf "${HOME}/.local/share/axon-os"
for f in "${SCRIPT_DIR}"/data/applications/*.desktop; do
    [[ -e "$f" ]] && run rm -f "${HOME}/.local/share/applications/$(basename "$f")"
done
if [[ -f "${HOME}/.config/mimeapps.list" ]] && grep -q "axon-apex-win.desktop" "${HOME}/.config/mimeapps.list"; then
    run sed -i '/axon-apex-win.desktop/d' "${HOME}/.config/mimeapps.list"
fi
run update-desktop-database "${HOME}/.local/share/applications" 2>/dev/null
say "done"

# -- 7. APEX-WIN (system files) ----------------------------------------------------------------------
step "Windows .exe handler (APEX-WIN), needs sudo"
if [[ -e /usr/bin/axon-apex-run || -e /usr/lib/binfmt.d/axon-apex-win.conf || -e /proc/sys/fs/binfmt_misc/APEX-WIN ]]; then
    if ask "Remove it? (.exe files go back to your normal Wine handling)"; then
        [[ -e /proc/sys/fs/binfmt_misc/APEX-WIN ]] && run sudo sh -c 'echo -1 > /proc/sys/fs/binfmt_misc/APEX-WIN'
        run sudo rm -f /usr/lib/binfmt.d/axon-apex-win.conf /usr/bin/axon-apex-run /usr/bin/win-sandbox-runner
    fi
else say "not there"; fi

# -- 8. Optional: pip extras, Ollama, apt ------------------------------------------------------------
if ((ALSO_PIP)); then
    step "Python extras (pip --user)"
    run pip3 uninstall -y --break-system-packages faster-whisper webrtcvad sqlite-vec
fi
if ((ALSO_OLLAMA)); then
    step "Ollama and models"
    say "This deletes every downloaded model."
    if ask "Really?"; then
        run sudo systemctl disable --now ollama.service
        run sudo rm -f /etc/systemd/system/ollama.service /usr/local/bin/ollama
        run sudo rm -rf /usr/share/ollama "${HOME}/.ollama"
    fi
fi
if ((ALSO_APT)); then
    step "apt packages"
    run sudo apt-get remove -y sox python3-httpx
fi

step "Finished"
say "Log out and back in so GNOME drops the extension and reads its settings again."
say "Kept: ~/.config/axon, Ollama and its models (--also-ollama), pip extras (--also-pip), apt packages (--also-apt)."
