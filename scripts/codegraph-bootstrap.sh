#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

CODEGRAPH_DIR="$PROJECT_ROOT/.codegraph"
OWNER_FILE="$CODEGRAPH_DIR/worktree-root"

init_codegraph() {
    echo "Initializing CodeGraph in $PROJECT_ROOT ..."
    codegraph init "$PROJECT_ROOT"
    echo "$PROJECT_ROOT" > "$OWNER_FILE"
}

if [[ -d "$CODEGRAPH_DIR" ]]; then
    if [[ ! -f "$OWNER_FILE" ]]; then
        echo "Error: $CODEGRAPH_DIR exists but has no owner marker ($OWNER_FILE missing)." >&2
        echo "Remove $CODEGRAPH_DIR and rerun 'just graph-bootstrap'." >&2
        exit 1
    fi

    OWNER_ROOT="$(cat "$OWNER_FILE")"
    if [[ "$OWNER_ROOT" != "$PROJECT_ROOT" ]]; then
        echo "Error: $CODEGRAPH_DIR is owned by another worktree: $OWNER_ROOT" >&2
        echo "Remove $CODEGRAPH_DIR and rerun 'just graph-bootstrap'." >&2
        exit 1
    fi

    if [[ ! -f "$CODEGRAPH_DIR/codegraph.db" ]]; then
        echo "CodeGraph owner marker present but database missing; re-initializing ..."
        init_codegraph
    else
        echo "CodeGraph already initialized for $PROJECT_ROOT"
    fi
else
    init_codegraph
fi

# Verify codegraph status reports the current worktree path.
STATUS_PROJECT="$(codegraph status --json "$PROJECT_ROOT" 2>/dev/null | python3 -c 'import sys, json; print(json.load(sys.stdin).get("projectPath", ""))' || true)"
if [[ "$STATUS_PROJECT" != "$PROJECT_ROOT" ]]; then
    echo "Error: codegraph status reports unexpected project path: ${STATUS_PROJECT:-<none>}" >&2
    exit 1
fi

echo "CodeGraph is ready for $PROJECT_ROOT"
