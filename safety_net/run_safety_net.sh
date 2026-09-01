#!/usr/bin/env bash
# Phase 0 safety net runner. See safety_net/README.md for what this guards.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The runner is the CHECK; blessing a new golden is a deliberate manual act
# (see README). Refuse to run with BLESS set so a stray env var can never
# silently rewrite the snapshots this suite exists to defend.
if [[ "${BLESS:-}" == "1" ]]; then
  echo "refusing to run: BLESS=1 is set — unset it, or bless deliberately" >&2
  echo "via the direct pytest command in safety_net/README.md" >&2
  exit 2
fi

export PRECUT_ROOT="${PRECUT_ROOT:-/home/user/precut}"

# Pinned so Python's per-process dict/set hash randomization can never be the
# reason a diff-based golden-master test flakes (belt and suspenders on top
# of the golden fixture deliberately using only one overlay style — see
# conftest.py — which is the actual mechanism keeping id-assignment order
# deterministic; PYTHONHASHSEED alone would not save an export that iterates
# a multi-element set of overlay styles).
export PYTHONHASHSEED=0

echo "PRECUT_ROOT=$PRECUT_ROOT"
python3 -m pytest "$SCRIPT_DIR/tests" -v
