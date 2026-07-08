#!/usr/bin/env bash
# Axon OS — Quick chroot entry
#
# One-liner to drop into the build chroot or run a command inside it.
# Shorthand for: sudo bash build/build.sh --chroot [--cmd '...']
#
# Usage:
#   sudo bash scripts/axon-chroot.sh              # interactive shell
#   sudo bash scripts/axon-chroot.sh "ls -la"     # run one command
#   sudo bash scripts/axon-chroot.sh "apt list --installed"
set -euo pipefail

BUILD_SH="$(cd "$(dirname "${BASH_SOURCE[0]}")/../build" && pwd)/build.sh"

if [[ ${EUID} -ne 0 ]]; then
    echo "[axon-chroot] Must run as root. Try: sudo bash scripts/axon-chroot.sh" >&2
    exit 1
fi

if [[ $# -gt 0 ]]; then
    bash "${BUILD_SH}" --chroot --cmd "$*"
else
    bash "${BUILD_SH}" --chroot
fi
