#!/bin/bash
# Build a .deb package for win-sandbox-runner (v0.3.0+)
# Usage: ./scripts/build-deb.sh [version]
set -euo pipefail

# Auto-detect version from Cargo.toml if not provided
if [[ -z "${1:-}" ]]; then
    VERSION=$(grep '^version' Cargo.toml | head -1 | sed 's/.*"\(.*\)"/\1/')
else
    VERSION="$1"
fi

ARCH="${2:-amd64}"
PKG_NAME="win-sandbox-runner"
DEB_DIR="target/deb"
PKG_DIR="$DEB_DIR/${PKG_NAME}_${VERSION}_${ARCH}"

echo "Building ${PKG_NAME} ${VERSION} for ${ARCH}..."

# Ensure release build exists
if [[ ! -f target/release/win-sandbox-runner ]]; then
    echo "Release build not found. Building..."
    cargo build --release --workspace
fi

# Clean and create package structure
rm -rf "$DEB_DIR"
mkdir -p "$PKG_DIR/DEBIAN"
mkdir -p "$PKG_DIR/usr/bin"
mkdir -p "$PKG_DIR/usr/share/applications"
mkdir -p "$PKG_DIR/usr/share/win-sandbox-runner/scripts"
mkdir -p "$PKG_DIR/etc/win-sandbox-runner"
mkdir -p "$PKG_DIR/etc/systemd/system"

# --- DEBIAN/control ---
cat > "$PKG_DIR/DEBIAN/control" << EOF
Package: $PKG_NAME
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Depends: wine, libgtk-4-1, libadwaita-1-0, bubblewrap
Recommends: winetricks, xserver-xephyr, xvfb
Maintainer: Gaming-RF <gamingrf@users.noreply.github.com>
Homepage: https://github.com/Gaming-RF/APEX-WIN
Description: Transparent tiered sandbox for Windows apps on Linux
 Run Windows executables via Wine with automatic sandboxing.
 Supports 4 isolation tiers (none, Landlock, Bubblewrap, OverlayFS),
 automatic Wine prefix management, DXVK installation, and a built-in
 app database with 35+ known-good profiles.
 .
 Features:
  - Transparent .exe interception via binfmt_misc
  - File manager double-click support via MIME handler
  - Background daemon with peer-authenticated socket dispatch
  - Per-app Wine prefix management
  - Automatic DXVK and winetricks setup
  - Built-in app database (Fusion 360, Steam, Office, games, etc.)
  - GTK4 GUI for trust decisions
  - Network isolation via TAP bridge
  - GPU passthrough support
  - Native Linux network optimizer (BBR, fq_codel, DSCP)
EOF

# --- DEBIAN/postinst ---
cat > "$PKG_DIR/DEBIAN/postinst" << 'POSTINST'
#!/bin/bash
set -euo pipefail

echo "Setting up win-sandbox-runner..."

# Register binfmt_misc handler via the shipped script, which is the single
# source of truth for the definition. Do not inline the registration string
# here: it was previously duplicated across five files and the same
# missing-mask bug (kernel returns EINVAL) had to be fixed three times.
REGISTER_SH="/usr/share/win-sandbox-runner/scripts/register-binfmt.sh"
if [[ -d /proc/sys/fs/binfmt_misc ]] && [[ -f "$REGISTER_SH" ]]; then
    sh "$REGISTER_SH" >/dev/null 2>&1 && \
        echo "binfmt_misc handler registered (.exe -> win-sandbox-runner)" || \
        echo "WARN: Failed to register binfmt handler"
fi

# Install desktop MIME handler for double-click support
if [[ -f /usr/share/applications/apex-win.desktop ]]; then
    update-desktop-database /usr/share/applications 2>/dev/null || true
    
    # Register as default handler for PE MIME types
    for m in application/vnd.microsoft.portable-executable application/x-ms-dos-executable application/x-msdownload application/x-msi; do
        if [[ -n "${SUDO_USER:-}" ]]; then
            sudo -u "$SUDO_USER" xdg-mime default apex-win.desktop "$m" 2>/dev/null || true
        else
            xdg-mime default apex-win.desktop "$m" 2>/dev/null || true
        fi
    done
    echo "Double-click handler registered for .exe files"
fi

# Create user config directory for the installing user
if [[ -n "${SUDO_USER:-}" ]]; then
    USER_HOME=$(eval echo "~$SUDO_USER")
    USER_CONF="$USER_HOME/.config/win-sandbox"
    mkdir -p "$USER_CONF"
    cp /etc/win-sandbox-runner/rules.json "$USER_CONF/rules.json" 2>/dev/null || true
    cp /etc/win-sandbox-runner/appdb.json "$USER_CONF/appdb.json" 2>/dev/null || true
    chown -R "$SUDO_USER:" "$USER_CONF"
fi

# Reload systemd
systemctl daemon-reload 2>/dev/null || true

echo ""
echo "win-sandbox-runner installed! Start the daemon:"
echo "  sudo systemctl enable --now win-sandbox-runner"
echo ""
echo "Then run any .exe:"
echo "  /path/to/program.exe"
POSTINST
chmod 755 "$PKG_DIR/DEBIAN/postinst"

# --- DEBIAN/prerm ---
cat > "$PKG_DIR/DEBIAN/prerm" << 'PRERM'
#!/bin/bash
# Stop daemon and remove binfmt handler on uninstall
systemctl stop win-sandbox-runner 2>/dev/null || true
systemctl disable win-sandbox-runner 2>/dev/null || true

if [[ -f /proc/sys/fs/binfmt_misc/APEX-WIN ]]; then
    echo -1 > /proc/sys/fs/binfmt_misc/APEX-WIN 2>/dev/null || true
fi
PRERM
chmod 755 "$PKG_DIR/DEBIAN/prerm"

# --- DEBIAN/postrm ---
cat > "$PKG_DIR/DEBIAN/postrm" << 'POSTRM'
#!/bin/bash
# Reload systemd after removal
systemctl daemon-reload 2>/dev/null || true
POSTRM
chmod 755 "$PKG_DIR/DEBIAN/postrm"

# --- Install files ---
# Binaries
cp target/release/win-sandbox-runner "$PKG_DIR/usr/bin/"
cp target/release/win-sandbox-gui "$PKG_DIR/usr/bin/"

# Config files
cp config/rules.json "$PKG_DIR/etc/win-sandbox-runner/"
cp config/appdb.json "$PKG_DIR/etc/win-sandbox-runner/"
cp config/rules.schema.json "$PKG_DIR/etc/win-sandbox-runner/"
cp config/net-optimizer.json "$PKG_DIR/etc/win-sandbox-runner/"

# Desktop MIME handler
cp scripts/apex-win.desktop "$PKG_DIR/usr/share/applications/"

# systemd service
cp scripts/win-sandbox-runner.service "$PKG_DIR/etc/systemd/system/"

# Helper scripts
cp scripts/*.sh "$PKG_DIR/usr/share/win-sandbox-runner/scripts/" 2>/dev/null || true

# --- Build .deb ---
dpkg-deb --build "$PKG_DIR"

echo ""
echo "Package built: ${PKG_DIR}.deb"
echo "Install with: sudo dpkg -i ${PKG_DIR}.deb"
echo "Fix deps with: sudo apt-get install -f"
