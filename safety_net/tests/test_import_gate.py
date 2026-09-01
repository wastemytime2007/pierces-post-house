"""Import gate for the exporter chain.

Phase 0 scope decision: the golden-master test targets the exporter chain
ONLY — multi_exporter, exporter, bin_builders, cutlist, overlay, presets,
markers, theme_categories — because those are (mostly — see below) the only
precut_pipeline modules that don't need lancedb/torch/whisper/anthropic, so
this safety net can run in any plain Python 3.11 + ffmpeg environment
without installing PreCut's full ML stack.

This file verifies that claim directly, module by module, rather than
assuming it. It found one exception (`markers`) — see the xfail-style case
below and conftest.py's "The markers.py surprise" docstring for the full
story and the golden-master test's workaround.

Every check here runs each import in its OWN subprocess rather than via
`importlib` in-process. Two reasons:
  1. conftest.py deliberately stubs lancedb/pyarrow/numpy/torch into
     sys.modules (for the golden-master test's benefit — see its
     docstring). Once installed, those stubs live for the rest of THIS
     pytest process, and once `precut_pipeline.markers` has been imported
     successfully anywhere in that process, Python caches it in
     sys.modules — a later `importlib.import_module` would return the
     cached module instead of re-attempting the real, unstubbed import.
     A subprocess has none of that history.
  2. It's simply what "does this import cleanly in a fresh environment"
     means — testing against a process that already loaded pytest,
     conftest fixtures, and whatever prior test files pulled in is not
     the same claim.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PRECUT_ROOT = Path(os.environ.get("PRECUT_ROOT", "/home/user/precut"))
BACKEND_DIR = PRECUT_ROOT / "python_backend"

# Modules the scope decision names as the "exporter chain."
CHAIN_MODULES = [
    "multi_exporter",
    "exporter",
    "bin_builders",
    "cutlist",
    "overlay",
    "presets",
    "theme_categories",
]


def _import_in_clean_subprocess(module_name: str) -> subprocess.CompletedProcess:
    code = f"import sys; sys.path.insert(0, {str(BACKEND_DIR)!r}); import {module_name}"
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("module_name", CHAIN_MODULES)
def test_chain_module_imports_cleanly(module_name):
    """Each of these must import with nothing beyond the standard library,
    in a fresh process with no stubs and no prior imports to lean on.

    A regression here (someone adds `import numpy` to cutlist.py, say)
    means the safety net itself stops being runnable in a plain cloud
    session — exactly the failure mode this test exists to catch early.
    """
    result = _import_in_clean_subprocess(f"precut_pipeline.{module_name}")
    assert result.returncode == 0, (
        f"precut_pipeline.{module_name} failed to import stdlib-only:\n{result.stderr}"
    )


def test_markers_module_does_not_import_cleanly_stdlib_only():
    """Documents a real discovery rather than silently special-casing it.

    `precut_pipeline.markers` is listed in the Phase 0 scope decision as
    part of the "stdlib-only" exporter chain, but it is NOT stdlib-only:
    it does `from .database import Database` and `from .transcriber import
    Phrase` at module scope, and those transitively require lancedb +
    numpy + pyarrow (database.py) and torch (transcriber.py) — none of
    which are installed here.

    This is asserted as an EXPECTED failure (not skipped) so that if a
    future PreCut change actually makes markers.py stdlib-only, this test
    starts failing and tells us to promote it back into CHAIN_MODULES
    above. Until then: `exporter.py`'s lazy `from .markers import
    format_marker_name, format_marker_comment` (used only when a CutList
    carries BRollMarkers) needs the sys.modules stubs conftest.py installs
    for lancedb/pyarrow/numpy/torch. The golden-master and quirk tests
    exercise that exact path with the stubs in place; this test runs in a
    clean subprocess specifically so it keeps proving the underlying,
    unstubbed fact regardless of what else this pytest session has done.
    """
    result = _import_in_clean_subprocess("precut_pipeline.markers")
    assert result.returncode != 0, (
        "precut_pipeline.markers imported cleanly with no ML deps installed — "
        "if this is now true, promote 'markers' into CHAIN_MODULES above and "
        "delete conftest.py's stub-injection workaround."
    )
    assert "ModuleNotFoundError" in result.stderr
    assert ("lancedb" in result.stderr) or ("torch" in result.stderr), (
        f"expected the failure to name lancedb or torch; got:\n{result.stderr}"
    )


@pytest.mark.skip(
    reason=(
        "Full backend import gate (all 35 precut_pipeline + python_backend "
        "modules, including database.py/embedder.py/matcher.py/"
        "transcriber.py/tagger.py/claude_tagger.py and friends) requires "
        "the real project venv: lancedb, torch, whisper, open_clip, PIL, "
        "rich, anthropic. That venv only exists on Ryan's Mac "
        "(~/precut-venv-fresh per precut/ARCHITECTURE.md 'Runtime "
        "environment'). This cloud safety net intentionally covers only "
        "the stdlib-only exporter chain (see module docstring); the full "
        "gate is a Tier-2 check to run there, not here."
    )
)
def test_full_backend_import_gate_ryans_mac_only():
    pass
