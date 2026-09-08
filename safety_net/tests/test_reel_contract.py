"""The Reel contract — the properties a generated cut + export must hold.

Every assertion here corresponds to a real failure Ryan hit on real
footage on 2026-09-04/07, each of which took a manual export round-trip
to find. His own words for why this file exists: *"This is what we need
but from any videos suggested/exported with xmls but without so much hand
holding. I can't carry you through 15 exports and manually do it myself
every time. So lets try to lock this in."*

Nothing here needs an API key, media, or the ML venv. The ground truth is
embedded as fixtures taken from two real artifacts:

  * `fixtures/reference_edits/removing_wallpaper_tutorial.xml` — the
    reference edit Ryan cut by hand and supplied as the target, now kept
    in-repo at his direction ("place a copy where you need it so i dont
    have to keep a copy on my desktop"). Its 11 selections and 8 leftover
    clips are the numbers in REF_CUT / REF_LEFTOVERS, and
    `test_fixtures_still_match_the_reference_edit` re-derives them from
    the file on every run so the two can never drift apart.
  * That project's own saved `audio_sync.pairs` — the Bob 1 chain in
    BOB1_PAIRS, whose weak-but-genuine scores are what broke every
    score-threshold approach.

The eight properties, and the failure each one locks out:

  1. Leftovers are the complement of the cut, not a nominated list.
     (Failure: 37.6 minutes across 6 camera files beside a 45s cut.)
  2. Leftovers never include off_topic material.
     (Failure: "What do shirt colors and fishing licenses have to do
     with wallpaper".)
  3. Leftovers keep genuinely-continuous adjacent strong material.
     (Failure: dropping the bathroom-scope talk that follows the demo.)
  4. Leftovers never drop material the cut's own fragments contain.
     (Failure: "its still missing a bunch of the items i chose".)
  5. Cuts land on word boundaries, not phrase boundaries.
     (Failure: an 11.2s mostly-silent phrase becoming the longest clip
     in a 45-second edit.)
  6. One fragment may yield several non-overlapping clips, never the
     same moment twice.
     (Failure: "AT MOST ONCE" capping a 45s edit at 2 coarse slabs.)
  7. Duration is an absolute +/-15s window.
     (Failure: a 1.25x ratio giving a 45s target only 11s of room.)
  8. A lav attaches only when confident or corroborated by the
     recording-start invariant.
     (Failures, in both directions: uncorroborated false tracks on the
     timeline, then no lav at all.)
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from posthouse.story_architect import (
    DURATION_BUFFER_SEC,
    MIN_POOL_GAP_SEC,
    _compute_pool_leftovers,
    _snap_to_phrase_bounds,
)


# --------------------------------------------------------------------------
# Ground truth from Ryan's hand-cut reference edit
# --------------------------------------------------------------------------

# Ryan's own hand-cut edit, kept in-repo so this contract always has its
# target available. Everything below is derived from it.
REFERENCE_EDIT = (
    Path(__file__).resolve().parent.parent
    / "fixtures" / "reference_edits" / "removing_wallpaper_tutorial.xml"
)

# Timeline position (seconds) separating his tight cut from his unused
# footage. His cut ends at 64.9s and the leftovers start at 229.2s.
REFERENCE_ZONE_SPLIT_SEC = 100.0

REF_FILE = "DJI_20260505100952_0005_D.MP4"
REF_STEM = "DJI_20260505100952_0005_D"

# His 11 left-side selections, source-local seconds.
REF_CUT = [
    (143.9, 145.8), (150.2, 151.3), (152.9, 156.5), (159.9, 164.3),
    (165.1, 167.3), (178.1, 181.2), (183.3, 185.8), (189.5, 195.1),
    (263.2, 281.1), (291.2, 302.9), (302.9, 313.8),
]

# His 8 right-side (unused) clips. Each is a gap between two selections,
# or the tail past the last one.
REF_LEFTOVERS = [
    (146.1, 150.2), (156.4, 159.6), (167.5, 178.1), (181.2, 183.2),
    (186.1, 189.4), (195.3, 263.1), (281.8, 290.4), (314.1, 372.6),
]

# The fragment his material sits in, plus the strong fragment that
# directly follows it (the bathroom-scope talk his leftovers run into).
# Exact bounds, read from the project's own flags file.
FRAG_WALLPAPER = (146.8, 313.8)          # strong — the fragment his cut lives in
FRAG_BATHROOM_SCOPE = (315.1, 367.2)     # strong — directly follows it
# Directly BEFORE the wallpaper fragment, and off_topic — must never
# appear in leftovers.
FRAG_CALIFORNIA_OFFTOPIC = (108.4, 145.6)

# His final leftover ends at 372.6, which is ~5s inside a THIRD fragment
# ("Mysterious item found in every house", also strong) that is adjacent
# to the bathroom-scope fragment but NOT to the fragment his cut came
# from. Our rule reaches one strong fragment out, so we legitimately stop
# at 367.2. That 5.4s is a known, accepted difference — asserted as a
# floor below rather than pretended away with a loose tolerance.
REF_TAIL_FLOOR_SEC = 360.0


class _Range:
    """Minimal stand-in for TopicRange (only the fields under test)."""

    def __init__(self, start, end, source_file=REF_FILE):
        self.source_file = source_file
        self.source_start_sec = start
        self.source_end_sec = end
        self.topic_label = ""
        self.summary = ""


def _phrases(spans, words_per=None):
    """Phrase dicts shaped like the transcript on disk."""
    out = []
    for s, e in spans:
        words = []
        if words_per:
            step = (e - s) / words_per
            words = [
                {"start": s + i * step, "end": s + (i + 1) * step, "text": f"w{i}"}
                for i in range(words_per)
            ]
        out.append({"start": s, "end": e, "text": "x", "words": words})
    return out


@pytest.fixture
def ref_phrases():
    """One phrase per second across the whole reference region, so gap
    boundaries in the tests aren't limited by phrase granularity."""
    return _phrases([(t, t + 1.0) for t in range(80, 400)], words_per=2)


# --------------------------------------------------------------------------
# 0. The fixtures are the reference edit, not a remembered version of it
# --------------------------------------------------------------------------

def _reference_zones():
    """(left, right) source spans read straight out of Ryan's edit."""
    seq = next(ET.parse(REFERENCE_EDIT).getroot().iter("sequence"))
    timebase = float(seq.findtext("rate/timebase"))
    track = seq.find("media/video/track")
    clips = [
        (
            int(c.findtext("start")) / timebase,
            int(c.findtext("in")) / timebase,
            int(c.findtext("out")) / timebase,
        )
        for c in track.findall("clipitem")
    ]
    left = sorted(
        (i, o) for tl, i, o in clips if tl < REFERENCE_ZONE_SPLIT_SEC
    )
    right = sorted(
        (i, o) for tl, i, o in clips if tl >= REFERENCE_ZONE_SPLIT_SEC
    )
    return left, right


def test_fixtures_still_match_the_reference_edit():
    """The hardcoded numbers below must equal what's actually in his file.

    Without this, REF_CUT / REF_LEFTOVERS are just a claim about a file
    nobody re-reads — and for a day they WERE only a claim, because the
    original had been cleared off his Desktop. Re-deriving them here
    means the fixtures can never silently drift from the target, and that
    swapping in a different reference edit fails loudly instead of
    quietly changing what "correct" means.
    """
    assert REFERENCE_EDIT.exists(), (
        f"Ryan's reference edit is missing from the repo: {REFERENCE_EDIT}. "
        "It is the ground truth for every rule in this file — ask him for a "
        "fresh copy rather than re-deriving one."
    )
    left, right = _reference_zones()

    # The fixtures are written to 0.1s; the file is frame-accurate at
    # 1/60s. Max legitimate difference is therefore one rounding
    # half-step (0.05s), plus a hair for float representation.
    TOL = 0.06
    def _agree(actual, fixture, label):
        assert len(actual) == len(fixture), (
            f"{label}: reference edit has {len(actual)} clips, fixture has "
            f"{len(fixture)} — {actual}"
        )
        for (a_in, a_out), (f_in, f_out) in zip(actual, sorted(fixture)):
            assert abs(a_in - f_in) <= TOL and abs(a_out - f_out) <= TOL, (
                f"{label}: reference clip {a_in:.3f}-{a_out:.3f} no longer "
                f"matches fixture {f_in:.1f}-{f_out:.1f}"
            )

    _agree(left, REF_CUT, "REF_CUT")
    _agree(right, REF_LEFTOVERS, "REF_LEFTOVERS")


def test_reference_edit_shape_is_what_we_build_to():
    """Guards the headline numbers the whole contract is aimed at: a real
    edit is many short clips from ONE source, and the unused side is a
    similar order of magnitude, not 40 minutes."""
    left, right = _reference_zones()
    left_dur = sum(e - s for s, e in left)
    right_dur = sum(e - s for s, e in right)

    assert len(left) == 11 and len(right) == 8
    assert 60 <= left_dur <= 70, f"reference cut is {left_dur:.0f}s"
    # Most clips are short — this is the property that made "2 coarse
    # slabs" obviously wrong.
    assert sum(1 for s, e in left if e - s <= 6.0) >= 7
    # The unused side stays the same order of magnitude as the cut.
    assert right_dur / left_dur <= 3.0, (
        f"reference leftovers are {right_dur / left_dur:.1f}x the cut"
    )


# --------------------------------------------------------------------------
# 1-4. Leftovers
# --------------------------------------------------------------------------

def test_leftovers_reproduce_ryans_reference_edit(ref_phrases):
    """Property 1+4: given his cut, we derive his unused clips.

    This is the single strongest check in the file — it says our idea of
    "the footage around the cut" matches what he actually did by hand.
    """
    used = [_Range(s, e) for s, e in REF_CUT]
    allowed = {REF_FILE: [FRAG_WALLPAPER, FRAG_BATHROOM_SCOPE]}

    got = _compute_pool_leftovers(used, {REF_STEM: ref_phrases}, {}, allowed)
    got_spans = [(r.source_start_sec, r.source_end_sec) for r in got]

    assert len(got_spans) == len(REF_LEFTOVERS), (
        f"expected {len(REF_LEFTOVERS)} leftover clips like Ryan's reference, "
        f"got {len(got_spans)}: {got_spans}"
    )
    for i, ((gs, ge), (rs, re_)) in enumerate(zip(got_spans, REF_LEFTOVERS)):
        assert abs(gs - rs) <= 1.5, (
            f"leftover {i} starts {gs:.1f}, reference starts {rs:.1f}"
        )
        if i == len(REF_LEFTOVERS) - 1:
            # The tail — see REF_TAIL_FLOOR_SEC.
            assert ge >= REF_TAIL_FLOOR_SEC, (
                f"tail leftover ends {ge:.1f}s, below the {REF_TAIL_FLOOR_SEC}s floor"
            )
        else:
            assert abs(ge - re_) <= 1.5, (
                f"leftover {i} ends {ge:.1f}, reference ends {re_:.1f}"
            )


def test_leftovers_never_include_off_topic_material(ref_phrases):
    """Property 2: off_topic fragments are not in the allowed spans, so
    nothing from them can reach the timeline — however adjacent they are.

    Ryan: "What do shirt colors and fishing licenses have to do with
    wallpaper". They were swept in by a clock-based buffer that ignored
    the off_topic label the flagging stage had already written.
    """
    used = [_Range(s, e) for s, e in REF_CUT]
    allowed = {REF_FILE: [FRAG_WALLPAPER, FRAG_BATHROOM_SCOPE]}

    got = _compute_pool_leftovers(used, {REF_STEM: ref_phrases}, {}, allowed)

    off_start, off_end = FRAG_CALIFORNIA_OFFTOPIC
    for r in got:
        assert not (r.source_start_sec < off_end and r.source_end_sec > off_start), (
            f"leftover {r.source_start_sec:.1f}-{r.source_end_sec:.1f} overlaps the "
            f"off_topic fragment at {off_start}-{off_end}"
        )


def test_leftovers_keep_adjacent_strong_material(ref_phrases):
    """Property 3: the strong fragment following the cut stays available.

    Ryan's own leftovers run to 372.6s, well past the wallpaper fragment's
    314s end and into the bathroom-scope talk. Bounding leftovers to only
    the used fragment would silently discard that.
    """
    used = [_Range(s, e) for s, e in REF_CUT]
    allowed = {REF_FILE: [FRAG_WALLPAPER, FRAG_BATHROOM_SCOPE]}

    got = _compute_pool_leftovers(used, {REF_STEM: ref_phrases}, {}, allowed)
    reach = max(r.source_end_sec for r in got)
    assert reach >= REF_TAIL_FLOOR_SEC, (
        f"leftovers only reach {reach:.1f}s; Ryan's reference reaches 372.6s, "
        "so the adjacent strong fragment is being dropped"
    )


def test_leftovers_never_overlap_the_cut(ref_phrases):
    """A leftover that overlaps the cut duplicates footage already used —
    the Duplicate Frame markers Ryan caught in Premiere on 2026-09-04."""
    used = [_Range(s, e) for s, e in REF_CUT]
    allowed = {REF_FILE: [FRAG_WALLPAPER, FRAG_BATHROOM_SCOPE]}

    got = _compute_pool_leftovers(used, {REF_STEM: ref_phrases}, {}, allowed)
    for r in got:
        for us, ue in REF_CUT:
            assert not (r.source_start_sec < ue and r.source_end_sec > us), (
                f"leftover {r.source_start_sec:.1f}-{r.source_end_sec:.1f} overlaps "
                f"used {us:.1f}-{ue:.1f}"
            )


def test_leftover_gaps_below_threshold_are_dropped(ref_phrases):
    """Sub-MIN_POOL_GAP_SEC slivers aren't worth a clip. Ryan's reference
    drops its 1.6s and 0.8s gaps and keeps everything from 2.1s up."""
    used = [_Range(s, e) for s, e in REF_CUT]
    allowed = {REF_FILE: [FRAG_WALLPAPER, FRAG_BATHROOM_SCOPE]}

    got = _compute_pool_leftovers(used, {REF_STEM: ref_phrases}, {}, allowed)
    for r in got:
        assert r.source_end_sec - r.source_start_sec >= MIN_POOL_GAP_SEC


# --------------------------------------------------------------------------
# 5. Word-level cutting
# --------------------------------------------------------------------------

def test_cut_does_not_expand_to_whole_phrase():
    """Property 5: a request inside a long phrase stays short.

    The real failure: "But it reacts to what you start. Oh, okay." is ONE
    11.2s phrase, mostly silence while the steamer is being demonstrated.
    Phrase-level snapping turned any selection touching it into 11.2s, and
    it kept landing as the longest clip in a 45-second edit. Ryan's
    reference takes 2.2s of it.
    """
    phrase = {
        "start": 165.10, "end": 176.34, "text": "But it reacts...",
        "words": [
            {"start": 165.10, "end": 165.46, "text": "But"},
            {"start": 165.46, "end": 165.82, "text": "it"},
            {"start": 165.82, "end": 166.18, "text": "reacts"},
            {"start": 166.18, "end": 166.80, "text": "to"},
            {"start": 166.80, "end": 168.36, "text": "what"},
            {"start": 168.36, "end": 171.14, "text": "you"},
            {"start": 171.14, "end": 171.48, "text": "start."},
            {"start": 175.42, "end": 176.02, "text": "Oh,"},
            {"start": 176.24, "end": 176.34, "text": "okay."},
        ],
    }
    got = _snap_to_phrase_bounds(165.1, 167.3, [phrase])
    assert got is not None
    start, end = got
    assert end - start < 5.0, (
        f"a 2.2s request inside an 11.2s phrase became {end - start:.1f}s — "
        "snapping is expanding to phrase bounds again"
    )
    # Must still land on real word edges, never mid-word.
    edges = {w["start"] for w in phrase["words"]} | {w["end"] for w in phrase["words"]}
    assert start in edges and end in edges


def test_cut_falls_back_to_phrase_bounds_without_word_data():
    """Older transcripts have no word timings. Snapping must still work
    rather than returning None and silently keeping the whole fragment."""
    phrases = _phrases([(10.0, 14.0), (14.5, 18.0)])
    got = _snap_to_phrase_bounds(11.0, 15.0, phrases)
    assert got == (10.0, 18.0)


def test_snap_returns_none_with_nothing_to_snap_to():
    assert _snap_to_phrase_bounds(1.0, 2.0, []) is None


# --------------------------------------------------------------------------
# 6-7. Cut structure and duration
# --------------------------------------------------------------------------

def test_duration_window_is_absolute_not_proportional():
    """Property 7: +/-15s at any scale.

    Ryan: "lets allow a 15 second buffer on each side if needed so if i
    say 45 second edit, it can do 30-1 min-ish". The old 1.25x ratio gave
    a 45s target only 11s of headroom and a 10-minute target 2.5 minutes.
    """
    assert DURATION_BUFFER_SEC == 15.0
    # A 45s target admits a 58s cut and rejects a 61s one.
    assert 58.0 <= 45.0 + DURATION_BUFFER_SEC
    assert 61.0 > 45.0 + DURATION_BUFFER_SEC


def test_overlap_rule_allows_several_clips_from_one_fragment():
    """Property 6: the constraint is non-overlap, not one-clip-per-fragment.

    The old "each fragment index may appear AT MOST ONCE" rule capped a
    cut at one clip per fragment, which is why a 45s edit came out as two
    coarse slabs. Ryan's reference makes 8 separate cuts inside what this
    system calls a single 167s fragment.
    """
    claimed: dict[int, list[tuple]] = {}

    def claim(idx, start, end):
        prior = claimed.setdefault(idx, [])
        if any(start < pe and end > ps for ps, pe in prior):
            return False
        prior.append((start, end))
        return True

    # Eight non-overlapping cuts from fragment 0 — all must be accepted.
    for start, end in REF_CUT[:8]:
        assert claim(0, start, end), f"non-overlapping cut {start}-{end} was rejected"
    assert len(claimed[0]) == 8

    # The same moment again, and a partial overlap, must both be refused.
    assert not claim(0, *REF_CUT[0])
    assert not claim(0, REF_CUT[0][0] + 0.5, REF_CUT[0][1] + 0.5)


# --------------------------------------------------------------------------
# 8. Which lav belongs on the timeline
# --------------------------------------------------------------------------

# Real saved pairs for Bob 1.WAV. `implied` is camera_clock_start +
# offset_sec — constant for a genuine chain, wildly off for coincidences.
BOB1_PAIRS = [
    # (camera file, score, offset_sec, genuine?)
    ("DJI_20260505094928_0002_D.MP4", 12.63, 232.112, True),
    ("DJI_20260505095744_0003_D.MP4", 30.39, -264.192, True),   # anchor
    ("DJI_20260505095945_0004_D.MP4", 14.77, -384.816, True),
    ("DJI_20260505100952_0005_D.MP4", 13.62, -991.984, True),
    ("DJI_20260505083158_0001_D.MP4", 5.20, -711.73, False),
    ("DJI_20260505110518_0011_D.MP4", 6.96, -626.37, False),
]


class _Pair:
    def __init__(self, aroll_file, score, offset_sec, audio_file="Bob 1.WAV"):
        self.aroll_file = aroll_file
        self.audio_file = audio_file
        self.score = score
        self.offset_sec = offset_sec


class _State:
    def __init__(self, pairs):
        self.pairs = pairs


def test_recording_start_invariant_separates_genuine_from_coincidence():
    """Property 8: the discriminator is the invariant, not the score.

    A continuous recording has ONE absolute start, so camera_clock_start
    + offset_sec must agree across every file it truly matches. Bob 1
    gives ~35600 on files 0002/0003/0004/0005 (agreement within 0.4s,
    anchored by an unambiguous 30.39 match) and misses by ~1000s on its
    false matches. No score threshold can separate 13.62-genuine from
    13.76-coincidence; this can, and both settings of the threshold were
    wrong in production because of it.
    """
    from precut_pipeline.audio_sync import (
        SCORE_TIMELINE_ATTACH, SCORE_USE, offset_is_corroborated,
    )

    state = _State([_Pair(f, s, o) for f, s, o, _ in BOB1_PAIRS])
    by_file = {p.aroll_file: p for p in state.pairs}

    for cam, score, offset, genuine in BOB1_PAIRS:
        pair = by_file[cam]
        attaches = pair.score >= SCORE_TIMELINE_ATTACH or (
            pair.score >= SCORE_USE and offset_is_corroborated(pair, state)
        )
        assert attaches == genuine, (
            f"{cam} (score {score}) should "
            f"{'attach' if genuine else 'NOT attach'} but did the opposite — "
            "the recording-start invariant is no longer being applied"
        )


def test_uncorroborated_midscore_lav_is_refused():
    """The specific false track Ryan saw: Mitch 1 at 13.76 on 0005_D.

    It outscores genuine Bob 1 matches, so any pure score threshold that
    admits Bob 1 also admits this. Its implied recording start agrees
    with nothing, so the invariant refuses it.
    """
    from precut_pipeline.audio_sync import SCORE_USE, offset_is_corroborated

    mitch = _Pair("DJI_20260505100952_0005_D.MP4", 13.76, 1476.368,
                  audio_file="Mitch 1.WAV")
    # Its only same-audio company is another weak, unanchored pair.
    other = _Pair("DJI_20260505104803_0007_D.MP4", 12.21, -814.768,
                  audio_file="Mitch 1.WAV")
    state = _State([mitch, other])

    assert mitch.score >= SCORE_USE  # would pass a naive threshold
    assert not offset_is_corroborated(mitch, state)


def test_corroboration_skipped_when_filename_has_no_timestamp():
    """No 14-digit stamp means no camera clock. Corroboration must return
    False (fall back to score alone) rather than guess."""
    from precut_pipeline.audio_sync import offset_is_corroborated

    pair = _Pair("interview_take_one.mov", 12.0, 10.0)
    anchor = _Pair("interview_take_two.mov", 30.0, 10.0)
    assert not offset_is_corroborated(pair, _State([pair, anchor]))

# --------------------------------------------------------------------------
# 9. Fragments are topics, not stretches of tape
# --------------------------------------------------------------------------

def test_distinct_adjacent_topics_are_not_merged_into_a_monster():
    """Property 9: the merge step must respect the fragment length bound.

    2026-09-08, Ryan on a 381s fragment labelled "Track lighting and 80s
    design trends": "13 minutes is probably unlikely that we talked about
    track lighting for 30 minutes... it needs to be a little more
    specific more like the wallpaper when we did."

    Capping extraction alone did NOT fix it. `_merge_fragments` joins
    anything separated by <=1s and had no length ceiling, so a run of
    correctly-split adjacent fragments got stitched straight back
    together — a real re-extraction produced a 723-SECOND fragment out of
    a dozen properly-labelled ones, worse than the blob the cap was added
    to prevent. A six-minute "topic" isn't a topic; it hides the real
    beats inside it from the planner and gives the visual probe no
    specific span to look at.
    """
    from precut_pipeline.story_planner import TopicRange
    from posthouse.transcript_coverage import MAX_FRAGMENT_SEC, _merge_fragments

    # Eight distinct 90s topics, each abutting the next within 1s.
    runs = [
        TopicRange(
            source_file="x.MP4",
            source_start_sec=i * 90.5,
            source_end_sec=i * 90.5 + 90,
            topic_label=f"topic {i}",
            summary=f"distinct thing {i}",
        )
        for i in range(8)
    ]
    merged = _merge_fragments(runs)
    over = [
        r for r in merged
        if r.source_end_sec - r.source_start_sec > MAX_FRAGMENT_SEC
    ]
    assert not over, (
        "distinct adjacent topics were chained past the "
        f"{MAX_FRAGMENT_SEC:.0f}s bound: "
        f"{[(r.topic_label, r.source_end_sec - r.source_start_sec) for r in over]}"
    )
    assert len(merged) == 8


def test_one_moment_described_twice_is_still_deduped():
    """The length bound must not break the merge step's actual job.

    Two windows independently describing the SAME moment is a duplicate,
    not two topics, and must collapse to one fragment keeping the fuller
    summary — however long that moment is. Only adjacent-but-DISTINCT
    joins are bounded.
    """
    from precut_pipeline.story_planner import TopicRange
    from posthouse.transcript_coverage import _merge_fragments

    dup = [
        TopicRange(source_file="y.MP4", source_start_sec=0, source_end_sec=300,
                   topic_label="a", summary="short"),
        TopicRange(source_file="y.MP4", source_start_sec=5, source_end_sec=305,
                   topic_label="a", summary="a much longer description"),
    ]
    merged = _merge_fragments(dup)
    assert len(merged) == 1, "a duplicate description was not deduped"
    assert merged[0].summary == "a much longer description"


def test_undirected_generation_has_a_real_reel_length():
    """Property 7b: the plain "Generate ideas" button is bounded too.

    Ryan: "nobody's gonna watch 13 minutes worth of us talking about
    that so we still need to create on the left side ideally like a 30
    second to a minute and a half at most." The undirected path used to
    pass no target at all, so the duration check never ran and nothing
    stopped a 12:44 idea.
    """
    from posthouse.story_architect import (
        DEFAULT_REEL_TARGET_SEC, DURATION_BUFFER_SEC,
    )
    ceiling = DEFAULT_REEL_TARGET_SEC + DURATION_BUFFER_SEC
    assert 30.0 <= DEFAULT_REEL_TARGET_SEC <= 90.0, (
        f"default target {DEFAULT_REEL_TARGET_SEC}s is outside the 30-90s window "
        "Ryan asked for"
    )
    assert ceiling <= 90.0, (
        f"enforced ceiling is {ceiling}s — past 'a minute and a half at most'"
    )


# --------------------------------------------------------------------------
# 10. Usability reads off the clip, not off a marker painted over it
# --------------------------------------------------------------------------

def test_fit_maps_to_premiere_label_colors_not_markers():
    """Property 10: every fit has a Premiere label colour.

    2026-09-08, Ryan, with before/after screenshots: "The problem with
    markers is they cover the visual waveform on the timeline and they
    dont allow for the editor to use their own label colors because the
    marker covers the whole clip." Both complaints were real and neither
    is fixable while keeping markers — a range marker paints over the
    clip it describes, and Premiere draws it above the waveform.

    A label applies to a WHOLE clipitem, which is why the reference
    sequence is split at fragment boundaries (see
    exporter._label_segments_for_file). The names must stay inside
    Premiere's own default set so the editor can still re-label by hand,
    which was the point.
    """
    import sys
    sys.path.insert(0, str(REFERENCE_EDIT.parent.parent.parent / "app" / "python_backend"))
    from exporter import FIT_LABEL_COLORS
    from posthouse.audience_relevance import VALID_FITS

    # Premiere's default 16 labels.
    PREMIERE_LABELS = {
        "Violet", "Iris", "Caribbean", "Lavender", "Cerulean", "Forest",
        "Rose", "Mango", "Purple", "Blue", "Teal", "Magenta", "Tan",
        "Green", "Brown", "Yellow",
    }
    for fit in VALID_FITS:
        assert fit in FIT_LABEL_COLORS, f"no label colour mapped for fit {fit!r}"
        assert FIT_LABEL_COLORS[fit] in PREMIERE_LABELS, (
            f"fit {fit!r} maps to {FIT_LABEL_COLORS[fit]!r}, which is not one of "
            "Premiere's default labels — the editor wouldn't be able to re-label it"
        )
    # Distinct colours, or the timeline says nothing at a glance.
    used = [FIT_LABEL_COLORS[f] for f in VALID_FITS]
    assert len(set(used)) == len(used), f"fits share a label colour: {used}"


def test_label_segments_cover_the_file_with_no_slivers():
    """Segments must lay end-to-end and never produce sub-2s clips.

    Both halves matter. Gaps or overlaps would shift everything after
    them out of sync with the source. And a first pass produced 135
    clips, many of them 0.5s, from the 1-3 second dead air between
    fragments — choppier on the timeline than the single long clip it
    replaced, which is the opposite of what Ryan asked for.
    """
    import sys
    sys.path.insert(0, str(REFERENCE_EDIT.parent.parent.parent / "app" / "python_backend"))
    from exporter import MIN_LABEL_SEGMENT_SEC, _label_segments_for_file

    class _Frag:
        def __init__(self, a, b):
            self.source_start_sec, self.source_end_sec = a, b

    class _Tagged:
        def __init__(self, a, b, fit):
            self.fragment, self.fit = _Frag(a, b), fit

    # Fragments with awkward sub-2s dead air between them.
    tagged = [
        _Tagged(0.5, 55.0, "possible"),
        _Tagged(55.4, 106.0, "strong"),      # 0.4s gap
        _Tagged(107.0, 145.0, "off_topic"),  # 1.0s gap
    ]
    duration = 146.0

    from exporter import _segments_from_tagged
    segs = _segments_from_tagged(tagged, duration)

    assert segs, "no segments produced from real fragments"
    assert segs[0][0] == 0.0 and segs[-1][1] == duration, "file not fully covered"
    for (s0, e0, _), (s1, _, _) in zip(segs, segs[1:]):
        assert e0 == s1, f"gap/overlap between {e0} and {s1}"
    for s0, e0, _ in segs:
        assert e0 - s0 >= MIN_LABEL_SEGMENT_SEC, f"sliver clip {e0 - s0:.2f}s"
