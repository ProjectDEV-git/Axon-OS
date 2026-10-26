#!/bin/sh
# register-binfmt.sh — the single source of truth for the APEX-WIN binfmt_misc
# handler. Every install path (Makefile, install.sh, .deb postinst) must call
# this rather than writing its own registration string.
#
# Rationale: the definition was previously duplicated across five files. The
# same missing-mask bug was found and fixed independently in three of them,
# because there was no one place to correct.
#
# Usage:
#   register-binfmt.sh                 register (default)
#   register-binfmt.sh --unregister    remove the handler
#   register-binfmt.sh --print         print the definition, register nothing
#
# Env:
#   BINDIR   install prefix for the interpreter (default /usr/bin)
#
# Run as root, except for --print.

set -e

BINDIR="${BINDIR:-/usr/bin}"
BIN="${BINDIR}/win-sandbox-runner"
BINFMT_DIR="/proc/sys/fs/binfmt_misc"
NAME="APEX-WIN"

# Format: :name:type:offset:magic:mask:interpreter:flags
#   type = M      magic match
#   offset = 0    check at byte 0
#   magic = 4d5a  the MZ header every PE file starts with
#   mask  = ffff  REQUIRED. Omitting it makes the kernel reject the whole
#                 registration with EINVAL ("Invalid argument").
#   flags = F     fix binary (interpreter opened at registration time).
#                 NOT C: C takes credentials from the .exe, so a setuid .exe
#                 would run the runner (and Wine) as the file's owner.
DEFINITION=":${NAME}:M:0:\\x4d\\x5a:\\xff\\xff:${BIN}:F"

if [ "$1" = "--print" ]; then
    echo "$DEFINITION"
    exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "Error: must run as root" >&2
    exit 1
fi

if [ ! -d "$BINFMT_DIR" ]; then
    echo "Error: binfmt_misc not available (is the kernel module loaded?)" >&2
    echo "Try: modprobe binfmt_misc" >&2
    exit 1
fi

# The kernel expects "-1" to remove a handler. "-" is silently not the same.
unregister() {
    if [ -f "$BINFMT_DIR/$NAME" ]; then
        echo -1 > "$BINFMT_DIR/$NAME"
        echo "✓ binfmt_misc unregistered: $NAME removed"
    else
        echo "binfmt_misc: $NAME not registered (nothing to do)"
    fi
}

if [ "$1" = "--unregister" ]; then
    unregister
    exit 0
fi

if [ ! -x "$BIN" ]; then
    echo "Error: $BIN not found or not executable" >&2
    echo "Install the binary first (make quick-install), or set BINDIR." >&2
    exit 1
fi

# Replace any existing registration so re-running is idempotent.
if [ -f "$BINFMT_DIR/$NAME" ]; then
    echo -1 > "$BINFMT_DIR/$NAME"
fi

echo "$DEFINITION" > "$BINFMT_DIR/register"

if [ -f "$BINFMT_DIR/$NAME" ]; then
    echo "✓ binfmt_misc registered: .exe -> $BIN"
    echo ""
    echo "Any .exe can now be run directly:  /path/to/app.exe"
    echo "To unregister:                     $0 --unregister"
else
    echo "Error: registration failed" >&2
    exit 1
fi
