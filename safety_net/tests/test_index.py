"""Tier-2: posthouse.harvest.index against the safety-net fixture clips.

Needs the real venv (torch, open_clip, PIL, lancedb, pyarrow — CLIP
embedding + the real Database class). No network call: tagger defaults to
None (CLIP-only), and this file never passes tagger="claude"/"llava".

Three claims under test, matching the task brief:
  1. The schema really is what `multi_exporter.load_broll_library` reads —
     proven by actually CALLING it on the index this test builds, not by
     asserting column names match some other source of truth.
  2. LanceDB holds one 512-dim vector per sampled frame.
  3. Re-indexing the same clip (unchanged mtime) is idempotent — no
     duplicate clip or frame rows.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.tier2

FIXTURES_MEDIA = Path(__file__).parent.parent / "fixtures" / "media"
STABLE = FIXTURES_MEDIA / "stable.mp4"
SHAKY = FIXTURES_MEDIA / "shaky.mp4"


def _require_deps_or_skip():
    missing = [
        m for m in ("torch", "open_clip", "PIL", "lancedb", "pyarrow", "numpy")
        if importlib.util.find_spec(m) is None
    ]
    if missing:
        pytest.skip(
            f"index test requires {', '.join(missing)}; run on Ryan's Mac "
            f"(~/precut-venv-fresh)."
        )


def test_index_broll_schema_consumed_by_load_broll_library(tmp_path: Path):
    _require_deps_or_skip()
    from posthouse.harvest.index import index_broll, load_broll_library

    project_dir = tmp_path / "project"
    stats = index_broll([STABLE, SHAKY], project_dir)

    assert stats.clips_processed == 2
    assert stats.clips_failed == 0
    assert stats.frames_extracted > 0
    assert stats.db_path.exists()

    # The real proof: load_broll_library (PreCut's own reader, re-exported
    # unchanged) must actually consume what index_broll wrote.
    entries = load_broll_library(stats.db_path)
    assert len(entries) == 2
    paths = {Path(e.source_path).name for e in entries}
    assert paths == {"stable.mp4", "shaky.mp4"}


def test_index_broll_lancedb_vectors_are_512d_one_per_frame(tmp_path: Path):
    _require_deps_or_skip()
    import lancedb

    from posthouse.harvest.index import index_broll

    project_dir = tmp_path / "project"
    stats = index_broll([STABLE], project_dir)

    db = lancedb.connect(str(stats.lance_path))
    table = db.open_table("frame_vectors")
    # No pandas in this venv — read via to_arrow() (pyarrow's to_pandas()
    # needs pandas installed, which this venv doesn't have) rather than
    # a similarity search, since we want every row, not nearest-neighbors.
    arrow_tbl = table.to_arrow()
    vectors = arrow_tbl.column("vector").to_pylist()

    assert len(vectors) == stats.frames_extracted
    assert {len(v) for v in vectors} == {512}


def test_index_broll_reindex_same_clip_is_idempotent(tmp_path: Path):
    _require_deps_or_skip()
    from posthouse.harvest.index import index_broll

    project_dir = tmp_path / "project"
    first = index_broll([STABLE], project_dir)
    assert first.clips_processed == 1
    assert first.clips_skipped == 0

    second = index_broll([STABLE], project_dir)
    assert second.clips_processed == 0
    assert second.clips_skipped == 1, (
        "re-indexing an unchanged clip should skip, not re-process — "
        "see Database.clip_exists_unchanged"
    )

    conn = sqlite3.connect(str(first.db_path))
    try:
        clip_rows = conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
        frame_rows = conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0]
    finally:
        conn.close()

    assert clip_rows == 1, "duplicate clip row after re-indexing the same path"
    assert frame_rows == first.frames_extracted, (
        "duplicate frame rows after re-indexing the same, unchanged clip"
    )
