"""Drop 4.0 + 4.1: Story Assembler.

Replaces the CLIP-based Matcher from earlier drops with a much simpler job:
given a StoryAngle (from story_planner.py) and a Transcript, build a CutList
whose A-roll track is one continuous clipitem per TopicRange.

Drop 4.1 change: emit ONE ARollPhrase per TopicRange (continuous chunk of
the source interview) instead of one per Whisper phrase. Pierce's Drop 4.0
output had ~20 back-to-back micro-clips because Whisper splits at sentence
boundaries; the editor actually wants 2-4 large continuous sections. The
planner now returns source_ranges directly, so the assembler just lays them
on the timeline.

What this module is responsible for:
  - Turning a StoryAngle's source_ranges into an ordered list of ARollPhrase
    objects with correct source/timeline timings. (ARollPhrase's name is
    now a misnomer — each object represents a continuous RANGE, not a
    single phrase. Kept the name to avoid churning the exporter/cutlist.)
  - Generating B-roll markers attached to the range clipitems when any
    Whisper phrase inside the range mentions a room category.
  - Wrapping the output in a CutList that includes a CreativeBrief for the
    frame-0 sequence marker.
"""
from __future__ import annotations

from typing import Optional

from .cutlist import (
    ARollPhrase, BRollMarker, CutList, CreativeBrief, StoryAngle, TopicRange,
)
from .transcriber import Transcript
from .presets import get_preset
from .database import Database
from .markers import (
    LibraryVocabulary, extract_categories_from_phrase,
)
from .theme_categories import get_category


def assemble_cut_from_angle(
    angle: StoryAngle,
    transcript: Transcript,
    db: Optional[Database] = None,
    preset_key: Optional[str] = None,
    source_offset_map: Optional[dict] = None,
    source_to_original: Optional[dict] = None,
    aspect_key: Optional[str] = None,
    platform_key: Optional[str] = None,
) -> CutList:
    """Build a CutList for one StoryAngle.

    Args:
        angle: the StoryAngle from the story planner (Drop 4.1 uses
            angle.source_ranges; Drop 4.0 angles without source_ranges
            fall back to deriving from phrase_ids)
        transcript: the original A-roll transcript (may be a combined
            multi-file transcript — see source_offset_map)
        db: B-roll database. If provided, range-attached B-roll markers
            are generated for ranges where ANY inner phrase hits a category.
            If None, no markers are emitted.
        preset_key: Drop 4.3 legacy single-preset override. Takes precedence
            over aspect_key/platform_key when set. Mostly used now by old
            persisted angles on disk.
        source_offset_map: Drop 4.2 — when the transcript was built by
            joining multiple A-roll files, this maps each transcript's
            source_path to the time offset in the combined timeline.
        source_to_original: Drop 4.2 — maps each transcript's source_path
            to the ORIGINAL camera file (not proxy).
        aspect_key: Drop 4.4 — aspect preset key (e.g. "aspect_vertical_9_16").
            When set, determines sequence dimensions. When unset, sequence
            falls back to A-roll native dims.
        platform_key: Drop 4.4 — platform overlay key (e.g. "platform_tiktok").
            Determines which overlay PNG lands on V3. When unset, no overlay.
    """
    # ----- Resolve sequence dims + overlay (Drop 4.4: two-field model) -----
    # The new model: aspect_key → sequence dimensions; platform_key → overlay PNG.
    # Either or both can be None. Legacy single preset_key still honored.
    from .presets import (
        PRESETS_BY_KEY, resolve_overlay_style_for, PLATFORMS_BY_KEY,
    )

    # Drop 4.4 aspect + platform selection. Prefer explicit aspect_key/platform_key
    # args (what the orchestrator passes from the UI); fall back to the angle's
    # persisted selections.
    effective_aspect_key = aspect_key or angle.selected_aspect_key or ""
    effective_platform_key = platform_key or angle.selected_platform_key or ""

    # Drop 4.6: if EITHER Drop 4.4 field has been set on the angle (meaning
    # the user has actively managed this angle via the new dropdowns), treat
    # "None" selections as authoritative and do NOT fall through to the
    # legacy suggested_preset. Otherwise (Drop 4.3 or earlier angle), allow
    # the legacy path so old exports still work.
    #
    # We detect "actively managed" by whether EITHER Drop 4.4 field arrived
    # on the angle (from the persistence envelope via load_angle_from_project,
    # which hydrates it onto the dataclass). The orchestrator also passes the
    # explicit args — if those are non-empty the user picked something.
    is_drop44_managed = (
        aspect_key is not None
        or platform_key is not None
        or angle.selected_aspect_key != ""
        or angle.selected_platform_key != ""
    ) and not (angle.suggested_preset and not any([
        aspect_key, platform_key,
        angle.selected_aspect_key, angle.selected_platform_key,
    ]) and not isinstance(aspect_key, str))
    # Simpler: managed when the orchestrator passed Drop 4.4 args at all
    # (even empty strings, which means "user picked None in both dropdowns")
    is_drop44_managed = (aspect_key is not None) or (platform_key is not None)

    # Back-compat: legacy preset_key path (Drop 4.3 and earlier)
    if is_drop44_managed:
        # User has touched the new dropdowns. Ignore suggested_preset —
        # their None/None choice is authoritative.
        legacy_preset_key = preset_key or ""
    else:
        legacy_preset_key = preset_key or angle.suggested_preset
    legacy_preset_key = _canonicalize_preset_key(legacy_preset_key)

    # If the user picked an aspect, that drives dims. Otherwise if they picked
    # a legacy preset (from Drop 4.3 saved angle), use that. Otherwise: None,
    # which means "use A-roll native dims" — resolved below after we know
    # which A-roll file we're pulling from.
    seq_w: Optional[int] = None
    seq_h: Optional[int] = None
    seq_fps: float = 30.0
    if effective_aspect_key and effective_aspect_key in PRESETS_BY_KEY:
        preset = PRESETS_BY_KEY[effective_aspect_key]
        seq_w = preset.sequence_width
        seq_h = preset.sequence_height
        seq_fps = preset.sequence_fps
    elif legacy_preset_key and legacy_preset_key in PRESETS_BY_KEY:
        # Drop 4.3 or earlier angle. Honor its preset (the aspect of which
        # was already canonicalized above).
        preset = PRESETS_BY_KEY[legacy_preset_key]
        seq_w = preset.sequence_width
        seq_h = preset.sequence_height
        seq_fps = preset.sequence_fps
    # else: dims stay None → resolved to A-roll native after ranges are built

    # Overlay: platform choice wins. If no platform, "none" → empty V3.
    overlay_style = resolve_overlay_style_for(effective_platform_key, effective_aspect_key)

    # ----- Resolve source ranges -----
    # Prefer Drop 4.1 source_ranges. If missing (old Drop 4.0 angle loaded
    # from disk), derive from phrase_ids by grouping consecutive phrases.
    ranges: list[TopicRange] = list(angle.source_ranges or [])
    if not ranges and angle.phrase_ids:
        ranges = _ranges_from_phrase_ids(
            angle.phrase_ids, transcript,
        )

    # ----- Build the A-roll track: one ARollPhrase per RANGE -----
    # Stable order: sort by source_file then source_start_sec. We don't
    # trust planner ordering to be editorially correct, but we do preserve
    # source-chronological order within a single file.
    ranges.sort(key=lambda r: (r.source_file, r.source_start_sec))

    # Drop 4.2: build a list of (transcript_source, combined_start, combined_end)
    # tuples so we can map any combined-timeline time to the real source file
    # that contains it. Ranges live in combined-timeline seconds (that's what
    # Claude saw via Transcript.format_for_llm).
    file_spans: list[tuple[str, float, float]] = []
    if source_offset_map:
        # source_offset_map maps transcript.source_path → time_offset in
        # the combined timeline. We also need each transcript's end offset;
        # derive it from the next transcript's start (or from
        # transcript.duration for the last one).
        sorted_items = sorted(source_offset_map.items(), key=lambda kv: kv[1])
        for idx, (src, start) in enumerate(sorted_items):
            end = (sorted_items[idx + 1][1] if idx + 1 < len(sorted_items)
                   else transcript.duration)
            file_spans.append((src, float(start), float(end)))

    def resolve_real_source(combined_time: float) -> tuple[str, float]:
        """Return (real_source_file, offset_to_subtract_from_combined_time)
        for a given combined-timeline time. Falls back to transcript's
        source_path and zero offset when no map is available."""
        for src, span_start, span_end in file_spans:
            if span_start <= combined_time < span_end:
                # Run through source_to_original if we have it (proxy → original)
                real_src = (source_to_original or {}).get(src, src)
                return real_src, span_start
        # Fallback: single-file project, or time sits past the last span
        fallback = (source_to_original or {}).get(transcript.source_path, transcript.source_path)
        return fallback, 0.0

    aroll_track: list[ARollPhrase] = []
    timeline_cursor = 0.0

    # We synthesize "phrase IDs" for these range clipitems. Keep them
    # distinct from real Whisper phrase IDs so the exporter's attached-
    # marker matching (keyed on phrase_id) works without colliding.
    next_range_id = 1_000_000  # far above any real phrase ID

    for r in ranges:
        combined_start = max(0.0, r.source_start_sec)
        combined_end = r.source_end_sec

        # Snap to phrase boundaries so we don't cut off mid-word. If any
        # Whisper phrases fall inside the range, widen the range slightly
        # to fully include the first and last phrase.
        inner_phrases = [
            p for p in transcript.phrases
            if p.end > combined_start and p.start < combined_end
        ]
        if inner_phrases:
            inner_phrases.sort(key=lambda p: p.start)
            first_start = inner_phrases[0].start
            last_end = inner_phrases[-1].end
            combined_start = min(combined_start, first_start)
            combined_end = max(combined_end, last_end)
            # Build a readable label from the first and last phrases
            range_text = f"{inner_phrases[0].text.strip()[:40]} ... {inner_phrases[-1].text.strip()[-40:]}"
        else:
            range_text = r.summary or f"Range {r.source_start_sec:.1f}-{r.source_end_sec:.1f}s"

        # Drop 4.2: resolve this range to its real source file + per-file
        # offset. If the range happens to span two files (rare but possible
        # if the user's A-roll was split across multiple clips mid-speech),
        # the range gets clipped to just the first file — emitting a clipitem
        # that references two files simultaneously isn't possible in FCP7.
        real_src, file_offset = resolve_real_source(combined_start)
        # Find the end of this file's span to clamp the range if it spills
        # over into the next file.
        file_span_end = transcript.duration
        for src, span_start, span_end in file_spans:
            if src == (source_to_original or {}).get(real_src, real_src) or \
               (source_to_original or {}).get(src, src) == real_src:
                file_span_end = span_end
                break
        clamped_end = min(combined_end, file_span_end)
        if clamped_end <= combined_start:
            # Degenerate — skip
            continue

        # Translate combined-timeline → per-file-timeline by subtracting offset
        src_start = max(0.0, combined_start - file_offset)
        src_end = max(src_start + 0.01, clamped_end - file_offset)
        duration = src_end - src_start

        aroll_track.append(ARollPhrase(
            phrase_id=next_range_id,
            source_file=real_src,
            source_start=src_start,
            source_end=src_end,
            timeline_start=timeline_cursor,
            timeline_end=timeline_cursor + duration,
            text=range_text,
        ))
        timeline_cursor += duration
        next_range_id += 1

    total_duration = timeline_cursor

    # ----- Drop 4.4: A-roll native dims fallback -----
    # If the user didn't pick an aspect, use the first A-roll file's native
    # dimensions. This matches "no choice → don't touch the footage" — Pierce's
    # explicit request.
    if seq_w is None or seq_h is None:
        native_w, native_h, native_fps = _probe_native_dims(aroll_track)
        seq_w = native_w if native_w else 1920
        seq_h = native_h if native_h else 1080
        if native_fps:
            seq_fps = native_fps

    # ----- B-roll markers, attached to range clipitems -----
    broll_markers: list[BRollMarker] = []
    if db is not None and aroll_track:
        try:
            vocab = LibraryVocabulary.from_database(db)
            broll_markers = _generate_phrase_attached_markers(
                aroll_track=aroll_track,
                transcript=transcript,
                vocab=vocab,
            )
        except Exception:
            # Fail open: missing library or DB issues shouldn't block the
            # Creative Brief export. The editor can still use the bin
            # manually via the library's searchable tag columns.
            broll_markers = []

    # Diagnostic label for CutList.deliverable_preset — used by XML naming +
    # logs. Prefer the explicit aspect_key, fall back to legacy preset.
    preset_label = (effective_aspect_key or legacy_preset_key or "aroll_native")

    # ----- Wrap it in a CutList -----
    return CutList(
        deliverable_concept=angle.brief.title,
        deliverable_preset=preset_label,
        total_duration=total_duration,
        aroll_track=aroll_track,
        broll_track=[],  # Markers-only; V2 stays omitted per Drop 3.7+
        broll_markers=broll_markers,
        creative_brief=angle.brief,
        sequence_width=seq_w,
        sequence_height=seq_h,
        sequence_fps=seq_fps,
        overlay_style=overlay_style,
        # Diagnostics: in Drop 4.0 the "segment" concept is retired, but
        # the fields are kept for CutList backwards compat.
        segments_with_broll=len({m.attach_to_phrase_id for m in broll_markers
                                 if m.attach_to_phrase_id is not None}),
        segments_without_broll=len(aroll_track)
            - len({m.attach_to_phrase_id for m in broll_markers
                   if m.attach_to_phrase_id is not None}),
    )


def _generate_phrase_attached_markers(
    aroll_track: list[ARollPhrase],
    transcript: Transcript,
    vocab: LibraryVocabulary,
) -> list[BRollMarker]:
    """Drop 4.1: emit markers attached to RANGE clipitems.

    Each ARollPhrase in the aroll_track now represents a TopicRange
    (continuous chunk of the interview), not a single Whisper phrase.
    To find category hits, we scan every Whisper phrase that falls
    inside the range and collect its categories. One marker per unique
    (range, category) pair is emitted, with the marker position set to
    the first phrase inside the range that hit that category (so the
    marker lands roughly at the moment the editor will hear the cue,
    not at the range's start).

    Category-strength floor (from 3.8.2) preserved: categories with <2
    clips AND <2 unique tags are suppressed. No fallback "Other" markers.
    """
    markers: list[BRollMarker] = []
    available = vocab.categories_with_clips()

    # Apply 3.8.2 strength floor
    strong = {
        k for k in available
        if vocab.category_clip_counts.get(k, 0) >= 2
        or len(vocab.category_tags.get(k, [])) >= 2
    }
    if not strong:
        strong = available
    if not strong:
        return []

    for aroll in aroll_track:
        # Find all Whisper phrases that overlap this range
        inner_phrases = [
            p for p in transcript.phrases
            if p.end > aroll.source_start and p.start < aroll.source_end
        ]
        if not inner_phrases:
            continue
        inner_phrases.sort(key=lambda p: p.start)

        # Collect category → first-phrase-that-hit-it within the range
        cat_first_phrase = {}  # cat_key -> (phrase, phrase_offset_within_range_sec)
        for p in inner_phrases:
            phrase_cats = extract_categories_from_phrase(p.text)
            for c in phrase_cats:
                if c not in strong or c in cat_first_phrase:
                    continue
                # Offset within the range where this phrase starts
                offset_within_range = max(0.0, p.start - aroll.source_start)
                cat_first_phrase[c] = (p, offset_within_range)

        if not cat_first_phrase:
            continue

        for cat_key, (trigger_phrase, offset_within_range) in cat_first_phrase.items():
            # Timeline position = start of this range + offset where the
            # triggering phrase sits inside the range.
            time_on_timeline = aroll.timeline_start + offset_within_range

            category = get_category(cat_key)
            cat_lib_tags = vocab.category_tags.get(cat_key, [])
            primary = cat_lib_tags[:5]

            markers.append(BRollMarker(
                timeline_time=time_on_timeline,
                primary_tags=primary,
                all_tags=cat_lib_tags,
                theme_category=category.key,
                color_rgb=category.color_rgb,
                phrase_id=aroll.phrase_id,
                segment_order=0,
                # Binds this marker to the range clipitem. The exporter
                # computes a clip-relative in-point so the marker rides
                # with the clip when the editor rearranges.
                attach_to_phrase_id=aroll.phrase_id,
            ))

    return markers


def _ranges_from_phrase_ids(
    phrase_ids: list[int],
    transcript: Transcript,
) -> list[TopicRange]:
    """Drop 4.1 compat: reconstruct TopicRange objects from a Drop 4.0 angle's
    phrase_ids list.

    Groups consecutive phrase IDs into contiguous ranges. Used when loading
    an angle saved under Drop 4.0 (which predates source_ranges).
    """
    phrases_by_id = {p.id: p for p in transcript.phrases}
    valid_ids = sorted(pid for pid in phrase_ids if pid in phrases_by_id)
    if not valid_ids:
        return []

    ranges: list[TopicRange] = []
    run_start = valid_ids[0]
    run_prev = valid_ids[0]
    for pid in valid_ids[1:]:
        if pid == run_prev + 1:
            run_prev = pid
            continue
        ranges.append(TopicRange(
            source_file=transcript.source_path,
            source_start_sec=float(phrases_by_id[run_start].start),
            source_end_sec=float(phrases_by_id[run_prev].end),
        ))
        run_start = pid
        run_prev = pid
    ranges.append(TopicRange(
        source_file=transcript.source_path,
        source_start_sec=float(phrases_by_id[run_start].start),
        source_end_sec=float(phrases_by_id[run_prev].end),
    ))
    return ranges


# Drop 4.3: mapping from legacy/hypothetical preset keys to aspect-only
# equivalents. Used when an older angle persisted a preset key that's no
# longer supported (or that never existed — the old UI dropdown in Drop 4.0
# and 4.1 had keys like "square_1080x1080" that didn't match anything in
# presets.py). Also handles the legacy duration-coupled keys (reel_30s,
# ad_60s, etc.) gracefully.
_LEGACY_PRESET_ALIASES = {
    # Vertical 9:16 variants
    "vertical_short_1080x1920": "aspect_vertical_9_16",
    "vertical_15s_1080x1920": "aspect_vertical_9_16",
    "vertical_30s_1080x1920": "aspect_vertical_9_16",
    "vertical_60s_1080x1920": "aspect_vertical_9_16",
    "youtube_short_1080x1920": "aspect_vertical_9_16",
    "reel_15s": "aspect_vertical_9_16",
    "reel_30s": "aspect_vertical_9_16",
    "tiktok_60s": "aspect_vertical_9_16",
    "facebook_reel_30s": "aspect_vertical_9_16",
    "youtube_shorts_60s": "aspect_vertical_9_16",
    "x_vertical_15s": "aspect_vertical_9_16",
    # Square 1:1 variants
    "square_1080x1080": "aspect_square_1_1",
    "square_30s_1080x1080": "aspect_square_1_1",
    # Horizontal 16:9 variants
    "horizontal_1920x1080": "aspect_horizontal_16_9",
    "horizontal_30s_1920x1080": "aspect_horizontal_16_9",
    "horizontal_60s_1920x1080": "aspect_horizontal_16_9",
    "horizontal_90s_1920x1080": "aspect_horizontal_16_9",
    "trailer_2min_1920x1080": "aspect_horizontal_16_9",
    "ad_15s": "aspect_horizontal_16_9",
    "ad_30s": "aspect_horizontal_16_9",
    "ad_60s": "aspect_horizontal_16_9",
    "ad_120s": "aspect_horizontal_16_9",
    "youtube_highlight": "aspect_horizontal_16_9",
    "youtube_episode": "aspect_horizontal_16_9",
    "talking_head_full": "aspect_horizontal_16_9",
}


def _canonicalize_preset_key(key: str) -> str:
    """Translate legacy/unknown preset keys to a valid aspect_ key.

    Strategy:
      1. Empty string → empty (Drop 4.4: signals A-roll native dims).
      2. If the key is already a known preset (including aspect_*), keep it.
      3. If it's in the alias table, map to the aspect equivalent.
      4. Otherwise return the key unchanged — the caller's try/except will
         catch it and fall back to horizontal defaults.
    """
    from .presets import PRESETS_BY_KEY
    if not key:
        return ""  # Empty → A-roll native (Drop 4.4 change from Drop 4.3)
    if key in PRESETS_BY_KEY:
        return key
    if key in _LEGACY_PRESET_ALIASES:
        return _LEGACY_PRESET_ALIASES[key]
    return key


def _probe_native_dims(aroll_track: list) -> tuple[Optional[int], Optional[int], Optional[float]]:
    """Drop 4.4: probe the first A-roll file for its native width/height/fps.

    Used when the user hasn't picked an aspect — the sequence falls back to
    matching the A-roll's native resolution. Returns (None, None, None) if
    the probe fails; caller should fall back to 1920x1080@30 in that case.
    """
    if not aroll_track:
        return (None, None, None)
    try:
        from .multi_exporter import _safe_probe
        from pathlib import Path
    except ImportError:
        return (None, None, None)

    first = aroll_track[0]
    path = Path(first.source_file)
    if not path.exists():
        return (None, None, None)

    info = _safe_probe(path)
    if info is None:
        return (None, None, None)
    return (
        int(info.get("width")) if info.get("width") else None,
        int(info.get("height")) if info.get("height") else None,
        float(info.get("fps")) if info.get("fps") else None,
    )
