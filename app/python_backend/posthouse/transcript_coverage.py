"""posthouse.transcript_coverage — exhaustive transcript reading.

**The confirmed, named gap, not a guess.** Ryan (2026-09-02,
``docs/REQUIREMENTS.md``): "the app or AI tends to skim through
transcripts and that leaves a lot on the table so that we end up missing
a lot of the story that matters." Measured: PreCut's
``story_planner.generate_angles()`` is ONE Claude call, ``max_tokens=4096``,
explicitly prompted for "3 distinct angles" of "1-3 ranges each" — a
mechanical ceiling of roughly 9 ranges / ~13 minutes of material per run,
regardless of how much real material a transcript actually contains. This
module does not touch that function; it is the missing exhaustive-reading
capability PreCut doesn't have (``precut-capabilities`` skill, gap #1).

**Same shape as the fix that already worked for an analogous problem**
(``posthouse.sync_coverage``, 2026-09-03): PreCut's audio sync ran ONE
whole-file correlation and missed real matches hidden by irrelevant
stretches; the fix was windowed, independently-scored passes instead of
trusting one pass over everything. Here: one whole-transcript call caps
output regardless of length; the fix is windowed, independently-exhaustive
passes over the whole transcript, merged, with an explicit coverage
check — because a plausible-looking fragment count with no coverage
check would just be a second, subtler way to skim.

**What this returns, and what it doesn't.** Per Ryan's own scoping
(2026-09-03, in conversation): this produces NEUTRAL fragments — what
storyline-worthy material exists in the transcript and where, nothing
about which audience or content goal it serves. Audience-informed
tagging is a separate, later layer (see ROADMAP.md Decision Log,
2026-09-03) that consumes this module's output plus a project's captured
audience/content-goal from Project Manager intake. This module doesn't
decide what anything is FOR — only what's there.

Reuses PreCut's own ``TopicRange`` shape and its ``_extract_json`` /
``_merge_overlapping_ranges`` helpers (``precut_pipeline.story_planner``)
rather than reinventing fragment representation or JSON-parsing/merge
logic it already has — reached through :func:`posthouse.precut_bridge.
import_precut`, per that module's own "Door 3" rule (never a bare
``import precut_pipeline`` from inside ``posthouse``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import anthropic

from posthouse.precut_bridge import import_precut

_anthropic_client = import_precut("precut_pipeline.anthropic_client")
_config = import_precut("precut_pipeline.config")
_cutlist = import_precut("precut_pipeline.cutlist")
_story_planner = import_precut("precut_pipeline.story_planner")
_transcriber = import_precut("precut_pipeline.transcriber")

build_anthropic_client = _anthropic_client.build_anthropic_client
ANTHROPIC_MODEL = _config.ANTHROPIC_MODEL
TopicRange = _cutlist.TopicRange
StoryPlannerError = _story_planner.StoryPlannerError
_extract_json = _story_planner._extract_json
_merge_overlapping_ranges = _story_planner._merge_overlapping_ranges
Transcript = _transcriber.Transcript

# A 10-minute window with 2-minute overlap: long enough that a normal
# output budget comfortably covers "list everything," short enough that
# each window is genuinely read in full rather than skimmed the same way
# a whole-project prompt gets skimmed. Overlap means a topic straddling a
# window boundary gets caught by both neighbors and merged into one
# continuous range, rather than truncated at an arbitrary cut point.
DEFAULT_WINDOW_SEC = 600.0
DEFAULT_OVERLAP_SEC = 120.0
MIN_FRAGMENT_SEC = 3.0

EXHAUSTIVE_SYSTEM_PROMPT = """You are a meticulous transcript reader helping an editor who needs to know EVERYTHING usable in an interview, not a curated highlight reel. Your job is completeness, not selection. Every distinct topic, anecdote, aside, or moment that could inform a story belongs in your output — including material that seems minor, repetitive, or only tangentially related to anything else. The editor decides what's useful; you decide nothing except what exists and where it lives.

Return ONLY valid JSON. No preamble, no markdown fences."""

EXHAUSTIVE_PROMPT = """Here is one window of a longer interview transcript. Each phrase has an ID, a start time in seconds, and an end time in seconds — these times are absolute, into the ORIGINAL full-length source file (not relative to this window).

<transcript_window>
{transcript}
</transcript_window>

This window covers {window_start:.1f}s to {window_end:.1f}s of the source file.

Your task: list EVERY distinct topic, anecdote, aside, or storyline-worthy moment that occurs in this window. Do not limit yourself to a fixed number — if the window contains 2 distinct moments, return 2; if it contains 15, return 15. Do not filter for "the best" material; filter only for "is this a genuinely distinct moment" (i.e. don't split one continuous thought into multiple fragments, and don't merge two unrelated topics into one).

Rules:

1. A fragment is defined by source_start_sec and source_end_sec (absolute seconds into the source file, using the phrase start/end times shown above).
2. Use SOFT topic boundaries within one fragment — if a thought drifts into a related aside and loops back, that's still one fragment. Only start a new fragment when the speaker clearly moves to a distinct topic.
3. Every fragment must cover AT LEAST {min_fragment_sec:.0f} seconds.
4. Fragments should not overlap each other.
5. Silence, filler, or pure logistics ("let me check the mic") don't need their own fragment, but when in doubt about whether something is a real moment, INCLUDE it — this task is measured by what you miss, not by how tight your list is.
6. Cover the FULL window from {window_start:.1f}s to {window_end:.1f}s — if there are gaps in your fragment list, that means you skipped material, which is exactly the failure this task exists to prevent.

Return JSON in this exact shape:

{{
  "fragments": [
    {{
      "source_start_sec": 120.4,
      "source_end_sec": 187.8,
      "topic_label": "short 2-5 word label",
      "summary": "1 sentence describing what's actually said in this fragment"
    }}
  ]
}}"""


@dataclass
class CoverageReport:
    """How much of the transcript's real duration ended up in at least
    one extracted fragment, and exactly which stretches didn't."""
    total_duration_sec: float
    covered_duration_sec: float
    fragment_count: int
    gaps: List[Tuple[float, float]] = field(default_factory=list)

    @property
    def coverage_fraction(self) -> float:
        if self.total_duration_sec <= 0:
            return 0.0
        return self.covered_duration_sec / self.total_duration_sec


def _split_into_windows(
    transcript: Transcript,
    window_sec: float,
    overlap_sec: float,
) -> List[Tuple[Transcript, float, float]]:
    """Slice a transcript into overlapping sub-transcripts by phrase time.

    Each returned Transcript keeps phrases' original absolute start/end
    times (and ids) — a window is a VIEW into the same timeline, not a
    renumbered/rebased copy, so fragments extracted from it are already
    in the source file's real coordinate space. Returns (window,
    window_start, window_end) triples so a caller never has to
    re-derive window boundaries independently — a transcript with a
    long silent stretch can produce an empty window that gets skipped,
    which would desync any parallel start/end computation done outside
    this function.
    """
    if not transcript.phrases:
        return []

    hop = window_sec - overlap_sec
    if hop <= 0:
        raise ValueError("overlap_sec must be smaller than window_sec")

    windows: List[Tuple[Transcript, float, float]] = []
    window_start = 0.0
    total = transcript.duration
    while window_start < total:
        window_end = min(window_start + window_sec, total)
        phrases_in_window = [
            p for p in transcript.phrases
            if p.end > window_start and p.start < window_end
        ]
        if phrases_in_window:
            windows.append((
                Transcript(
                    source_path=transcript.source_path,
                    language=transcript.language,
                    duration=transcript.duration,
                    phrases=phrases_in_window,
                ),
                window_start,
                window_end,
            ))
        if window_end >= total:
            break
        window_start += hop

    return windows


def _call_claude(system: str, user: str, model: str, api_key: Optional[str]) -> str:
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise StoryPlannerError(
            "No Anthropic API key. Set ANTHROPIC_API_KEY env var or pass api_key."
        )
    client = build_anthropic_client(api_key=key)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=8192,
            temperature=0.2,  # completeness, not creative framing — low temp
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.APIError as e:
        raise StoryPlannerError(f"Anthropic API error: {e}") from e

    parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
    text = "".join(parts).strip()
    if not text:
        raise StoryPlannerError("Empty response from Claude.")
    return text


def _extract_window_fragments(
    window: Transcript,
    window_start: float,
    window_end: float,
    model: str,
    api_key: Optional[str],
) -> List[TopicRange]:
    user_prompt = EXHAUSTIVE_PROMPT.format(
        transcript=window.format_for_llm(),
        window_start=window_start,
        window_end=window_end,
        min_fragment_sec=MIN_FRAGMENT_SEC,
    )
    response_text = _call_claude(EXHAUSTIVE_SYSTEM_PROMPT, user_prompt, model, api_key)
    data = _extract_json(response_text)

    fragments: List[TopicRange] = []
    for entry in data.get("fragments", []):
        try:
            start = float(entry.get("source_start_sec", -1))
            end = float(entry.get("source_end_sec", -1))
        except (TypeError, ValueError):
            continue
        if start < 0 or end <= start:
            continue
        # Clamp to this window's own bounds — a fragment naming a time
        # outside what the model was actually shown is a hallucination,
        # not real coverage of that stretch (the neighboring window, if
        # any, is responsible for its own bounds).
        start = max(window_start, start)
        end = min(window_end, end)
        if end - start < MIN_FRAGMENT_SEC:
            continue
        fragments.append(TopicRange(
            source_file=window.source_path,
            source_start_sec=start,
            source_end_sec=end,
            topic_label=str(entry.get("topic_label", ""))[:40],
            summary=str(entry.get("summary", ""))[:400],
        ))
    return fragments


def _compute_coverage(
    fragments: List[TopicRange],
    total_duration: float,
) -> CoverageReport:
    if not fragments:
        return CoverageReport(
            total_duration_sec=total_duration,
            covered_duration_sec=0.0,
            fragment_count=0,
            gaps=[(0.0, total_duration)] if total_duration > 0 else [],
        )

    ordered = sorted(fragments, key=lambda r: r.source_start_sec)
    covered = 0.0
    gaps: List[Tuple[float, float]] = []
    cursor = 0.0
    for r in ordered:
        if r.source_start_sec > cursor:
            gaps.append((cursor, r.source_start_sec))
        covered += max(0.0, min(r.source_end_sec, total_duration) - max(r.source_start_sec, cursor))
        cursor = max(cursor, r.source_end_sec)
    if cursor < total_duration:
        gaps.append((cursor, total_duration))

    return CoverageReport(
        total_duration_sec=total_duration,
        covered_duration_sec=covered,
        fragment_count=len(fragments),
        gaps=gaps,
    )


def extract_exhaustive_fragments(
    transcript: Transcript,
    window_sec: float = DEFAULT_WINDOW_SEC,
    overlap_sec: float = DEFAULT_OVERLAP_SEC,
    model: str = ANTHROPIC_MODEL,
    api_key: Optional[str] = None,
) -> Tuple[List[TopicRange], CoverageReport]:
    """Exhaustively extract every storyline-worthy fragment from a full
    transcript, windowed so no single call's output budget caps how much
    of a long interview gets surfaced.

    Returns (fragments, coverage_report). Callers should treat a
    coverage_fraction well below 1.0 as a signal to inspect, not silently
    accept — that's the whole point of measuring it.
    """
    windows = _split_into_windows(transcript, window_sec, overlap_sec)

    all_fragments: List[TopicRange] = []
    for window, window_start, window_end in windows:
        all_fragments.extend(
            _extract_window_fragments(window, window_start, window_end, model, api_key)
        )

    merged = _merge_overlapping_ranges(all_fragments)
    coverage = _compute_coverage(merged, transcript.duration)
    return merged, coverage
