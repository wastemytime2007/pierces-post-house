"""Data models for Deliverables — the output of the planner.

A Deliverable is a complete plan for one final cut: which A-roll segments to use,
in what order, with B-roll guidance per segment. This is what Stage 3 (matching)
and Stage 4 (XML export) consume.
"""
from dataclasses import dataclass, field, asdict
from typing import Optional
import json
from pathlib import Path


@dataclass
class SegmentPlan:
    """One A-roll segment selected for inclusion in a deliverable.

    Note: 'order' is the position in the FINAL cut, which may differ from
    chronological order in the source. The planner can reorder for narrative effect.
    """
    phrase_ids: list[int]          # phrase IDs from transcript (contiguous or not)
    order: int                     # 0-indexed position in final cut
    source_start: float            # original A-roll start time
    source_end: float              # original A-roll end time
    text: str                      # transcript text of this segment
    role: str                      # "hook", "development", "proof", "close", "beat"

    # B-roll guidance (only populated when plan includes B-roll strategy)
    broll_themes: list[str] = field(default_factory=list)
    broll_pacing: str = "medium"   # "sparse", "medium", "heavy"
    cutaway_density: float = 0.5   # 0=stay on speaker, 1=heavy cutaways
    editorial_notes: str = ""      # pacing/cut style hints for human editor

    @property
    def duration(self) -> float:
        return self.source_end - self.source_start

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Deliverable:
    """A complete plan for one final cut."""
    concept: str                   # one-line pitch ("60s ad about sustainability")
    pitch: str                     # longer explanation of the editorial angle
    preset_key: str                # which preset was targeted
    target_duration: float         # target length (sec)
    actual_duration: float         # sum of selected segment durations
    segments: list[SegmentPlan]    # in output order

    # Metadata the planner generates
    suggested_title: str = ""
    tone: str = ""                 # "punchy", "reflective", "energetic", etc.
    opening_hook: str = ""         # what makes the first beat grab attention
    why_it_works: str = ""         # editorial rationale

    def to_dict(self) -> dict:
        return {
            "concept": self.concept,
            "pitch": self.pitch,
            "preset_key": self.preset_key,
            "target_duration": self.target_duration,
            "actual_duration": self.actual_duration,
            "segments": [s.to_dict() for s in self.segments],
            "suggested_title": self.suggested_title,
            "tone": self.tone,
            "opening_hook": self.opening_hook,
            "why_it_works": self.why_it_works,
        }

    def save(self, path: Path):
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def from_dict(cls, data: dict) -> "Deliverable":
        segments = [
            SegmentPlan(**s) for s in data.get("segments", [])
        ]
        return cls(
            concept=data["concept"],
            pitch=data.get("pitch", ""),
            preset_key=data["preset_key"],
            target_duration=data["target_duration"],
            actual_duration=data["actual_duration"],
            segments=segments,
            suggested_title=data.get("suggested_title", ""),
            tone=data.get("tone", ""),
            opening_hook=data.get("opening_hook", ""),
            why_it_works=data.get("why_it_works", ""),
        )


@dataclass
class DeliverableConcept:
    """A pitched deliverable idea from 'Analyze & Recommend'.

    These are proposals — the user picks one (or several) and the planner then
    generates full Deliverable objects for the ones they accept.
    """
    concept: str                   # one-line pitch
    pitch: str                     # fuller paragraph explaining the angle
    suggested_preset: str          # which preset this would best fit
    estimated_duration: float      # how long the planner thinks it should be
    key_phrase_ids: list[int]      # the core phrases this concept is built around
    tone: str
    why_it_works: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AnalysisReport:
    """Output of 'Analyze & Recommend' mode."""
    transcript_source: str
    total_duration: float
    summary: str                   # one-paragraph summary of what's in the A-roll
    concepts: list[DeliverableConcept]

    def to_dict(self) -> dict:
        return {
            "transcript_source": self.transcript_source,
            "total_duration": self.total_duration,
            "summary": self.summary,
            "concepts": [c.to_dict() for c in self.concepts],
        }

    def save(self, path: Path):
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))
