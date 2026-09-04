"""Audio sync via BBC's audio-offset-finder.

Replaces the audalign-based implementation (audalign required scipy builds
from source on Python 3.12 which failed on macOS without a Fortran compiler).

audio-offset-finder uses MFCC cross-correlation via ffmpeg — a tool we
already require. Pure Python, no compilation, tested on macOS py3.8-3.12.
It returns a standard_score that indicates match prominence; scores >= 10
are near-certain matches, scores 5-10 are suggestive but unreliable.

This module exposes:
    * `sync_pair(aroll, audio)` → one (offset, score, duration) result
    * `sync_project(project)` → full project sync, cached in project.json
    * `group_audio_files(files)` → detects rollover-companion groupings
    * `find_covering_audio_for_phrase(...)` → per-phrase coverage lookup

Scoring threshold is 10.0 — verified on Pierce's real Celeste interview
footage where true matches scored 18-38 and false matches (lavs that
weren't rolling) scored 3-7. 10.0 gives a safe margin.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional


# Score thresholds — calibrated on Pierce's Celeste test footage.
# True matches there scored 18-38; false-positives scored 3-7. We use 10.0
# as the floor with a small margin above the highest observed false match.
SCORE_USE = 10.0
SCORE_MAYBE = 5.0


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SyncPair:
    """One (A-roll file, audio source file) sync result.

    The `offset_sec` is where the AUDIO file's t=0 lands in the A-roll's
    timeline. Negative = audio started recording BEFORE the A-roll (common
    case for lavs rolling before camera).

    To convert a time in the A-roll's timeline to a time in the audio file:
        audio_time = aroll_time - offset_sec
    (subtracts the offset — if audio started earlier, audio's clock is ahead)

    To find where the audio file's content sits in the A-roll timeline:
        audio_starts_at_aroll_t = offset_sec
        audio_ends_at_aroll_t = offset_sec + audio_duration_sec
    """
    aroll_file: str            # absolute path to A-roll ORIGINAL (not proxy)
    aroll_proxy: str           # absolute path to A-roll proxy we actually synced
    audio_file: str            # absolute path to the audio source file
    offset_sec: float
    score: float               # audio-offset-finder standard_score
    audio_duration_sec: float  # duration of the audio file (for coverage math)
    computed_at: float
    # Drop 4.47.4: cross-validation. When the raw score is below
    # SCORE_USE but the offset is consistent with strong matches on
    # the same clip or audio file (offset-difference agreement across
    # paired files), we promote the pair via this flag rather than
    # mutating the score. Lets the UI keep showing the original score
    # while is_reliable returns True.
    promoted_via_consistency: bool = False
    # 2026-09-03: the windowed coverage rescue (posthouse/sync_coverage.py,
    # run automatically for every unreliable pair) is expensive -- N
    # windowed correlations vs one whole-file pass. Ryan hit this for
    # real: clicking "Organize" again re-ran the rescue for all 42 weak
    # pairs from scratch, unconditionally, every time, even though
    # sync_project()'s own reliability score for a pair never changes
    # run to run (it's deterministic). Set True the first time a rescue
    # is ATTEMPTED for this pair (success or not) so a later run can
    # reuse that outcome instead of re-scanning -- distinct from
    # promoted_via_consistency, which is only true on a SUCCESSFUL
    # rescue; this tracks "already tried," including failed attempts,
    # so those don't get retried forever either.
    rescue_attempted: bool = False

    @property
    def is_reliable(self) -> bool:
        return self.score >= SCORE_USE or self.promoted_via_consistency


@dataclass
class TrackGroup:
    """A set of audio files that represent rollovers of the same physical mic.

    Files grouped together will be placed on the same Premiere audio track
    at export time, with Premiere handling the transition between them as
    adjacent clipitems on the same track (no track switching).

    Files in a group must:
      - Share a common filename prefix up to the last timestamp-looking token
      - Have time-adjacent coverage (file B starts within ±60s of file A's
        end, computed from offset + audio_duration)
    """
    group_id: str                    # stable identifier, e.g. "DJI_06"
    display_name: str                # human-readable, e.g. "DJI 06"
    audio_files: list[str]           # sorted by recording start time

    def to_dict(self) -> dict:
        return {
            "group_id": self.group_id,
            "display_name": self.display_name,
            "audio_files": list(self.audio_files),
        }


@dataclass
class AudioSyncState:
    """Full sync state for a project. Persisted in project.json."""
    pairs: list[SyncPair] = field(default_factory=list)
    groups: list[TrackGroup] = field(default_factory=list)
    source_hash: str = ""              # for cache invalidation
    computed_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "pairs": [asdict(p) for p in self.pairs],
            "groups": [g.to_dict() for g in self.groups],
            "source_hash": self.source_hash,
            "computed_at": self.computed_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AudioSyncState":
        return cls(
            pairs=[SyncPair(**p) for p in d.get("pairs", [])],
            groups=[TrackGroup(**g) for g in d.get("groups", [])],
            source_hash=d.get("source_hash", ""),
            computed_at=d.get("computed_at", 0.0),
        )


# ---------------------------------------------------------------------------
# Cache key computation
# ---------------------------------------------------------------------------

def compute_source_hash(
    aroll_files: list[tuple[str, str]],   # [(original_path, proxy_path), ...]
    audio_files: list[str],
) -> str:
    """Hash of the file set + mtimes — changes on add/remove/edit.

    We hash PATHS and MTIMES rather than contents. Path changes (rename/
    move) invalidate the cache even if contents didn't change, which is
    correct because sync is path-sensitive.
    """
    h = hashlib.sha256()
    for orig, proxy in sorted(aroll_files):
        h.update(orig.encode("utf-8"))
        h.update(b"\x00")
        h.update(proxy.encode("utf-8"))
        h.update(b"\x00")
        try:
            h.update(str(Path(proxy).stat().st_mtime).encode("utf-8"))
        except OSError:
            h.update(b"missing")
        h.update(b"|")
    h.update(b"|audio|")
    for audio in sorted(audio_files):
        h.update(audio.encode("utf-8"))
        h.update(b"\x00")
        try:
            h.update(str(Path(audio).stat().st_mtime).encode("utf-8"))
        except OSError:
            h.update(b"missing")
        h.update(b"|")
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Core alignment
# ---------------------------------------------------------------------------

def _load_finder():
    """Import audio-offset-finder, attempting pip install if missing.

    On first run after a fresh install, the package is present from
    requirements.txt. For users updating from older drops (where the
    requirement was audalign), we auto-install rather than failing.
    """
    try:
        from audio_offset_finder.audio_offset_finder import find_offset_between_files
        return find_offset_between_files
    except ImportError:
        pass

    # Attempt install. Uses --user and --break-system-packages to match
    # install.sh's behavior on macOS 14+.
    import subprocess as _sp
    try:
        _sp.run(
            [__import__("sys").executable, "-m", "pip", "install",
             "--user", "--break-system-packages", "--quiet",
             "audio-offset-finder"],
            check=True,
            capture_output=True,
            timeout=120,
        )
    except Exception:
        return None
    try:
        from audio_offset_finder.audio_offset_finder import find_offset_between_files
        return find_offset_between_files
    except ImportError:
        return None


def sync_pair(aroll_proxy: Path, audio_file: Path) -> Optional[SyncPair]:
    """Run audio-offset-finder on a single (A-roll, audio) pair.

    Returns None if the library is missing or both files couldn't be read.
    Returns a SyncPair on success (with whatever score the algorithm found
    — caller decides whether to use it based on SCORE_USE threshold).
    """
    find_offset_between_files = _load_finder()
    if find_offset_between_files is None:
        return None

    if not aroll_proxy.exists() or not audio_file.exists():
        return None

    try:
        # No trim — scan full files. Verified ~10s per pair on real footage.
        result = find_offset_between_files(str(aroll_proxy), str(audio_file))
    except Exception:
        return None

    offset = float(result.get("time_offset", 0.0))
    score = float(result.get("standard_score", 0.0))

    audio_dur = _probe_audio_duration(audio_file) or 0.0

    return SyncPair(
        aroll_file="",          # filled in by caller (proxy → original mapping)
        aroll_proxy=str(aroll_proxy),
        audio_file=str(audio_file),
        offset_sec=offset,
        score=score,
        audio_duration_sec=audio_dur,
        computed_at=time.time(),
    )


def _probe_audio_duration(path: Path) -> Optional[float]:
    """Use ffprobe to get the audio duration in seconds. None on error."""
    try:
        from audio_indexer import find_ffprobe, probe_audio
    except ImportError:
        return None
    ffprobe_bin = find_ffprobe()
    if not ffprobe_bin:
        return None
    try:
        info = probe_audio(path, ffprobe_bin)
        return info.duration_sec if info else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Track grouping (rollover detection)
# ---------------------------------------------------------------------------

def _extract_group_prefix(filename: str) -> Optional[str]:
    """Given 'DJI_06_20260416_074120.WAV', return 'DJI_06'.

    Strips the file extension, then repeatedly strips trailing _DIGITS
    tokens (at least 6 digits to avoid false positives on short IDs).
    Returns None if the result is empty.
    """
    stem = Path(filename).stem
    while True:
        m = re.match(r"^(.+)_(\d{6,8})$", stem)
        if m is None:
            break
        stem = m.group(1)
    return stem if stem else None


def _safe_group_id(name: str) -> str:
    """Sanitize a filename into something usable as a dict key."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def _earliest_real_time(audio_file: str, pairs: list[SyncPair]) -> float:
    """For sorting: what's the 'real time' t=0 of this audio file in an
    A-roll's timeline?

    Returns the minimum offset_sec across reliable pairs. offset_sec is
    WHERE this audio's t=0 lands in A-roll time — more negative = earlier
    in real time.
    """
    relevant = [p for p in pairs if p.audio_file == audio_file and p.is_reliable]
    if not relevant:
        return 0.0
    return min(p.offset_sec for p in relevant)


def _verify_adjacent(audio_files: list[str], pairs: list[SyncPair]) -> bool:
    """Check that a set of audio files describe adjacent coverage windows.

    Uses the sync results to compute each audio file's coverage window in
    a common A-roll's timeline. If adjacent (gap within ±60s), files are
    rollover companions; otherwise they're separate recordings and should
    each get their own track.
    """
    if len(audio_files) <= 1:
        return True

    # Try to find an A-roll covered by all files in the set
    by_aroll: dict[str, dict[str, tuple[float, float, float]]] = {}
    for p in pairs:
        if p.audio_file not in audio_files:
            continue
        # Drop 4.47.4: include consistency-promoted pairs alongside
        # native strong pairs. Both indicate the same thing — the
        # audio file's coverage window in the A-roll timeline is known.
        if not p.is_reliable:
            continue
        # Audio coverage in A-roll timeline: audio starts at A-roll time
        # offset_sec and runs for audio_duration_sec
        start = p.offset_sec
        end = start + p.audio_duration_sec
        by_aroll.setdefault(p.aroll_proxy, {})[p.audio_file] = (start, end, p.score)

    for aroll, coverage in by_aroll.items():
        if set(coverage.keys()) == set(audio_files):
            sorted_files = sorted(coverage.items(), key=lambda kv: kv[1][0])
            for i in range(len(sorted_files) - 1):
                _, (s1, e1, _) = sorted_files[i]
                _, (s2, e2, _) = sorted_files[i + 1]
                gap = s2 - e1
                if gap > 60 or gap < -60:
                    return False
            return True

    # Transitive fallback: pairs of files adjacent via shared A-rolls
    ordered = sorted(audio_files, key=lambda f: _earliest_real_time(f, pairs))
    for i in range(len(ordered) - 1):
        a, b = ordered[i], ordered[i + 1]
        if not _pairwise_adjacent(a, b, pairs):
            return False
    return True


def _pairwise_adjacent(file_a: str, file_b: str, pairs: list[SyncPair]) -> bool:
    """Check adjacency using any A-roll that's covered by BOTH files."""
    # Drop 4.47.4: use is_reliable so consistency-promoted pairs count.
    a_pairs = {p.aroll_proxy: p for p in pairs
               if p.audio_file == file_a and p.is_reliable}
    b_pairs = {p.aroll_proxy: p for p in pairs
               if p.audio_file == file_b and p.is_reliable}
    shared = set(a_pairs.keys()) & set(b_pairs.keys())
    if not shared:
        return False
    for aroll in shared:
        a = a_pairs[aroll]
        b = b_pairs[aroll]
        a_start = a.offset_sec
        a_end = a_start + a.audio_duration_sec
        b_start = b.offset_sec
        gap = b_start - a_end
        if abs(gap) <= 60:
            return True
    return False


def group_audio_files(
    audio_files: list[str],
    pairs: list[SyncPair],
) -> list[TrackGroup]:
    """Group audio files by physical mic (rollover-companion files share a group).

    Two files are grouped together if:
      1. Their filename prefixes (with trailing timestamps stripped) match
      2. Their coverage windows are time-adjacent (verified via sync data)

    Falls back to singleton groups if either check fails.
    """
    if not audio_files:
        return []

    prefix_buckets: dict[str, list[str]] = {}
    for af in audio_files:
        prefix = _extract_group_prefix(Path(af).name)
        if prefix:
            prefix_buckets.setdefault(prefix, []).append(af)
        else:
            prefix_buckets.setdefault(f"__solo__{Path(af).name}", []).append(af)

    groups: list[TrackGroup] = []

    for prefix, files in prefix_buckets.items():
        if prefix.startswith("__solo__") or len(files) == 1:
            for f in files:
                groups.append(TrackGroup(
                    group_id=_safe_group_id(Path(f).name),
                    display_name=Path(f).stem,
                    audio_files=[f],
                ))
            continue

        if _verify_adjacent(files, pairs):
            sorted_files = sorted(files, key=lambda f: _earliest_real_time(f, pairs))
            groups.append(TrackGroup(
                group_id=prefix,
                display_name=prefix,
                audio_files=sorted_files,
            ))
        else:
            for f in files:
                groups.append(TrackGroup(
                    group_id=_safe_group_id(Path(f).name),
                    display_name=Path(f).stem,
                    audio_files=[f],
                ))

    groups.sort(key=lambda g: g.display_name)
    return groups


# ---------------------------------------------------------------------------
# Per-phrase coverage lookup (used at export time)
# ---------------------------------------------------------------------------

@dataclass
class CoveringAudio:
    """Which audio file covers a phrase, and where in that audio file the
    phrase's time range lands.

    audio_start/end are in the audio FILE's own timeline, ready to write
    as in/out points in the FCP7 XML.

    Drop 4.22: aroll_start/end record the A-roll-timeline window of the
    overlap between the phrase and the audio file's coverage. With partial
    coverage these are a subset of the phrase's full range, and the
    exporter uses them to place the clipitem at the correct offset on
    the timeline (not just at phrase.timeline_start).
    """
    audio_file: str
    group_id: str                  # determines track slot (A2/A3/...)
    audio_start_sec: float
    audio_end_sec: float
    score: float
    # Defaulted for back-compat with any callers that instantiate directly.
    # The coverage-finder always fills these in.
    aroll_start_sec: float = 0.0
    aroll_end_sec: float = 0.0


def find_covering_audio_for_phrase(
    aroll_original_path: str,
    phrase_source_start: float,
    phrase_source_end: float,
    state: AudioSyncState,
) -> list[CoveringAudio]:
    """For a single A-roll phrase slice, find which audio files cover it.

    Coverage criteria (Drop 4.22):
      1. Pair score >= SCORE_USE for this (A-roll, audio) combination
      2. The audio file's coverage window OVERLAPS with the phrase's
         source window by at least 100ms. Partial coverage is accepted
         and the resulting CoveringAudio is clamped to the intersection.

    Prior drops required FULL coverage (audio spans the entire phrase),
    which filtered out lav recordings that rolled over mid-interview —
    common with long takes where the recorder swaps files. With partial
    coverage the editor still gets the audio for whichever portion the
    lav was actually rolling.
    """
    file_to_group: dict[str, str] = {}
    for g in state.groups:
        for f in g.audio_files:
            file_to_group[f] = g.group_id

    covering: list[CoveringAudio] = []

    for p in state.pairs:
        if p.aroll_file != aroll_original_path:
            continue
        # Drop 4.47.4: include promoted pairs. is_reliable returns True
        # for native strong matches AND for weak matches that were
        # cross-validated against strong anchors.
        if not p.is_reliable:
            continue

        # Audio file's coverage in A-roll timeline: starts at offset_sec,
        # runs for audio_duration_sec
        audio_coverage_start = p.offset_sec
        audio_coverage_end = audio_coverage_start + p.audio_duration_sec

        # Intersection of [phrase] and [audio coverage] — both in A-roll time
        overlap_start = max(phrase_source_start, audio_coverage_start)
        overlap_end = min(phrase_source_end, audio_coverage_end)
        if overlap_end - overlap_start < 0.1:  # < 100ms is noise
            continue

        # Convert A-roll time to audio-file time (audio_time = aroll_time - offset)
        audio_start = overlap_start - p.offset_sec
        audio_end = overlap_end - p.offset_sec

        covering.append(CoveringAudio(
            audio_file=p.audio_file,
            group_id=file_to_group.get(p.audio_file, _safe_group_id(Path(p.audio_file).name)),
            audio_start_sec=audio_start,
            audio_end_sec=audio_end,
            score=p.score,
            aroll_start_sec=overlap_start,
            aroll_end_sec=overlap_end,
        ))

    covering.sort(key=lambda c: -c.score)
    return covering


# ---------------------------------------------------------------------------
# Cross-validation: promoting weak matches via offset-difference consistency
# ---------------------------------------------------------------------------
#
# Background. When two mics record the same physical session, their offsets
# relative to any A-roll clip differ by a fixed delta (the time between when
# the two mics started recording). That delta is a property of the recording
# session, not of any particular video clip.
#
# So if clip A2 strongly matches mic M1 at offset O_A2_M1 and mic M2 at
# offset O_A2_M2, the difference D = O_A2_M2 - O_A2_M1 is established with
# high confidence. ANY other A-roll clip that ALSO matched both mics should
# show the SAME difference, even if its individual scores are weak.
#
# Concrete example from the user's screenshot (Drop 4.47.4 motivation):
#   Clip 0002 + mic_055422 → offset -1405.1s, score 22.6 (strong)
#   Clip 0002 + mic_055517 → offset -1387.1s, score 23.9 (strong)
#                                              D = +18.0s
#   Clip 0001 + mic_055422 → offset  -63.9s, score  7.5 (weak)
#   Clip 0001 + mic_055517 → offset  -45.9s, score  5.2 (weak)
#                                              D = +18.0s   ✓ matches
#
# Both clips agree on the 18s difference, so the weak matches on clip 0001
# are confirmed by the strong matches on clip 0002. We promote them.

# Tolerance for offset-difference equality. audio-offset-finder is typically
# accurate to <100ms when it locks on; 1.0s gives generous headroom for
# minor clock drift while still rejecting random coincidences.
CONSISTENCY_TOL_SEC = 1.0


def _promote_consistent_pairs(pairs: list["SyncPair"]) -> int:
    """In-place: flip promoted_via_consistency on weak pairs whose offsets
    are consistent with strong matches via the offset-difference invariant.

    Returns the count of pairs newly promoted, for logging.

    Algorithm:
      1. For every clip A_i, compute the set of (mic_j, mic_k) pairs where
         BOTH (A_i, M_j) and (A_i, M_k) have score >= SCORE_MAYBE.
      2. For every such (A_i, M_j, M_k), compute D = O_ij - O_ik.
      3. Group all such D values by (M_j, M_k). If at least one observation
         of D is from a STRONG pair (both ends score >= SCORE_USE), the
         consensus D is established for that mic pair. Use the median of
         strong observations as the canonical value.
      4. For any clip where (A_i, M_j) is WEAK (score in [MAYBE, USE)) but
         the offset-difference vs some other (A_i, M_k) agrees with the
         canonical D for (M_j, M_k) within tolerance, promote (A_i, M_j).
         M_k itself can be either strong OR weak in clip A_i — the
         agreement is what matters.

    Conservative choices:
      - We never promote pairs whose raw score is below SCORE_MAYBE.
      - We never promote unless there's at least one externally-anchored
        strong observation of the relevant mic-pair difference.
      - We never mutate the score field — the original score remains
        visible in the UI (with the SyncMatrix tooltip explaining why a
        weak-scored pair is considered reliable).
    """
    if not pairs:
        return 0

    # Index pairs by (aroll_file, audio_file) for fast lookup.
    by_clip_and_mic: dict[tuple[str, str], "SyncPair"] = {}
    for p in pairs:
        by_clip_and_mic[(p.aroll_file, p.audio_file)] = p

    # Group pairs by clip so we can look at intra-clip mic combinations.
    by_clip: dict[str, list["SyncPair"]] = {}
    for p in pairs:
        by_clip.setdefault(p.aroll_file, []).append(p)

    # Step 1+2+3: collect strong-anchored deltas per (mic_a, mic_b).
    # Key the dict on a sorted tuple so (M_a, M_b) and (M_b, M_a) both
    # land at the same entry. We store the sign-corrected delta D such
    # that D = O[clip, sorted_b] - O[clip, sorted_a].
    from statistics import median

    strong_deltas: dict[tuple[str, str], list[float]] = {}
    for clip_path, ps in by_clip.items():
        # All pairs of pairs within the same clip
        for i in range(len(ps)):
            for j in range(i + 1, len(ps)):
                p_a, p_b = ps[i], ps[j]
                # Both ends must be STRONG to anchor a delta
                if p_a.score < SCORE_USE or p_b.score < SCORE_USE:
                    continue
                # Canonicalize order so (a,b) and (b,a) merge
                if p_a.audio_file < p_b.audio_file:
                    key = (p_a.audio_file, p_b.audio_file)
                    delta = p_b.offset_sec - p_a.offset_sec
                else:
                    key = (p_b.audio_file, p_a.audio_file)
                    delta = p_a.offset_sec - p_b.offset_sec
                strong_deltas.setdefault(key, []).append(delta)

    if not strong_deltas:
        return 0  # No anchors available; nothing we can do.

    # Canonical delta per mic-pair: median of strong observations.
    # Median (over mean) defends against an outlier strong match that
    # locked onto a different region of the audio.
    canonical: dict[tuple[str, str], float] = {
        k: median(v) for k, v in strong_deltas.items()
    }

    # Step 4: walk each clip again, promote weak pairs when consistent.
    promoted = 0
    for clip_path, ps in by_clip.items():
        # Find weak candidates and possible companions in the same clip.
        # A "weak" pair is in [SCORE_MAYBE, SCORE_USE). A "companion" is
        # any OTHER pair on the same clip with score >= SCORE_MAYBE.
        for p_weak in ps:
            if p_weak.promoted_via_consistency:
                continue  # already done
            if p_weak.score >= SCORE_USE:
                continue  # already reliable
            if p_weak.score < SCORE_MAYBE:
                continue  # too weak to promote even with anchors

            for p_other in ps:
                if p_other is p_weak:
                    continue
                if p_other.score < SCORE_MAYBE:
                    continue

                # Look up the canonical delta for this mic-pair
                if p_weak.audio_file < p_other.audio_file:
                    key = (p_weak.audio_file, p_other.audio_file)
                    observed = p_other.offset_sec - p_weak.offset_sec
                else:
                    key = (p_other.audio_file, p_weak.audio_file)
                    observed = p_weak.offset_sec - p_other.offset_sec

                anchor = canonical.get(key)
                if anchor is None:
                    continue  # no strong evidence for this mic-pair

                if abs(observed - anchor) <= CONSISTENCY_TOL_SEC:
                    p_weak.promoted_via_consistency = True
                    promoted += 1
                    break  # one consistent companion is enough

    return promoted


# ---------------------------------------------------------------------------
# Full project sync (batch operation)
# ---------------------------------------------------------------------------

def sync_project(project, emit_progress=None) -> AudioSyncState:
    """Run full audio sync for a project.

    Walks all A-roll sources (using their proxy files for speed) × all
    audio sources. Computes offsets, filters by score, builds track groups.

    Checks cache via source_hash; if unchanged since last run, returns the
    cached state unchanged.

    Progress events (if emit_progress is given):
      {"type": "audio_sync_started", "total_pairs": N}
      {"type": "audio_sync_pair", "i": i, "total": N, "aroll": ..., "audio": ..., "score": ..., "offset": ...}
      {"type": "audio_sync_done", "reliable_pairs": N}
    """
    def emit(ev):
        if emit_progress is not None:
            try:
                emit_progress(ev)
            except Exception:
                pass

    aroll_entries: list[tuple[str, str]] = []
    for src in project.sources_by_kind("aroll"):
        for file_path, info in src.files.items():
            proxy_path = info.get("proxy_path")
            if not proxy_path or not Path(proxy_path).exists():
                continue
            try:
                if Path(proxy_path).stat().st_size < 500_000:
                    continue
            except OSError:
                continue
            aroll_entries.append((file_path, proxy_path))

    audio_files: list[str] = []
    for src in project.sources_by_kind("audio"):
        for file_path in src.files.keys():
            if Path(file_path).exists():
                audio_files.append(file_path)

    new_hash = compute_source_hash(aroll_entries, audio_files)

    # Cache check
    existing = getattr(project, "audio_sync", None)
    if existing and existing.get("source_hash") == new_hash:
        try:
            cached = AudioSyncState.from_dict(existing)
            emit({"type": "audio_sync_cached", "pair_count": len(cached.pairs)})
            return cached
        except Exception:
            pass

    if not aroll_entries or not audio_files:
        emit({"type": "audio_sync_done", "reliable_pairs": 0, "total": 0,
              "reason": "no aroll or audio sources"})
        return AudioSyncState(source_hash=new_hash, computed_at=time.time())

    total = len(aroll_entries) * len(audio_files)
    emit({"type": "audio_sync_started", "total_pairs": total})

    # Warm-up: trigger a one-time library load (auto-installs if missing).
    # This can take 30-90s on first run; surface that to the UI so it
    # doesn't look stuck.
    if _load_finder() is None:
        emit({"type": "audio_sync_done", "reliable_pairs": 0, "total": 0,
              "reason": "audio-offset-finder unavailable; install failed"})
        return AudioSyncState(source_hash=new_hash, computed_at=time.time())

    pairs: list[SyncPair] = []
    i = 0
    for aroll_orig, aroll_proxy in aroll_entries:
        for audio in audio_files:
            i += 1
            result = sync_pair(Path(aroll_proxy), Path(audio))
            if result is None:
                emit({"type": "audio_sync_pair", "i": i, "total": total,
                      "aroll": Path(aroll_orig).name,
                      "audio": Path(audio).name,
                      "error": "sync_pair returned None"})
                continue
            result.aroll_file = aroll_orig
            pairs.append(result)
            emit({"type": "audio_sync_pair", "i": i, "total": total,
                  "aroll": Path(aroll_orig).name,
                  "audio": Path(audio).name,
                  "score": round(result.score, 2),
                  "offset": round(result.offset_sec, 2),
                  "reliable": result.is_reliable})

    groups = group_audio_files(audio_files, pairs)

    # Drop 4.47.4: promote weak-score pairs whose offsets are consistent
    # with strong matches via the offset-difference invariant. See
    # _promote_consistent_pairs() for the algorithm. Mutates pairs in
    # place — sets promoted_via_consistency=True on qualifying entries.
    promoted_count = _promote_consistent_pairs(pairs)
    if promoted_count:
        emit({"type": "audio_sync_promoted",
              "count": promoted_count,
              "via": "offset-difference consistency"})

    state = AudioSyncState(
        pairs=pairs,
        groups=groups,
        source_hash=new_hash,
        computed_at=time.time(),
    )
    reliable = sum(1 for p in pairs if p.is_reliable)
    emit({"type": "audio_sync_done",
          "reliable_pairs": reliable,
          "total": len(pairs),
          "group_count": len(groups),
          "promoted_via_consistency": promoted_count})
    return state
