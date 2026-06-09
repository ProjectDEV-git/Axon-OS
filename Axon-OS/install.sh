#!/usr/bin/env bash
# Axon OS — Installer
# AI-Native Linux Desktop
set -euo pipefail

# ---------------------------------------------------------------------------
# ANSI color helpers (degrade gracefully when tput is unavailable)
# ---------------------------------------------------------------------------
if command -v tput &>/dev/null && tput setaf 1 &>/dev/null 2>&1; then
    RED="$(tput setaf 1)"
    GREEN="$(tput setaf 2)"
    YELLOW="$(tput setaf 3)"
    CYAN="$(tput setaf 6)"
    BOLD="$(tput bold)"
    RESET="$(tput sgr0)"
else
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    RESET='\033[0m'
fi

info()    { printf "${CYAN}  ℹ  %s${RESET}\n" "$*"; }
success() { printf "${GREEN}  ✔  %s${RESET}\n" "$*"; }
warn()    { printf "${YELLOW}  ⚠  %s${RESET}\n" "$*"; }
error()   { printf "${RED}  ✖  %s${RESET}\n" "$*" >&2; }
step()    { printf "\n${BOLD}${CYAN}══▶  %s${RESET}\n" "$*"; }

# ---------------------------------------------------------------------------
# ASCII banner
# ---------------------------------------------------------------------------
printf "${CYAN}"
cat <<'BANNER'
╔═══════════════════════════════════════╗
║          ⬡  AXON  OS  v0.1           ║
║   AI-Native Linux Desktop             ║
╚═══════════════════════════════════════╝
BANNER
printf "${RESET}\n"

# ---------------------------------------------------------------------------
# PREFLIGHT CHECKS
# ---------------------------------------------------------------------------
step "Running preflight checks"

# Must not be root
if [[ "${EUID}" -eq 0 ]]; then
    error "Do not run this installer as root. Run as your regular user."
    exit 1
fi
success "Running as non-root user: ${USER}"

# Python 3.11+
if ! python3 -c "import sys; assert sys.version_info >= (3,11)" 2>/dev/null; then
    error "Python 3.11 or newer is required. Install it and retry."
    exit 1
fi
PYVER="$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
success "Python ${PYVER} found"

# Ubuntu
if ! grep -q 'ID=ubuntu' /etc/os-release 2>/dev/null; then
    error "Axon OS requires Ubuntu. This system does not appear to be Ubuntu."
    exit 1
fi
success "Ubuntu detected"

# GNOME available
if [[ -z "${GNOME_SHELL_SESSION_MODE:-}" ]] && ! command -v gnome-shell &>/dev/null; then
    error "GNOME Shell is not available. Axon OS requires a GNOME session."
    exit 1
fi
success "GNOME Shell available"

# Resolve installer directory (follow symlinks)
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

# ---------------------------------------------------------------------------
# STEP 1: Install Python / GTK dependencies
# ---------------------------------------------------------------------------
step "Step 1/9 — Installing Python/GTK dependencies"
sudo apt-get install -y \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-4.0 \
    gir1.2-adw-1 \
    python3-httpx
success "Python/GTK dependencies installed"

# ---------------------------------------------------------------------------
# STEP 2: Install Ollama
# ---------------------------------------------------------------------------
step "Step 2/9 — Installing Ollama"
if command -v ollama &>/dev/null; then
    info "Ollama already installed at $(command -v ollama) — skipping"
else
    info "Downloading and installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
    success "Ollama installed"
fi

# ---------------------------------------------------------------------------
# STEP 3: Hardware-profiled model selection
# ---------------------------------------------------------------------------
step "Step 3/9 — Profiling hardware and recommending AI models"

PROFILER="${SCRIPT_DIR}/services/axon-brain/hardware_profiler.py"
HW_JSON="$(python3 "${PROFILER}" 2>/dev/null)" || HW_JSON=""

if [[ -n "${HW_JSON}" ]]; then
    SYS_RAM="$(echo "${HW_JSON}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"{d['system']['ram_gb']:.1f}\")" 2>/dev/null || echo "?")"
    GPU_TYPE="$(echo "${HW_JSON}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['system']['gpu_type'])" 2>/dev/null || echo "Unknown")"
    SPEED_MODEL="$(echo "${HW_JSON}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['recommendations']['speed']['model'])" 2>/dev/null || echo "llama3.2:1b")"
    GENERAL_MODEL="$(echo "${HW_JSON}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['recommendations']['general']['model'])" 2>/dev/null || echo "llama3.2:3b")"
    DEEP_MODEL="$(echo "${HW_JSON}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['recommendations']['deep']['model'])" 2>/dev/null || echo "llama3:8b")"

    printf "\n"
    info "Detected: ${BOLD}${SYS_RAM} GB RAM${RESET}, GPU: ${BOLD}${GPU_TYPE}${RESET}"
    printf "\n"
    printf "  ${BOLD}Recommended models for your hardware:${RESET}\n"
    printf "  ${CYAN}⚡ Speed${RESET}  (instant responses):  ${GREEN}${SPEED_MODEL}${RESET}\n"
    printf "  ${CYAN}🔧 General${RESET} (everyday queries):   ${GREEN}${GENERAL_MODEL}${RESET}\n"
    printf "  ${CYAN}🧠 Deep${RESET}   (complex reasoning):  ${GREEN}${DEEP_MODEL}${RESET}\n"
    printf "\n"
    printf "  ${BOLD}d)${RESET} Download all recommended models ${GREEN}(Recommended)${RESET}\n"
    printf "  ${BOLD}g)${RESET} Download General model only (fastest install)\n"
    printf "  ${BOLD}s)${RESET} Skip — do not download models now\n"
    printf "\n"
    read -rp "  Choose [d/g/s]: " MODEL_CHOICE
else
    warn "Hardware profiler unavailable — falling back to defaults"
    SPEED_MODEL="llama3.2:1b"
    GENERAL_MODEL="llama3.2:3b"
    DEEP_MODEL="llama3:8b"
    MODEL_CHOICE="g"
fi

case "${MODEL_CHOICE}" in
    d|D)
        for m in "${SPEED_MODEL}" "${GENERAL_MODEL}" "${DEEP_MODEL}"; do
            info "Pulling model: ${m}"
            ollama pull "${m}" || warn "Failed to pull ${m} — you can retry later with: ollama pull ${m}"
            success "Model ${m} ready"
        done
        ;;
    g|G)
        info "Pulling model: ${GENERAL_MODEL}"
        ollama pull "${GENERAL_MODEL}" || warn "Failed to pull ${GENERAL_MODEL}"
        success "Model ${GENERAL_MODEL} ready"
        ;;
    s|S)
        info "Skipping model download — you can pull models later via the Welcome app or: ollama pull <model>"
        ;;
    *)
        warn "Unrecognised choice '${MODEL_CHOICE}', downloading General model"
        info "Pulling model: ${GENERAL_MODEL}"
        ollama pull "${GENERAL_MODEL}" || warn "Failed to pull ${GENERAL_MODEL}"
        success "Model ${GENERAL_MODEL} ready"
        ;;
esac

# ---------------------------------------------------------------------------
# STEP 4: Install GNOME extension
# ---------------------------------------------------------------------------
step "Step 4/9 — Installing GNOME Shell extension"
DEST="${HOME}/.local/share/gnome-shell/extensions/axon-shell@axon-os"
mkdir -p "${DEST}"
cp -r "${SCRIPT_DIR}/shell/axon-shell/." "${DEST}/"

if command -v glib-compile-schemas &>/dev/null; then
    glib-compile-schemas "${DEST}/schemas/" 2>/dev/null || true
fi

gnome-extensions enable axon-shell@axon-os 2>/dev/null \
    || warn "Restart GNOME Shell to enable the extension (Alt+F2 → r, or log out and back in)"
success "GNOME extension installed to ${DEST}"

# ---------------------------------------------------------------------------
# STEP 5: Install GTK theme
# ---------------------------------------------------------------------------
step "Step 5/9 — Installing Axon GTK theme"
mkdir -p "${HOME}/.themes/axon-gtk/gtk-4.0"
cp "${SCRIPT_DIR}/theme/axon-gtk/gtk-dark.css" \
   "${HOME}/.themes/axon-gtk/gtk-4.0/gtk.css"
cp "${SCRIPT_DIR}/theme/axon-gtk/index.theme" \
   "${HOME}/.themes/axon-gtk/"

gsettings set org.gnome.desktop.interface gtk-theme axon-gtk
gsettings set org.gnome.desktop.interface color-scheme prefer-dark
success "Axon GTK theme installed and applied"

# ---------------------------------------------------------------------------
# STEP 6: Install apps and .desktop files
# ---------------------------------------------------------------------------
step "Step 6/9 — Installing Axon OS applications"
APPS_DIR="${HOME}/.local/share/axon-os"
mkdir -p "${APPS_DIR}"
cp -r "${SCRIPT_DIR}/apps/"* "${APPS_DIR}/"

mkdir -p "${HOME}/.local/share/applications"
for f in "${SCRIPT_DIR}/data/applications/"*.desktop; do
    DEST_FILE="${HOME}/.local/share/applications/$(basename "${f}")"
    sed "s|AXON_APPS_DIR|${APPS_DIR}|g" "${f}" > "${DEST_FILE}"
    info "Installed $(basename "${f}")"
done
success "Applications installed to ${APPS_DIR}"

# ---------------------------------------------------------------------------
# STEP 7: Install D-Bus services and configure Systemd
# ---------------------------------------------------------------------------
step "Step 7/9 — Installing Axon OS D-Bus services"
SERVICES_DIR="${HOME}/.local/share/axon-os/services"
mkdir -p "${SERVICES_DIR}"
cp -r "${SCRIPT_DIR}/services/"* "${SERVICES_DIR}/"

# Register D-Bus session services
DBUS_DIR="${HOME}/.local/share/dbus-1/services"
mkdir -p "${DBUS_DIR}"
sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" "${SERVICES_DIR}/axon-brain/org.axonos.Brain.service" > "${DBUS_DIR}/org.axonos.Brain.service"
sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" "${SERVICES_DIR}/axon-context/org.axonos.Context.service" > "${DBUS_DIR}/org.axonos.Context.service"
info "D-Bus session service configs installed."

# Register D-Bus session policies
DBUS_POLICY_DIR="/usr/share/dbus-1/session.d"
if [[ -d "${DBUS_POLICY_DIR}" ]]; then
    if [[ -w "${DBUS_POLICY_DIR}" ]]; then
        cp "${SERVICES_DIR}/axon-brain/org.axonos.Brain.conf" "${DBUS_POLICY_DIR}/"
        cp "${SERVICES_DIR}/axon-context/org.axonos.Context.conf" "${DBUS_POLICY_DIR}/"
        info "D-Bus session policies installed directly."
    elif command -v sudo &>/dev/null; then
        sudo cp "${SERVICES_DIR}/axon-brain/org.axonos.Brain.conf" "${DBUS_POLICY_DIR}/"
        sudo cp "${SERVICES_DIR}/axon-context/org.axonos.Context.conf" "${DBUS_POLICY_DIR}/"
        info "D-Bus session policies installed via sudo."
    else
        warn "Could not install D-Bus policies: write permission denied and sudo unavailable."
    fi
fi

# Register Systemd user units
SYSTEMD_DIR="${HOME}/.config/systemd/user"
mkdir -p "${SYSTEMD_DIR}"
sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" "${SERVICES_DIR}/axon-brain/axon-brain.service" > "${SYSTEMD_DIR}/axon-brain.service"
sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" "${SERVICES_DIR}/axon-context/axon-context.service" > "${SYSTEMD_DIR}/axon-context.service"
info "Systemd user service units installed."

# Reload and enable user units
systemctl --user daemon-reload
systemctl --user enable axon-brain.service axon-context.service
systemctl --user restart axon-brain.service axon-context.service 2>/dev/null || true
success "Services installed and registered."

# ---------------------------------------------------------------------------
# STEP 8: Configure GNOME workspaces
# ---------------------------------------------------------------------------
step "Step 8/9 — Configuring GNOME workspaces"
gsettings set org.gnome.desktop.wm.preferences num-workspaces 9
gsettings set org.gnome.mutter dynamic-workspaces false
gsettings set org.gnome.desktop.interface enable-animations true
success "GNOME workspace configuration applied"

# ---------------------------------------------------------------------------
# STEP 9: Set up autostart for firstboot.sh
# ---------------------------------------------------------------------------
step "Step 9/9 — Configuring first-boot autostart"
mkdir -p "${HOME}/.config/autostart"

FIRSTBOOT_SCRIPT="${SCRIPT_DIR}/build/config/firstboot.sh"
chmod +x "${FIRSTBOOT_SCRIPT}"

cat > "${HOME}/.config/autostart/axon-firstboot.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Axon OS First Boot Setup
Comment=Runs once on first login to complete Axon OS setup
Exec=bash ${FIRSTBOOT_SCRIPT}
Icon=axon-os
Terminal=false
StartupNotify=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Phase=Applications
EOF

success "First-boot autostart configured"

# ---------------------------------------------------------------------------
# Completion banner
# ---------------------------------------------------------------------------
printf "\n"
printf "${GREEN}${BOLD}"
cat <<'SUCCESS'
╔══════════════════════════════════════════════════════╗
║        Axon OS installed successfully!               ║
║                                                      ║
║  Log out and back in to activate all components.     ║
║                                                      ║
║  Super+Space  =  Intent Bar                          ║
║  Super+A      =  AI Panel                            ║
╚══════════════════════════════════════════════════════╝
SUCCESS
printf "${RESET}\n"
