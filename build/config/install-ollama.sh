#!/usr/bin/env bash
# Axon OS — install a pinned Ollama release, verified by SHA-256.
#
# Replaces `curl https://ollama.com/install.sh | sh`, which ran unverified
# remote code as root. Downloads the release tarball named in
# ollama-release.env, checks it against the pinned hash, and installs it with
# a dedicated `ollama` system user and systemd unit. Must run as root.
#
# Exit codes: 0 installed (or already present), 1 failure, 3 no pinned hash.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIN_FILE="${OLLAMA_PIN_FILE:-${HERE}/ollama-release.env}"
PREFIX="/usr/local"

log() { echo "[install-ollama] $*"; }

[[ ${EUID} -eq 0 ]] || { log "must run as root"; exit 1; }
[[ -f "${PIN_FILE}" ]] || { log "pin file not found: ${PIN_FILE}"; exit 3; }
# shellcheck source=/dev/null
source "${PIN_FILE}"

if [[ -z "${OLLAMA_VERSION:-}" || -z "${OLLAMA_SHA256:-}" || -z "${OLLAMA_ASSET:-}" ]]; then
    log "no pinned Ollama release in ${PIN_FILE}; refusing to install unverified code"
    log "pin one with: bash scripts/pin-ollama.sh <version>"
    exit 3
fi

if command -v ollama >/dev/null 2>&1; then
    log "ollama already installed at $(command -v ollama)"
    exit 0
fi

if [[ "${OLLAMA_ASSET}" == *.tar.zst ]] && ! command -v zstd >/dev/null 2>&1; then
    log "zstd is required to unpack ${OLLAMA_ASSET}; install it first (apt install zstd)"
    exit 1
fi

url="https://github.com/ollama/ollama/releases/download/${OLLAMA_VERSION}/${OLLAMA_ASSET}"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

log "downloading ${url}"
curl -fL --retry 3 --retry-delay 5 -o "${tmp}/${OLLAMA_ASSET}" "${url}"

log "verifying SHA-256"
if ! echo "${OLLAMA_SHA256}  ${tmp}/${OLLAMA_ASSET}" | sha256sum -c --status -; then
    log "SHA-256 mismatch for ${OLLAMA_ASSET}; refusing to install"
    exit 1
fi

log "extracting into ${PREFIX}"
case "${OLLAMA_ASSET}" in
    *.tgz|*.tar.gz) tar -xzf "${tmp}/${OLLAMA_ASSET}" -C "${PREFIX}" ;;
    *.tar.zst) tar --zstd -xf "${tmp}/${OLLAMA_ASSET}" -C "${PREFIX}" ;;
    *) log "unsupported asset type: ${OLLAMA_ASSET}"; exit 1 ;;
esac

if ! id ollama >/dev/null 2>&1; then
    useradd -r -s /bin/false -U -m -d /usr/share/ollama ollama
fi

cat > /etc/systemd/system/ollama.service <<UNIT
[Unit]
Description=Ollama Service
After=network-online.target

[Service]
ExecStart=${PREFIX}/bin/ollama serve
User=ollama
Group=ollama
Restart=always
RestartSec=3
Environment="PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload 2>/dev/null || true
log "installed Ollama ${OLLAMA_VERSION}"
