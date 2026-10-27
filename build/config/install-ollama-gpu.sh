#!/usr/bin/env bash
# Axon OS — add or remove Ollama's GPU libraries.
#
# The ISO ships Ollama for the CPU only. When a graphics card is found, Game Center
# (or the welcome app) runs this through pkexec to add the matching libraries:
#
#   install-ollama-gpu.sh cuda           NVIDIA CUDA libraries (about 0.8-1.2 GB installed)
#   install-ollama-gpu.sh rocm           AMD ROCm libraries (about 2.5 GB installed)
#   install-ollama-gpu.sh remove cuda    take them out again
#   install-ollama-gpu.sh remove rocm
#   install-ollama-gpu.sh status         one line per installed backend
#
# Downloads the Ollama release named in ollama-release.env, checks it against the pinned
# SHA-256, and unpacks only the one backend directory. Never run through a shell pipe.
# Must run as root. Exit codes: 0 done, 1 failure, 2 bad usage, 3 Ollama or pin missing.
#
# For tests: OLLAMA_PIN_FILE, OLLAMA_PREFIX (default /usr/local), OLLAMA_DOWNLOAD_DIR (use
# a local copy of the asset instead of downloading) and OLLAMA_GPU_DRY_RUN=1 (print the
# plan, change nothing). pkexec removes all of these from the environment.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIN_FILE="${OLLAMA_PIN_FILE:-${HERE}/ollama-release.env}"
PREFIX="${OLLAMA_PREFIX:-/usr/local}"
LIBDIR="${PREFIX}/lib/ollama"
DRY_RUN="${OLLAMA_GPU_DRY_RUN:-0}"

log() { echo "[ollama-gpu] $*"; }
usage() {
    echo "usage: install-ollama-gpu.sh cuda|rocm | remove cuda|rocm | status" >&2
    exit 2
}

# The CUDA directory that fits the driver. Ollama ships cuda_v12 and cuda_v13: v13 needs a
# 580+ driver. "nvidia-smi" says which CUDA the loaded driver supports; right after a driver
# install it is not loaded yet, so fall back to the newest driver package that is installed.
cuda_dir() {
    local major=""
    if command -v nvidia-smi >/dev/null 2>&1; then
        major="$(nvidia-smi 2>/dev/null | sed -n 's/.*CUDA Version: *\([0-9]*\)\..*/\1/p' | head -1)"
    fi
    if [[ -z "${major}" ]]; then
        local branch
        branch="$(dpkg-query -W -f='${Package}\n' 'nvidia-driver-*' 2>/dev/null |
            sed -n 's/^nvidia-driver-\([0-9]*\).*/\1/p' | sort -n | tail -1 || true)"
        if [[ -n "${branch}" && "${branch}" -ge 580 ]]; then major=13; else major=12; fi
    fi
    if [[ "${major}" -ge 13 ]]; then echo cuda_v13; else echo cuda_v12; fi
}

restart_ollama() {
    if [[ -d /run/systemd/system ]]; then
        systemctl try-restart ollama.service 2>/dev/null || true
    fi
}

installed_backends() {
    local f
    for f in "${LIBDIR}"/.axon-gpu-*; do
        [[ -e "${f}" ]] && echo "${f##*/.axon-gpu-} $(cat "${f}")"
    done
}

[[ $# -ge 1 ]] || usage
action="install"
case "$1" in
    cuda | rocm) backend="$1" ;;
    remove) [[ $# -eq 2 ]] || usage; action="remove"; backend="$2" ;;
    status) installed_backends; exit 0 ;;
    *) usage ;;
esac
[[ "${backend}" == "cuda" || "${backend}" == "rocm" ]] || usage

# Root is needed for the real /usr/local; a test prefix the caller owns needs none
if [[ "${DRY_RUN}" != "1" && "${PREFIX}" == "/usr/local" ]]; then
    [[ ${EUID} -eq 0 ]] || { log "must run as root"; exit 1; }
fi

if [[ "${action}" == "remove" ]]; then
    log "removing ${backend} libraries"
    if [[ "${DRY_RUN}" == "1" ]]; then
        echo "remove ${LIBDIR}/$([[ ${backend} == cuda ]] && echo 'cuda_*' || echo 'rocm*')"
        exit 0
    fi
    if [[ "${backend}" == "cuda" ]]; then rm -rf "${LIBDIR}"/cuda_*; else rm -rf "${LIBDIR}"/rocm*; fi
    rm -f "${LIBDIR}/.axon-gpu-${backend}"
    restart_ollama
    exit 0
fi

[[ -f "${PIN_FILE}" ]] || { log "pin file not found: ${PIN_FILE}"; exit 3; }
# shellcheck source=/dev/null
source "${PIN_FILE}"
[[ -x "${PREFIX}/bin/ollama" || "${DRY_RUN}" == "1" ]] || { log "Ollama is not installed"; exit 3; }

if [[ "${backend}" == "cuda" ]]; then
    asset="${OLLAMA_ASSET:-}"; sha="${OLLAMA_SHA256:-}"
    dir="$(cuda_dir)"
    need_mb=3500
else
    asset="${OLLAMA_ROCM_ASSET:-}"; sha="${OLLAMA_ROCM_SHA256:-}"
    dir="rocm*"
    need_mb=5000
fi
if [[ -z "${asset}" || -z "${sha}" || -z "${OLLAMA_VERSION:-}" ]]; then
    log "no pinned ${backend} release in ${PIN_FILE}; refusing to install unverified code"
    exit 3
fi
if [[ "${asset}" != *.tar.zst ]]; then
    log "unsupported asset type: ${asset}"
    exit 1
fi

if [[ "${DRY_RUN}" == "1" ]]; then
    echo "download ${asset} (${OLLAMA_VERSION})"
    echo "verify sha256 ${sha}"
    echo "extract lib/ollama/${dir}"
    exit 0
fi

command -v zstd >/dev/null 2>&1 || { log "zstd is required (apt install zstd)"; exit 1; }
tmp="$(mktemp -d /var/tmp/axon-ollama-gpu.XXXXXX)"  # not /tmp: that may live in RAM
trap 'rm -rf "${tmp}"' EXIT
free_mb="$(df -Pm "${tmp}" | awk 'NR == 2 { print $4 }')"
if [[ "${free_mb}" -lt "${need_mb}" ]]; then
    log "not enough disk space: ${free_mb} MB free, ${need_mb} MB needed"
    exit 1
fi

if [[ -n "${OLLAMA_DOWNLOAD_DIR:-}" ]]; then
    cp "${OLLAMA_DOWNLOAD_DIR}/${asset}" "${tmp}/${asset}"
else
    url="https://github.com/ollama/ollama/releases/download/${OLLAMA_VERSION}/${asset}"
    log "downloading ${url}"
    # Resume dropped transfers; give up on a stalled connection so the retry reconnects
    curl -fL --retry 10 --retry-delay 5 --retry-all-errors -C - \
        --speed-limit 1024 --speed-time 60 -o "${tmp}/${asset}" "${url}"
fi

log "verifying SHA-256"
if ! echo "${sha}  ${tmp}/${asset}" | sha256sum -c --status -; then
    log "SHA-256 mismatch for ${asset}; refusing to install"
    exit 1
fi

log "unpacking lib/ollama/${dir}"
mkdir "${tmp}/x"
tar --zstd -xf "${tmp}/${asset}" -C "${tmp}/x" --wildcards "lib/ollama/${dir}"
mapfile -t found < <(compgen -G "${tmp}/x/lib/ollama/${dir}" || true)
if [[ ${#found[@]} -eq 0 ]]; then
    log "the release has no ${dir} directory; the pin may be out of date"
    exit 1
fi
mkdir -p "${LIBDIR}"
# Replace an older install of this backend, then move the new directory in
if [[ "${backend}" == "cuda" ]]; then rm -rf "${LIBDIR}"/cuda_*; else rm -rf "${LIBDIR}"/rocm*; fi
for d in "${found[@]}"; do
    mv "${d}" "${LIBDIR}/"
done
echo "${OLLAMA_VERSION}" > "${LIBDIR}/.axon-gpu-${backend}"
restart_ollama
log "installed ${backend} libraries for Ollama ${OLLAMA_VERSION}"
