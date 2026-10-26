#!/bin/bash
# install-macos.sh - APEX-WIN installer for macOS.
#
# Mirrors scripts/install.sh's role on Linux, but the mechanisms are
# different: no systemd (launchd instead), no binfmt_misc (Launch
# Services + a .app bundle instead), and Homebrew's install prefix differs
# by CPU architecture (Apple Silicon vs Intel), which scripts/install.sh
# never has to think about since Linux only has one convention (/usr,
# /usr/local).
#
# This installs the CLI-only sandbox core (win-sandbox-runner). It does NOT
# attempt to build or install win-sandbox-gui: that crate hard-links
# GTK4/libadwaita, which have no macOS build here (see .github/workflows/
# ci.yml's check-macos job comment, which hit the exact same wall trying to
# cross-compile it).
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

check_platform() {
    if [[ "$(uname -s)" != "Darwin" ]]; then
        error "This script is for macOS only. Use scripts/install.sh on Linux."
    fi
}

# Unlike scripts/install.sh, this script must NOT be run as root outright:
# it writes per-user files (~/.config/win-sandbox, ~/Library/LaunchAgents)
# whose ownership would end up as root and then be unusable by the user's
# own LaunchAgent. But two of its targets (the bin directory and
# /Applications) may need elevation depending on the machine. So instead of
# a blanket root check, elevate per-target only where the filesystem says
# it is actually needed.
check_not_root() {
    if [[ $EUID -eq 0 ]]; then
        error "Do not run this with sudo. It installs per-user files into \$HOME
(~/.config/win-sandbox, ~/Library/LaunchAgents) which must belong to you, not root.
It will prompt for sudo only for the specific steps that need it."
    fi
}

# Echo "sudo" when the given directory requires elevation to write, else
# echo nothing. Checked against the real filesystem rather than assumed:
# /opt/homebrew/bin is typically owned by the installing user (no sudo
# needed), while /usr/local/bin and /Applications usually are not.
sudo_for() {
    local target="$1"
    # Walk up to the nearest existing ancestor: writing a new file into a
    # directory needs write permission on that directory, and creating the
    # directory itself needs it on the parent.
    while [[ ! -e "$target" && "$target" != "/" ]]; do
        target="$(dirname "$target")"
    done
    if [[ -w "$target" ]]; then
        echo ""
    else
        echo "sudo"
    fi
}

check_deps() {
    local missing=()
    for cmd in cargo; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        error "Missing dependencies: ${missing[*]}. Install Rust via https://rustup.rs"
    fi
    if ! command -v wine &>/dev/null; then
        warn "wine not found on PATH. Install a macOS Wine build (e.g. 'brew install --cask wine-stable') before running any .exe."
    fi
}

# Homebrew's default prefix differs by CPU: /opt/homebrew on Apple Silicon,
# /usr/local on Intel. Neither is universally correct, so detect rather than
# assume -- this is the same ambiguity documented in
# macos/APEX-WIN.app/Contents/MacOS/apex-win-launcher and
# macos/com.apex-win.daemon.plist's @BINDIR@ placeholder.
detect_bindir() {
    if command -v brew &>/dev/null; then
        echo "$(brew --prefix)/bin"
    elif [[ -d /opt/homebrew/bin ]]; then
        echo "/opt/homebrew/bin"
    else
        echo "/usr/local/bin"
    fi
}

build_rust() {
    info "Building win-sandbox-runner (release)..."
    # -p, not --workspace: win-sandbox-gui (GTK4/libadwaita) is Linux-only.
    cargo build --release -p win-sandbox-runner -p win-sandbox-common
}

install_binary() {
    local bindir="$1"
    local sudo_cmd
    sudo_cmd="$(sudo_for "$bindir")"
    info "Installing win-sandbox-runner to ${bindir}..."
    if [[ -n "$sudo_cmd" ]]; then
        warn "${bindir} is not writable by you; requesting sudo for this step"
    fi
    $sudo_cmd mkdir -p "$bindir"
    $sudo_cmd install -m755 target/release/win-sandbox-runner "${bindir}/win-sandbox-runner"
}

install_app_bundle() {
    local sudo_cmd
    sudo_cmd="$(sudo_for "/Applications/APEX-WIN.app")"
    info "Installing APEX-WIN.app to /Applications..."
    if [[ -n "$sudo_cmd" ]]; then
        warn "/Applications is not writable by you; requesting sudo for this step"
    fi
    $sudo_cmd rm -rf "/Applications/APEX-WIN.app"
    $sudo_cmd cp -R "macos/APEX-WIN.app" "/Applications/APEX-WIN.app"
    $sudo_cmd chmod +x "/Applications/APEX-WIN.app/Contents/MacOS/apex-win-launcher"

    # Register the bundle's document type claim (com.microsoft.windows-
    # executable) with Launch Services immediately, rather than waiting for
    # the OS to notice it on its own (which can take a Finder relaunch or a
    # login cycle) -- same motivation as scripts/install.sh's immediate
    # binfmt_misc registration instead of only waiting for systemd-binfmt.
    local lsregister="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
    if [[ -x "$lsregister" ]]; then
        # Deliberately NOT elevated: Launch Services registrations are
        # per-user, so registering as root would populate root's database
        # instead of the database Finder consults for this user.
        "$lsregister" -f "/Applications/APEX-WIN.app"
        info "Registered APEX-WIN.app with Launch Services"
    else
        warn "lsregister not found at the expected path; APEX-WIN may not appear in Open With until the next login or Finder relaunch"
    fi
}

# Unlike scripts/install.sh, this writes ONLY the per-user config
# (~/.config/win-sandbox/rules.json). There is no macOS equivalent of
# /etc/win-sandbox-runner: config.rs's RULES_SEARCH_PATHS/CONFIG_SEARCH_PATHS
# fall back to None if a search path doesn't exist on disk (verified in
# find_rules_path/load_config), so simply not creating the Linux-only system
# path is sufficient; it is not a hardcoded requirement.
install_config() {
    info "Installing user configuration..."
    local cfg_dir="${HOME}/.config/win-sandbox"
    mkdir -p "$cfg_dir"
    if [[ ! -f "${cfg_dir}/rules.json" ]]; then
        cp config/rules.json "${cfg_dir}/rules.json"
        info "User rules created at ${cfg_dir}/rules.json"
    else
        info "Existing rules at ${cfg_dir}/rules.json left untouched"
    fi
    # win-sandbox-runner.conf's tap_bridge_socket/rules_path defaults
    # (/var/run/win-tap-bridge.sock, /etc/win-sandbox-runner/rules.json) are
    # Linux-specific literals from config/win-sandbox-runner.conf; write a
    # macOS-appropriate conf instead of copying that file verbatim.
    if [[ ! -f "${cfg_dir}/win-sandbox-runner.conf" ]]; then
        cat > "${cfg_dir}/win-sandbox-runner.conf" <<'EOF'
[sandbox]
gui_enabled = false
default_tier = 0
display_mode = nested-x11

[logging]
level = info
EOF
        info "User config created at ${cfg_dir}/win-sandbox-runner.conf"
    fi
}

install_launch_agent() {
    local bindir="$1"
    info "Installing launchd LaunchAgent..."
    local agents_dir="${HOME}/Library/LaunchAgents"
    mkdir -p "$agents_dir"
    sed "s|@BINDIR@|${bindir}|" macos/com.apex-win.daemon.plist \
        > "${agents_dir}/com.apex-win.daemon.plist"
    info "LaunchAgent written to ${agents_dir}/com.apex-win.daemon.plist (not loaded automatically -- see notes below)"
}

main() {
    echo "APEX-WIN installer (macOS)"
    echo "==========================="
    echo ""

    check_platform
    check_not_root
    check_deps

    local bindir
    bindir="$(detect_bindir)"
    info "Using bindir: ${bindir}"

    build_rust
    install_binary "$bindir"
    install_app_bundle
    install_config
    install_launch_agent "$bindir"

    echo ""
    info "Installation complete!"
    echo ""
    echo "Double-click any .exe and choose 'Open With -> APEX-WIN' (or set it"
    echo "as default in Finder's Get Info panel). This does NOT require the"
    echo "background daemon -- see macos/com.apex-win.daemon.plist for why."
    echo ""
    echo "Manual mode (Terminal):"
    echo "  win-sandbox-runner --exe app.exe"
    echo ""
    echo "Optional: pre-load rules/app-db for repeated Terminal use:"
    echo "  launchctl load ~/Library/LaunchAgents/com.apex-win.daemon.plist"
    echo "  launchctl unload ~/Library/LaunchAgents/com.apex-win.daemon.plist  # to stop"
    echo ""
    echo "Optional: catch bare 'game.exe' or './game.exe' typed directly in"
    echo "Terminal too, not just double-click (add one line to ~/.zshrc or"
    echo "~/.bashrc; not done automatically):"
    echo "  source ${PWD}/scripts/apex-win-shell-hook.sh"
    echo ""
    echo "Isolation note: Tier 1/2 use Apple's Seatbelt sandbox (sandbox-exec)"
    echo "for filesystem (Tier 1) and filesystem+network (Tier 2) isolation."
    echo "This is a real kernel-enforced boundary but weaker than Linux's"
    echo "Landlock/bubblewrap on axes Seatbelt doesn't cover (no mount/PID"
    echo "namespace, no resource limits). Tier 3 (ephemeral overlay) has no"
    echo "macOS equivalent and is refused if requested explicitly. See"
    echo "HANDOFF.md and crates/win-sandbox-runner/src/seatbelt.rs."
    echo ""
}

main "$@"
