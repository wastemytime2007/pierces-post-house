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
    import importlib.util

    def _really_installed(name: str) -> bool:
        try:
            return importlib.util.find_spec(name) is not None
        except ValueError:
            # find_spec raises ValueError for a module in sys.modules with
            # __spec__=None — i.e. conftest's inert stub, not a real install.
            return False

    if any(_really_installed(m) for m in ("lancedb", "torch")):
        pytest.skip(
            "ML deps are installed in this environment (e.g. Ryan's Mac venv), "
            "so markers.py imports fine here — the stdlib-only fact this test "
            "proves is only provable where the deps are absent."
        )
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


# precut_pipeline/ submodules, minus __init__ (imported implicitly by the
# package itself) and cutlist/exporter/multi_exporter/overlay/presets/
# theme_categories/markers, which already have dedicated coverage above.
_PIPELINE_DIR = PRECUT_ROOT / "python_backend" / "precut_pipeline"
_ALREADY_COVERED = set(CHAIN_MODULES) | {"markers", "__init__"}
PIPELINE_MODULES_REMAINING = sorted(
    p.stem for p in _PIPELINE_DIR.glob("*.py") if p.stem not in _ALREADY_COVERED
) if _PIPELINE_DIR.is_dir() else []

# python_backend/ top-level modules. Not a package (no __init__.py), so
# these import as bare names once BACKEND_DIR is on sys.path — same as the
# posthouse bridge does it. `exporter.py` exists at BOTH this level and
# inside precut_pipeline/; the bare name here is the top-level one.
_BACKEND_DIR = PRECUT_ROOT / "python_backend"
TOP_LEVEL_MODULES = sorted(
    p.stem for p in _BACKEND_DIR.glob("*.py")
) if _BACKEND_DIR.is_dir() else []


def _require_ml_deps_or_skip():
    import importlib.util
    missing = [
        m for m in ("lancedb", "torch", "whisper", "open_clip", "PIL", "rich", "anthropic")
        if importlib.util.find_spec(m) is None
    ]
    if missing:
        pytest.skip(
            f"Tier-2 full import gate requires the real project venv; "
            f"missing here: {', '.join(missing)}. Run this on Ryan's Mac "
            f"(~/precut-venv-fresh per precut/ARCHITECTURE.md)."
        )


@pytest.mark.parametrize("module_name", PIPELINE_MODULES_REMAINING)
def test_full_pipeline_module_imports_cleanly_ryans_mac_only(module_name):
    """The Tier-2 half of the import gate: every precut_pipeline module NOT
    already covered by the stdlib-only chain test above (database, embedder,
    matcher, transcriber, tagger, claude_tagger, and friends — the ones that
    genuinely need lancedb/torch/whisper/open_clip/anthropic). Runs only
    where those deps exist; skips everywhere else rather than failing.
    """
    _require_ml_deps_or_skip()
    result = _import_in_clean_subprocess(f"precut_pipeline.{module_name}")
    assert result.returncode == 0, (
        f"precut_pipeline.{module_name} failed to import:\n{result.stderr}"
    )


@pytest.mark.parametrize("module_name", TOP_LEVEL_MODULES)
def test_top_level_backend_module_imports_cleanly_ryans_mac_only(module_name):
    """python_backend/*.py — backend.py, pipeline.py, producer.py, project.py,
    settings.py, setup_helper.py, proxy_manager.py, audio_indexer.py, and
    friends. Same clean-subprocess, ML-deps-required treatment as the
    precut_pipeline half above.
    """
    _require_ml_deps_or_skip()
    code = f"import sys; sys.path.insert(0, {str(_BACKEND_DIR)!r}); import {module_name}"
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"{module_name} failed to import:\n{result.stderr}"
    )
