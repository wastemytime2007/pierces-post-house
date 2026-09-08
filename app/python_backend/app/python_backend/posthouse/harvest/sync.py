"""posthouse.harvest.sync — re-export of PreCut's lav/audio sync.

Provenance: ``precut_pipeline.audio_sync`` at the pin recorded in
``posthouse/PRECUT_PIN`` (see ``posthouse.precut_bridge``). The module
itself imports clean at module scope (stdlib only: ``hashlib``, ``re``,
``time``, ``dataclasses``), but its cross-correlation work lazily imports
``audio_offset_finder.audio_offset_finder.find_offset_between_files`` at
call time — real audio-processing deps, and per ``safety_net/README.md``
"Scoped out," synthetic sine-tone audio can't clear ``SCORE_USE`` at all,
which is why this is Tier 2 even beyond the ML-dependency question:
real(ish) correlated audio is required, not just a real venv.

**API.** :func:`sync_pairs` walks the full ``aroll_paths`` x ``lav_paths``
cross product exactly the way PreCut's own ``audio_sync.sync_project``
does (see that function — "Walks all A-roll sources x all audio sources"),
calling the same per-pair primitive (``audio_sync.sync_pair``) this module
re-exports unchanged. Each result carries ``offset_sec``, ``score``, and
``passed_threshold`` (``score >= SCORE_USE`` — PreCut's own threshold,
re-exported below, never a locally redefined one).

**Below-threshold policy is NOT this wrapper's call.** ROADMAP.md Phase 4
lists "a settled answer for below-threshold pairs (included-and-flagged,
dropped, or surfaced)" as still open. This wrapper returns EVERY pair it
computes, flagged via ``passed_threshold`` — it never drops a pair for
scoring low, and it never silently includes one either. The Assistant
Editor (Phase 4) decides what to do with an unreliable pair; this module's
job stops at telling the truth about the score.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Union

from posthouse.precut_bridge import import_precut

_mod = import_precut("precut_pipeline.audio_sync")

SyncPair = _mod.SyncPair
AudioSyncState = _mod.AudioSyncState
sync_pair = _mod.sync_pair
sync_project = _mod.sync_project
group_audio_files = _mod.group_audio_files
compute_source_hash = _mod.compute_source_hash

SCORE_USE = _mod.SCORE_USE
SCORE_MAYBE = _mod.SCORE_MAYBE


@dataclass
class PairSyncResult:
    """One (A-roll, lav/audio) sync result, PreCut's raw score made explicit
    about the one thing every downstream caller actually needs to branch
    on: did it clear the threshold. ``raw`` is PreCut's own ``SyncPair``
    (or ``None`` if the underlying library couldn't produce a result at
    all — missing file, library unavailable — see ``error``) for callers
    that want the full detail (audio_duration_sec, promoted_via_consistency,
    etc.)."""
    aroll_path: str
    lav_path: str
    offset_sec: Optional[float]
    score: Optional[float]
    passed_threshold: bool
    error: Optional[str]
    raw: Optional["SyncPair"]


@dataclass
class SyncResult:
    """Result of :func:`sync_pairs`: every (A-roll, lav) pair attempted."""
    pairs: List[PairSyncResult]
    score_threshold: float


def sync_pairs(
    aroll_paths: Iterable[Union[str, Path]],
    lav_paths: Iterable[Union[str, Path]],
) -> SyncResult:
    """Cross-correlate every (A-roll, lav) pair via PreCut's MFCC matcher.

    Returns every pair, each flagged with ``passed_threshold``. Never
    drops a pair, never decides what to do with one that scores below
    ``SCORE_USE`` — see module docstring.
    """
    results: List[PairSyncResult] = []
    for aroll_raw in aroll_paths:
        aroll_path = Path(aroll_raw)
        for lav_raw in lav_paths:
            lav_path = Path(lav_raw)
            if not aroll_path.exists():
                results.append(PairSyncResult(
                    aroll_path=str(aroll_path), lav_path=str(lav_path),
                    offset_sec=None, score=None, passed_threshold=False,
                    error=f"A-roll file not found: {aroll_path}", raw=None,
                ))
                continue
            if not lav_path.exists():
                results.append(PairSyncResult(
                    aroll_path=str(aroll_path), lav_path=str(lav_path),
                    offset_sec=None, score=None, passed_threshold=False,
                    error=f"lav/audio file not found: {lav_path}", raw=None,
                ))
                continue

            raw = sync_pair(aroll_path, lav_path)
            if raw is None:
                results.append(PairSyncResult(
                    aroll_path=str(aroll_path), lav_path=str(lav_path),
                    offset_sec=None, score=None, passed_threshold=False,
                    error="audio_offset_finder unavailable or both files "
                          "unreadable — see precut_pipeline.audio_sync.sync_pair",
                    raw=None,
                ))
                continue

            raw.aroll_file = str(aroll_path)
            results.append(PairSyncResult(
                aroll_path=str(aroll_path), lav_path=str(lav_path),
                offset_sec=raw.offset_sec, score=raw.score,
                passed_threshold=raw.score >= SCORE_USE,
                error=None, raw=raw,
            ))

    return SyncResult(pairs=results, score_threshold=SCORE_USE)


__all__ = [
    "SyncPair",
    "AudioSyncState",
    "sync_pair",
    "sync_project",
    "group_audio_files",
    "compute_source_hash",
    "SCORE_USE",
    "SCORE_MAYBE",
    "PairSyncResult",
    "SyncResult",
    "sync_pairs",
]
