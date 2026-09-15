"""posthouse.final_review — did Ryan's finished edit match what the app found?

Built 2026-09-15, Ryan: "would it make sense to add a section to upload the
final edited videos for each project so that the app can analyze the final
product and see how its ideas were implemented, what it may have missed, and
learn how to do a better job in the future?" Agreed scope for THIS slice
(his "sure" to the cheap-version proposal): a project convention plus a
diff report a human reads. It does NOT feed back into planning automatically
— that is a separate, much bigger, not-yet-approved step per rule 7 (prove
on one real unit first) and rule 5 (every slice ends in something Ryan can
judge, not a layer of machinery).

The convention
--------------
Alongside a project's existing ``plans/idea_<hash>.json``, drop the
finished Premiere export into ``finals/idea_<hash>_final.xml`` (File >
Export > Final Cut Pro XML, exporting the actual edited sequence — not a
selects-only sequence, the real thing). A rendered ``finals/idea_<hash>
_final.mp4`` alongside it is optional but recommended: it is the only
reliable source of the finished runtime (see "Why not sum clip durations"
below), and separately worth a qualitative pacing/tone pass the way
``docs/reference/WALLPAPER_REEL_ANATOMY.md`` was built from Ryan's own
wallpaper Reel — this module does not attempt that; it only diffs ranges.

What this answers
------------------
Four questions, all Ryan asked directly:
  1. What did the final cut KEEP from the idea's proposed tight cut
     (``source_ranges``)?
  2. What did it DROP?
  3. What did it PULL FROM THE POOL (``pool_ranges``) — proof the
     leftovers side of the two-zone contract is actually useful, not
     just clutter?
  4. What did it ADD that the idea never surfaced at all, in either
     zone — footage the app should have found but didn't?

The matching problem, and why a naive path-match silently fails
------------------------------------------------------------------
An idea's ``source_ranges``/``pool_ranges`` carry the PROXY path (that is
what the planner read: ``.../proxies/A005_..._Proxy.mp4``). A real
Premiere export references the ORIGINAL camera file, resolved at export
time by ``exporter._build_proxy_to_original_map`` (``.../A005_..._Proxy
.mov`` — same stem, different extension, different directory). This is
the exact proxy/original distinction that caused the wrong-camera export
bug fixed 2026-09-11 (``ROADMAP.md`` Decision Log). Matching by exact path
or even exact basename (what ``posthouse.benchmark._group_by_source``
does, correctly, for ITS OWN different purpose) would silently produce
zero matches here — a clean parse with a wrong number, precisely the
failure mode the ``footage-analysis`` skill warns about. This module
matches by filename STEM instead (case-insensitive, extension stripped).

**That is only half the problem, and the other half was missed on the
first pass.** The FILENAME is per-file; the TIMES are not — they are in
COMBINED-timeline seconds, every transcript concatenated in filename
order, because that is what the planner read. Comparing those directly
against a Premiere export's per-file source timecodes compares two
different coordinate systems and produces a plausible, wrong answer: on
the wallpaper project it reported "1 of 4 kept" from ranges that
actually pointed at a story about a lawnmower and snakes rather than the
wallpaper glue the range's own summary described. A bounds check does
NOT catch this — both readings were in bounds for that project. The
definitive test is whether the transcript text at the resolved position
matches the range's own summary. See ``to_per_file`` below; the
conversion mirrors ``story_assembler.to_combined()`` inverted.

Why not sum clip durations for "final runtime"
-------------------------------------------------
Summing every clipitem's own (out - in) double-counts overlapping tracks,
ignores speed changes, and says nothing about titles/music-only stretches.
That is exactly the kind of number that parses clean and is wrong. This
module reports the final's real runtime ONLY when a rendered video is
supplied (via ffprobe), and says "not available" rather than approximate
it from the XML when one isn't.

What this reuses, and why
--------------------------
``posthouse.benchmark.parse_answer_key_xml`` for the XML itself — it
already solves the two real frame-rate gotchas (conform-to-sequence vs. a
retimed source) that produce a clean-but-wrong parse if reimplemented from
intuition, per the ``footage-analysis`` skill. Its interval-math helpers
(``_merge_intervals``, ``_overlap_sec``, ``_coverage_of``) are reused too.
The stem-based grouping and the kept/dropped/pool/added classification
are new — ``benchmark.py``'s own grouping is basename-exact and answers a
different question (cull precision/recall against a human answer key, not
"which of these specific proposed ranges survived").
"""
from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import benchmark as _bench

Interval = tuple[float, float]

# A tiny overlap (frame-boundary rounding, a half-second bleed at a cut
# point) must not count as "kept" — that would credit the plan for
# content Ryan didn't actually use. Matches the spirit of MIN_FRAGMENT_SEC
# (3.0s) already established in transcript_coverage.py as the shortest
# span worth calling a real fragment.
MIN_KEPT_SEC = 3.0
MIN_KEPT_FRAC = 0.5


class FinalReviewError(Exception):
    """Raised on a malformed idea file or an idea with no ranges to
    compare — never silently produces an empty, misleading report."""


@dataclass(frozen=True)
class IdeaRange:
    source_stem: str
    start_sec: float
    end_sec: float
    topic_label: str
    summary: str


@dataclass
class RangeVerdict:
    topic_label: str
    summary: str
    source_stem: str
    start_sec: float
    end_sec: float
    kept: bool
    covered_sec: float
    coverage_frac: float


@dataclass
class ExtraClip:
    """A stretch of Ryan's final that the idea never proposed, in either
    zone. Always a real, checkable (file, timecode) — never a vague
    summary — per the "cite or don't assert" rule."""
    source_stem: str
    start_sec: float
    end_sec: float
    from_pool: bool


@dataclass
class FinalReview:
    idea_id: str
    idea_title: str
    final_xml_path: str
    cut_kept: list[RangeVerdict] = field(default_factory=list)
    cut_dropped: list[RangeVerdict] = field(default_factory=list)
    extra_clips: list[ExtraClip] = field(default_factory=list)
    planned_cut_duration_sec: float = 0.0
    target_duration_sec: Optional[float] = None
    final_duration_sec: Optional[float] = None  # None unless a video was supplied

    @property
    def pool_used(self) -> list[ExtraClip]:
        return [c for c in self.extra_clips if c.from_pool]

    @property
    def added_from_elsewhere(self) -> list[ExtraClip]:
        return [c for c in self.extra_clips if not c.from_pool]


def _stem(path: str) -> str:
    return Path(path).stem.strip().lower()


def _subtract(base: list[Interval], remove: list[Interval]) -> list[Interval]:
    """base minus remove, both already merged/disjoint. Small and local
    because posthouse.benchmark has merge/overlap/coverage but no
    subtraction — this diff is the only place that needs it."""
    if not remove:
        return list(base)
    out: list[Interval] = []
    for s, e in base:
        cur = s
        for rs, re_ in sorted(remove):
            if re_ <= cur or rs >= e:
                continue
            if rs > cur:
                out.append((cur, min(rs, e)))
            cur = max(cur, re_)
            if cur >= e:
                break
        if cur < e:
            out.append((cur, e))
    # Drop degenerate slivers: floating-point subtraction leaves 0.0s and
    # sub-frame fragments that are real numbers but not real footage, and
    # they clutter the report with entries like "629.9-629.9s (0.0s)".
    return [(s, e) for s, e in out if e - s > 0.05]


# --- Combined-timeline vs per-file times -----------------------------------
#
# 2026-09-15, and this one nearly shipped as a wrong answer. An idea's ranges
# carry a per-file `source_file` but their TIMES are in COMBINED-timeline
# seconds — every transcript concatenated in filename order — because that is
# what the planner read (Transcript.format_for_llm over the combined
# transcript). `story_assembler.to_combined()` already encodes this on the
# export side; this is the same conversion in reverse.
#
# This is the same bug class as the wrong-camera export bug of 2026-09-11,
# and this module's docstring claimed to have solved it — but only the
# FILENAME half (proxy .mp4 vs original .mov). The TIME half was missed, and
# it silently produced a plausible-looking diff: on the wallpaper project it
# reported "1 of 4 kept" from ranges that actually pointed at a story about a
# lawnmower and snakes rather than the wallpaper glue the idea's own summary
# described. It went undetected because both readings were IN BOUNDS for that
# project's files — a bounds check alone does not catch it. The definitive
# test is whether the transcript text at the resolved position matches the
# range's own summary, which is what proved it.
def build_offset_map(transcripts_dir: Path) -> dict[str, tuple[float, float]]:
    """stem -> (combined_offset_sec, own_duration_sec), accumulated in the
    same filename order as exporter._build_source_offset_map."""
    offsets: dict[str, tuple[float, float]] = {}
    cursor = 0.0
    for p in sorted(Path(transcripts_dir).glob("*.json")):
        try:
            dur = float(json.loads(p.read_text()).get("duration") or 0.0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        offsets[_stem(p.name)] = (cursor, dur)
        cursor += dur
    return offsets


def to_per_file(stem: str, start: float, end: float,
                offsets: dict[str, tuple[float, float]]) -> tuple[float, float]:
    """Convert a range's times to that file's own clock.

    Mirrors story_assembler.to_combined()'s discriminator exactly, inverted:
    a time inside the window this file occupies on the COMBINED timeline is
    read as combined and shifted back; a time already inside the file's own
    duration but outside that window is already per-file and left alone.
    Neither fitting means the range is left untouched rather than moved to an
    invented position.
    """
    span = offsets.get(stem)
    if span is None:
        return start, end
    offset, duration = span
    if offset <= start < offset + duration:
        return start - offset, end - offset
    if start < duration:
        return start, end
    return start, end


def load_idea_ranges(
    idea_json_path: Path,
    offsets: Optional[dict[str, tuple[float, float]]] = None,
) -> tuple[str, Optional[float], list[IdeaRange], list[IdeaRange]]:
    """Returns (title, target_duration_sec, cut_ranges, pool_ranges), with
    times converted to each file's own clock when `offsets` is supplied."""
    try:
        raw = json.loads(idea_json_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise FinalReviewError(f"Can't read idea file {idea_json_path}: {e}") from e

    data = raw.get("data", {})
    cut_raw = data.get("source_ranges") or []
    pool_raw = data.get("pool_ranges") or []
    if not cut_raw:
        raise FinalReviewError(
            f"{idea_json_path} has no source_ranges — nothing to diff against."
        )

    def _conv(rs: list[dict]) -> list[IdeaRange]:
        out = []
        for r in rs:
            stem = _stem(r["source_file"])
            start = float(r["source_start_sec"])
            end = float(r["source_end_sec"])
            if offsets:
                start, end = to_per_file(stem, start, end, offsets)
            out.append(IdeaRange(
                source_stem=stem, start_sec=start, end_sec=end,
                topic_label=r.get("topic_label") or "",
                summary=r.get("summary") or "",
            ))
        return out

    title = (data.get("brief") or {}).get("title") or raw.get("idea_id", "")
    target = (data.get("brief") or {}).get("target_duration_sec")
    return title, target, _conv(cut_raw), _conv(pool_raw)


# A real Premiere export's <media><video>/<media><audio> tracks legitimately
# hold more than camera footage: music, SFX, title-card PNGs, stock loops, the
# brand logo. An idea's source_ranges/pool_ranges only ever reference camera
# footage, so none of that other material was ever "surfaceable" in the first
# place — reporting it in the "added from elsewhere" bucket isn't a footage
# gap, it's noise. Found 2026-09-15 on a real export: 60 "added" entries, the
# large majority Artlist loops, "censor bleep sound effect," CopyPasta title
# PNGs, and one 12-HOUR-long entry that was a still-image template's bogus
# declared duration, not real content. Restricting to camera-plausible
# extensions is the same filter PreCut itself implicitly relies on (aroll/
# broll sources are always declared as video files, never stills or audio).
CAMERA_VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".m4v", ".mxf", ".avi", ".mts", ".m2ts",
}


def load_final_ranges_by_stem(final_xml_path: Path) -> dict[str, list[Interval]]:
    """Parse the final export and group its ranges by source-file stem,
    merged into disjoint intervals per stem. Non-camera-footage clipitems
    (music, SFX, graphics/stills — see CAMERA_VIDEO_EXTENSIONS above) are
    dropped before grouping, not just before reporting, so they can never
    silently inflate a stem's coverage either."""
    ranges = _bench.parse_answer_key_xml(final_xml_path)
    by_stem: dict[str, list[Interval]] = {}
    for r in ranges:
        ext = Path(r.source_path).suffix.lower()
        if ext not in CAMERA_VIDEO_EXTENSIONS:
            continue
        by_stem.setdefault(_stem(r.source_path), []).append((r.in_sec, r.out_sec))
    return {stem: _bench._merge_intervals(ivs) for stem, ivs in by_stem.items()}


def _probe_duration_sec(video_path: Path) -> Optional[float]:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return float(out.stdout.strip())
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


def diff_idea_against_final(
    idea_json_path: Path,
    final_xml_path: Path,
    final_video_path: Optional[Path] = None,
    transcripts_dir: Optional[Path] = None,
) -> FinalReview:
    """`transcripts_dir` is REQUIRED in practice: without it the idea's
    combined-timeline times cannot be converted to each file's own clock, and
    the diff silently compares two different coordinate systems (see the
    module's combined-vs-per-file note). Pass it, or accept that every number
    here is meaningless — so this raises instead of guessing."""
    if transcripts_dir is None:
        raise FinalReviewError(
            "transcripts_dir is required: an idea's range times are in "
            "COMBINED-timeline seconds and must be converted to each file's "
            "own clock before they can be compared against a Premiere export. "
            "Without the transcripts there is no way to do that, and the diff "
            "would compare two different coordinate systems and look fine."
        )
    offsets = build_offset_map(Path(transcripts_dir))
    if not offsets:
        raise FinalReviewError(f"No transcripts found in {transcripts_dir}.")

    idea_data = json.loads(idea_json_path.read_text())
    idea_id = idea_data.get("idea_id", idea_json_path.stem)
    title, target_duration, cut_ranges, pool_ranges = load_idea_ranges(
        idea_json_path, offsets)
    final_by_stem = load_final_ranges_by_stem(final_xml_path)

    review = FinalReview(
        idea_id=idea_id,
        idea_title=title,
        final_xml_path=str(final_xml_path),
        target_duration_sec=target_duration,
        planned_cut_duration_sec=sum(r.end_sec - r.start_sec for r in cut_ranges),
    )
    if final_video_path is not None:
        review.final_duration_sec = _probe_duration_sec(final_video_path)

    # ---- kept / dropped, per proposed cut range -------------------------
    cut_ivs_by_stem: dict[str, list[Interval]] = {}
    for r in cut_ranges:
        cut_ivs_by_stem.setdefault(r.source_stem, []).append((r.start_sec, r.end_sec))
    cut_ivs_by_stem = {s: _bench._merge_intervals(v) for s, v in cut_ivs_by_stem.items()}

    for r in cut_ranges:
        final_ivs = final_by_stem.get(r.source_stem, [])
        covered = _bench._overlap_sec([(r.start_sec, r.end_sec)], final_ivs)
        duration = r.end_sec - r.start_sec
        frac = covered / duration if duration > 0 else 0.0
        kept = covered >= MIN_KEPT_SEC or frac >= MIN_KEPT_FRAC
        verdict = RangeVerdict(
            topic_label=r.topic_label, summary=r.summary, source_stem=r.source_stem,
            start_sec=r.start_sec, end_sec=r.end_sec, kept=kept,
            covered_sec=covered, coverage_frac=frac,
        )
        (review.cut_kept if kept else review.cut_dropped).append(verdict)

    # ---- what's left in the final after removing what the cut claims ---
    pool_ivs_by_stem: dict[str, list[Interval]] = {}
    for r in pool_ranges:
        pool_ivs_by_stem.setdefault(r.source_stem, []).append((r.start_sec, r.end_sec))
    pool_ivs_by_stem = {s: _bench._merge_intervals(v) for s, v in pool_ivs_by_stem.items()}

    for stem, final_ivs in final_by_stem.items():
        leftover = _subtract(final_ivs, cut_ivs_by_stem.get(stem, []))
        if not leftover:
            continue
        pool_ivs = pool_ivs_by_stem.get(stem, [])
        from_pool = [
            (max(s, ps), min(e, pe))
            for s, e in leftover
            for ps, pe in pool_ivs
            if min(e, pe) > max(s, ps)
        ]
        not_pool = _subtract(leftover, pool_ivs)
        for s, e in _bench._merge_intervals(from_pool):
            review.extra_clips.append(ExtraClip(stem, s, e, from_pool=True))
        for s, e in not_pool:
            review.extra_clips.append(ExtraClip(stem, s, e, from_pool=False))

    review.extra_clips.sort(key=lambda c: (c.source_stem, c.start_sec))
    review.cut_kept.sort(key=lambda v: (v.source_stem, v.start_sec))
    review.cut_dropped.sort(key=lambda v: (v.source_stem, v.start_sec))
    return review


def render_report_markdown(review: FinalReview) -> str:
    lines = [f"# Final review — {review.idea_title}", "", f"idea: `{review.idea_id}`", ""]

    lines.append("## Duration")
    lines.append(f"- Planned cut (sum of proposed ranges): {review.planned_cut_duration_sec:.1f}s")
    if review.target_duration_sec:
        lines.append(f"- Idea's own target: {review.target_duration_sec:.1f}s")
    if review.final_duration_sec is not None:
        lines.append(f"- Final rendered runtime: {review.final_duration_sec:.1f}s")
    else:
        lines.append("- Final rendered runtime: not available (no video supplied — "
                      "summing clipitem durations from the XML was deliberately not "
                      "done here; see module docstring, \"Why not sum clip durations\")")
    lines.append("")

    n_kept, n_total = len(review.cut_kept), len(review.cut_kept) + len(review.cut_dropped)
    lines.append(f"## Kept from the proposed cut — {n_kept}/{n_total}")
    for v in review.cut_kept:
        lines.append(f"- **{v.topic_label or v.source_stem}** "
                     f"({v.source_stem} {v.start_sec:.1f}-{v.end_sec:.1f}s, "
                     f"{v.coverage_frac*100:.0f}% used): {v.summary[:120]}")
    lines.append("")

    lines.append(f"## Dropped from the proposed cut — {len(review.cut_dropped)}")
    for v in review.cut_dropped:
        lines.append(f"- **{v.topic_label or v.source_stem}** "
                     f"({v.source_stem} {v.start_sec:.1f}-{v.end_sec:.1f}s): {v.summary[:120]}")
    lines.append("")

    pool_used = review.pool_used
    lines.append(f"## Pulled from the pool — {len(pool_used)}")
    lines.append("(proof the leftovers side of the export is actually useful)")
    for c in pool_used:
        lines.append(f"- {c.source_stem} {c.start_sec:.1f}-{c.end_sec:.1f}s "
                     f"({c.end_sec - c.start_sec:.1f}s)")
    lines.append("")

    added = review.added_from_elsewhere
    lines.append(f"## Added from elsewhere — {len(added)}")
    lines.append("(footage the idea never surfaced in either zone — a real gap "
                  "if this keeps happening across projects)")
    for c in added:
        lines.append(f"- {c.source_stem} {c.start_sec:.1f}-{c.end_sec:.1f}s "
                     f"({c.end_sec - c.start_sec:.1f}s)")
    lines.append("")

    return "\n".join(lines)


def _main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("idea_json", type=Path)
    ap.add_argument("final_xml", type=Path)
    ap.add_argument("--video", type=Path, default=None,
                     help="the rendered final MP4, for a real runtime figure")
    ap.add_argument("--transcripts", type=Path, required=True,
                     help="the project's transcripts/ dir — required, see module docstring")
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args(argv)

    review = diff_idea_against_final(args.idea_json, args.final_xml, args.video,
                                     transcripts_dir=args.transcripts)
    print(render_report_markdown(review))
    if args.json_out:
        from dataclasses import asdict
        args.json_out.write_text(json.dumps(asdict(review), indent=2))
        print(f"\nWrote {args.json_out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main())
