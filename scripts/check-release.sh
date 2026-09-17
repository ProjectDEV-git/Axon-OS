#!/usr/bin/env bash
# Axon OS release checks — strictly read-only, non-mutating validation.
#
# Usage:
#   bash scripts/check-release.sh          # run all read-only checks
#   AXON_QA_ROOT=/path bash scripts/check-release.sh   # check an alternate tree
#
# This script NEVER: installs anything, builds ISOs, runs pre-commit hooks
# (which can auto-fix files), auto-formats, writes caches, or mutates the tree.
# It only parses/validates files in place.
set -euo pipefail

ROOT="${AXON_QA_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$ROOT"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass=0
fail=0

run_check() {
    local name="$1"
    shift
    local output_file
    output_file=$(mktemp)
    printf "${YELLOW}▸ %s${NC} ... " "$name"
    if "$@" > "$output_file" 2>&1; then
        printf "${GREEN}PASS${NC}\n"
        pass=$((pass + 1))
    else
        printf "${RED}FAIL${NC}\n"
        tail -20 "$output_file" | sed 's/^/  /'
        fail=$((fail + 1))
    fi
    rm -f "$output_file"
}

run_optional() {
    local name="$1"
    shift
    if ! command -v "$1" >/dev/null 2>&1; then
        printf "${YELLOW}▸ %s${NC} ... SKIP (not installed)\n" "$name"
        return
    fi
    run_check "$name" "$@"
}

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Axon OS Release Checks (read-only)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo

# --- Bash syntax (parse only; does not execute the scripts) ---
for script in install.sh build/build.sh build/config/chroot-setup.sh \
              build/config/firstboot.sh scripts/keep-chroot.sh; do
    run_check "Bash syntax ($script)" bash -n "$script"
done

# --- Genuine static shell analysis (optional; respects .shellcheckrc) ---
run_optional "ShellCheck (static analysis)" shellcheck \
    install.sh build/build.sh build/config/chroot-setup.sh \
    build/config/firstboot.sh scripts/keep-chroot.sh

# --- Python syntax: compile() in-memory, no __pycache__ writes ---
for pyfile in services/service_base.py services/plugin_registry.py services/plugin_deploy.py; do
    run_check "Python syntax ($pyfile)" python3 -I -B -c \
        "import sys; compile(open(sys.argv[1], 'rb').read(), sys.argv[1], 'exec')" "$pyfile"
done

# --- JSON validation (read-only) ---
run_check "JSON validation" python3 -I -B -c \
    "import json; json.load(open('shell/axon-shell/metadata.json'))"

# --- Ruff lint & format check (no fixes or caches; skipped if ruff missing) ---
run_optional "Ruff lint (--no-fix)" ruff check --no-fix --no-cache apps/ services/ tests/ installer/
run_optional "Ruff format check" ruff format --check --no-cache apps/ services/ tests/ installer/

echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
printf " Results: ${GREEN}%d passed${NC}, ${RED}%d failed${NC}\n" "$pass" "$fail"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ "$fail" -gt 0 ]; then
    exit 1
fi
