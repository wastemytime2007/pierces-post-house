"""posthouse.transcript_markers — the last piece of the transcript-
flagging arc: turn TaggedFragment output into real, color-coded FCP7 XML
range markers on a synced sequence.

**What this closes out.** Ryan's original description of the Assistant
Editor's job (`docs/REQUIREMENTS.md`): "flag things by pulling up usable
pieces of information that were interesting or help to inform the
story ... color code it based on the storyline being told." The prior
three pieces (`transcript_coverage.py`'s exhaustive extraction, Project
Manager's audience-goal intake, `audience_relevance.py`'s fit scoring)
all produce data; nothing was visible in Premiere until this module
writes it as markers.

**Why a translation step is needed.** `audience_relevance.py`'s
fragments carry times in ONE source A-roll file's own coordinate space
(seconds into that file). The sequence they need to land on — PreCut's
"All Synced A-Roll" reference sequence (`exporter.py`'s
`_build_all_synced_request`) — lays multiple A-roll files end-to-end, so
a fragment's marker position on that sequence is
``phrase.timeline_start + (fragment.source_start_sec - phrase.source_start)``,
not the fragment's own source-relative time. Markers are also attached
to their phrase (``attach_to_phrase_id``) rather than placed at the
sequence level, so they ride with the clip if the editor moves it —
same convention `exporter.py` already uses for B-roll suggestion
markers.

**Real range markers, not points.** `precut_pipeline.cutlist.FlagMarker`
(added alongside this module) is a genuinely new marker type — PreCut's
existing `BRollMarker` is a POINT marker (``<out>-1</out>``) by design,
which would only mark a fragment's start, not the color-coded BLOCK
Ryan described. `FlagMarker` carries a real ``timeline_end`` and the
exporter emits a real ``<out>`` frame for it.
"""
from __future__ import annotations

from typing import List, Optional

from posthouse.precut_bridge import import_precut

_cutlist = import_precut("precut_pipeline.cutlist")
FlagMarker = _cutlist.FlagMarker


def build_flag_markers_for_phrase(
    tagged_fragments: List,
    phrase,
    max_comment_len: int = 300,
) -> List["FlagMarker"]:
    """Translate one A-roll file's TaggedFragments into FlagMarkers
    attached to its ARollPhrase on a synced sequence.

    ``tagged_fragments`` must all be for the SAME source file as
    ``phrase`` — this function does not filter by source_file, since the
    caller (building one phrase at a time) already knows which fragments
    belong to which file.
    """
    markers: List[FlagMarker] = []
    for tf in tagged_fragments:
        f = tf.fragment
        # Clamp to the phrase's own source range — a fragment can't
        # legitimately extend past what's actually on this phrase's clip.
        clamped_start = max(f.source_start_sec, phrase.source_start)
        clamped_end = min(f.source_end_sec, phrase.source_end)
        if clamped_end <= clamped_start:
            continue

        timeline_start = phrase.timeline_start + (clamped_start - phrase.source_start)
        timeline_end = phrase.timeline_start + (clamped_end - phrase.source_start)

        comment = f"{tf.fit}: {tf.reasoning}" if tf.reasoning else tf.fit
        markers.append(FlagMarker(
            timeline_start=timeline_start,
            timeline_end=timeline_end,
            name=f.topic_label or "Flagged moment",
            comment=comment[:max_comment_len],
            color_rgb=tf.color_rgb,
            attach_to_phrase_id=getattr(phrase, "phrase_id", None),
        ))
    return markers


def build_flag_markers_for_synced_sequence(
    tagged_fragments_by_source_file: dict,
    phrases: List,
) -> List["FlagMarker"]:
    """Build FlagMarkers for every phrase in a synced sequence's
    aroll_track, given a {source_file: [TaggedFragment]} mapping.

    Convenience wrapper over :func:`build_flag_markers_for_phrase` for
    the common case: one "All Synced A-Roll" cutlist covering several
    files, each with its own tagged fragments.
    """
    all_markers: List[FlagMarker] = []
    for phrase in phrases:
        fragments = tagged_fragments_by_source_file.get(phrase.source_file)
        if not fragments:
            continue
        all_markers.extend(build_flag_markers_for_phrase(fragments, phrase))
    return all_markers
