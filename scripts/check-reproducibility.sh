#!/bin/bash
# check-reproducibility.sh — Verify ISO build reproducibility
#
# Builds the ISO twice and compares SHA-256 checksums to verify
# that the build produces identical output.
#
# Usage: ./scripts/check-reproducibility.sh
#
# Requirements:
# - All build dependencies installed
# - Sufficient disk space for two ISO builds
# - Runs with sudo (required for debootstrap/chroot)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUILD_SCRIPT="${BASE_DIR}/build/build.sh"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*"; }

# Use a fixed timestamp for reproducibility
export SOURCE_DATE_EPOCH=1704067200  # 2024-01-01 00:00:00 UTC

BUILD_DIR_1="/tmp/axon-build-repro-1"
BUILD_DIR_2="/tmp/axon-build-repro-2"

cleanup() {
    echo "Cleaning up..."
    sudo rm -rf "${BUILD_DIR_1}" "${BUILD_DIR_2}" 2>/dev/null || true
}

trap cleanup EXIT

echo "=========================================="
echo " Axon OS Reproducibility Check"
echo "=========================================="
echo ""
echo "SOURCE_DATE_EPOCH: ${SOURCE_DATE_EPOCH}"
echo ""

# Build 1
log "Starting build 1..."
AXON_BUILD_DIR="${BUILD_DIR_1}" sudo -E bash "${BUILD_SCRIPT}" --ci 2>&1 | tail -20

ISO_1=$(find "${BUILD_DIR_1}" -name "*.iso" -type f | head -1)
if [[ -z "${ISO_1}" ]]; then
    error "Build 1 failed: no ISO found"
    exit 1
fi

HASH_1=$(sha256sum "${ISO_1}" | awk '{print $1}')
log "Build 1 complete: ${ISO_1}"
log "SHA-256: ${HASH_1}"
echo ""

# Build 2
log "Starting build 2..."
AXON_BUILD_DIR="${BUILD_DIR_2}" sudo -E bash "${BUILD_SCRIPT}" --ci 2>&1 | tail -20

ISO_2=$(find "${BUILD_DIR_2}" -name "*.iso" -type f | head -1)
if [[ -z "${ISO_2}" ]]; then
    error "Build 2 failed: no ISO found"
    exit 1
fi

HASH_2=$(sha256sum "${ISO_2}" | awk '{print $1}')
log "Build 2 complete: ${ISO_2}"
log "SHA-256: ${HASH_2}"
echo ""

# Compare
echo "=========================================="
echo " Comparison Result"
echo "=========================================="
echo ""
echo "Build 1: ${HASH_1}"
echo "Build 2: ${HASH_2}"
echo ""

if [[ "${HASH_1}" == "${HASH_2}" ]]; then
    log "BUILD IS REPRODUCIBLE"
    echo ""
    echo "Both builds produced identical ISO images."
    exit 0
else
    error "BUILD IS NOT REPRODUCIBLE"
    echo ""
    echo "The builds produced different ISO images."
    echo "This may be due to:"
    echo "  - Non-deterministic timestamps in packages"
    echo "  - Random UUIDs or keys generated during build"
    echo "  - Network-dependent package versions"
    exit 1
fi
