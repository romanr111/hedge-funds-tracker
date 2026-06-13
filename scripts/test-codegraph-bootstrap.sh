#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOTSTRAP="$ROOT_DIR/scripts/codegraph-bootstrap.sh"

TMP_BASE="$(mktemp -d)"
trap 'rm -rf "$TMP_BASE"' EXIT

run_test() {
    local name="$1"
    echo "TEST: $name"
}

copy_bootstrap_to() {
    local dest="$1"
    mkdir -p "$dest/scripts"
    cp "$BOOTSTRAP" "$dest/scripts/codegraph-bootstrap.sh"
    chmod +x "$dest/scripts/codegraph-bootstrap.sh"
}

# Test 1: Fresh initialization works.
run_test "fresh init"
PROJECT1="$TMP_BASE/project1"
copy_bootstrap_to "$PROJECT1"
(cd "$PROJECT1" && scripts/codegraph-bootstrap.sh >/dev/null)
[[ -d "$PROJECT1/.codegraph" ]]
[[ -f "$PROJECT1/.codegraph/worktree-root" ]]
[[ "$(cat "$PROJECT1/.codegraph/worktree-root")" == "$PROJECT1" ]]
echo "  PASS"

# Test 2: Re-running bootstrap on owned DB is idempotent.
run_test "idempotent re-bootstrap"
(cd "$PROJECT1" && scripts/codegraph-bootstrap.sh >/dev/null)
[[ "$(cat "$PROJECT1/.codegraph/worktree-root")" == "$PROJECT1" ]]
echo "  PASS"

# Test 3: Missing owner marker is rejected.
run_test "missing owner marker rejected"
PROJECT2="$TMP_BASE/project2"
copy_bootstrap_to "$PROJECT2"
codegraph init "$PROJECT2" >/dev/null 2>&1 || true
rm -f "$PROJECT2/.codegraph/worktree-root"
if (cd "$PROJECT2" && scripts/codegraph-bootstrap.sh >/dev/null 2>&1); then
    echo "  FAIL: expected rejection" >&2
    exit 1
fi
echo "  PASS"

# Test 4: Copied .codegraph directory from another worktree is rejected.
run_test "copied .codegraph rejected"
PROJECT3="$TMP_BASE/project3"
mkdir -p "$PROJECT3"
cp -R "$PROJECT1/.codegraph" "$PROJECT3/.codegraph"
copy_bootstrap_to "$PROJECT3"
if (cd "$PROJECT3" && scripts/codegraph-bootstrap.sh >/dev/null 2>&1); then
    echo "  FAIL: expected rejection" >&2
    exit 1
fi
echo "  PASS"

# Test 5: Bootstrap from a subdirectory still targets the correct project root.
run_test "subdirectory invocation targets project root"
SUBDIR="$PROJECT1/subdir/deep"
mkdir -p "$SUBDIR"
(cd "$SUBDIR" && "$ROOT_DIR/scripts/codegraph-bootstrap.sh" >/dev/null)
[[ "$(cat "$PROJECT1/.codegraph/worktree-root")" == "$PROJECT1" ]]
echo "  PASS"

# Test 6: Missing database is re-initialized while preserving ownership.
run_test "missing database re-initialized"
cp "$PROJECT1/.codegraph/worktree-root" "$TMP_BASE/owner-backup"
rm -f "$PROJECT1/.codegraph/codegraph.db"
(cd "$PROJECT1" && scripts/codegraph-bootstrap.sh >/dev/null)
[[ -f "$PROJECT1/.codegraph/codegraph.db" ]]
[[ "$(cat "$PROJECT1/.codegraph/worktree-root")" == "$PROJECT1" ]]
echo "  PASS"

echo "All tests passed."
