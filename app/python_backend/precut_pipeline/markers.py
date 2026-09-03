"""Marker generation for B-roll suggestions.

Replaces the Stage 3 "pick specific B-roll clips and place them on V2"
behavior (which produced arbitrary, often-wrong in/out points) with
sequence-marker suggestions instead. The editor sees a colored marker at
each phrase, with the marker name showing 3-5 suggested tags and the
comment containing the full tag list to paste into the project panel search
bar.

Design (Drop 3.7 + 3.8 + 3.8.1):
  - One BRollMarker per (A-roll phrase, theme category) pair.
  - If a phrase mentions multiple categories (e.g. kitchen AND bathroom),
    it gets multiple markers, staggered within the phrase.
  - **Category-level matching** (Drop 3.8.1): rather than matching the
    phrase's literal words against library tag text (which fails when
    Celeste says "couch" but the tagger said "sofa"), we route both sides
    through the THEME_CATEGORIES synonym vocabulary:
      - Phrase text → set of categories it mentions
      - Library clips → for each clip, set of categories its tags touch
    A marker is emitted for category X when the phrase text mentions X
    AND at least one library clip has tags in X.
  - Marker body text contains the UNION of all relevant tags from clips
    in that category, so pasting any one of them into the Premiere bin
    search filters to clips matching that category.
  - No Claude call — pure local text matching, fast and deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .cutlist import BRollMarker, ARollPhrase
from .database import Database
from .transcriber import Phrase
from .theme_categories import (
    categorize_tag, categorize_tags, get_category,
    THEME_CATEGORIES, DEFAULT_CATEGORY,
)


# ---------------------------------------------------------------------------
# Library vocabulary (what tags + categories are available to suggest)
# ---------------------------------------------------------------------------


@dataclass
class LibraryVocabulary:
    """Everything we know about the library that's relevant to matching.

    tag_counts: how many clips mention each tag (for ranking/display)
    category_tags: for each category key, the union of tag strings from
                   clips whose tags touch that category
    category_clip_counts: for each category, how many clips contribute
    """
    tag_counts: dict[str, int]
    category_tags: dict[str, list[str]]
    category_clip_counts: dict[str, int]

    @classmethod
    def from_database(cls, db: Database) -> "LibraryVocabulary":
        """Scan the DB for all tags that appear on any frame of any clip.

        Builds both the flat tag vocabulary AND the per-category aggregates
        needed for category-level matching. Also includes clip-level motion
        tags (static, pan_left, wide_shot, etc.) so shot-type queries can
        still surface via direct bin search even though they never trigger
        markers directly (filtered out in generate_markers).
        """
        # Tag universe with clip frequency
        seen_per_tag: dict[str, set[int]] = {}
        # Per-clip tag set, for category aggregation
        clip_tags: dict[int, set[str]] = {}

        with db.connect() as conn:
            # Drop 3.8.2: JOIN against clips so orphan frames (from clips
            # that were deleted but whose frame rows linger — e.g., when
            # a clip was removed via a code path that didn't have
            # PRAGMA foreign_keys=ON, leaving CASCADE un-fired) don't
            # pollute the vocabulary. Pierce's Drop 3.8.1 symptom — a
            # single lone 'person' marker with no people clips in his
            # current library — traces back to exactly this class of
            # stale-state bug.
            #
            # We also now read BOTH tags_text AND tags (JSON). load_broll_library
            # prefers tags JSON and falls back to tags_text; this reader
            # previously only read tags_text. That mismatch meant the library
            # bin could carry data the vocab couldn't see. Reading both keeps
            # the two code paths in sync.
            rows = conn.execute(
                "SELECT f.clip_id, f.tags, f.tags_text FROM frames f "
                "JOIN clips c ON c.id = f.clip_id "
                "WHERE (f.tags_text IS NOT NULL AND f.tags_text != '') "
                "   OR (f.tags IS NOT NULL AND f.tags != '' AND f.tags != '[]')"
            ).fetchall()
            import json as _json
            for row in rows:
                clip_id = row["clip_id"]
                # Prefer tags_text (already pre-split); fall back to tags JSON
                parsed_tags: list[str] = []
                raw_text = row["tags_text"] or ""
                if raw_text:
                    parsed_tags = _split_tags(raw_text)
                if not parsed_tags:
                    raw_json = row["tags"] or ""
                    if raw_json:
                        try:
                            j = _json.loads(raw_json)
                            if isinstance(j, list):
                                parsed_tags = [str(t) for t in j]
                        except (_json.JSONDecodeError, TypeError):
                            parsed_tags = []
                for tag in parsed_tags:
                    t = tag.strip().lower()
                    if not t:
                        continue
                    seen_per_tag.setdefault(t, set()).add(clip_id)
                    clip_tags.setdefault(clip_id, set()).add(t)

            # Clip-level motion tags
            try:
                motion_rows = conn.execute(
                    "SELECT id, motion_tags FROM clips "
                    "WHERE motion_tags IS NOT NULL AND motion_tags != ''"
                ).fetchall()
            except Exception:
                motion_rows = []
            for row in motion_rows:
                clip_id = row["id"]
                raw = row["motion_tags"] or ""
                try:
                    import json as _json
                    parsed = _json.loads(raw)
                except Exception:
                    parsed = None
                if isinstance(parsed, list):
                    for t in parsed:
                        ts = str(t).strip().lower()
                        if ts:
                            seen_per_tag.setdefault(ts, set()).add(clip_id)
                            clip_tags.setdefault(clip_id, set()).add(ts)

        # Build category → tag union
        category_tags: dict[str, set[str]] = {}
        category_clips: dict[str, set[int]] = {}
        for clip_id, tags in clip_tags.items():
            for tag in tags:
                if _is_motion_tag(tag):
                    continue  # motion tags don't define a category
                cat = categorize_tag(tag)
                if cat.key == DEFAULT_CATEGORY.key:
                    continue  # 'other' is not a useful marker category
                category_tags.setdefault(cat.key, set()).add(tag)
                category_clips.setdefault(cat.key, set()).add(clip_id)

        # Freeze the category tag sets as sorted lists for stable output
        final_cat_tags = {
            k: sorted(v, key=lambda t: (-len(seen_per_tag.get(t, set())), t))
            for k, v in category_tags.items()
        }
        final_cat_clip_counts = {k: len(v) for k, v in category_clips.items()}

        tag_counts = {t: len(ids) for t, ids in seen_per_tag.items()}
        return cls(
            tag_counts=tag_counts,
            category_tags=final_cat_tags,
            category_clip_counts=final_cat_clip_counts,
        )

    def has_tag(self, tag: str) -> bool:
        return tag.strip().lower() in self.tag_counts

    def rank(self, tag: str) -> int:
        """How many CLIPS have this tag. 0 = none."""
        return self.tag_counts.get(tag.strip().lower(), 0)

    def categories_with_clips(self) -> set[str]:
        """Category keys for which we have at least one library clip."""
        return {k for k, n in self.category_clip_counts.items() if n > 0}


def _split_tags(raw: str) -> list[str]:
    """Split a comma/semicolon-joined tag string into individual tags."""
    if not raw:
        return []
    pieces = []
    for chunk in raw.replace(";", ",").split(","):
        c = chunk.strip()
        if c:
            pieces.append(c)
    return pieces


# ---------------------------------------------------------------------------
# Phrase → category extraction
# ---------------------------------------------------------------------------


# Motion/framing tags live on the clip, not on phrases. A phrase saying
# "panning past the kitchen" shouldn't generate a 'pan_right' marker from
# phrase text, so we exclude these from phrase-level matching. They're
# still available in the library vocab for direct bin search use.
_MOTION_TAG_PREFIXES = (
    "static", "camera_motion",
    "pan_", "tilt_", "zoom_",
    "wide_shot", "medium_shot", "close_up",
)


def _is_motion_tag(tag: str) -> bool:
    t = tag.strip().lower()
    return any(t == p or t.startswith(p) for p in _MOTION_TAG_PREFIXES)


def _contains_term(text_lc: str, term_lc: str) -> bool:
    """Case-insensitive whole-word (or multi-word phrase) match in
    already-lowercased text."""
    if " " in term_lc:
        return term_lc in text_lc
    idx = 0
    while idx < len(text_lc):
        pos = text_lc.find(term_lc, idx)
        if pos < 0:
            return False
        before_ok = pos == 0 or not text_lc[pos - 1].isalnum()
        after_end = pos + len(term_lc)
        after_ok = after_end >= len(text_lc) or not text_lc[after_end].isalnum()
        if before_ok and after_ok:
            return True
        idx = pos + 1
    return False


def extract_categories_from_phrase(phrase_text: str) -> list[str]:
    """Scan the spoken text for keywords that trigger any theme category.

    Returns a list of category keys, most-matched first. Each category is
    triggered by any of its keywords appearing as a whole word (or phrase)
    in the text.

    Examples:
      "the kitchen has new cabinets" → ['kitchen']
      "we redid the living room and the kitchen" → ['living_room', 'kitchen']
      "I love sitting on the couch" → ['living_room']   (couch is in living_room keywords)
      "this is just nice" → []   (no category triggers)
    """
    if not phrase_text:
        return []
    text_lc = phrase_text.lower()

    category_hits: dict[str, int] = {}
    for cat in THEME_CATEGORIES:
        hit_count = 0
        for keyword in cat.keywords:
            if _contains_term(text_lc, keyword):
                hit_count += 1
        if hit_count > 0:
            category_hits[cat.key] = hit_count

    # Sort by hit count desc (more keyword matches = stronger signal)
    return sorted(category_hits.keys(), key=lambda k: -category_hits[k])


def extract_marker_tags_for_phrase(
    phrase_text: str,
    vocab: LibraryVocabulary,
    max_tags: int = 15,
) -> list[str]:
    """[Legacy helper, kept for tests.] Find library tags literally in phrase.

    generate_markers no longer uses this — it uses category-level matching
    via extract_categories_from_phrase instead. Kept exported so older
    tests and external callers still work.
    """
    if not phrase_text or not vocab.tag_counts:
        return []
    text_lc = phrase_text.lower()
    hits: list[tuple[int, str]] = []
    for tag, count in vocab.tag_counts.items():
        if _is_motion_tag(tag):
            continue
        if _contains_term(text_lc, tag):
            hits.append((count, tag))
    hits.sort(key=lambda x: (-x[0], x[1]))
    return [t for _, t in hits[:max_tags]]


# ---------------------------------------------------------------------------
# Main API — category-based marker generation
# ---------------------------------------------------------------------------


def generate_markers(
    aroll_track: list[ARollPhrase],
    source_phrases_by_id: dict[int, Phrase],
    vocab: LibraryVocabulary,
    segment_order_by_phrase: dict[int, int],
) -> list[BRollMarker]:
    """Produce marker suggestions for the whole cut using category matching.

    For each A-roll phrase on the final timeline:
      1. Look up the ORIGINAL spoken text.
      2. Detect which THEME CATEGORIES the phrase mentions (via keyword
         triggers — 'couch' maps to living_room, 'kitchen' to kitchen, etc.)
      3. Intersect with the set of categories that have library clips
         (no point emitting a bedroom marker if we don't have any bedroom
         clips in the library).
      4. For each matching category, emit one marker whose body carries the
         union of all library tags in that category (so any tag → bin filter).

    If a phrase mentions no categories OR a phrase's categories don't
    match any library clips → no marker for that phrase.
    """
    markers: list[BRollMarker] = []
    available_categories = vocab.categories_with_clips()

    # Drop 3.8.2: category strength floor. A category with only 1 clip OR
    # only 1 unique tag is nearly always noise — a stray frame that got
    # picked up from stale state or an out-of-domain hit. We suppress
    # these rather than emit a marker pointing at a one-tag "library"
    # that's actually empty. This is what caused Pierce's "1 people
    # marker, comment: 'person'" output: the people category had exactly
    # 1 clip and 1 tag, both probably leftovers. Living_room, bedroom,
    # etc. have many clips and many tags each when the library is real,
    # so this filter is safe for valid libraries.
    strong_categories = {
        k for k in available_categories
        if vocab.category_clip_counts.get(k, 0) >= 2
        or len(vocab.category_tags.get(k, [])) >= 2
    }
    # If the filter would leave nothing, fall back to the unfiltered set
    # rather than producing zero markers — some small libraries legitimately
    # have single-clip categories and we'd rather over-suggest than silently
    # produce an empty cut.
    if not strong_categories:
        strong_categories = available_categories
    available_categories = strong_categories

    if not available_categories:
        # Library has no clips in any category → nothing useful to mark
        return []

    for aroll in aroll_track:
        src_phrase = source_phrases_by_id.get(aroll.phrase_id)
        phrase_text = src_phrase.text if src_phrase else aroll.text
        if not phrase_text:
            continue

        phrase_categories = extract_categories_from_phrase(phrase_text)
        matching_categories = [c for c in phrase_categories if c in available_categories]

        phrase_duration = max(0.1, aroll.timeline_end - aroll.timeline_start)
        segment_order = segment_order_by_phrase.get(aroll.phrase_id, 0)

        # Drop 3.8.2: fallback for phrases with no category hit. An interview
        # often has phrases that don't mention rooms ("your house needs a
        # dating profile, not a makeover") but the editor still wants a
        # B-roll suggestion spot. We emit ONE generic marker per such
        # phrase, offering the union of all available library categories
        # so the editor can pick any category to search. Without this,
        # Pierce's 30-second Celeste cut produced only 1 marker for 5
        # phrases — he correctly flagged that as under-matching.
        if not matching_categories:
            # Round-robin across categories for the primary display so one
            # big category (e.g. living_room with 5 tags) doesn't crowd out
            # the others. Full union still goes into all_tags for the
            # comment body.
            per_cat_lists = [
                list(vocab.category_tags.get(c, []))
                for c in sorted(available_categories)
            ]
            primary_tags: list[str] = []
            primary_seen: set[str] = set()
            idx = 0
            while len(primary_tags) < 5 and any(per_cat_lists):
                bucket = per_cat_lists[idx % len(per_cat_lists)]
                if bucket:
                    t = bucket.pop(0)
                    if t not in primary_seen:
                        primary_seen.add(t)
                        primary_tags.append(t)
                idx += 1
                if idx > 500:
                    break  # safety
            all_cat_tags: list[str] = []
            seen: set[str] = set()
            for cat_key in sorted(available_categories):
                for tag in vocab.category_tags.get(cat_key, []):
                    if tag not in seen:
                        seen.add(tag)
                        all_cat_tags.append(tag)
            if not all_cat_tags:
                continue
            fallback_cat = get_category(DEFAULT_CATEGORY.key)
            offset = min(0.1, phrase_duration * 0.05)
            markers.append(BRollMarker(
                timeline_time=aroll.timeline_start + offset,
                primary_tags=primary_tags,
                all_tags=all_cat_tags,
                theme_category=fallback_cat.key,
                color_rgb=fallback_cat.color_rgb,
                phrase_id=aroll.phrase_id,
                segment_order=segment_order,
            ))
            continue

        n_cats = len(matching_categories)

        for i, cat_key in enumerate(matching_categories):
            # Stagger within phrase: 1 marker → at start; N markers → evenly
            # distributed across the phrase duration.
            if n_cats == 1:
                offset = min(0.1, phrase_duration * 0.05)
            else:
                step = phrase_duration / (n_cats + 1)
                offset = step * (i + 1)
            time_on_timeline = aroll.timeline_start + offset

            category = get_category(cat_key)

            # Pull the library tags in this category (already sorted by
            # clip frequency when the vocab was built)
            cat_lib_tags = vocab.category_tags.get(cat_key, [])
            primary = cat_lib_tags[:5]

            markers.append(BRollMarker(
                timeline_time=time_on_timeline,
                primary_tags=primary,
                all_tags=cat_lib_tags,
                theme_category=category.key,
                color_rgb=category.color_rgb,
                phrase_id=aroll.phrase_id,
                segment_order=segment_order,
            ))

    markers.sort(key=lambda m: m.timeline_time)
    return markers


def build_segment_order_map(
    aroll_track: list[ARollPhrase],
    deliverable_segments,
) -> dict[int, int]:
    """Helper: map each phrase_id to its segment order."""
    mapping: dict[int, int] = {}
    for seg in deliverable_segments:
        for pid in seg.phrase_ids:
            mapping[pid] = seg.order
    return mapping


# ---------------------------------------------------------------------------
# Display helpers for marker name + comment text
# ---------------------------------------------------------------------------


def format_marker_name(marker: BRollMarker) -> str:
    """Short marker name shown in the Premiere timeline.

    Format: 'Category: tag1, tag2, tag3'
    Kept short so it's readable while scrubbing.
    """
    category = get_category(marker.theme_category)
    prefix = category.display_name
    if not marker.primary_tags:
        return prefix
    tag_snippet = ", ".join(marker.primary_tags)
    return f"{prefix}: {tag_snippet}"


def format_marker_comment(marker: BRollMarker) -> str:
    """Full marker comment shown when editor opens the marker inspector.

    Contains the full tag list in a form that pastes directly into the
    Premiere project-panel search bar and filters the B-Roll Library bin
    to matching clips.
    """
    if not marker.all_tags:
        return "(no matching library tags)"
    tags_line = ", ".join(marker.all_tags)
    return (
        f"B-roll suggestion. Copy one of these tags into the Project "
        f"panel search bar to filter the B-Roll Library bin:\n\n{tags_line}"
    )
