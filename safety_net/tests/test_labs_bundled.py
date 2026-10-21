"""Every kind of file the app's copy of labs/ holds must be listed in Tauri's `resources`, or the running app does not have it.

On 2026-10-21 the whoosh template (`labs/uxp_transitions/templates/*.xml`) was synced into `app/python_backend/labs/` but no resource glob matched `.xml`, so the app's bundled
backend folder lacked it and "Open in Premiere" with a transition failed with FileNotFoundError inside the app, while every direct test passed.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
APP_LABS = REPO_ROOT / "app" / "python_backend" / "labs"
CONF = REPO_ROOT / "app" / "src-tauri" / "tauri.conf.json"
NOT_NEEDED = {".DS_Store", ".gitignore", "index.js"}          # not part of what the app runs (index.js is the Premiere plugin panel, loaded by Premiere itself)


def test_every_file_type_in_the_app_copy_of_labs_is_bundled():
    patterns = json.loads(CONF.read_text())["bundle"]["resources"]
    globs = {p.rsplit("*", 1)[-1] for p in patterns if "labs/**/*" in p or p.startswith("../python_backend/**/*")}      # e.g. ".html", ".xml", ".py"
    missing = sorted({p.suffix for p in APP_LABS.rglob("*")
                      if p.is_file() and p.name not in NOT_NEEDED and "__pycache__" not in p.parts and p.suffix not in globs})
    assert not missing, f"file types in app/python_backend/labs/ that no Tauri resource glob bundles: {missing}; add `../python_backend/labs/**/*<ext>` to bundle.resources in app/src-tauri/tauri.conf.json"
