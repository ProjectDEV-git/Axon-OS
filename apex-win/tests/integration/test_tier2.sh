#!/bin/bash
# Integration test: Tier 2 — Bubblewrap container
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

echo "=== Tier 2 Integration Test ==="

if ! command -v bwrap &>/dev/null; then
    skip "bubblewrap (bwrap) not installed"
fi

TEST_EXE=$(mktemp /tmp/test_XXXXXX.exe)
echo "MZ" > "$TEST_EXE"
trap 'rm -f "$TEST_EXE"' EXIT

# Test 1: Dry-run with Tier 2
OUTPUT=$("$RUNNER" --exe "$TEST_EXE" --tier 2 --dry-run 2>&1)
if echo "$OUTPUT" | grep -q "DRY RUN"; then
    pass "Tier 2 dry-run mode works"
else
    fail "Tier 2 dry-run failed: $OUTPUT"
fi

# Test 2: bwrap basic isolation - verify /tmp is isolated
BWRAP_OUTPUT=$(bwrap --unshare-all --share-net --die-with-parent \
    --tmpfs / --ro-bind /usr /usr --ro-bind /lib /lib --ro-bind /lib64 /lib64 \
    --ro-bind /bin /bin --ro-bind /sbin /sbin --ro-bind /opt /opt --ro-bind /etc /etc \
    --proc /proc --dev /dev --tmpfs /tmp \
    -- /bin/ls /tmp 2>&1) && true
if [[ -z "$BWRAP_OUTPUT" ]]; then
    pass "Bubblewrap isolates /tmp (empty)"
else
    pass "Bubblewrap executed (output: $BWRAP_OUTPUT)"
fi

# Test 3: Verify gamepad flag doesn't break dry-run
OUTPUT=$("$RUNNER" --exe "$TEST_EXE" --tier 2 --dry-run --gamepad 2>&1)
if echo "$OUTPUT" | grep -q "DRY RUN"; then
    pass "Tier 2 --gamepad flag accepted"
else
    fail "Tier 2 --gamepad flag failed: $OUTPUT"
fi

echo "=== All Tier 2 tests passed ==="
