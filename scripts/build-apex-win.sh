#!/usr/bin/env bash
# Build the APEX-WIN runner (apex-win/) and stage it at apex-win/dist/.
#
# Usage: bash scripts/build-apex-win.sh
#
# build/build.sh calls this on the build host before copying the sources into
# the chroot, and chroot-setup.sh installs apex-win/dist/win-sandbox-runner.
# Only the runner is built: Axon never shows APEX-WIN's GTK trust dialog, so
# the GUI crate (and its GTK4/libadwaita -dev dependencies) is not needed.
#
# Under sudo the build runs as the invoking user with their Rust toolchain,
# so target/ is not left owned by root. Exit codes: 0 built (or an existing
# binary kept), 1 build failed, 3 no cargo and no binary staged.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${ROOT}/apex-win"
DIST="${SRC}/dist"
OUT="${DIST}/win-sandbox-runner"

log() { echo "[build-apex-win] $*"; }

# Find cargo: on PATH, or in the sudo user's ~/.cargo (sudo resets PATH).
CARGO="$(command -v cargo || true)"
RUN_AS=()
if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    user_home="$(getent passwd "${SUDO_USER}" | cut -d: -f6)"
    if [[ -x "${user_home}/.cargo/bin/cargo" ]]; then
        CARGO="${user_home}/.cargo/bin/cargo"
    fi
    if [[ -n "${CARGO}" ]]; then
        RUN_AS=(sudo -u "${SUDO_USER}" -H env "PATH=$(dirname "${CARGO}"):${PATH}")
    fi
fi

if [[ -z "${CARGO}" ]]; then
    if [[ -x "${OUT}" ]]; then
        log "cargo not found; keeping existing ${OUT}"
        exit 0
    fi
    log "cargo not found and no prebuilt runner; install Rust (https://rustup.rs)"
    exit 3
fi

log "building win-sandbox-runner with $("${CARGO}" --version)"
if ! "${RUN_AS[@]}" "${CARGO}" build --release --locked \
        --manifest-path "${SRC}/Cargo.toml" -p win-sandbox-runner; then
    log "build failed"
    exit 1
fi

install -Dm755 "${SRC}/target/release/win-sandbox-runner" "${OUT}"
log "staged ${OUT}"
