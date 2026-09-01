"""Tier-2: DB migrations are additive-only (quirk 6, precut/DECISIONS.md).

"DB schema migrations are additive only (`ALTER TABLE ADD COLUMN`) and
wrapped in try/except for idempotence. Never drop a column." This is not
observable in exported XML, so it was never covered by the golden master —
it needs a real SQLite file and (because `Database.__init__` also opens
LanceDB) the real venv. Skips everywhere else; runs on Ryan's Mac.

The claim under test: opening an OLD-schema project database with the
current `Database` class must (a) not lose or corrupt any pre-existing row,
(b) add the missing columns so current code can read/write them, and
(c) be idempotent — opening it a second time must not error or duplicate
anything.
"""
from __future__ import annotations

import importlib.util
import os
import sqlite3
from pathlib import Path

import pytest


def _require_ml_deps_or_skip():
    missing = [m for m in ("lancedb", "pyarrow", "numpy") if importlib.util.find_spec(m) is None]
    if missing:
        pytest.skip(
            f"DB-migration test requires lancedb/pyarrow/numpy; missing here: "
            f"{', '.join(missing)}. Run this on Ryan's Mac (~/precut-venv-fresh)."
        )


# The pre-Drop-3.8/4.28 schema: no tagger_id, motion_tags, or original_path.
# Copied from database.py's SCHEMA with those three additive columns removed
# — i.e. what a project database looked like before those migrations
# existed, which is exactly the case MIGRATIONS is designed to heal.
_OLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    filename TEXT NOT NULL,
    duration_sec REAL,
    width INTEGER,
    height INTEGER,
    fps REAL,
    file_size INTEGER,
    mtime REAL,
    ingested_at REAL NOT NULL,
    frame_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS frames (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id INTEGER NOT NULL,
    timestamp_sec REAL NOT NULL,
    frame_path TEXT NOT NULL,
    tags TEXT,
    tags_text TEXT,
    FOREIGN KEY (clip_id) REFERENCES clips(id) ON DELETE CASCADE
);
"""


@pytest.fixture
def old_schema_project(tmp_path: Path, precut_pipeline_db_module) -> Path:
    """A project dir holding an old-schema precut.db with one real row."""
    project_dir = tmp_path / "old_project"
    project_dir.mkdir()
    db_path = project_dir / precut_pipeline_db_module.DB_FILENAME
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_OLD_SCHEMA)
        conn.execute(
            "INSERT INTO clips (path, filename, duration_sec, width, height, "
            "fps, file_size, mtime, ingested_at, frame_count, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("/fixtures/media/stable.mp4", "stable.mp4", 4.0, 640, 360,
             30.0, 12345, 1000000.0, 1000000.0, 0, "done"),
        )
        conn.commit()
    finally:
        conn.close()
    return project_dir


@pytest.fixture
def precut_pipeline_db_module():
    _require_ml_deps_or_skip()
    import sys
    from pathlib import Path as _P
    backend_dir = _P(os.environ.get("PRECUT_ROOT", "/home/user/precut")) / "python_backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from precut_pipeline import database
    return database


def test_migration_adds_missing_columns_without_dropping_the_row(old_schema_project, precut_pipeline_db_module):
    db = precut_pipeline_db_module.Database(old_schema_project)
    with db.connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM clips WHERE path = ?", ("/fixtures/media/stable.mp4",)).fetchone()

    assert row is not None, "the pre-existing row must survive migration"
    assert row["filename"] == "stable.mp4"
    assert row["duration_sec"] == 4.0
    assert row["status"] == "done"

    # The additive columns must now exist and be readable (NULL is correct
    # for a row that predates them — that's what "additive" means).
    assert "tagger_id" in row.keys()
    assert "motion_tags" in row.keys()
    assert "original_path" in row.keys()
    assert row["tagger_id"] is None
    assert row["motion_tags"] is None
    assert row["original_path"] is None


def test_migration_is_idempotent_on_second_open(old_schema_project, precut_pipeline_db_module):
    precut_pipeline_db_module.Database(old_schema_project)
    # Second open must not raise (ALTER TABLE ADD COLUMN on an already-
    # migrated DB errors "duplicate column name" — MIGRATIONS' try/except
    # is what this proves is actually wired up) and must not duplicate rows.
    db2 = precut_pipeline_db_module.Database(old_schema_project)
    with db2.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
    assert count == 1, "opening an already-migrated DB must not duplicate or lose rows"


def test_new_columns_are_writable_after_migration(old_schema_project, precut_pipeline_db_module):
    db = precut_pipeline_db_module.Database(old_schema_project)
    with db.connect() as conn:
        conn.execute(
            "UPDATE clips SET tagger_id = ?, motion_tags = ?, original_path = ? WHERE path = ?",
            ("claude-vision", '["static"]', "/originals/stable.mov", "/fixtures/media/stable.mp4"),
        )
        conn.commit()
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM clips WHERE path = ?", ("/fixtures/media/stable.mp4",)).fetchone()
    assert row["tagger_id"] == "claude-vision"
    assert row["motion_tags"] == '["static"]'
    assert row["original_path"] == "/originals/stable.mov"
