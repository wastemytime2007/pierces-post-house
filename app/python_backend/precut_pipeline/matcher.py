"""Stage 3: Matching Engine.

Takes a Deliverable plan (from Stage 2.5) and an indexed B-roll library,
produces a CutList ready for Premiere XML export.

Design decisions:
  - Balanced matching: use a similarity threshold; leave gaps rather than
    force bad matches below the hard floor.
  - No clip reuse within a single deliverable (strictest policy).
  - Variable shot length based on A-roll words-per-second, modulated by the
    planner's broll_pacing hint (heavy=shorter, sparse=longer).
  - Configurable phrase padding to correct for Whisper's early-end bias.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from rich.console import Console

from .config import (
    MATCH_DISTANCE_THRESHOLD, MATCH_HARD_FLOOR, CANDIDATES_PER_THEME,
    PACING_CUT_LENGTHS, PACING_PLAN_OVERRIDE,
    MIN_SHOT_DURATION_SEC, MAX_SHOT_DURATION_SEC,
    SEGMENT_LEAD_IN_SEC, MIN_DENSITY_FOR_BROLL,
    PHRASE_PADDING_START, PHRASE_PADDING_END,
)
from .database import Database
from .embedder import CLIPEmbedder
from .deliverable import Deliverable, SegmentPlan
from .transcriber import Transcript, Phrase
from .cutlist import CutList, ARollPhrase, BRollShot
from .presets import get_preset
from .markers import (
    LibraryVocabulary, generate_markers, build_segment_order_map,
)


console = Console()


@dataclass
class Candidate:
    """A candidate B-roll frame for a specific segment+theme."""
    frame_id: int
    clip_id: int
    clip_path: str
    clip_duration: float
    source_frame_timestamp: float
    distance: float            # cosine distance from query (lower = better)
    matched_theme: str

    @property
    def score(self) -> float:
        """Convert distance to a 0-1 score (higher = better)."""
        return max(0.0, 1.0 - self.distance)


class Matcher:
    """Turns a Deliverable plan into a CutList."""

    def __init__(
        self,
        database: Database,
        embedder: Optional[CLIPEmbedder] = None,
    ):
        self.db = database
        self.embedder = embedder or CLIPEmbedder()

    def match(
        self,
        deliverable: Deliverable,
        transcript: Transcript,
    ) -> CutList:
        """Produce a CutList from a Deliverable plan + source transcript."""
        phrase_by_id = {p.id: p for p in transcript.phrases}

        # --- Step 1: Build A-roll track from plan segments ---
        aroll_track, segment_timeline = self._build_aroll_track(
            deliverable, phrase_by_id, transcript.source_path, transcript
        )

        # --- Step 2: Find candidates for each segment ---
        segment_candidates = self._find_all_candidates(deliverable)
        total_candidates = sum(len(c) for c in segment_candidates.values())

        # --- Step 3: Assign candidates to segments, respecting no-reuse ---
        # Priority: segments with higher cutaway_density get first pick.
        sorted_segments = sorted(
            deliverable.segments,
            key=lambda s: (-s.cutaway_density, s.order),
        )

        used_clip_ids: set[int] = set()
        segment_assignments: dict[int, list[Candidate]] = {}

        for segment in sorted_segments:
            # Respect density floor — skip B-roll entirely on low-density segments
            if segment.cutaway_density < MIN_DENSITY_FOR_BROLL:
                segment_assignments[segment.order] = []
                continue

            # For talking-head "stay on speaker" segments, skip
            if not segment.broll_themes:
                segment_assignments[segment.order] = []
                continue

            n_shots = self._target_shot_count(segment, phrase_by_id)
            chosen = self._pick_shots_for_segment(
                segment=segment,
                candidates=segment_candidates.get(segment.order, []),
                n_shots=n_shots,
                used_clip_ids=used_clip_ids,
            )
            segment_assignments[segment.order] = chosen
            for c in chosen:
                used_clip_ids.add(c.clip_id)

        # --- Step 4: Lay out B-roll shots on the timeline ---
        broll_track: list[BRollShot] = []
        segments_with_broll = 0
        segments_without_broll = 0
        unmatched = []

        for segment in deliverable.segments:
            chosen = segment_assignments.get(segment.order, [])
            timeline_start, timeline_end = segment_timeline[segment.order]
            if not chosen:
                segments_without_broll += 1
                # Only log as "unmatched" if the plan WANTED broll here
                if segment.broll_themes and segment.cutaway_density >= MIN_DENSITY_FOR_BROLL:
                    unmatched.append(segment.order)
                continue
            shots = self._lay_out_shots(
                segment=segment,
                phrase_by_id=phrase_by_id,
                candidates=chosen,
                timeline_start=timeline_start,
                timeline_end=timeline_end,
            )
            broll_track.extend(shots)
            if shots:
                segments_with_broll += 1
            else:
                segments_without_broll += 1

        total_duration = aroll_track[-1].timeline_end if aroll_track else 0

        # Look up the preset to copy its sequence settings onto the cutlist
        try:
            preset = get_preset(deliverable.preset_key)
            seq_w = preset.sequence_width
            seq_h = preset.sequence_height
            seq_fps = preset.sequence_fps
            overlay_style = preset.overlay_style
        except KeyError:
            # Unknown/custom preset — use safe horizontal defaults
            seq_w, seq_h, seq_fps = 1920, 1080, 30.0
            overlay_style = "horizontal_1920x1080"

        # ------------------------------------------------------------------
        # Drop 3.7: generate B-roll MARKERS instead of V2 clip placements.
        # The broll_track list is kept (empty) for backwards compatibility
        # with ExportModal and CutList's loader, but XML emission now uses
        # broll_markers as the source of truth for B-roll suggestions.
        # ------------------------------------------------------------------
        vocab = LibraryVocabulary.from_database(self.db)
        phrases_by_id_for_markers = {p.id: p for p in transcript.phrases}
        segment_order_map = build_segment_order_map(
            aroll_track, deliverable.segments
        )
        marker_list = generate_markers(
            aroll_track=aroll_track,
            source_phrases_by_id=phrases_by_id_for_markers,
            vocab=vocab,
            segment_order_by_phrase=segment_order_map,
        )

        return CutList(
            deliverable_concept=deliverable.concept,
            deliverable_preset=deliverable.preset_key,
            total_duration=total_duration,
            aroll_track=aroll_track,
            broll_track=[],  # empty in Drop 3.7; markers replace V2 clips
            broll_markers=marker_list,
            sequence_width=seq_w,
            sequence_height=seq_h,
            sequence_fps=seq_fps,
            overlay_style=overlay_style,
            segments_with_broll=segments_with_broll,
            segments_without_broll=segments_without_broll,
            unmatched_segments=unmatched,
            total_matches_considered=total_candidates,
        )

    # ------------------------------------------------------------
    # A-roll assembly (respects planner reordering, applies padding)
    # ------------------------------------------------------------

    def _build_aroll_track(
        self,
        deliverable: Deliverable,
        phrase_by_id: dict[int, Phrase],
        source_path: str,
        transcript: Transcript,
    ) -> tuple[list[ARollPhrase], dict[int, tuple[float, float]]]:
        """Lay down A-roll phrases on the timeline in planner order.

        Applies PHRASE_PADDING_START/END to each phrase's source in/out, clamped
        by:
          - Source file start (can't go below 0s)
          - Source file end (can't go past transcript.duration)
          - Adjacent phrase in SOURCE (can't eat into the prev/next spoken phrase)

        The adjacency clamp is the important one. Whisper cuts ends early, so we
        extend end points — but only into actual silence. If the next phrase
        begins 0.1s after this one ends, we can only extend by 0.1s even if
        PHRASE_PADDING_END is 0.25s.

        Returns (track, segment_timeline_map) where segment_timeline_map tells
        the broll laydown where each segment sits in the final cut.
        """
        track: list[ARollPhrase] = []
        segment_timeline: dict[int, tuple[float, float]] = {}
        cursor = 0.0

        # Build an ordered list of phrase-source-ranges for adjacency lookup.
        # Maps phrase_id -> (prev_phrase_end, next_phrase_start) in source time.
        adjacency = _build_adjacency_map(transcript)

        for segment in sorted(deliverable.segments, key=lambda s: s.order):
            seg_start_on_timeline = cursor

            # Each phrase in the segment contributes to the timeline sequentially.
            # Phrases within a segment are kept in their SOURCE chronological order.
            phrases = sorted(
                [phrase_by_id[pid] for pid in segment.phrase_ids
                 if pid in phrase_by_id],
                key=lambda p: p.start,
            )
            for phrase in phrases:
                padded_start, padded_end = _apply_padding(
                    phrase=phrase,
                    adjacency=adjacency,
                    transcript_duration=transcript.duration,
                )
                dur = padded_end - padded_start

                track.append(ARollPhrase(
                    phrase_id=phrase.id,
                    source_file=source_path,
                    source_start=padded_start,
                    source_end=padded_end,
                    timeline_start=cursor,
                    timeline_end=cursor + dur,
                    text=phrase.text,
                ))
                cursor += dur

            segment_timeline[segment.order] = (seg_start_on_timeline, cursor)

        return track, segment_timeline

    # ------------------------------------------------------------
    # Candidate retrieval
    # ------------------------------------------------------------

    def _find_all_candidates(
        self,
        deliverable: Deliverable,
    ) -> dict[int, list[Candidate]]:
        """For each segment, fetch top-K candidates per theme and merge."""
        result: dict[int, list[Candidate]] = {}

        for segment in deliverable.segments:
            if not segment.broll_themes:
                result[segment.order] = []
                continue

            # Dedupe candidates by frame_id, keeping best distance
            frame_best: dict[int, Candidate] = {}
            for theme in segment.broll_themes:
                query_vec = self.embedder.embed_text(theme)
                hits = self.db.search_vectors(query_vec, limit=CANDIDATES_PER_THEME)
                for hit in hits:
                    distance = float(hit.get("_distance", 1.0))
                    # Drop candidates that are hopeless
                    if distance > MATCH_HARD_FLOOR:
                        continue
                    frame_id = int(hit["frame_id"])
                    frame_info = self.db.get_frame_with_clip(frame_id)
                    if frame_info is None:
                        continue
                    cand = Candidate(
                        frame_id=frame_id,
                        clip_id=int(frame_info["clip_id"]),
                        clip_path=frame_info["clip_path"],
                        clip_duration=float(frame_info["clip_duration"]),
                        source_frame_timestamp=float(frame_info["timestamp_sec"]),
                        distance=distance,
                        matched_theme=theme,
                    )
                    existing = frame_best.get(frame_id)
                    if existing is None or distance < existing.distance:
                        frame_best[frame_id] = cand

            # Sort by score (best first) for this segment
            sorted_cands = sorted(frame_best.values(), key=lambda c: c.distance)
            result[segment.order] = sorted_cands

        return result

    # ------------------------------------------------------------
    # Pacing-aware shot selection
    # ------------------------------------------------------------

    def _target_shot_count(
        self,
        segment: SegmentPlan,
        phrase_by_id: dict[int, Phrase],
    ) -> int:
        """Decide how many B-roll shots this segment should contain.

        Based on:
          (a) segment duration
          (b) A-roll words-per-second in the segment (faster talk → shorter cuts)
          (c) planner's broll_pacing hint (heavy/medium/sparse)
        """
        duration = segment.source_end - segment.source_start
        if duration <= 0:
            return 0

        wps = _words_per_second(segment, phrase_by_id)
        if wps >= 3.0:
            base_cut = PACING_CUT_LENGTHS["fast"]
        elif wps >= 2.0:
            base_cut = PACING_CUT_LENGTHS["normal"]
        else:
            base_cut = PACING_CUT_LENGTHS["slow"]

        pacing_mult = PACING_PLAN_OVERRIDE.get(segment.broll_pacing, 1.0)
        target_cut = base_cut * pacing_mult
        target_cut = max(MIN_SHOT_DURATION_SEC, min(target_cut, MAX_SHOT_DURATION_SEC))

        # Cutaway density scales how much of the segment is B-roll covered.
        # A density of 0.5 + 3s segment = cover ~1.5s of it.
        covered_duration = duration * segment.cutaway_density
        n_shots = max(1, round(covered_duration / target_cut))
        return int(n_shots)

    def _pick_shots_for_segment(
        self,
        segment: SegmentPlan,
        candidates: list[Candidate],
        n_shots: int,
        used_clip_ids: set[int],
    ) -> list[Candidate]:
        """Pick N clips for this segment from candidates.

        Rules:
          - Exclude clips already used elsewhere in this deliverable
          - Skip candidates below the balanced threshold
          - Prefer visual variety: don't pick two frames from the same clip back-to-back
          - If we can't find n_shots above threshold, return fewer (don't force bad matches)
        """
        chosen: list[Candidate] = []
        chosen_clip_ids: set[int] = set()

        for cand in candidates:
            if len(chosen) >= n_shots:
                break
            if cand.distance > MATCH_DISTANCE_THRESHOLD:
                continue
            if cand.clip_id in used_clip_ids:
                continue
            if cand.clip_id in chosen_clip_ids:
                continue
            chosen.append(cand)
            chosen_clip_ids.add(cand.clip_id)

        return chosen

    # ------------------------------------------------------------
    # Timeline layout: place shots within segment bounds
    # ------------------------------------------------------------

    def _lay_out_shots(
        self,
        segment: SegmentPlan,
        phrase_by_id: dict[int, Phrase],
        candidates: list[Candidate],
        timeline_start: float,
        timeline_end: float,
    ) -> list[BRollShot]:
        """Place chosen candidates onto the timeline within a segment."""
        if not candidates:
            return []

        segment_duration = timeline_end - timeline_start
        lead_in = min(SEGMENT_LEAD_IN_SEC, segment_duration * 0.1)

        covered_duration = max(0.0, (segment_duration - lead_in) * segment.cutaway_density)
        per_shot_duration = covered_duration / len(candidates)
        per_shot_duration = max(
            MIN_SHOT_DURATION_SEC,
            min(per_shot_duration, MAX_SHOT_DURATION_SEC),
        )

        shots: list[BRollShot] = []
        cursor = timeline_start + lead_in

        if segment.cutaway_density >= 0.95:
            gap = 0.0
        else:
            total_shot_time = per_shot_duration * len(candidates)
            free_time = max(0.0, (segment_duration - lead_in) - total_shot_time)
            gap = free_time / (len(candidates) + 1) if len(candidates) > 0 else 0.0
            cursor += gap

        for cand in candidates:
            shot_timeline_start = cursor
            shot_timeline_end = min(cursor + per_shot_duration, timeline_end)
            if shot_timeline_end - shot_timeline_start < MIN_SHOT_DURATION_SEC:
                break

            src_start = cand.source_frame_timestamp
            src_end = src_start + (shot_timeline_end - shot_timeline_start)

            if src_end > cand.clip_duration:
                shift = src_end - cand.clip_duration
                src_start = max(0.0, src_start - shift)
                src_end = min(cand.clip_duration, src_start + (shot_timeline_end - shot_timeline_start))
                if src_end - src_start < MIN_SHOT_DURATION_SEC:
                    continue
                shot_timeline_end = shot_timeline_start + (src_end - src_start)

            shots.append(BRollShot(
                clip_id=cand.clip_id,
                source_file=cand.clip_path,
                source_start=src_start,
                source_end=src_end,
                timeline_start=shot_timeline_start,
                timeline_end=shot_timeline_end,
                match_score=cand.score,
                matched_theme=cand.matched_theme,
                segment_order=segment.order,
                source_frame_timestamp=cand.source_frame_timestamp,
            ))
            cursor = shot_timeline_end + gap

        return shots


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def _words_per_second(segment: SegmentPlan, phrase_by_id: dict[int, Phrase]) -> float:
    """Compute A-roll speech rate across the phrases in this segment."""
    total_words = 0
    total_duration = 0.0
    for pid in segment.phrase_ids:
        phrase = phrase_by_id.get(pid)
        if phrase is None:
            continue
        total_words += phrase.word_count
        total_duration += phrase.end - phrase.start
    if total_duration <= 0:
        return 2.5
    return total_words / total_duration


def _build_adjacency_map(transcript: Transcript) -> dict[int, tuple[float, float]]:
    """For each phrase ID, return (prev_phrase_end, next_phrase_start) in source time.

    These are the bounds we can pad INTO without overlapping another spoken phrase.
    For the very first phrase, prev_end is 0 (start of file).
    For the very last phrase, next_start is transcript.duration (end of file).
    """
    phrases = sorted(transcript.phrases, key=lambda p: p.start)
    adjacency: dict[int, tuple[float, float]] = {}

    for i, phrase in enumerate(phrases):
        prev_end = phrases[i - 1].end if i > 0 else 0.0
        next_start = phrases[i + 1].start if i < len(phrases) - 1 else transcript.duration
        adjacency[phrase.id] = (prev_end, next_start)

    return adjacency


def _apply_padding(
    phrase: Phrase,
    adjacency: dict[int, tuple[float, float]],
    transcript_duration: float,
) -> tuple[float, float]:
    """Apply PHRASE_PADDING_START/END to a phrase, clamped to adjacent phrases.

    Corrects for Whisper's tendency to cut phrase endings early, while
    refusing to pad into another spoken phrase.
    """
    prev_end, next_start = adjacency.get(
        phrase.id, (0.0, transcript_duration)
    )

    # How much silence (gap) exists before and after this phrase?
    gap_before = max(0.0, phrase.start - prev_end)
    gap_after = max(0.0, next_start - phrase.end)

    # Take at most half the available gap — the other half is a safety buffer
    # that belongs to the neighboring phrase if someone pads it too.
    # (This matters if the user cranks up padding and we process both neighbors.)
    pad_start = min(PHRASE_PADDING_START, gap_before * 0.5)
    pad_end = min(PHRASE_PADDING_END, gap_after * 0.5)

    padded_start = max(0.0, phrase.start - pad_start)
    padded_end = min(transcript_duration, phrase.end + pad_end)

    return padded_start, padded_end
