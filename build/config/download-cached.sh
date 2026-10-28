#!/usr/bin/env bash
# Axon OS — download a file once, verified by SHA-256, and keep it for the next run.
# Sourced by install-ollama.sh (and usable by other build scripts).
#
#   fetch_cached URL SHA256 NAME CACHE_DIR FALLBACK_DIR
#
# Prints the path of the verified file on stdout (everything else goes to stderr).
# The cached file is named after the hash, so it can only ever be the pinned content. An
# interrupted download is kept as <file>.part and resumed on the next run. If CACHE_DIR cannot be
# written, FALLBACK_DIR (a temporary folder) is used and nothing is kept. Returns 1 on a hash
# mismatch that survives one fresh download.

fetch_cached() {
    local url="$1" sha="$2" name="$3" cache="$4" fallback="$5"
    local archive partial
    _fc_log() { echo "[download] $*" >&2; }
    _fc_ok() { echo "${sha}  $1" | sha256sum -c --status -; }

    if ! mkdir -p "${cache}" 2>/dev/null || [[ ! -w "${cache}" ]]; then
        _fc_log "cannot use ${cache} as a download cache; using ${fallback}"
        cache="${fallback}"
    fi
    archive="${cache}/${sha}-${name}"

    if [[ -f "${archive}" ]] && _fc_ok "${archive}"; then
        _fc_log "using the cached download ${archive}"
        echo "${archive}"
        return 0
    fi
    rm -f "${archive}"
    partial="${archive}.part"
    for _ in 1 2; do
        _fc_log "downloading ${url}"
        # Resume after dropped transfers (also from an earlier run), and give up on a stalled
        # connection (under 1 KB/s for a minute) so the retry reconnects.
        curl -fL --retry 10 --retry-delay 5 --retry-all-errors -C - \
            --speed-limit 1024 --speed-time 60 \
            ${AXON_CURL_OPTS:-} -o "${partial}" "${url}" >&2 || return 1
        _fc_log "verifying SHA-256"
        if _fc_ok "${partial}"; then
            mv "${partial}" "${archive}"
            echo "${archive}"
            return 0
        fi
        # A resumed file can be wrong from the start (another release, a corrupt part): once from
        # scratch, then give up
        rm -f "${partial}"
        _fc_log "SHA-256 mismatch for ${name}"
    done
    return 1
}
