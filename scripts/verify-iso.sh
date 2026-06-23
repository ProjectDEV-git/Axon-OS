#!/bin/bash
# verify-iso.sh — Verify Axon OS ISO integrity and signature
#
# Usage: ./verify-iso.sh <iso-file> [signature-file] [checksum-file]

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log() { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*"; }

usage() {
    echo "Usage: $0 <iso-file> [signature-file] [checksum-file]"
    echo ""
    echo "Verifies the integrity and authenticity of an Axon OS ISO image."
    echo ""
    echo "Arguments:"
    echo "  iso-file        Path to the ISO file to verify"
    echo "  signature-file  Path to the GPG signature file (optional)"
    echo "  checksum-file   Path to the SHA-256 checksum file (optional)"
    echo ""
    echo "Examples:"
    echo "  $0 axon-os-0.3.0-amd64.iso"
    echo "  $0 axon-os-0.3.0-amd64.iso axon-os-0.3.0-amd64.iso.sig"
    echo "  $0 axon-os-0.3.0-amd64.iso axon-os-0.3.0-amd64.iso.sig axon-os-0.3.0-amd64.iso.sha256"
    exit 1
}

if [[ $# -lt 1 ]]; then
    usage
fi

ISO_FILE="$1"
SIG_FILE="${2:-}"
CHECKSUM_FILE="${3:-}"

# Check if ISO exists
if [[ ! -f "${ISO_FILE}" ]]; then
    error "ISO file not found: ${ISO_FILE}"
    exit 1
fi

echo "=========================================="
echo " Axon OS ISO Verification"
echo "=========================================="
echo ""

# Step 1: Verify SHA-256 checksum
if [[ -n "${CHECKSUM_FILE}" && -f "${CHECKSUM_FILE}" ]]; then
    echo "Verifying SHA-256 checksum..."
    if sha256sum -c "${CHECKSUM_FILE}" 2>/dev/null; then
        log "SHA-256 checksum verified successfully"
    else
        error "SHA-256 checksum verification FAILED"
        error "The ISO file may be corrupted or tampered with"
        exit 1
    fi
else
    # Try to find checksum file automatically
    AUTO_CHECKSUM="${ISO_FILE}.sha256"
    if [[ -f "${AUTO_CHECKSUM}" ]]; then
        echo "Found checksum file: ${AUTO_CHECKSUM}"
        if sha256sum -c "${AUTO_CHECKSUM}" 2>/dev/null; then
            log "SHA-256 checksum verified successfully"
        else
            error "SHA-256 checksum verification FAILED"
            exit 1
        fi
    else
        warn "No checksum file found, skipping checksum verification"
        warn "Expected: ${AUTO_CHECKSUM}"
    fi
fi

echo ""

# Step 2: Verify GPG signature
if [[ -n "${SIG_FILE}" && -f "${SIG_FILE}" ]]; then
    echo "Verifying GPG signature..."
    if gpg --verify "${SIG_FILE}" "${ISO_FILE}" 2>/dev/null; then
        log "GPG signature verified successfully"
    else
        error "GPG signature verification FAILED"
        error "The ISO may not be from an official Axon OS release"
        exit 1
    fi
else
    # Try to find signature file automatically
    AUTO_SIG="${ISO_FILE}.sig"
    if [[ -f "${AUTO_SIG}" ]]; then
        echo "Found signature file: ${AUTO_SIG}"
        if gpg --verify "${AUTO_SIG}" "${ISO_FILE}" 2>/dev/null; then
            log "GPG signature verified successfully"
        else
            error "GPG signature verification FAILED"
            exit 1
        fi
    else
        warn "No signature file found, skipping signature verification"
        warn "Expected: ${AUTO_SIG}"
    fi
fi

echo ""

# Step 3: Basic ISO structure check
echo "Checking ISO structure..."
if file "${ISO_FILE}" | grep -q "ISO 9660"; then
    log "Valid ISO 9660 image"
else
    warn "File does not appear to be a standard ISO image"
fi

# Check for El Torito boot catalog (BIOS+UEFI)
if isoinfo -d -i "${ISO_FILE}" 2>/dev/null | grep -q "El Torito"; then
    log "El Torito boot catalog found (BIOS boot supported)"
else
    warn "No El Torito boot catalog found"
fi

echo ""
echo "=========================================="
echo " Verification Complete"
echo "=========================================="
