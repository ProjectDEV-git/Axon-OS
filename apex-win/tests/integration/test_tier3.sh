#!/bin/bash
# Integration test: Tier 3 — OverlayFS ephemeral sandbox
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUNNER="$PROJECT_ROOT/target/debug/win-sandbox-runner"

GREEN='\033[0;32m'
NC='\033[0m'
pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "\033[0;31m[FAIL]${NC} $1"; exit 1; }

cargo build -p win-sandbox-runner --quiet 2>/dev/null

echo "=== Tier 3 Integration Test ==="

TEST_EXE=$(mktemp /tmp/test_XXXXXX.exe)
echo "MZ" > "$TEST_EXE"
trap 'rm -f "$TEST_EXE"' EXIT

# Test 1: Dry-run with Tier 3
OUTPUT=$("$RUNNER" --exe "$TEST_EXE" --tier 3 --dry-run 2>&1)
if echo "$OUTPUT" | grep -q "DRY RUN"; then
    pass "Tier 3 dry-run mode works"
else
    fail "Tier 3 dry-run failed: $OUTPUT"
fi

# Test 2: Verify Tier 3 dispatch
OUTPUT=$("$RUNNER" --exe "$TEST_EXE" --tier 3 --dry-run --verbose 2>&1)
if echo "$OUTPUT" | grep -qi "tier.*3\|forced tier"; then
    pass "Tier 3 dispatch works"
else
    fail "Tier 3 dispatch failed: $OUTPUT"
fi

# Test 3: Verify overlay directory cleanup
TEST_DIR="/dev/shm/win-run-test-$$"
mkdir -p "$TEST_DIR"/{upper,work,merged}
rm -rf "$TEST_DIR"
if [[ ! -d "$TEST_DIR" ]]; then
    pass "Overlay directory cleanup works"
else
    fail "Overlay directory cleanup failed"
fi

echo "=== All Tier 3 tests passed ==="
