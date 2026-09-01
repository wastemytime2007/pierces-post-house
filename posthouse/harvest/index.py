"""posthouse.harvest.index — re-export of PreCut's B-roll CLIP index.

Provenance: ``precut_pipeline.embedder`` (CLIP ViT-B-32, 512-dim),
``precut_pipeline.database`` (SQLite + LanceDB), ``precut_pipeline.
extractor`` (ffmpeg keyframe sampling), and ``precut_pipeline.ingest``
(``_process_clip`` — the real per-clip worker PreCut's own pipeline runs,
re-used here rather than re-derived) at the pin recorded in
``posthouse/PRECUT_PIN`` (see ``posthouse.precut_bridge``). ROADMAP.md's
role→skill map lists CLIP tags/index as Phase 1 harvest material feeding
the Assistant Editor's subject-grouping skill (Phase 4).

**Heavy dependency, Ryan's Mac only.** ``embedder.py`` imports ``torch``,
``open_clip``, ``PIL`` and ``numpy`` at module scope; ``database.py``
imports ``lancedb``, ``numpy``, and ``pyarrow`` at module scope. Self-skips
in a cloud session the way the Tier-2 safety-net tests already do.

**Schema.** :func:`index_broll` writes ``<project_dir>/broll_index/
precut.db`` + ``<project_dir>/broll_index/vectors.lance`` through the real
``Database`` class — the exact schema
``multi_exporter.load_broll_library`` reads and ``safety_net/conftest.py``
's ``_make_broll_index_db`` mirrors for its own fixture (a ``clips`` table
and a ``frames`` table; ``load_broll_library`` selects a subset of
``Database``'s columns, so the real superset schema satisfies it exactly).
:data:`load_broll_library` is re-exported here for convenience so a caller
can round-trip an index it just built without a second import path into
PreCut.

**Idempotency.** Re-indexing the same clip path with an unchanged mtime is
a no-op (``Database.clip_exists_unchanged`` — checked before calling
``_process_clip``, the same guard PreCut's own ``ingest_folder`` and
pipeline stage use); a *changed* mtime re-processes the clip and
``_process_clip`` itself clears the clip's old frame rows first
(``Database.delete_frames_for_clip``) before inserting new ones, so a
re-index never leaves duplicate frame/vector rows behind either way.
``clips.path`` is ``UNIQUE`` in the real schema (``upsert_clip`` is an
``ON CONFLICT(path) DO UPDATE``), which is the other half of the
no-duplicates guarantee.

**Vision tagging is optional and OFF by default.** ``tagger=None`` (the
default) builds a CLIP-only index — no network call, matching this
harvest's no-network-in-tests constraint. ``tagger="claude"`` uses
``precut_pipeline.claude_tagger.ClaudeVisionTagger`` and requires
``ANTHROPIC_API_KEY`` in the environment (raises if unavailable rather
than silently degrading, so a caller who asked for tagging finds out
immediately if the key is missing). ``tagger="llava"`` uses
``precut_pipeline.tagger.OllamaTagger`` and requires a reachable Ollama
instance with the model already pulled (same fail-loud behavior). Neither
path is exercised by this module's own tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Union

from posthouse.precut_bridge import import_precut

_ingest_mod = import_precut("precut_pipeline.ingest")
_database_mod = import_precut("precut_pipeline.database")
_embedder_mod = import_precut("precut_pipeline.embedder")
_extractor_mod = import_precut("precut_pipeline.extractor")
_multi_exporter_mod = import_precut("precut_pipeline.multi_exporter")
_config = import_precut("precut_pipeline.config")

Database = _database_mod.Database
CLIPEmbedder = _embedder_mod.CLIPEmbedder
probe_clip = _extractor_mod.probe_clip
extract_all_frames = _extractor_mod.extract_all_frames
load_broll_library = _multi_exporter_mod.load_broll_library
BrollLibraryEntry = _multi_exporter_mod.BrollLibraryEntry
_process_clip = _ingest_mod._process_clip  # the real per-clip worker; see module docstring

DB_FILENAME = _config.DB_FILENAME
LANCE_DIR = _config.LANCE_DIR
FRAMES_DIR = _config.FRAMES_DIR


@dataclass
class IndexStats:
    """Summary of one :func:`index_broll` call."""
    clips_processed: int
    clips_skipped: int
    clips_failed: int
    frames_extracted: int
    db_path: Path
    lance_path: Path


def _build_tagger(tagger: Optional[str]):
    """Construct the optional vision tagger. See module docstring — this
    is the ONLY place a tagger gets instantiated, and it never happens
    unless the caller explicitly asks for one."""
    if tagger is None:
        return None
    if tagger == "claude":
        claude_mod = import_precut("precut_pipeline.claude_tagger")
        instance = claude_mod.ClaudeVisionTagger()
        if not instance.is_available():
            raise RuntimeError(
                "tagger='claude' requires ANTHROPIC_API_KEY in the "
                "environment (ClaudeVisionTagger.is_available() returned "
                "False) — set it, or pass tagger=None for a CLIP-only index."
            )
        return instance
    if tagger == "llava":
        tagger_mod = import_precut("precut_pipeline.tagger")
        instance = tagger_mod.OllamaTagger()
        if not instance.is_available():
            raise RuntimeError(
                "tagger='llava' requires a reachable Ollama instance with "
                "the model already pulled (OllamaTagger.is_available() "
                "returned False) — set it up, or pass tagger=None for a "
                "CLIP-only index."
            )
        return instance
    raise ValueError(f"unknown tagger {tagger!r} — expected None, 'claude', or 'llava'")


def index_broll(
    clip_paths: Iterable[Union[str, Path]],
    project_dir: Union[str, Path],
    *,
    tagger: Optional[str] = None,
) -> IndexStats:
    """Index a list of B-roll clips into ``<project_dir>/broll_index/``.

    ``tagger`` is ``None`` (CLIP embeddings only, no network — the
    default), ``"claude"`` (needs ``ANTHROPIC_API_KEY``), or ``"llava"``
    (needs Ollama). Re-running with the same clip paths and unchanged
    mtimes is a no-op per clip (see module docstring — no duplicate rows).
    """
    project_dir = Path(project_dir)
    index_dir = project_dir / "broll_index"
    index_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = index_dir / FRAMES_DIR

    db = Database(index_dir)
    embedder = CLIPEmbedder()
    tagger_obj = _build_tagger(tagger)
    required_tagger_id = getattr(tagger_obj, "TAGGER_ID", None) if tagger_obj else None

    clips_processed = 0
    clips_skipped = 0
    clips_failed = 0
    frames_extracted = 0

    for raw_path in clip_paths:
        clip_path = Path(raw_path)
        try:
            mtime = clip_path.stat().st_mtime
        except OSError:
            clips_failed += 1
            continue

        if db.clip_exists_unchanged(
            str(clip_path), mtime, required_tagger_id=required_tagger_id
        ):
            clips_skipped += 1
            continue

        frame_count = _process_clip(clip_path, db, frames_dir, embedder, tagger_obj)
        if frame_count > 0:
            clips_processed += 1
            frames_extracted += frame_count
        else:
            clips_failed += 1

    return IndexStats(
        clips_processed=clips_processed,
        clips_skipped=clips_skipped,
        clips_failed=clips_failed,
        frames_extracted=frames_extracted,
        db_path=index_dir / DB_FILENAME,
        lance_path=index_dir / LANCE_DIR,
    )


__all__ = [
    "Database",
    "CLIPEmbedder",
    "probe_clip",
    "extract_all_frames",
    "load_broll_library",
    "BrollLibraryEntry",
    "DB_FILENAME",
    "LANCE_DIR",
    "FRAMES_DIR",
    "IndexStats",
    "index_broll",
]
