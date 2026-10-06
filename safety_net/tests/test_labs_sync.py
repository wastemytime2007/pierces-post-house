"""The two `labs/` copies must not drift (same reason as test_posthouse_sync.py).

`labs/` at the repo root is where the creator-workflow tools are edited and tested; `app/python_backend/labs/` is the copy the running app imports and
Tauri bundles. Tests, docs, the reel-specific `recruit/` folder and the skill stay out of the app copy (see safety_net/sync_labs.sh).

To fix a failure here, run: ./safety_net/sync_labs.sh
"""
from __future__ import annotations

import hashlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SOURCE = REPO_ROOT / "labs"
APP_COPY = REPO_ROOT / "app" / "python_backend" / "labs"
LEFT_OUT_DIRS = {"__pycache__", "tests", "recruit", "skill", ".pytest_cache"}
FIX_HINT = "run ./safety_net/sync_labs.sh to bring the app copy up to date"


def _files(root: Path) -> dict:
    out = {}
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if p.is_dir() or LEFT_OUT_DIRS & set(rel.parts) or p.suffix == ".md":
            continue
        out[str(rel)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_the_app_copy_of_labs_has_the_same_files_as_the_source():
    src, app = _files(SOURCE), _files(APP_COPY)
    assert src, "labs/ has no files to compare"
    assert sorted(src) == sorted(app), f"file lists differ: only in labs/ {sorted(set(src) - set(app))}, only in the app {sorted(set(app) - set(src))}; {FIX_HINT}"


def test_the_export_check_the_labs_import_is_bundled_beside_them():
    a, b = REPO_ROOT / "safety_net" / "verify_export.py", REPO_ROOT / "app" / "python_backend" / "safety_net" / "verify_export.py"
    assert b.exists() and a.read_bytes() == b.read_bytes(), FIX_HINT


def test_every_file_in_the_app_copy_is_byte_identical_to_its_source():
    src, app = _files(SOURCE), _files(APP_COPY)
    stale = sorted(k for k in src if k in app and src[k] != app[k])
    assert not stale, f"stale in the app copy: {stale}; {FIX_HINT}"
