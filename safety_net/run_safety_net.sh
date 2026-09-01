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

# No hardcoded default: a cloud checkout and Ryan's Mac never share a path,
# and silently defaulting to the wrong one would run Tier 1 against nothing
# (or, worse, against a stale checkout) with no error. Caller must set it.
if [[ -z "${PRECUT_ROOT:-}" ]]; then
  echo "PRECUT_ROOT is not set — point it at a PreCut checkout and re-run." >&2
  echo "  Cloud/CI:   PRECUT_ROOT=/home/user/precut ./safety_net/run_safety_net.sh" >&2
  echo "  Ryan's Mac: PRECUT_ROOT=~/precut-checkout ./safety_net/run_safety_net.sh" >&2
  exit 2
fi
export PRECUT_ROOT

# Pinned so Python's per-process dict/set hash randomization can never be the
# reason a diff-based golden-master test flakes (belt and suspenders on top
# of the golden fixture deliberately using only one overlay style — see
# conftest.py — which is the actual mechanism keeping id-assignment order
# deterministic; PYTHONHASHSEED alone would not save an export that iterates
# a multi-element set of overlay styles).
export PYTHONHASHSEED=0

# Prefer the real project venv when present (Ryan's Mac — runs the full
# Tier-2 suite for real) and fall back to plain python3 (cloud/CI — Tier 1
# only, Tier-2 tests self-skip when the ML deps aren't importable).
if [[ -x "$HOME/precut-venv-fresh/bin/python" ]]; then
  PYTHON="$HOME/precut-venv-fresh/bin/python"
else
  PYTHON="python3"
fi

echo "PRECUT_ROOT=$PRECUT_ROOT"
echo "python=$PYTHON"
"$PYTHON" -m pytest "$SCRIPT_DIR/tests" -v
