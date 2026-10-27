#!/usr/bin/env bash
# Pin an Ollama release for build/config/install-ollama.sh.
#
# Usage: bash scripts/pin-ollama.sh <version> [asset]
#   e.g. bash scripts/pin-ollama.sh v0.35.1
# Downloads the release asset, prints its SHA-256 and writes version, asset
# and hash to build/config/ollama-release.env. Review the diff before
# committing: the hash is what every install will trust.
set -euo pipefail

VERSION="${1:?usage: pin-ollama.sh <version> [asset]}"
ASSET="${2:-ollama-linux-amd64.tar.zst}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIN_FILE="${ROOT}/build/config/ollama-release.env"

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
curl -fL --retry 3 -o "${tmp}/${ASSET}" \
    "https://github.com/ollama/ollama/releases/download/${VERSION}/${ASSET}"
SHA="$(sha256sum "${tmp}/${ASSET}" | cut -d' ' -f1)"

sed -i \
    -e "s|^OLLAMA_VERSION=.*|OLLAMA_VERSION=\"${VERSION}\"|" \
    -e "s|^OLLAMA_ASSET=.*|OLLAMA_ASSET=\"${ASSET}\"|" \
    -e "s|^OLLAMA_SHA256=.*|OLLAMA_SHA256=\"${SHA}\"|" \
    "${PIN_FILE}"
echo "Pinned Ollama ${VERSION} (${ASSET}) sha256=${SHA} in ${PIN_FILE}"

# The ROCm libraries are a separate asset of the same release
ROCM_ASSET="ollama-linux-amd64-rocm.tar.zst"
curl -fL --retry 3 -o "${tmp}/${ROCM_ASSET}" \
    "https://github.com/ollama/ollama/releases/download/${VERSION}/${ROCM_ASSET}"
ROCM_SHA="$(sha256sum "${tmp}/${ROCM_ASSET}" | cut -d' ' -f1)"
sed -i \
    -e "s|^OLLAMA_ROCM_ASSET=.*|OLLAMA_ROCM_ASSET=\"${ROCM_ASSET}\"|" \
    -e "s|^OLLAMA_ROCM_SHA256=.*|OLLAMA_ROCM_SHA256=\"${ROCM_SHA}\"|" \
    "${PIN_FILE}"
echo "Pinned ROCm libraries (${ROCM_ASSET}) sha256=${ROCM_SHA}"
