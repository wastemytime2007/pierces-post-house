"""Data models for the final cut list.

A CutList is the end product of Stage 3. It represents a fully-assembled
sequence: A-roll phrases on V1, specific B-roll clips on V2 at specific
timecodes. Stage 4 (XML export) serializes this directly to Premiere.
"""
from dataclasses import dataclass, field, asdict
from typing import Optional
from pathlib import Path
import json


@dataclass
class ARollPhrase:
    """A phrase placed on V1 in the final cut.

    'source_start' and 'source_end' are timestamps in the ORIGINAL A-roll file.
    'timeline_start' and 'timeline_end' are timestamps in the FINAL cut —
    these can differ because the planner may have reordered phrases.
    """
    phrase_id: int
    source_file: str
    source_start: float
    source_end: float
    timeline_start: float
    timeline_end: float
    text: str

    @property
    def duration(self) -> float:
        return self.source_end - self.source_start


@dataclass
class BRollShot:
    """A single B-roll shot placed on V2.

    'source_start' and 'source_end' are what portion of the source clip to use.
    'timeline_start' and 'timeline_end' are where it sits in the final cut.
    """
    clip_id: int              # database ID of the source clip
    source_file: str          # full path to the source video
    source_start: float       # in-point in source clip
    source_end: float         # out-point in source clip
    timeline_start: float     # where it appears in the final cut
    timeline_end: float       # where it ends in the final cut
    match_score: float        # 1.0 - cosine_distance (higher is better)
    matched_theme: str        # which broll_theme from the plan triggered this pick
    segment_order: int        # which deliverable segment this belongs to
    source_frame_timestamp: float  # original frame we embedded from

    @property
    def timeline_duration(self) -> float:
        return self.timeline_end - self.timeline_start


@dataclass
class BRollMarker:
    """A suggested B-roll insertion point as a timeline marker.

    Added in Drop 3.7 to replace arbitrary B-roll clip placement. Instead of
    the app picking in/out points from a clip (which was often wrong), we
    place a marker at the phrase and let the editor choose a clip from the
    B-Roll Library bin. The marker name surfaces 3-5 primary tags so the
    editor can scan the timeline at a glance, and the comment contains the
    full tag query to paste into the bin search.

    timeline_time is a single point (we emit as <in>N</in><out>-1</out>).

    Drop 4.0: added `attach_to_phrase_id`. When set, the exporter places
    this marker INSIDE the phrase's clipitem rather than on the sequence,
    so it rides with the clip when the editor rearranges the timeline.
    """
    timeline_time: float       # seconds into the final cut
    primary_tags: list[str]    # 3-5 short tags shown in marker name
    all_tags: list[str]        # full list placed in comment
    theme_category: str        # kitchen/bedroom/exterior/... drives color
    color_rgb: tuple[int, int, int]  # Premiere marker color (0-255 per channel)
    phrase_id: int             # source A-roll phrase that this marker describes
    segment_order: int         # which deliverable segment this belongs to
    # Drop 4.0: when set, the marker is emitted INSIDE the phrase's
    # <clipitem> block so it moves with the clip if the editor rearranges.
    # When None (legacy behavior), the marker lives at the sequence level.
    attach_to_phrase_id: Optional[int] = None


@dataclass
class FlagMarker:
    """2026-09-03: a color-coded RANGE marker for transcript flagging —
    Ryan's original Assistant Editor description: "flag things by
    pulling up usable pieces of information ... color code it based on
    the storyline being told." Distinct from BRollMarker (a POINT marker
    for B-roll tag suggestions) because a flagged transcript fragment has
    real duration that matters — the whole point is seeing a colored
    block spanning the actual moment on the timeline, not a single tick.

    timeline_start/timeline_end are seconds into the final sequence
    (post-translation from the source file's own coordinates — see
    posthouse.transcript_markers for that translation). Emitted with a
    real <out> frame, not the -1 point-marker convention BRollMarker
    uses (`_build_markers`/`_build_attached_markers` in exporter.py
    branch on which dataclass they're building for).
    """
    timeline_start: float
    timeline_end: float
    name: str                          # shown on the marker, e.g. the topic label
    comment: str                       # fit + reasoning, e.g. "strong fit: ..."
    color_rgb: tuple[int, int, int]
    # Same attach convention as BRollMarker: when set, emitted inside the
    # phrase's <clipitem> so it rides with the clip on rearrangement.
    attach_to_phrase_id: Optional[int] = None


@dataclass
class CreativeBrief:
    """Drop 4.0: a short editorial brief describing what this cut is ABOUT.

    Emitted as a locked sequence marker at frame 0. The editor reads this
    to understand the pitch before touching the timeline. The AI is doing
    the comprehension work (reading the transcript and spotting the angle);
    the editor is doing the editorial work (cutting the selects into a
    final piece).
    """
    title: str                 # short concept title, e.g. "Box Truck Economics"
    hook: str                  # opening hook/headline, 1-2 sentences
    why_it_works: str          # why this angle will engage/go viral
    tone: str                  # editorial tone guidance, e.g. "punchy, authoritative"
    target_duration_sec: float # rough guide, not enforced
    source_phrase_ids: list[int] = field(default_factory=list)
    # Audience/hook framing metadata the planner surfaced; optional but useful
    target_audience: str = ""
    call_to_action: str = ""


@dataclass
class TopicRange:
    """Drop 4.1: a continuous time range in one source A-roll file.

    Replaces Drop 4.0's fine-grained `phrase_ids` selection. The planner now
    returns TopicRange objects that describe where in the source interview
    an angle's material lives — as contiguous stretches, not phrase-level
    picks. The assembler emits ONE clipitem per TopicRange, so a 90-second
    stretch of the interview becomes a single continuous clip on the
    timeline instead of 20 tiny back-to-back phrase cuts.

    source_file is the ORIGINAL A-roll file path (not the proxy), matching
    the format the planner sees in the transcript. source_start_sec and
    source_end_sec are inclusive-start/exclusive-end in seconds into that
    source file.

    An angle can have multiple TopicRanges — the hook from early in the
    interview plus the payoff from later, for example. Ranges are ordered
    by the planner's editorial preference; the assembler preserves that
    order on the timeline.
    """
    source_file: str
    source_start_sec: float
    source_end_sec: float
    # Optional editorial context the planner attaches to the range
    topic_label: str = ""          # short tag, e.g. "hook", "setup", "payoff"
    summary: str = ""              # 1-line description of what's in this range

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.source_end_sec - self.source_start_sec)


@dataclass
class StoryAngle:
    """Drop 4.0 + 4.1: one of the 3 (or more) angles the planner produces
    from a transcript.

    Drop 4.1 replaces Drop 4.0's `phrase_ids: list[int]` with
    `source_ranges: list[TopicRange]`. Planner now identifies continuous
    topical regions instead of individual phrases. See TopicRange docstring.

    `phrase_ids` is kept as a computed/fallback field populated from the
    ranges for UI compatibility and for legacy-loading angles saved under
    Drop 4.0. Don't rely on it for assembly — use source_ranges.
    """
    angle_id: str              # stable identifier for this angle (uuid-ish)
    brief: CreativeBrief
    source_ranges: list[TopicRange] = field(default_factory=list)  # NEW in 4.1
    phrase_ids: list[int] = field(default_factory=list)            # kept for 4.0 compat
    suggested_preset: str = ""
    # Drop 4.4: platform + aspect pair replaces the old single-preset concept.
    # Either or both can be empty — when both unset, the assembler uses
    # the A-roll's native dims and emits no overlay.
    selected_platform_key: str = ""   # e.g. "platform_ig_reels" or ""
    selected_aspect_key: str = ""     # e.g. "aspect_vertical_9_16" or ""
    # Optional preview strings the UI uses to show a phrase summary without
    # having to re-load the full transcript every time.
    phrase_previews: list[str] = field(default_factory=list)

    @property
    def total_duration_sec(self) -> float:
        """Sum of all range durations. Used for UI display and budget guard."""
        return sum(r.duration_sec for r in self.source_ranges)


@dataclass
class CutList:
    """The complete assembled cut — ready for XML export."""
    deliverable_concept: str
    deliverable_preset: str
    total_duration: float      # final cut length
    aroll_track: list[ARollPhrase]   # V1
    broll_track: list[BRollShot]     # V2 (kept for backwards compatibility; empty in Drop 3.7+)
    broll_markers: list[BRollMarker] = field(default_factory=list)  # Drop 3.7: sequence markers instead of V2 clips
    flag_markers: list["FlagMarker"] = field(default_factory=list)  # 2026-09-03: color-coded transcript-flagging range markers

    # Drop 4.0: optional creative brief, emitted as a frame-0 sequence marker
    # when present. When None (legacy behavior), no brief marker is emitted.
    creative_brief: Optional[CreativeBrief] = None

    # Sequence settings (derived from the preset) — drive the XML export
    sequence_width: int = 1920
    sequence_height: int = 1080
    sequence_fps: float = 30.0
    overlay_style: str = "horizontal_1920x1080"

    # Diagnostics
    segments_with_broll: int = 0
    segments_without_broll: int = 0
    unmatched_segments: list[int] = field(default_factory=list)  # segment orders that got nothing
    total_matches_considered: int = 0

    def to_dict(self) -> dict:
        return {
            "deliverable_concept": self.deliverable_concept,
            "deliverable_preset": self.deliverable_preset,
            "total_duration": self.total_duration,
            "aroll_track": [asdict(p) for p in self.aroll_track],
            "broll_track": [asdict(s) for s in self.broll_track],
            "broll_markers": [asdict(m) for m in self.broll_markers],
            "flag_markers": [asdict(m) for m in self.flag_markers],
            "creative_brief": asdict(self.creative_brief) if self.creative_brief else None,
            "sequence_width": self.sequence_width,
            "sequence_height": self.sequence_height,
            "sequence_fps": self.sequence_fps,
            "overlay_style": self.overlay_style,
            "segments_with_broll": self.segments_with_broll,
            "segments_without_broll": self.segments_without_broll,
            "unmatched_segments": self.unmatched_segments,
            "total_matches_considered": self.total_matches_considered,
        }

    def save(self, path: Path):
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: Path) -> "CutList":
        data = json.loads(Path(path).read_text())
        # broll_markers may be missing in pre-3.7 saved cutlists; default to []
        raw_markers = data.get("broll_markers", [])
        markers = []
        for m in raw_markers:
            # color_rgb is saved as a list in JSON; restore as tuple
            color = tuple(m.get("color_rgb", (120, 120, 120)))
            markers.append(BRollMarker(
                timeline_time=m["timeline_time"],
                primary_tags=m.get("primary_tags", []),
                all_tags=m.get("all_tags", []),
                theme_category=m.get("theme_category", "other"),
                color_rgb=color,
                phrase_id=m.get("phrase_id", 0),
                segment_order=m.get("segment_order", 0),
                attach_to_phrase_id=m.get("attach_to_phrase_id"),
            ))
        # flag_markers absent in older saved cutlists; default to []
        raw_flag_markers = data.get("flag_markers", [])
        flag_markers = [
            FlagMarker(
                timeline_start=m["timeline_start"],
                timeline_end=m["timeline_end"],
                name=m.get("name", ""),
                comment=m.get("comment", ""),
                color_rgb=tuple(m.get("color_rgb", (140, 140, 140))),
                attach_to_phrase_id=m.get("attach_to_phrase_id"),
            )
            for m in raw_flag_markers
        ]
        # Creative brief (Drop 4.0); absent in older saved cutlists
        raw_brief = data.get("creative_brief")
        brief = None
        if raw_brief:
            brief = CreativeBrief(
                title=raw_brief.get("title", ""),
                hook=raw_brief.get("hook", ""),
                why_it_works=raw_brief.get("why_it_works", ""),
                tone=raw_brief.get("tone", ""),
                target_duration_sec=float(raw_brief.get("target_duration_sec", 0.0)),
                source_phrase_ids=list(raw_brief.get("source_phrase_ids", [])),
                target_audience=raw_brief.get("target_audience", ""),
                call_to_action=raw_brief.get("call_to_action", ""),
            )
        return cls(
            deliverable_concept=data["deliverable_concept"],
            deliverable_preset=data["deliverable_preset"],
            total_duration=data["total_duration"],
            aroll_track=[ARollPhrase(**p) for p in data["aroll_track"]],
            broll_track=[BRollShot(**s) for s in data["broll_track"]],
            broll_markers=markers,
            flag_markers=flag_markers,
            creative_brief=brief,
            sequence_width=data.get("sequence_width", 1920),
            sequence_height=data.get("sequence_height", 1080),
            sequence_fps=data.get("sequence_fps", 30.0),
            overlay_style=data.get("overlay_style", "horizontal_1920x1080"),
            segments_with_broll=data.get("segments_with_broll", 0),
            segments_without_broll=data.get("segments_without_broll", 0),
            unmatched_segments=data.get("unmatched_segments", []),
            total_matches_considered=data.get("total_matches_considered", 0),
        )
