#!/bin/bash
# Integration test: Tier 1 — Landlock LSM sandbox
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUNNER="$PROJECT_ROOT/target/debug/win-sandbox-runner"

GREEN='\033[0;32m'
NC='\033[0m'
pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "\033[0;31m[FAIL]${NC} $1"; exit 1; }
skip() { echo -e "[SKIP] $1"; exit 0; }

cargo build -p win-sandbox-runner --quiet 2>/dev/null

echo "=== Tier 1 Integration Test ==="

KERNEL_VER=$(uname -r | cut -d. -f1)
if [[ "$KERNEL_VER" -lt 5 ]]; then
    skip "Kernel too old for Landlock ($(uname -r))"
fi

TEST_EXE=$(mktemp /tmp/test_XXXXXX.exe)
echo "MZ" > "$TEST_EXE"
trap 'rm -f "$TEST_EXE"' EXIT

# Test 1: Dry-run with Tier 1
OUTPUT=$("$RUNNER" --exe "$TEST_EXE" --tier 1 --dry-run 2>&1)
if echo "$OUTPUT" | grep -q "DRY RUN"; then
    pass "Tier 1 dry-run mode works"
else
    fail "Tier 1 dry-run failed: $OUTPUT"
fi

# Test 2: Verify Landlock dispatch works
OUTPUT=$("$RUNNER" --exe "$TEST_EXE" --tier 1 --dry-run --verbose 2>&1)
if echo "$OUTPUT" | grep -qi "tier.*1\|forced tier"; then
    pass "Tier 1 dispatch works"
else
    fail "Tier 1 dispatch failed: $OUTPUT"
fi

echo "=== All Tier 1 tests passed ==="
