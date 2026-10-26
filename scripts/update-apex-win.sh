#!/usr/bin/env bash
# Replace apex-win/ with a fresh copy of upstream APEX-WIN.
#
# Usage: bash scripts/update-apex-win.sh [git-ref]   (default: main)
# Afterwards re-apply the Axon patches listed in apex-win/AXON-VENDOR.md,
# update the commit recorded there, and review the diff before committing.
set -euo pipefail

REF="${1:-main}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${ROOT}/apex-win"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

git clone --quiet https://github.com/Gaming-RF/APEX-WIN.git "${tmp}/src"
git -C "${tmp}/src" checkout --quiet "${REF}"
commit="$(git -C "${tmp}/src" rev-parse HEAD)"

keep="${tmp}/AXON-VENDOR.md"
cp "${DEST}/AXON-VENDOR.md" "${keep}"
rm -rf "${DEST}"
mkdir -p "${DEST}"
git -C "${tmp}/src" archive HEAD | tar -x -C "${DEST}"
cp "${keep}" "${DEST}/AXON-VENDOR.md"
grep -qx '/dist/' "${DEST}/.gitignore" || echo '/dist/' >> "${DEST}/.gitignore"

echo "apex-win/ now at upstream ${commit}"
echo "Re-apply the patches in apex-win/AXON-VENDOR.md and update its commit line."
