#!/usr/bin/env bash
#
# Copy the creator-workflow tools (labs/) into the app so the running backend and the
# Tauri bundle (resources glob ../python_backend/**/*.py) carry the code that was edited.
# Same reason and same shape as sync_posthouse.sh: two copies on purpose, one script that
# resolves the repo root from its own path, and a diff that proves the result.
#
# Left out of the app copy: tests (they stay in labs/ and run there), caches, docs, the
# reel-specific recruit/ folder and the skill (Ryan's global config, not app code).
#
# Usage:  ./safety_net/sync_labs.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO_ROOT/labs/"
DEST="$REPO_ROOT/app/python_backend/labs/"

if [ ! -d "$SRC" ]; then
    echo "error: no labs/ at $SRC" >&2
    exit 1
fi

mkdir -p "$DEST"

EXCLUDES=(--exclude='__pycache__' --exclude='tests/' --exclude='*.md' --exclude='recruit/' --exclude='skill/' --exclude='.pytest_cache')

DIFFX=(-x __pycache__ -x tests -x '*.md' -x recruit -x skill -x .pytest_cache)   # diff takes bare names, rsync takes patterns

rsync -a --delete "${EXCLUDES[@]}" "$SRC" "$DEST"
echo "synced labs/ -> app/python_backend/labs/"

# The labs find the export check at <root>/safety_net/verify_export.py, where <root> is the repo root in the repo and python_backend/ in the app.
mkdir -p "$REPO_ROOT/app/python_backend/safety_net"
cp "$REPO_ROOT/safety_net/verify_export.py" "$REPO_ROOT/app/python_backend/safety_net/verify_export.py"
echo "copied safety_net/verify_export.py -> app/python_backend/safety_net/"

if diff -rq "${DIFFX[@]}" "$SRC" "$DEST" >/dev/null 2>&1; then
    echo "verified: both copies identical"
else
    echo "error: copies still differ after sync" >&2
    diff -rq "${DIFFX[@]}" "$SRC" "$DEST" >&2 || true
    exit 1
fi
