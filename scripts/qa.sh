#!/usr/bin/env bash
# Axon OS Quality Assurance — run all checks before pushing.
# Usage: bash scripts/qa.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
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
        rm -f "$output_file"
        pass=$((pass + 1))
    else
        printf "${RED}FAIL${NC}\n"
        echo "  Last 20 lines of output:"
        tail -20 "$output_file" | sed 's/^/  /'
        rm -f "$output_file"
        fail=$((fail + 1))
    fi
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
echo " Axon OS QA Pipeline"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo

# --- Formatting & Linting ---
run_check "Ruff lint"        ruff check apps/ services/ tests/ installer/
run_check "Ruff format"      ruff format --check apps/ services/ tests/ installer/

# --- Type Checking ---
run_check "Mypy"             mypy apps/ services/ --ignore-missing-imports

# --- Syntax & Build Checks ---
run_check "Python syntax"    python3 -m py_compile services/service_base.py
run_check "Python syntax"    python3 -m py_compile services/plugin_registry.py
run_check "Python syntax"    python3 -m py_compile services/plugin_deploy.py
run_check "ShellCheck"       bash -n install.sh
run_check "ShellCheck build" bash -n build/build.sh
run_check "ShellCheck chroot" bash -n build/config/chroot-setup.sh
run_check "ShellCheck firstboot" bash -n build/config/firstboot.sh
run_check "ShellCheck keep-chroot" bash -n scripts/keep-chroot.sh
run_check "JSON validation"  python3 -c "import json; json.load(open('shell/axon-shell/metadata.json'))"
run_check "Pre-commit hooks" pre-commit run --all-files

# --- Security Scanning ---
run_optional "Bandit security"  bandit -r apps/ services/ -f json -q

echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Tests"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo

run_check "Pytest" python3 -m pytest tests/ -v --tb=short --timeout=30 \
    --cov=apps --cov=services --cov-report=term-missing --cov-fail-under=40

echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
printf " Results: ${GREEN}%d passed${NC}, ${RED}%d failed${NC}\n" "$pass" "$fail"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ "$fail" -gt 0 ]; then
    exit 1
fi
