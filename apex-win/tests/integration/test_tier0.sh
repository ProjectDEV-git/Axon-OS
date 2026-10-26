#!/bin/bash
# Integration test: Tier 0 — Direct Wine exec
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUNNER="$PROJECT_ROOT/target/debug/win-sandbox-runner"

GREEN='\033[0;32m'
NC='\033[0m'
pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "\033[0;31m[FAIL]${NC} $1"; exit 1; }

cargo build -p win-sandbox-runner --quiet 2>/dev/null

echo "=== Tier 0 Integration Test ==="

# Create temp test exe (empty file with MZ header for realism)
TEST_EXE=$(mktemp /tmp/test_XXXXXX.exe)
echo "MZ" > "$TEST_EXE"
trap 'rm -f "$TEST_EXE"' EXIT

# Test 1: --dry-run should succeed
OUTPUT=$("$RUNNER" --exe "$TEST_EXE" --tier 0 --dry-run 2>&1)
if echo "$OUTPUT" | grep -q "DRY RUN"; then
    pass "Tier 0 dry-run mode works"
else
    fail "Tier 0 dry-run did not produce expected output: $OUTPUT"
fi

# Test 2: --verbose shows tier dispatch
OUTPUT=$("$RUNNER" --exe "$TEST_EXE" --tier 0 --dry-run --verbose 2>&1)
if echo "$OUTPUT" | grep -qi "tier.*0\|forced tier"; then
    pass "Tier 0 dispatch recognizes tier argument"
else
    fail "Tier 0 dispatch did not recognize tier: $OUTPUT"
fi

echo "=== All Tier 0 tests passed ==="
