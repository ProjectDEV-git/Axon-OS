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
prompt_yes_no() {
    local prompt="$1"
    local answer="${2:-Y}"
    read -rp "${prompt} " reply || reply="${answer}"
    reply="${reply:-${answer}}"
    [[ "${reply}" =~ ^[Yy]([Ee][Ss])?$ ]]
}

apt_install_best_effort() {
    local pkg
    for pkg in "$@"; do
        if ! sudo apt-get install -y "${pkg}"; then
            warn "Package ${pkg} is unavailable on this system — skipping"
        fi
    done
}

internet_available() {
    curl -fsI --max-time 8 https://ollama.com/install.sh >/dev/null 2>&1
}

# ---------------------------------------------------------------------------
# ASCII banner
# ---------------------------------------------------------------------------
printf "${CYAN}"
cat <<'BANNER'
╔═══════════════════════════════════════╗
║          ⬡  AXON  OS  v0.1            ║
║        AI-Native Linux Desktop        ║
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

# Ubuntu or Ubuntu-based distro (Zorin, Pop!_OS, Mint, etc.)
if [[ -r /etc/os-release ]]; then
    # shellcheck source=/dev/null
    source /etc/os-release
else
    error "Cannot read /etc/os-release — unable to detect the distribution."
    exit 1
fi

if [[ "${ID:-}" == "ubuntu" ]]; then
    success "Ubuntu detected"
elif [[ "${ID_LIKE:-}" == *"ubuntu"* ]]; then
    warn "Detected ${NAME:-unknown} (Ubuntu-based). Proceeding, but Ubuntu 24.04 is the officially supported base."
else
    error "Axon OS requires Ubuntu or an Ubuntu-based distribution."
    exit 1
fi

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
# Additional packages for voice, sandbox and search features
apt_install_best_effort \
    alsa-utils \
    pulseaudio-utils \
    sox \
    python3-pip \
    python3-venv \
    bubblewrap \
    ubuntu-drivers-common
info "Installing Python packages (faster-whisper, webrtcvad, sqlite-vec) into user environment"
pip3 install --user faster-whisper webrtcvad sqlite-vec || warn "pip install of AI extras failed — you can retry later"
success "Python/GTK dependencies installed"

# ---------------------------------------------------------------------------
# STEP 2: Install Ollama
# ---------------------------------------------------------------------------
step "Step 2/9 — Installing Ollama"
if command -v ollama &>/dev/null; then
    info "Ollama already installed at $(command -v ollama) — skipping"
else
    if internet_available; then
        if prompt_yes_no "Use the internet now to install Ollama and download local AI models? [Y/n]"; then
            info "Downloading and installing Ollama..."
            ollama_installer="$(mktemp /tmp/ollama-install.XXXXXX.sh)"
            trap 'rm -f "$ollama_installer"' EXIT
            if curl -fsSL --retry 3 --retry-delay 5 -o "$ollama_installer" https://ollama.com/install.sh; then
                # Validate: must be a shell script from Ollama, reasonable size (<100KB)
                INSTALLER_SIZE=$(wc -c < "$ollama_installer")
                if [[ "${INSTALLER_SIZE}" -gt 102400 ]]; then
                    error "Downloaded installer is suspiciously large (${INSTALLER_SIZE} bytes) — refusing to execute"
                    rm -f "$ollama_installer"
                elif head -c 100 "$ollama_installer" | grep -q '#!'; then
                    # Verify script starts with known Ollama installer header
                    if head -c 500 "$ollama_installer" | grep -q 'ollama.com\|Ollama'; then
                        sh "$ollama_installer"
                        success "Ollama installed"
                    else
                        error "Downloaded script does not appear to be the Ollama installer — refusing to execute"
                        rm -f "$ollama_installer"
                    fi
                else
                    error "Downloaded Ollama installer does not appear to be a valid script"
                fi
            else
                error "Failed to download Ollama installer — check your internet connection"
            fi
            rm -f "$ollama_installer"
        else
            warn "Skipping Ollama setup — you can install it later from the Welcome app or first boot"
        fi
    else
        warn "No internet connection detected — skipping Ollama install for now"
    fi
fi

if prompt_yes_no "Install recommended hardware drivers now (GPU/Wi-Fi/Bluetooth support)? [Y/n]"; then
    if command -v ubuntu-drivers &>/dev/null; then
        sudo ubuntu-drivers autoinstall || warn "Driver autoinstall reported a problem; the ISO still includes broad open-source driver coverage"
    else
        warn "ubuntu-drivers is unavailable — driver auto-install skipped"
    fi
fi

# ---------------------------------------------------------------------------
# STEP 3: Hardware-profiled model selection
# ---------------------------------------------------------------------------
step "Step 3/9 — Profiling hardware and recommending AI models"

if command -v ollama &>/dev/null; then
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
else
    warn "Ollama is not installed yet, so AI model downloads will be skipped for now"
    SPEED_MODEL="llama3.2:1b"
    GENERAL_MODEL="llama3.2:3b"
    DEEP_MODEL="llama3:8b"
    MODEL_CHOICE="s"
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

# Register D-Bus session services for all Axon services
DBUS_DIR="${HOME}/.local/share/dbus-1/services"
mkdir -p "${DBUS_DIR}"
for svc in "${SERVICES_DIR}"/*; do
    if [[ -d "${svc}" ]]; then
        for f in "${svc}"/*.service; do
            [ -e "${f}" ] || continue
            name=$(basename "${f}")
            sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" "${f}" > "${DBUS_DIR}/${name}"
        done
    fi
done
info "D-Bus session service configs installed."

# Register D-Bus session policies (may require sudo)
DBUS_POLICY_DIR="/usr/share/dbus-1/session.d"
if [[ -d "${DBUS_POLICY_DIR}" ]]; then
    for conf in "${SERVICES_DIR}"/*/*.conf; do
        [ -e "${conf}" ] || continue
        if [[ -w "${DBUS_POLICY_DIR}" ]]; then
            cp "${conf}" "${DBUS_POLICY_DIR}/"
        elif command -v sudo &>/dev/null; then
            sudo cp "${conf}" "${DBUS_POLICY_DIR}/"
        else
            warn "Could not install ${conf} to ${DBUS_POLICY_DIR}: permission denied"
        fi
    done
    info "D-Bus session policies installed (best-effort)."
fi

# Register Systemd user units for available services
SYSTEMD_DIR="${HOME}/.config/systemd/user"
mkdir -p "${SYSTEMD_DIR}"
for svc in "${SERVICES_DIR}"/*; do
    if [[ -d "${svc}" ]]; then
        for u in "${svc}"/*.service; do
            [ -e "${u}" ] || continue
            name=$(basename "${u}")
            sed "s|AXON_SERVICES_DIR|${SERVICES_DIR}|g" "${u}" > "${SYSTEMD_DIR}/${name}"
        done
    fi
done
info "Systemd user service units installed."

# Reload and enable user units (enable commonly useful ones)
systemctl --user daemon-reload
systemctl --user enable --now axon-brain.service axon-context.service 2>/dev/null || true
systemctl --user enable --now axon-voice.service axon-search.service axon-sandbox.service axon-gui-agent.service 2>/dev/null || true
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
