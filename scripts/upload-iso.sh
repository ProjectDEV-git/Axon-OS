#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<EOF
Usage: $0 [release-tag]

Upload ISO files from dist/ to a GitHub release.
If no release tag is provided, the script will infer one from
an ISO filename in dist/ or from the current git tag.
EOF
}

if [[ ${1:-} == "-h" || ${1:-} == "--help" ]]; then
    usage
    exit 0
fi

if ! command -v gh >/dev/null 2>&1; then
    echo "ERROR: GitHub CLI ('gh') not found. Install it first." >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

if [[ ! -d dist ]]; then
    echo "ERROR: dist/ directory not found. Build the ISO first." >&2
    exit 1
fi

shopt -s nullglob
isos=(dist/*.iso)
shas=(dist/*.sha256)
shopt -u nullglob

if [[ ${#isos[@]} -eq 0 ]]; then
    echo "ERROR: No ISO files found in dist/." >&2
    exit 1
fi
if [[ ${#shas[@]} -eq 0 ]]; then
    echo "ERROR: No checksum files found in dist/." >&2
    exit 1
fi

infer_tag() {
    local file version
    file="${isos[0]}"
    if [[ "$(basename "${file}")" =~ axon-os-([0-9]+\.[0-9]+\.[0-9]+)-amd64\.iso ]]; then
        echo "v${BASH_REMATCH[1]}"
        return 0
    fi
    if git describe --tags --abbrev=0 >/dev/null 2>&1; then
        git describe --tags --abbrev=0
        return 0
    fi
    echo "v0.3.0"  # fallback — prefer ISO filename or git tag
}

release_tag="${1:-$(infer_tag)}"

if gh release view "${release_tag}" >/dev/null 2>&1; then
    echo "Using existing release: ${release_tag}"
    gh release upload "${release_tag}" "${isos[@]}" "${shas[@]}" --clobber
else
    echo "Creating release: ${release_tag}"
    gh release create "${release_tag}" "${isos[@]}" "${shas[@]}" \
        --title "Axon OS ${release_tag#v}" \
        --notes "Automated ISO upload for ${release_tag}."
fi

echo "Upload complete."
