#!/usr/bin/env bash
#
# Copy posthouse/ into the app so the running backend and the Tauri
# bundle see the code you actually edited.
#
# There are two copies on purpose (see tests/test_posthouse_sync.py), and
# on 2026-09-08 they silently drifted by four days because an rsync was
# run with RELATIVE paths from the wrong working directory. This script
# resolves the repo root from its own location, so it does the right
# thing no matter where it's invoked from — which is the whole reason it
# exists rather than a command to remember.
#
# Usage:  ./safety_net/sync_posthouse.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO_ROOT/posthouse/"
DEST="$REPO_ROOT/app/python_backend/posthouse/"

if [ ! -d "$SRC" ]; then
    echo "error: no posthouse/ at $SRC" >&2
    exit 1
fi

mkdir -p "$DEST"

# --delete so files removed from the source don't linger in the app copy
# and get imported. __pycache__ excluded: compiled bytecode differing is
# normal, and copying it across just causes churn.
rsync -a --delete --exclude='__pycache__' "$SRC" "$DEST"

echo "synced posthouse/ -> app/python_backend/posthouse/"

# Prove it, rather than trusting the exit code — this is the exact thing
# that was assumed and wrong before.
if diff -rq --exclude='__pycache__' "$SRC" "$DEST" >/dev/null; then
    echo "verified: both copies identical"
else
    echo "error: copies still differ after sync" >&2
    diff -rq --exclude='__pycache__' "$SRC" "$DEST" >&2 || true
    exit 1
fi
