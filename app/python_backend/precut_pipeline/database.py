"""Database layer: SQLite for clip metadata, LanceDB for vector embeddings."""
import sqlite3
import json
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

import lancedb
import numpy as np
import pyarrow as pa

from .config import DB_FILENAME, LANCE_DIR


SCHEMA = """
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
    status TEXT DEFAULT 'pending',  -- pending, tagging, done, error
    tagger_id TEXT,                  -- which tagger produced the tags (Drop 3.8)
    motion_tags TEXT                 -- JSON list of shot-type tags (Drop 3.8)
);

CREATE TABLE IF NOT EXISTS frames (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id INTEGER NOT NULL,
    timestamp_sec REAL NOT NULL,
    frame_path TEXT NOT NULL,
    tags TEXT,              -- JSON array of descriptive tags from VLM
    tags_text TEXT,         -- raw comma-separated string for fulltext fallback
    FOREIGN KEY (clip_id) REFERENCES clips(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_frames_clip ON frames(clip_id);
CREATE INDEX IF NOT EXISTS idx_clips_status ON clips(status);
"""


# Drop 3.8 migration: existing databases won't have tagger_id / motion_tags.
# Add them if missing. This runs every time the DB opens and is idempotent.
MIGRATIONS = [
    "ALTER TABLE clips ADD COLUMN tagger_id TEXT",
    "ALTER TABLE clips ADD COLUMN motion_tags TEXT",
    # Drop 4.28: store the ORIGINAL camera file alongside the proxy so
    # the FCP7 exporter can emit pathurls pointing at the originals
    # (Premiere will auto-reconnect proxies manually if the editor wants,
    # but the default media should be the full-res source).
    "ALTER TABLE clips ADD COLUMN original_path TEXT",
]


class Database:
    """Thin wrapper around SQLite + LanceDB."""

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.project_dir.mkdir(parents=True, exist_ok=True)

        self.sqlite_path = self.project_dir / DB_FILENAME
        self.lance_path = self.project_dir / LANCE_DIR

        self._init_sqlite()
        self._lance_db = lancedb.connect(str(self.lance_path))
        self._init_lance()

    def _init_sqlite(self):
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            # Apply additive migrations idempotently (ignore "duplicate column"
            # errors so we never crash on an already-migrated DB).
            for stmt in MIGRATIONS:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass

    def _init_lance(self):
        """Create the frames vector table if it doesn't exist.

        CLIP ViT-B-32 outputs 512-dim embeddings.
        """
        table_names = self._lance_db.table_names()
        if "frame_vectors" not in table_names:
            # LanceDB needs a schema; create with a dummy row we immediately delete
            schema = pa.schema([
                pa.field("frame_id", pa.int64()),
                pa.field("clip_id", pa.int64()),
                pa.field("timestamp_sec", pa.float32()),
                pa.field("vector", pa.list_(pa.float32(), 512)),
            ])
            self._lance_db.create_table("frame_vectors", schema=schema, mode="create")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------- Clip operations ----------

    def upsert_clip(self, path: str, filename: str, duration_sec: float,
                    width: int, height: int, fps: float,
                    file_size: int, mtime: float, ingested_at: float,
                    original_path: Optional[str] = None) -> int:
        """Insert or update a clip, return its ID.

        Drop 4.28: `original_path` is the full-res camera file, which we
        store so the exporter can emit pathurls pointing to the original
        instead of the proxy. If None, stores NULL and the exporter
        falls back to legacy path-reconstruction heuristics.
        """
        with self.connect() as conn:
            cursor = conn.execute("""
                INSERT INTO clips (path, filename, duration_sec, width, height, fps,
                                   file_size, mtime, ingested_at, status, original_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                ON CONFLICT(path) DO UPDATE SET
                    mtime = excluded.mtime,
                    file_size = excluded.file_size,
                    original_path = COALESCE(excluded.original_path, clips.original_path)
                RETURNING id
            """, (path, filename, duration_sec, width, height, fps,
                  file_size, mtime, ingested_at, original_path))
            row = cursor.fetchone()
            return row["id"]

    def set_clip_status(self, clip_id: int, status: str, frame_count: Optional[int] = None):
        with self.connect() as conn:
            if frame_count is not None:
                conn.execute("UPDATE clips SET status = ?, frame_count = ? WHERE id = ?",
                             (status, frame_count, clip_id))
            else:
                conn.execute("UPDATE clips SET status = ? WHERE id = ?", (status, clip_id))

    def get_pending_clips(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM clips WHERE status IN ('pending', 'error') ORDER BY id"
            ).fetchall()

    def get_all_clips(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM clips ORDER BY id").fetchall()

    def clip_exists_unchanged(self, path: str, mtime: float,
                              required_tagger_id: Optional[str] = None) -> bool:
        """True if this clip is already ingested with matching mtime.

        Drop 3.8: if `required_tagger_id` is given, we also require the clip
        was tagged with that tagger. Clips tagged with an older tagger
        (e.g. llava-7b) are treated as "needs re-tagging" so the next ingest
        upgrades their tags.
        """
        with self.connect() as conn:
            row = conn.execute(
                "SELECT mtime, status, tagger_id FROM clips WHERE path = ?", (path,)
            ).fetchone()
            if row is None:
                return False
            if row["mtime"] != mtime or row["status"] != "done":
                return False
            if required_tagger_id is not None:
                current = row["tagger_id"]
                if current != required_tagger_id:
                    return False
            return True

    def set_clip_tagger(self, clip_id: int, tagger_id: str):
        """Record which tagger produced the tags for this clip."""
        with self.connect() as conn:
            conn.execute(
                "UPDATE clips SET tagger_id = ? WHERE id = ?",
                (tagger_id, clip_id),
            )

    def set_clip_motion_tags(self, clip_id: int, motion_tags: list[str]):
        """Store motion/framing tags on the clip."""
        with self.connect() as conn:
            conn.execute(
                "UPDATE clips SET motion_tags = ? WHERE id = ?",
                (json.dumps(motion_tags), clip_id),
            )

    def get_clip_motion_tags(self, clip_id: int) -> list[str]:
        """Read motion tags as a list; [] if unset."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT motion_tags FROM clips WHERE id = ?", (clip_id,)
            ).fetchone()
        if not row or not row["motion_tags"]:
            return []
        try:
            raw = json.loads(row["motion_tags"])
            return [str(t) for t in raw] if isinstance(raw, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    # ---------- Frame operations ----------

    def insert_frame(self, clip_id: int, timestamp_sec: float,
                     frame_path: str, tags: list[str], embedding: np.ndarray) -> int:
        tags_text = ", ".join(tags)
        with self.connect() as conn:
            cursor = conn.execute("""
                INSERT INTO frames (clip_id, timestamp_sec, frame_path, tags, tags_text)
                VALUES (?, ?, ?, ?, ?)
                RETURNING id
            """, (clip_id, timestamp_sec, frame_path, json.dumps(tags), tags_text))
            frame_id = cursor.fetchone()["id"]

        # Store vector in LanceDB
        table = self._lance_db.open_table("frame_vectors")
        table.add([{
            "frame_id": frame_id,
            "clip_id": clip_id,
            "timestamp_sec": float(timestamp_sec),
            "vector": embedding.astype(np.float32).tolist(),
        }])
        return frame_id

    def delete_frames_for_clip(self, clip_id: int):
        """Remove frames for a clip (used when re-ingesting)."""
        with self.connect() as conn:
            conn.execute("DELETE FROM frames WHERE clip_id = ?", (clip_id,))
        table = self._lance_db.open_table("frame_vectors")
        table.delete(f"clip_id = {clip_id}")

    def search_vectors(self, query_vector: np.ndarray, limit: int = 10) -> list[dict]:
        """Nearest-neighbor search using cosine distance.

        CLIP vectors are L2-normalized, so cosine is the correct metric.
        Distance range: 0 (identical direction) to 2 (opposite direction).
        A distance around 0.2-0.3 is a strong semantic match for CLIP.
        """
        table = self._lance_db.open_table("frame_vectors")
        results = (
            table.search(query_vector.astype(np.float32))
            .distance_type("cosine")
            .limit(limit)
            .to_list()
        )
        return results

    def get_frame_with_clip(self, frame_id: int) -> Optional[dict]:
        """Fetch a frame joined with its clip info. Returns None if not found."""
        with self.connect() as conn:
            row = conn.execute("""
                SELECT f.id AS frame_id, f.clip_id, f.timestamp_sec, f.tags, f.tags_text,
                       c.path AS clip_path, c.filename, c.duration_sec AS clip_duration,
                       c.fps
                FROM frames f
                JOIN clips c ON f.clip_id = c.id
                WHERE f.id = ?
            """, (frame_id,)).fetchone()
            if row is None:
                return None
            return dict(row)

    def get_stats(self) -> dict:
        with self.connect() as conn:
            clip_count = conn.execute("SELECT COUNT(*) as n FROM clips").fetchone()["n"]
            done_count = conn.execute(
                "SELECT COUNT(*) as n FROM clips WHERE status = 'done'"
            ).fetchone()["n"]
            frame_count = conn.execute("SELECT COUNT(*) as n FROM frames").fetchone()["n"]
            total_duration = conn.execute(
                "SELECT COALESCE(SUM(duration_sec), 0) as t FROM clips WHERE status = 'done'"
            ).fetchone()["t"]
        return {
            "clips_total": clip_count,
            "clips_indexed": done_count,
            "frames_indexed": frame_count,
            "hours_indexed": total_duration / 3600,
        }
