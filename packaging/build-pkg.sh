#!/usr/bin/env bash
# Axon OS — build one of the small packages kept in packaging/<name>/.
#
# Layout of packaging/<name>/:
#   control     the DEBIAN/control file; "@VERSION@" becomes the pyproject version
#   files/      optional tree copied to / in the package (everything under etc/
#               becomes a conffile, so local edits survive upgrades)
#   postinst, prerm, postrm   optional maintainer scripts
#
# The big axon-os package has its own script (build-deb.sh).
#
# Usage: packaging/build-pkg.sh <name> [OUTPUT_DIR]   (default: dist/)
#        packaging/build-pkg.sh --all [OUTPUT_DIR]    every packaging/*/control
# Needs: dpkg-deb
set -euo pipefail
umask 022

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
log() { echo "[build-pkg] $*"; }

[[ $# -ge 1 ]] || { echo "usage: build-pkg.sh <name>|--all [OUTPUT_DIR]" >&2; exit 2; }
WHAT="$1"
OUT_DIR="${2:-${SRC}/dist}"
VERSION="$(sed -n 's/^version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "${SRC}/pyproject.toml" | head -1)"
[[ -n "${VERSION}" ]] || { echo "build-pkg: no version in pyproject.toml" >&2; exit 1; }

build_one() {
    local name="$1" dir="${SRC}/packaging/$1"
    [[ -f "${dir}/control" ]] || { echo "build-pkg: ${dir}/control not found" >&2; exit 1; }
    local stage root
    stage="$(mktemp -d)"
    root="${stage}/root"
    mkdir -p "${root}/DEBIAN"

    [[ -d "${dir}/files" ]] && cp -a "${dir}/files/." "${root}/"
    sed "s/@VERSION@/${VERSION}/g" "${dir}/control" > "${root}/DEBIAN/control"
    local script
    for script in postinst prerm postrm; do
        if [[ -f "${dir}/${script}" ]]; then
            install -m 755 "${dir}/${script}" "${root}/DEBIAN/${script}"
        fi
    done
    if [[ -d "${root}/etc" ]]; then
        (cd "${root}" && find etc -type f -printf '/%p\n' | sort) > "${root}/DEBIAN/conffiles"
    fi

    mkdir -p "${OUT_DIR}"
    local deb="${OUT_DIR}/${name}_${VERSION}_all.deb"
    dpkg-deb --root-owner-group -Zxz --build "${root}" "${deb}" >/dev/null
    rm -rf "${stage}"
    log "Built ${deb}"
}

if [[ "${WHAT}" == "--all" ]]; then
    for control in "${SRC}"/packaging/*/control; do
        [[ -f "${control}" ]] || continue
        build_one "$(basename "$(dirname "${control}")")"
    done
else
    build_one "${WHAT}"
fi
