"""Where a project's vectors live when its drive cannot hold LanceDB tables.

Ryan, 2026-10-15: B-roll tagging failed on all four clips of a new project with "Table 'frame_vectors' was not found". The project is on an exFAT drive, where LanceDB's commit fails with "Unable to rename
file: Operation not supported (os error 45)" and leaves a table folder with empty _versions and _transactions. On the internal disk the same calls work.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app" / "python_backend"))

from precut_pipeline import database as dbmod  # noqa: E402


def test_a_drive_that_can_commit_keeps_the_vectors_beside_the_database(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "_COMMIT_OK", {})
    assert dbmod.choose_lance_path(tmp_path) == tmp_path / "vectors.lance"                  # the real probe, on a real folder: LanceDB works here
    assert not list(tmp_path.glob(".lance_probe_*"))                                          # and the probe left nothing behind


def test_a_drive_that_cannot_commit_keeps_them_on_the_internal_disk_and_says_where(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "_COMMIT_OK", {})
    monkeypatch.setattr(dbmod, "_lance_can_commit", lambda d: False)
    monkeypatch.setenv("POSTHOUSE_VECTOR_ROOT", str(tmp_path / "local"))
    proj = tmp_path / "drive" / "Runnells Wall Removal" / "broll_index"
    proj.mkdir(parents=True)
    got = dbmod.choose_lance_path(proj)
    assert (tmp_path / "local") in got.parents and got.name == "vectors.lance"
    assert str(got) in (proj / "vectors_location.txt").read_text()
    assert dbmod.choose_lance_path(proj) == got                                               # the same project always finds the same place


def test_a_table_that_already_works_is_left_where_it_is_even_if_the_probe_says_no(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "_lance_can_commit", lambda d: False)
    v = tmp_path / "vectors.lance" / "frame_vectors.lance" / "_versions"
    v.mkdir(parents=True)
    (v / "1.manifest").write_text("x")
    assert dbmod.choose_lance_path(tmp_path) == tmp_path / "vectors.lance"


def test_a_half_made_table_folder_with_nothing_committed_is_not_mistaken_for_a_working_one(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "_lance_can_commit", lambda d: False)
    monkeypatch.setenv("POSTHOUSE_VECTOR_ROOT", str(tmp_path / "local"))
    (tmp_path / "vectors.lance" / "frame_vectors.lance" / "_versions").mkdir(parents=True)     # what the failed run left on the exFAT drive
    (tmp_path / "vectors.lance" / "frame_vectors.lance" / "_transactions").mkdir()
    assert dbmod.choose_lance_path(tmp_path) != tmp_path / "vectors.lance"


def test_the_database_works_end_to_end_with_vectors_moved_off_the_drive(tmp_path, monkeypatch):
    import numpy as np
    monkeypatch.setattr(dbmod, "_lance_can_commit", lambda d: False)
    monkeypatch.setenv("POSTHOUSE_VECTOR_ROOT", str(tmp_path / "local"))
    db = dbmod.Database(tmp_path / "index")
    cid = db.upsert_clip(path="/x/a.mp4", filename="a.mp4", duration_sec=10.0, width=960, height=540, fps=30.0, file_size=1, mtime=1.0, ingested_at=1.0)
    v = np.zeros(512, dtype=np.float32)
    v[0] = 1.0
    db.insert_frame(clip_id=cid, timestamp_sec=2.0, frame_path="/x/f.jpg", tags=["wall"], embedding=v)
    hits = db.search_vectors(v, limit=1)
    assert hits and abs(hits[0]["timestamp_sec"] - 2.0) < 1e-3 and (tmp_path / "local") in db.lance_path.parents
