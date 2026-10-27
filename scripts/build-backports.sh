#!/usr/bin/env bash
# Axon OS — rebuild newer Ubuntu source packages for Ubuntu 24.04 (noble).
#
# Reads packaging/backports/backports.list, fetches each source package from the Ubuntu
# archive with apt (so the source is checked against the signed archive indexes), changes only
# the version (adding "~24.04.axon1", which sorts below any later official Ubuntu version), and
# builds it with sbuild in a clean noble chroot. Packages are built in the order listed, and each
# can use the ones built before it. The .debs are written to the output directory.
#
# Usage: scripts/build-backports.sh [--out DIR] [--chroot noble.tar] [--only NAME]
# Needs: sbuild mmdebstrap devscripts dpkg-dev (and user namespaces, or run as root)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
LIST="${AXON_BACKPORTS_LIST:-${ROOT}/packaging/backports/backports.list}"
SUFFIX="${AXON_BACKPORT_SUFFIX:-~24.04.axon1}"
MIRROR="${AXON_MIRROR:-http://archive.ubuntu.com/ubuntu}"
OUT="${ROOT}/dist/backports"
CHROOT=""
ONLY=""

log() { echo "[backports] $*"; }
die() { echo "[backports] ERROR: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --out) OUT="${2:?--out needs a directory}"; shift 2 ;;
        --chroot) CHROOT="${2:?--chroot needs a file}"; shift 2 ;;
        --only) ONLY="${2:?--only needs a package name}"; shift 2 ;;
        -h | --help) sed -n '2,12p' "$0"; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done
[[ -f "${LIST}" ]] || die "list not found: ${LIST}"
for tool in sbuild mmdebstrap dch dpkg-source apt-get; do
    command -v "${tool}" >/dev/null 2>&1 || die "${tool} is required"
done

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
mkdir -p "${OUT}"
OUT="$(cd "${OUT}" && pwd)"

# Entries: "name suite version"
mapfile -t ENTRIES < <(grep -vE '^\s*(#|$)' "${LIST}")
[[ ${#ENTRIES[@]} -gt 0 ]] || die "no packages listed in ${LIST}"
for entry in "${ENTRIES[@]}"; do
    read -r name suite version extra <<<"${entry}"
    [[ -n "${name}" && -n "${suite}" && -n "${version}" && -z "${extra:-}" ]] \
        || die "bad line in ${LIST}: ${entry}"
    [[ "${name}" =~ ^[a-z0-9][a-z0-9+.-]*$ && "${suite}" =~ ^[a-z]+$ ]] || die "bad name or suite: ${entry}"
done

# apt that only knows the source suites, kept away from the host's own configuration
APT_DIR="${WORK}/apt"
mkdir -p "${APT_DIR}/lists/partial" "${APT_DIR}/cache"
: >"${APT_DIR}/sources.list"
for suite in $(printf '%s\n' "${ENTRIES[@]}" | awk '{ print $2 }' | sort -u); do
    echo "deb-src ${MIRROR} ${suite} main restricted universe multiverse" >>"${APT_DIR}/sources.list"
done
APT=(-o "Dir::Etc::sourcelist=${APT_DIR}/sources.list" -o "Dir::Etc::sourceparts=/dev/null"
    -o "Dir::State::lists=${APT_DIR}/lists" -o "Dir::Cache=${APT_DIR}/cache" -o Debug::NoLocking=1)
log "reading the source indexes"
apt-get "${APT[@]}" update -qq

if [[ -z "${CHROOT}" ]]; then
    CHROOT="${WORK}/noble.tar"
    log "creating a clean Ubuntu 24.04 build chroot"
    mmdebstrap --variant=buildd --components="main restricted universe multiverse" \
        --include=fakeroot,build-essential noble "${CHROOT}" \
        "deb ${MIRROR} noble main restricted universe multiverse
deb ${MIRROR} noble-updates main restricted universe multiverse
deb ${MIRROR} noble-security main restricted universe multiverse" >/dev/null
fi
[[ -f "${CHROOT}" ]] || die "chroot not found: ${CHROOT}"
# sbuild's unshare mode finds the chroot by name in its cache folder, and needs a subordinate
# id range for the user (mmdebstrap and sbuild map them into a user namespace)
CACHE="${XDG_CACHE_HOME:-${HOME}/.cache}/sbuild"
mkdir -p "${CACHE}"
ln -sf "${CHROOT}" "${CACHE}/noble-amd64.tar"
for ids in /etc/subuid /etc/subgid; do
    grep -q "^$(id -un):" "${ids}" 2>/dev/null \
        || die "$(id -un) has no entry in ${ids}; add one, e.g.: echo '$(id -un):100000:65536' | sudo tee -a ${ids}"
done

export DEBFULLNAME="Axon OS"
export DEBEMAIL="axon-os@users.noreply.github.com"
built=0
for entry in "${ENTRIES[@]}"; do
    read -r name suite version <<<"${entry}"
    [[ -z "${ONLY}" || "${ONLY}" == "${name}" ]] || continue
    log "== ${name} ${version} (from ${suite})"
    src="${WORK}/src/${name}"
    mkdir -p "${src}"
    (cd "${src}" && apt-get "${APT[@]}" source --download-only "${name}=${version}" >/dev/null 2>&1) \
        || die "cannot fetch ${name}=${version} from ${suite}"
    (cd "${src}" && dpkg-source -x "${name}_${version#*:}.dsc" tree >/dev/null)
    # Patches kept in packaging/backports/patches/<package>/ (only where a rebuild needs one)
    patches=("${ROOT}/packaging/backports/patches/${name}"/*.patch)
    if [[ -e "${patches[0]}" ]]; then
        mkdir -p "${src}/tree/debian/patches"
        for patch in "${patches[@]}"; do
            cp "${patch}" "${src}/tree/debian/patches/axon-$(basename "${patch}")"
            echo "axon-$(basename "${patch}")" >>"${src}/tree/debian/patches/series"
            log "   patch: $(basename "${patch}")"
        done
    fi
    (cd "${src}/tree" && dch --force-bad-version --newversion "${version}${SUFFIX}" \
        --distribution noble "Rebuild for Ubuntu 24.04 for Axon OS. Source changes only where packaging/backports/patches has them." >/dev/null)
    (cd "${src}" && dpkg-source -b tree >/dev/null)
    dsc="${src}/${name}_${version#*:}${SUFFIX}.dsc"
    [[ -f "${dsc}" ]] || die "source package was not created: ${dsc}"

    extra=()
    for deb in "${OUT}"/*.deb; do
        [[ -f "${deb}" ]] && extra+=("--extra-package=${deb}")
    done
    (cd "${src}" && sbuild --chroot-mode=unshare -d noble --no-run-lintian \
        --no-arch-all --build-dir="${OUT}" "${extra[@]}" "${dsc}") \
        || die "${name} failed to build; the log is in ${OUT}"
    built=$((built + 1))
done
[[ "${built}" -gt 0 ]] || die "nothing was built (is --only right?)"
log "done: $(find "${OUT}" -maxdepth 1 -name '*.deb' | wc -l) packages in ${OUT}"
