"""posthouse.sync_coverage — windowed sync recovery for pairs PreCut's own
whole-file correlation can't confidently sync.

**Not a harvest wrapper — genuinely new capability.** PreCut's
``precut_pipeline.audio_sync.sync_pair`` (re-exported unchanged by
``posthouse.harvest.sync``) runs ONE cross-correlation over the entire
A-roll proxy against the entire audio file and returns a single score.
That's fine when a lav mic stays in the room the whole time. It breaks
down on a real interview/walkthrough shoot where the subject leaves the
room, comes back, or talks on the phone somewhere else: long stretches
with real audio ENERGY (the phone call) that has nothing acoustically to
do with what the camera hears, mixed in with genuinely correlated
stretches. A single global correlation over the whole mixed signal gets
diluted or defeated, even when a real, constant clock offset holds
throughout every usable stretch.

**Confirmed on real footage, not assumed** (2026-09-03, Runnells Day 1,
``benchmark/runnells-day-1``): PreCut's own ``sync_project()`` scored
(clip ``DJI_20260430075045_0006_D.MP4``, lav
``DJI_02_20260430_044337.WAV``) at 3.15 — noise level, below even the
"maybe" floor of 5.0, meaning don't trust this pair at all. A 30-second-
window scan of the same two files found four windows (420-540s into the
lav file) that all agree on an offset of -308.6s with scores 8.7-18.6
(three of them well above the STRONG threshold of 10.0) — a real, precise
sync point invisible to the whole-file pass. The other lav on the same
clip showed the identical pattern (six windows from 330s onward,
converging on -306.8s, scores up to 31.0). Exact commands and full output
are in ``docs/STATUS.md``'s dated entry.

**Deliberately opt-in, not part of the default pipeline.** N ffmpeg
extracts + N correlation calls per pair is far more expensive than
PreCut's one whole-file pass (documented at ~10s/pair). This is the
Assistant Editor's tool for rescuing a specific pair the matrix already
flagged as weak/unreliable — triggered on demand, never run automatically
for every pair on every ``run_pipeline`` call.

**What this returns, and what it doesn't.** Per Ryan's own scoping
(2026-09-03): coverage information only. It reports ``usable_ranges`` (in
the A-roll's own timeline) and an ``accepted_offset_sec`` for reference,
but it never rewrites PreCut's own persisted ``SyncPair.offset_sec`` /
``score`` — those stay exactly what ``sync_project()`` computed. A human
decides what to do with a rescued pair; this module's job stops at
telling the truth about which stretches are trustworthy.
"""
from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import List, Optional, Tuple

from posthouse.harvest.sync import sync_pair, SCORE_MAYBE, SCORE_USE

DEFAULT_WINDOW_SEC = 30.0
DEFAULT_HOP_SEC = 30.0
DEFAULT_CONSENSUS_TOL_SEC = 1.5
DEFAULT_SILENCE_DB = -35.0
DEFAULT_MIN_SILENCE_SEC = 2.0
DEFAULT_MIN_WINDOW_SEC = 8.0
# Real cost, measured (2026-09-03): correlating even an 18s clip against
# a 1992s (33min) A-roll proxy took 7.3s -- PreCut's own sync_pair() cost
# is driven by the A-ROLL's length, not the window's, since the window is
# always the shorter signal in the cross-correlation. A long lav file's
# non-silent stretches can produce 50-60+ candidate windows against a
# long A-roll, which is 6-8+ minutes for ONE pair with no feedback and no
# way to cancel -- exactly what happened to Ryan (clicked 3, waited 5
# minutes, "they still just say analyzing"). This caps it.
DEFAULT_MAX_WINDOWS = 10


@dataclass
class CoverageWindow:
    """One candidate window's own, independently-computed correlation."""
    lav_start: float
    lav_end: float
    score: float
    implied_offset_sec: float  # if the WHOLE audio file started at t=0 here
    agrees_with_consensus: bool = False


@dataclass
class PairCoverage:
    aroll_proxy: str
    audio_file: str
    accepted_offset_sec: Optional[float]
    usable_ranges: List[Tuple[float, float]] = field(default_factory=list)
    windows: List[CoverageWindow] = field(default_factory=list)
    windows_tried: int = 0
    windows_used: int = 0
    windows_available: int = 0  # before max_windows subsampling

    def to_dict(self) -> dict:
        return {
            "aroll_proxy": self.aroll_proxy,
            "audio_file": self.audio_file,
            "accepted_offset_sec": self.accepted_offset_sec,
            "usable_ranges": [list(r) for r in self.usable_ranges],
            "windows_tried": self.windows_tried,
            "windows_used": self.windows_used,
            "windows_available": self.windows_available,
            "windows": [
                {
                    "lav_start": w.lav_start, "lav_end": w.lav_end,
                    "score": w.score, "implied_offset_sec": w.implied_offset_sec,
                    "agrees_with_consensus": w.agrees_with_consensus,
                }
                for w in self.windows
            ],
        }


def _probe_duration(path: Path) -> Optional[float]:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return None


def find_energetic_ranges(
    audio_path: Path,
    *,
    noise_db: float = DEFAULT_SILENCE_DB,
    min_silence_sec: float = DEFAULT_MIN_SILENCE_SEC,
) -> List[Tuple[float, float]]:
    """Non-silent stretches of `audio_path`, via one ffmpeg silencedetect
    pass over the whole file -- the cheap gate that decides which
    stretches are worth the expensive per-window correlation. Silence
    here means "no signal at all" (someone left the room, or a genuine
    gap) -- it does NOT mean "correlates with the camera," which is
    exactly why a person talking on the phone elsewhere still shows up as
    non-silent and still gets tried (and correctly scored low on its own
    merits by the correlation step, not filtered out here).
    """
    duration = _probe_duration(audio_path)
    if duration is None:
        return []
    proc = subprocess.run(
        ["ffmpeg", "-i", str(audio_path), "-af",
         f"silencedetect=noise={noise_db}dB:d={min_silence_sec}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    silences: List[Tuple[float, float]] = []
    start: Optional[float] = None
    for line in proc.stderr.splitlines():
        line = line.strip()
        if "silence_start:" in line:
            try:
                start = float(line.split("silence_start:")[1].strip().split()[0])
            except (IndexError, ValueError):
                start = None
        elif "silence_end:" in line and start is not None:
            try:
                end = float(line.split("silence_end:")[1].split("|")[0].strip())
            except (IndexError, ValueError):
                continue
            silences.append((start, end))
            start = None

    ranges: List[Tuple[float, float]] = []
    cursor = 0.0
    for s, e in sorted(silences):
        if s > cursor:
            ranges.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < duration:
        ranges.append((cursor, duration))
    return ranges


def _split_into_windows(
    ranges: List[Tuple[float, float]],
    window_sec: float,
    hop_sec: float,
    min_window_sec: float = DEFAULT_MIN_WINDOW_SEC,
) -> List[Tuple[float, float]]:
    windows: List[Tuple[float, float]] = []
    for r_start, r_end in ranges:
        t = r_start
        while t < r_end:
            w_end = min(t + window_sec, r_end)
            if w_end - t >= min_window_sec:
                windows.append((t, w_end))
            t += hop_sec
    return windows


def _extract_clip(src: Path, start: float, dur: float, out_path: Path) -> bool:
    proc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", str(start), "-t", str(dur),
         "-i", str(src), "-ac", "1", "-ar", "16000", str(out_path)],
        capture_output=True,
    )
    return proc.returncode == 0 and out_path.exists()


def _subsample_evenly(items: list, k: int) -> list:
    """Pick k items spread across the whole list, preserving order --
    used to cap window count without biasing toward the start of the
    file (a plain truncation would silently only ever look at the first
    few minutes, missing exactly the "comes back later" case this module
    exists for)."""
    n = len(items)
    if n <= k:
        return items
    return [items[round(i * (n - 1) / (k - 1))] for i in range(k)]


def analyze_pair_coverage(
    aroll_proxy: Path,
    audio_file: Path,
    *,
    window_sec: float = DEFAULT_WINDOW_SEC,
    hop_sec: float = DEFAULT_HOP_SEC,
    consensus_tol_sec: float = DEFAULT_CONSENSUS_TOL_SEC,
    min_window_sec: Optional[float] = None,
    max_windows: int = DEFAULT_MAX_WINDOWS,
    progress_callback=None,
    cancel_flag=None,
) -> PairCoverage:
    """Find sync coverage for one (A-roll, audio) pair by windowed
    correlation. See module docstring for why, and for the real numbers
    this was verified against.

    Never trusts a single window: at least two windows must independently
    agree (within `consensus_tol_sec`) on the same implied whole-file
    offset before either is accepted, mirroring the anchoring discipline
    `precut_pipeline.audio_sync._promote_consistent_pairs` already uses
    (never promote on one observation).

    `progress_callback(dict)`, if given, is called after each window
    finishes (`{"i", "total", "score"}`) -- correlation cost is driven by
    the A-ROLL's length (measured: 7.3s for an 18s clip against a 33min
    proxy), so a long pair can take real time and a caller needs a way to
    show it's progressing, not just "started."

    `cancel_flag` (a `threading.Event`-like object with `.is_set()`), if
    given, is checked between windows -- lets a caller abort a slow run
    instead of leaving a user stuck waiting with no way out, which is
    exactly what happened without this (see DEFAULT_MAX_WINDOWS's own
    comment).
    """
    effective_min = min(min_window_sec if min_window_sec is not None else DEFAULT_MIN_WINDOW_SEC, window_sec)
    ranges = find_energetic_ranges(audio_file)
    all_windows = _split_into_windows(ranges, window_sec, hop_sec, min_window_sec=effective_min)
    candidate_windows = _subsample_evenly(all_windows, max_windows)

    scored: List[CoverageWindow] = []
    total = len(candidate_windows)
    with tempfile.TemporaryDirectory(prefix="posthouse_sync_coverage_") as tmpdir:
        tmp = Path(tmpdir)
        for i, (w_start, w_end) in enumerate(candidate_windows):
            if cancel_flag is not None and cancel_flag.is_set():
                break
            clip_path = tmp / f"w_{i}.wav"
            score = None
            if _extract_clip(audio_file, w_start, w_end - w_start, clip_path):
                result = sync_pair(aroll_proxy, clip_path)
                if result is not None:
                    score = result.score
                    implied_offset = result.offset_sec - w_start
                    scored.append(CoverageWindow(
                        lav_start=w_start, lav_end=w_end,
                        score=result.score, implied_offset_sec=implied_offset,
                    ))
            if progress_callback is not None:
                progress_callback({"i": i + 1, "total": total, "score": score})

    candidates = [w for w in scored if w.score >= SCORE_MAYBE]
    accepted_offset: Optional[float] = None
    if candidates:
        best_cluster: List[CoverageWindow] = []
        for seed in candidates:
            cluster = [
                w for w in candidates
                if abs(w.implied_offset_sec - seed.implied_offset_sec) <= consensus_tol_sec
            ]
            if len(cluster) > len(best_cluster):
                best_cluster = cluster
        if len(best_cluster) >= 2:  # one observation is never enough to trust
            accepted_offset = median(w.implied_offset_sec for w in best_cluster)
            agreeing_ids = {id(w) for w in best_cluster}
            for w in scored:
                if id(w) in agreeing_ids:
                    w.agrees_with_consensus = True

    usable_ranges: List[Tuple[float, float]] = []
    if accepted_offset is not None:
        aroll_ranges = sorted(
            (w.lav_start + accepted_offset, w.lav_end + accepted_offset)
            for w in scored if w.agrees_with_consensus
        )
        for start, end in aroll_ranges:
            if usable_ranges and start <= usable_ranges[-1][1] + hop_sec:
                usable_ranges[-1] = (usable_ranges[-1][0], max(usable_ranges[-1][1], end))
            else:
                usable_ranges.append((start, end))

    return PairCoverage(
        aroll_proxy=str(aroll_proxy),
        audio_file=str(audio_file),
        accepted_offset_sec=accepted_offset,
        usable_ranges=usable_ranges,
        windows=scored,
        windows_tried=len(candidate_windows),
        windows_used=sum(1 for w in scored if w.agrees_with_consensus),
        windows_available=len(all_windows),
    )
