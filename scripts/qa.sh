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

run() {
    local label="$1"
    shift
    printf "${YELLOW}▸ %s${NC} ... " "$label"
    if "$@" >/dev/null 2>&1; then
        printf "${GREEN}PASS${NC}\n"
        ((pass++))
    else
        printf "${RED}FAIL${NC}\n"
        ((fail++))
    fi
}

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Axon OS QA Pipeline"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo

run "Ruff lint"        ruff check apps/ services/ tests/ installer/
run "Python syntax"    python3 -m py_compile services/service_base.py
run "Python syntax"    python3 -m py_compile services/plugin_registry.py
run "Python syntax"    python3 -m py_compile services/plugin_deploy.py
run "ShellCheck"       bash -n install.sh
run "JSON validation"  python3 -c "import json; json.load(open('shell/axon-shell/metadata.json'))"
run "Pre-commit hooks" pre-commit run --all-files

echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Tests"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo

printf "${YELLOW}▸ Pytest${NC} ... "
if python3 -m pytest tests/ -v --tb=short --timeout=30 2>&1; then
    printf "${GREEN}PASS${NC}\n"
    ((pass++))
else
    printf "${RED}FAIL${NC}\n"
    ((fail++))
fi

echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
printf " Results: ${GREEN}%d passed${NC}, ${RED}%d failed${NC}\n" "$pass" "$fail"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ "$fail" -gt 0 ]; then
    exit 1
fi
