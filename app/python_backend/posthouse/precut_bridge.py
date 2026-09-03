"""posthouse.precut_bridge — Door 3, and the ONLY place it is opened.

Per docs/ARCHITECTURE.md, "the third door" is ``precut_pipeline`` imported
as a Python library, pinned to a tagged PreCut commit. Every other module
in this package that needs anything from PreCut goes through
:func:`import_precut` in this file — never a bare ``sys.path`` hack or a
direct ``import precut_pipeline...`` of its own. That keeps the pin check,
the path insertion, and the "what does posthouse depend on inside PreCut"
question in exactly one place.

What this module does, in order, the first time anything needs PreCut:

1. Resolves ``PRECUT_ROOT`` (the ``PRECUT_ROOT`` env var, defaulting to
   ``/home/user/precut`` — same default the safety net uses).
2. Reads the pinned commit hash out of ``posthouse/PRECUT_PIN`` (a
   one-line file, sibling to this module) and compares it against
   ``git -C $PRECUT_ROOT rev-parse HEAD``. A mismatch does NOT raise —
   PreCut is Ryan's live production tool and a stale pin should never be
   able to break a running session — but it prints an impossible-to-miss
   warning naming both hashes, because harvested code that silently drifts
   from what the safety net actually blessed is exactly the failure mode
   the pin exists to catch.
3. Inserts ``<PRECUT_ROOT>/python_backend`` onto ``sys.path`` exactly
   once (idempotent — safe to call from every module that touches this
   bridge without ever double-inserting).

Re-pinning to a newer PreCut commit is a deliberate act: update
``PRECUT_PIN``, re-run the safety net, and log the bump per
ROADMAP.md's Decision Log (same rule as re-blessing the golden master).

The markers.py surprise, again — this time in production code
-----------------------------------------------------------------
``safety_net/conftest.py`` documents a discovery (see its "The markers.py
surprise" docstring) and describes it as: the exporter chain is
stdlib-only "for CutLists with an EMPTY broll_markers list", because
``exporter.py``'s ``_build_markers()``/``_build_attached_markers()``
lazily ``from .markers import ...`` only when needed.

Building this module found that claim is stricter than the code actually
is: ``FCPXMLWriter._build_markers()`` does ``from .markers import
format_marker_name, format_marker_comment`` **unconditionally at the top
of the method**, and ``_build_sequence()`` calls ``_build_markers()`` on
every export, marker-having or not — including a Cold Footage sequence
with an empty ``broll_markers`` list and no ``creative_brief``. So
``precut_pipeline.markers`` (and, transitively, lancedb + numpy + pyarrow
via ``.database`` and torch via ``.transcriber``) is required for
*every* call into the exporter chain today, not just marker-carrying
ones. That contradicts this slice's constraint that heavy ML deps must
not be required.

Rather than route around PreCut (forbidden — it's protected and
unmodified) or accept the ML deps (forbidden — not installed here, and
the whole point of Phase 1 is that the harvest layer doesn't need them),
this bridge reuses the exact inert-stub technique ``conftest.py`` already
uses for the safety net: inject minimal placeholder modules for numpy,
pyarrow, lancedb, and torch into ``sys.modules`` — ONLY if the real
package isn't already importable, so this is a complete no-op on Ryan's
Mac, where the real venv has all of them. The stubs satisfy `import` and
the couple of type annotations `database.py` needs; nothing here ever
calls a method on them, and ``format_marker_name``/``format_marker_comment``
(the only two names the exporter actually uses out of ``markers.py``)
never touch them at runtime either. See
``posthouse/README.md`` "Friction worth recording" for the suggested
upstream fix (make the import conditional on there being anything to
render) that would remove the need for this workaround entirely.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
import threading
import types
from pathlib import Path
from types import ModuleType
from typing import Optional

_PACKAGE_DIR = Path(__file__).resolve().parent
PIN_FILE = _PACKAGE_DIR / "PRECUT_PIN"

PRECUT_ROOT = Path(os.environ.get("PRECUT_ROOT", "/home/user/precut"))
BACKEND_DIR = PRECUT_ROOT / "python_backend"

_lock = threading.Lock()
_prepared = False


def _read_pin() -> Optional[str]:
    if not PIN_FILE.exists():
        return None
    text = PIN_FILE.read_text(encoding="utf-8").strip()
    return text or None


def _actual_commit() -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(PRECUT_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _warn(*lines: str) -> None:
    banner = "!" * 78
    print(banner, file=sys.stderr)
    print("POSTHOUSE / PRECUT_BRIDGE WARNING", file=sys.stderr)
    for line in lines:
        print(f"  {line}", file=sys.stderr)
    print(banner, file=sys.stderr)


def _verify_pin() -> None:
    expected = _read_pin()
    if expected is None:
        _warn(
            f"No pin file at {PIN_FILE} — cannot verify the PreCut checkout.",
            "Every harvested capability is unverified against a known commit.",
        )
        return

    actual = _actual_commit()
    if actual is None:
        _warn(
            f"Could not read `git rev-parse HEAD` at PRECUT_ROOT={PRECUT_ROOT}.",
            "Is PRECUT_ROOT a real git checkout of the precut repo?",
        )
        return

    if expected != actual:
        _warn(
            "PreCut checkout commit does NOT match the pin.",
            f"expected (posthouse/PRECUT_PIN): {expected}",
            f"actual   (PRECUT_ROOT HEAD):     {actual}",
            f"PRECUT_ROOT={PRECUT_ROOT}",
            "Harvested code may behave differently than what the safety net "
            "blessed. Re-run safety_net/run_safety_net.sh before trusting "
            "anything built against this checkout.",
        )


def _install_stub_if_missing(name: str, build) -> None:
    try:
        __import__(name)
        return  # real package is installed (e.g. Ryan's Mac venv) — use it
    except ImportError:
        pass
    sys.modules[name] = build()


def _install_marker_dependency_stubs() -> None:
    """See module docstring, "The markers.py surprise, again". Mirrors
    safety_net/conftest.py's ``_install_marker_dependency_stubs`` exactly —
    inert placeholders so ``precut_pipeline.markers`` (pulled in
    unconditionally by every ``FCPXMLWriter._build_markers()`` call) can
    import, without pulling in ~1.5GB of real ML packages this bridge has
    no business needing just to format two strings that, for a
    marker-less/brief-less CutList, are never even called."""
    def _numpy_stub() -> types.ModuleType:
        mod = types.ModuleType("numpy")
        mod.ndarray = type("ndarray", (), {})  # only used as a type annotation
        mod.float32 = "float32"
        return mod

    def _empty_stub(name: str) -> types.ModuleType:
        return types.ModuleType(name)

    _install_stub_if_missing("numpy", _numpy_stub)
    _install_stub_if_missing("pyarrow", lambda: _empty_stub("pyarrow"))
    _install_stub_if_missing("lancedb", lambda: _empty_stub("lancedb"))
    _install_stub_if_missing("torch", lambda: _empty_stub("torch"))


def ensure_precut_on_path() -> None:
    """Idempotent: verify the pin, put python_backend on sys.path, and
    install the marker-dependency stubs so the exporter chain works
    without PreCut's full ML venv.

    Safe to call any number of times, from any module — the pin check,
    the sys.path insertion, and the stub installation each happen exactly
    once per process.
    """
    global _prepared
    with _lock:
        if _prepared:
            return
        _verify_pin()
        backend_str = str(BACKEND_DIR)
        if backend_str not in sys.path:
            sys.path.insert(0, backend_str)
        _install_marker_dependency_stubs()
        _prepared = True


def import_precut(module_path: str) -> ModuleType:
    """Import a module from the PreCut checkout by its dotted path.

    ``module_path`` is exactly what you'd write after ``python_backend`` is
    on ``sys.path`` — e.g. ``"precut_pipeline.cutlist"`` for a package
    module, or ``"proxy_manager"`` for one of the top-level
    ``python_backend/*.py`` modules. This is the ONLY function in
    ``posthouse`` that reaches into PreCut; every other module calls this
    instead of importing precut_pipeline (or a top-level backend module)
    directly.
    """
    ensure_precut_on_path()
    return importlib.import_module(module_path)


# Open the door as soon as this module is imported — mirrors the safety
# net's own conftest.py, which does its sys.path insertion at import time
# rather than waiting for the first fixture to run. Anything that imports
# posthouse.precut_bridge has, by definition, decided it needs PreCut.
ensure_precut_on_path()
