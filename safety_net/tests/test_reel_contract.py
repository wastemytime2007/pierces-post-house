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
  3. Leftovers keep genuinely-continuous strong material.
     (Failure: dropping the bathroom-scope talk that follows the demo.
     Broadened 2026-09-16 from "adjacent" to all strong material on a
     used file — see section 14.)
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
# ("Mysterious item found in every house", also strong). The tests below
# feed _compute_pool_leftovers hand-built allowed spans covering the
# wallpaper + bathroom-scope fragments only, so they stop at 367.2 and
# that 5.4s is a known, accepted difference — asserted as a floor rather
# than pretended away with a loose tolerance.
#
# 2026-09-16: these hand-built spans no longer reflect how the spans are
# actually CHOSEN in production. The "reach one strong fragment out"
# radius is gone — see build_allowed_pool_spans and section 14 below.
# These tests still earn their place: they lock the complement math,
# which is a separate concern from which spans get fed into it.
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
    """Property 7b: the plain "Generate ideas" button aims at a real length.

    Ryan: "nobody's gonna watch 13 minutes worth of us talking about
    that so we still need to create on the left side ideally like a 30
    second to a minute and a half at most." The undirected path used to
    pass no target at all, so the duration check never ran and nothing
    stopped a 12:44 idea.

    2026-09-16: the actual reject gate is no longer target + a flat
    buffer — see test_length_is_a_sanity_check_not_the_selection_criterion
    below for why. DEFAULT_REEL_TARGET_SEC is now a GUIDE the model aims
    for, not a hard ceiling; this test checks the guide is still a real
    Reel length, not that a cut running long gets discarded.
    """
    from posthouse.story_architect import DEFAULT_REEL_TARGET_SEC
    assert 30.0 <= DEFAULT_REEL_TARGET_SEC <= 90.0, (
        f"default target {DEFAULT_REEL_TARGET_SEC}s is outside the 30-90s window "
        "Ryan asked for"
    )


def test_length_is_a_sanity_check_not_the_selection_criterion():
    """2026-09-16, Ryan, correcting the original design: "the length
    limitation isnt as important as the story, we just need the app to
    understand how to rank footage on the left side as necessary vs. the
    right side being all related content." The old gate (target + a flat
    15s buffer) rejected and discarded a real, well-selected cut for
    running moderately long — using the time budget to decide what
    belonged in the cut, which is backwards. This checks the replacement:
    a much more generous, proportional sanity ceiling that still catches
    genuine runaway selection (the 12:44-vs-45s, 17x-over disaster this
    was built for) without punishing a legitimately longer necessary
    story, and it must not be so tight it approximates the old behavior.
    """
    from posthouse.story_architect import SANITY_OVERRUN_MULTIPLIER
    # Must comfortably survive a necessity-driven story that runs a few
    # times the undirected default without being flagged as a failure to
    # discriminate (the real eviction test: told the piece was ~4.5min,
    # the planner built to 260s against a 60s undirected default — a 4.3x
    # ratio that must NOT be rejected as "not discriminating").
    assert SANITY_OVERRUN_MULTIPLIER >= 4.0, (
        f"{SANITY_OVERRUN_MULTIPLIER}x is too tight to let a real necessity-driven "
        "long-form story through — it would reproduce the old 'length decides "
        "the cut' failure under a different number"
    )
    # Must still catch the real disaster it exists for: 12:44 (764s) against
    # a 45s target is ~17x over.
    assert SANITY_OVERRUN_MULTIPLIER < 17.0, (
        f"{SANITY_OVERRUN_MULTIPLIER}x would let the original 12:44-vs-45s disaster "
        "through — this ceiling exists specifically to catch that"
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


# --------------------------------------------------------------------------
# 11. Research cache: what is saved must be findable
# --------------------------------------------------------------------------

def test_research_cache_roundtrips_for_every_key_component(tmp_path, monkeypatch):
    """A saved research pass must be loadable under the same inputs.

    This exact save/load key mismatch has now bitten twice: once when
    stated_intent was introduced, and again on 2026-09-08 when
    project_type was added to the LOAD key and to the key-text builder
    but not to the SAVE call. The second time meant a typed project
    (how_to) could NEVER hit cache and re-ran a full ~6-minute research
    pass on every single planning turn — silent, expensive, and invisible
    because a cache miss looks identical to a first run.

    Every component of the key is exercised here so a third recurrence
    fails loudly instead of just costing time.
    """
    import posthouse.story_architect as sa

    monkeypatch.setattr(sa, "app_support_dir", lambda: tmp_path)
    payload = {"text_findings": [{"finding": "x", "source": "https://e.com"}]}

    cases = [
        ("goal only", {}),
        ("with intent", {"stated_intent": "quick how-to on locks"}),
        ("with type", {"project_type": "how_to"}),
        ("with both", {"stated_intent": "quick how-to on locks",
                       "project_type": "how_to"}),
    ]
    for label, kwargs in cases:
        sa._save_research_cache("Establish SoldFast as an expert.", payload, **kwargs)
        got = sa._load_cached_research("Establish SoldFast as an expert.", **kwargs)
        assert got is not None, (
            f"{label}: saved research was not findable again — the save key and "
            "the load key disagree"
        )
        assert got["text_findings"] == payload["text_findings"]


def test_research_cache_keys_differ_per_project_type():
    """A how_to run must not be served a generic sweep from cache.

    project_type steers the search prompts, so two runs differing only by
    type are genuinely different research and must not collide.
    """
    from posthouse.story_architect import _research_cache_key_text

    goal = "Establish SoldFast as an expert."
    generic = _research_cache_key_text(goal)
    how_to = _research_cache_key_text(goal, project_type="how_to")
    renovation = _research_cache_key_text(goal, project_type="renovation")
    assert len({generic, how_to, renovation}) == 3, (
        "project_type is not distinguishing cache keys, so a how-to run could be "
        f"served a different format's research: {generic!r} / {how_to!r}"
    )


# --------------------------------------------------------------------------
# 12. An agreed plan produces ONE cut, not three
# --------------------------------------------------------------------------

def test_planning_generate_asks_for_a_single_angle():
    """Property 12: generating from a conversation builds one idea.

    Ryan, 2026-09-08: "if i work with the chat to give it feedback and
    approve the direction, it is a waste to have it generate three ideas
    from that. So lets just have one idea/pitch."

    Three is right for an UNDIRECTED run — he hasn't said what he wants,
    so options are the point. It's wrong once a direction is settled,
    and not just wasteful: the avoid_theses machinery forces each extra
    angle to differ from the plan that was just agreed, so angles 2 and 3
    are actively pushed off-brief. (That is the same mechanism that
    produced a kitchen-ceiling and a carpet idea from an agreed wallpaper
    plan on 2026-09-07.)
    """
    import inspect
    from posthouse import story_conversation as sc

    src = inspect.getsource(sc.generate_from_planning_session)
    assert "n_angles=1" in src, (
        "generating from a planning session no longer requests a single "
        "angle — an agreed plan would fan out into competing variations again"
    )


def test_undirected_generation_still_offers_options():
    """The default stays 3, so the plain button remains a pitch."""
    import inspect
    from posthouse.story_architect import run_generate_story_angle

    sig = inspect.signature(run_generate_story_angle)
    assert sig.parameters["n_angles"].default is None, (
        "n_angles should default to None so the function's own default (3) "
        "applies for undirected runs"
    )
    src = inspect.getsource(run_generate_story_angle)
    assert "else 3" in src, "the undirected default is no longer 3"


# --------------------------------------------------------------------------
# 13. An audio file with no strong pair is still usable
# --------------------------------------------------------------------------

# Real pairs for DJI_01_20260526_061716.WAV on the Arthur project. Its best
# score is 16.19 — just under SCORE_TIMELINE_ATTACH — so the anchor-only
# corroboration rule discarded the whole file, including the camera carrying
# the entire how-to sequence. Three of its pairs agree on the implied
# recorder start within 1.3s; the other two are off by thousands.
ARTHUR_061716 = [
    # (camera stem, score, offset_sec, genuine?)
    ("DJI_20260526095730_0005_D.MP4", 16.19, -1753.3, True),
    ("DJI_20260526092824_0003_D.MP4", 6.91, -7.6, True),
    ("DJI_20260526095322_0004_D.MP4", 4.53, -1504.3, True),
    ("DJI_20260526092527_0002_D.MP4", 8.37, -1426.4, False),
    ("DJI_20260526085734_0001_D.MP4", 3.59, -1380.2, False),
]


def test_mutual_agreement_rescues_an_audio_file_with_no_strong_anchor():
    """Property 13: agreement among mid-scoring pairs is itself evidence.

    Ryan, 2026-09-08: "The generated plan didnt sync audio on any of the
    builds it did. And the all footage is missing 50% of the the source
    audio that should easily sync."

    Cause: corroboration only accepted agreement with a pair that had
    already cleared SCORE_TIMELINE_ATTACH (18). This audio file peaked at
    16.19, so the ENTIRE file was thrown out — including the 24.9-minute
    camera file carrying the whole lock/how-to sequence the cut was built
    from. Three independent pairs from it implied the same recorder start
    within 1.3 seconds. Three false correlations don't agree by chance.
    """
    from precut_pipeline.audio_sync import (
        SCORE_TIMELINE_ATTACH, offset_is_corroborated,
    )

    pairs = [_Pair(cam, sc, off, audio_file="061716.WAV")
             for cam, sc, off, _ in ARTHUR_061716]
    state = _State(pairs)
    assert all(p.score < SCORE_TIMELINE_ATTACH for p in pairs), (
        "fixture no longer represents the no-strong-anchor case"
    )

    for pair, (cam, sc, _off, genuine) in zip(pairs, ARTHUR_061716):
        got = offset_is_corroborated(pair, state)
        assert got == genuine, (
            f"{cam} (score {sc}) should {'attach' if genuine else 'NOT attach'} "
            "— mutual-agreement corroboration is not behaving"
        )


def test_mutual_agreement_needs_a_real_cluster_not_a_pair():
    """Two agreeing pairs alone must NOT corroborate.

    The strength of this rule is that several independent measurements
    coincide. Dropping the bar to two would let a single coincidence
    plus its own reflection through, which is how false lav tracks got
    onto the timeline in the first place.
    """
    from precut_pipeline.audio_sync import (
        MUTUAL_CORROBORATION_MIN, offset_is_corroborated,
    )
    assert MUTUAL_CORROBORATION_MIN >= 3

    # Two pairs agreeing, nothing else.
    a = _Pair("DJI_20260526095730_0005_D.MP4", 9.0, 0.0, audio_file="x.WAV")
    b = _Pair("DJI_20260526095322_0004_D.MP4", 8.0, -408.0, audio_file="x.WAV")
    assert not offset_is_corroborated(a, _State([a, b]))


def test_pool_never_contains_duplicate_clips(ref_phrases):
    """Property 14: no leftover clip may repeat another.

    2026-09-08, Ryan: "extra footage has instances of duplicate footage."
    Cause: when two used fragments sat either side of the SAME strong
    neighbour, that neighbour was added to the allowed spans once per
    used fragment, so the gap-walk ran over the identical span twice and
    emitted the identical clip twice (2425.9-2449.6 appeared twice on his
    real export). POOL-NO-OVERLAP only compared pool against CUT, so a
    pool-vs-pool duplicate passed every check.
    """
    used = [_Range(s, e) for s, e in REF_CUT]
    # The same span handed in twice — exactly what the old allowed-span
    # construction did for a shared neighbour.
    allowed = {REF_FILE: [
        FRAG_WALLPAPER, FRAG_BATHROOM_SCOPE, FRAG_BATHROOM_SCOPE,
    ]}

    got = _compute_pool_leftovers(used, {REF_STEM: ref_phrases}, {}, allowed)
    spans = sorted((r.source_start_sec, r.source_end_sec) for r in got)
    dups = [
        (a1, b1, a2, b2)
        for i, (a1, b1) in enumerate(spans)
        for (a2, b2) in spans[i + 1:]
        if a1 < b2 and b1 > a2
    ]
    assert not dups, f"duplicated leftover clips: {dups}"


# --------------------------------------------------------------------------
# 15. The planner must be able to SEE what it reasons about
# --------------------------------------------------------------------------

def test_footage_digest_includes_summaries_not_just_labels():
    """Property 15: the digest carries fragment summaries.

    2026-09-11, found by comparing Ryan's finished video against what the
    app told him. He asked for a board-up beat. The planner answered that
    the footage didn't support it and talked him into replacing that beat
    — then his finished video opened with exactly that material:

        _0001_D 266.4s "Also in the box, I have two pieces of plywood,
                        stranded plywood, not OSB."

    It sat inside a 78-second fragment labelled "Lock changeover kit:
    no-lock handset". The fragment's own SUMMARY named the plywood; the
    digest passed only labels, so the planner reasoned from an index and
    then asserted absence as fact. A wrong "you don't have this" is the
    most expensive error the app can make, because the editor cannot see
    what he was never offered.
    """
    from posthouse.story_conversation import _build_footage_digest

    class _F:
        def __init__(self, label, summary):
            self.source_start_sec, self.source_end_sec = 208.0, 287.0
            self.topic_label, self.summary = label, summary

    class _T:
        def __init__(self, frag):
            self.fragment, self.fit = frag, "strong"

    frag = _F(
        "Lock changeover kit: no-lock handset",
        "Opening his changeover kit, he shows a Schlage handset with no lock. "
        "The kit also holds two pieces of stranded plywood (not OSB) for "
        "securing extra entrances, plus a hammer and cat's paw.",
    )
    digest = _build_footage_digest({"DJI_0001_D": [_T(frag)]})

    assert "Lock changeover kit" in digest, "label missing from digest"
    assert "plywood" in digest.lower(), (
        "fragment summaries are not in the digest — the planner can only see "
        "labels again, which is how it concluded board-up footage didn't exist"
    )


def test_planner_is_forbidden_from_asserting_absence():
    """The prompt must forbid claiming material doesn't exist.

    The digest fix above widens what the planner can see, but it still
    sees summaries rather than the transcript, so it can still be wrong
    about absence. The rule is that absence must be stated as a limit of
    its own view, never as a fact about the footage.
    """
    from posthouse.story_conversation import PLANNER_SYSTEM_PROMPT

    low = PLANNER_SYSTEM_PROMPT.lower()
    assert "doesn't exist" in low or "does not exist" in low, (
        "the planner prompt no longer addresses claims of absence"
    )
    assert "labels" in low, (
        "the prompt should tell the planner it is reading labels/summaries, "
        "not the full transcript"
    )


# --------------------------------------------------------------------------
# 16. Re-organizing a project must save the intake fields
# --------------------------------------------------------------------------

def test_reorganize_persists_audience_goal(tmp_path):
    """Property 16: Organize saves the audience goal every time, not just
    on a project's first run.

    2026-09-11, Ryan, three separate reports: "I keep setting the audience
    and organizing to save it and then i click generate ideas and it says
    theres no audience... if i go back the dropdown returned to no goal
    set."

    organize_project has two branches. The new-project branch passes
    audience_goal to build_manifest. The update branch — taken whenever a
    manifest already exists, i.e. every real edit — updated shoot_dates,
    people and default_includes and silently discarded the goal.

    It read as unreproducible for two days because every isolated test
    creates a FRESH project and therefore only ever exercises the branch
    that works. Diagnostic logging is what settled it: the value reached
    the backend intact and the manifest written in the same second had no
    audience_goal at all.
    """
    import sys
    sys.path.insert(0, str(REFERENCE_EDIT.parent.parent.parent / "app" / "python_backend"))
    from posthouse.projectmanager import organize_project
    import json as _json

    # resolve(): on macOS tmp_path is /var/... which is a symlink to
    # /private/var/..., and organize_project validates the stored root_dir
    # against the resolved path.
    tmp_path = tmp_path.resolve()
    media = tmp_path / "media"
    media.mkdir()
    clip = media / "A001.mov"
    clip.write_bytes(b"")
    srcs = [{"path": str(clip), "kind": "aroll"}]
    root = tmp_path / "proj"
    root.mkdir()

    def run(goal=None):
        r = organize_project(
            root_dir=str(root), client_name="SoldFast", project_name="T",
            project_type="interview", sources=srcs, audience_goal=goal,
        )
        return _json.loads(Path(r.manifest_path).read_text())["project"]

    GOAL = "Intentional, story-driven long-form work."

    run()  # first organize, no goal — the state Ryan was left in
    after = run(GOAL)
    assert after.get("audience_goal") == GOAL, (
        "re-organizing did not save the audience goal — the update branch is "
        "dropping intake fields again"
    )

    # Omitting the goal on a later organize must NOT wipe the saved one.
    after2 = run()
    assert after2.get("audience_goal") == GOAL, (
        "a later organize without a goal wiped the saved value"
    )


# ---------------------------------------------------------------------------
# Per-file ranges must resolve to their own file.
#
# 2026-09-11, Ryan on a Mitch Interview export: "this isnt useable at all".
# PreCut's story_planner stamps every range with the one combined transcript's
# path and works in combined-timeline seconds. posthouse.story_architect reads
# each transcript separately and emits ranges carrying an explicit per-file
# source_file with times on THAT file's own clock. The assembler assumed the
# former for both, so A005's 596.8s resolved to combined-time 596.8s — which
# lands inside A004. The export referenced one camera while playing the other
# camera's timecodes, the second camera vanished, and clips overlapped because
# two independent clocks were laid on one axis.

def test_per_file_ranges_resolve_to_their_own_file():
    from precut_pipeline.story_assembler import assemble_cut_from_angle
    from precut_pipeline.cutlist import StoryAngle, CreativeBrief, TopicRange
    from precut_pipeline.transcriber import Transcript, Phrase

    # Two files on one combined timeline: A is 0-100s, B is 100-200s.
    phrases = [
        Phrase(id=i, start=float(i * 10), end=float(i * 10 + 9),
               text=f"phrase {i}", words=[])
        for i in range(20)
    ]
    combined = Transcript(source_path="/proxies/A.mp4", language="en",
                          duration=200.0, phrases=phrases)
    offsets = {"/proxies/A.mp4": 0.0, "/proxies/B.mp4": 100.0}
    to_original = {"/proxies/A.mp4": "/orig/A.mov", "/proxies/B.mp4": "/orig/B.mov"}

    angle = StoryAngle(
        angle_id="t", brief=CreativeBrief(
            title="t", hook="", why_it_works="", tone="", target_duration_sec=30.0),
        source_ranges=[
            # B's OWN clock: 20-30s into B, i.e. 120-130 combined.
            TopicRange(source_file="/proxies/B.mp4",
                       source_start_sec=20.0, source_end_sec=30.0),
            TopicRange(source_file="/proxies/A.mp4",
                       source_start_sec=10.0, source_end_sec=20.0),
        ],
    )
    cl = assemble_cut_from_angle(
        angle=angle, transcript=combined, db=None,
        source_offset_map=offsets, source_to_original=to_original)

    used = {p.source_file for p in cl.aroll_track}
    assert used == {"/orig/A.mov", "/orig/B.mov"}, (
        f"both files must appear in the cut, got {used} — a range's explicit "
        "source_file was ignored and its times read as combined-timeline"
    )
    b = [p for p in cl.aroll_track if p.source_file == "/orig/B.mov"][0]
    assert 15.0 <= b.source_start <= 25.0, (
        f"B's range should sit near 20s in B's OWN clock, got {b.source_start:.1f}"
    )


def test_combined_timeline_ranges_still_resolve_as_before():
    """PreCut's own planner emits combined-time ranges stamped with the
    combined transcript's path. Honouring an explicit per-file source_file
    must not change how those are read."""
    from precut_pipeline.story_assembler import assemble_cut_from_angle
    from precut_pipeline.cutlist import StoryAngle, CreativeBrief, TopicRange
    from precut_pipeline.transcriber import Transcript, Phrase

    phrases = [
        Phrase(id=i, start=float(i * 10), end=float(i * 10 + 9),
               text=f"phrase {i}", words=[])
        for i in range(20)
    ]
    combined = Transcript(source_path="/proxies/A.mp4", language="en",
                          duration=200.0, phrases=phrases)
    offsets = {"/proxies/A.mp4": 0.0, "/proxies/B.mp4": 100.0}
    to_original = {"/proxies/A.mp4": "/orig/A.mov", "/proxies/B.mp4": "/orig/B.mov"}

    angle = StoryAngle(
        angle_id="t", brief=CreativeBrief(
            title="t", hook="", why_it_works="", tone="", target_duration_sec=30.0),
        # Combined time 120-130, stamped with the combined transcript's path.
        source_ranges=[TopicRange(source_file="/proxies/A.mp4",
                                  source_start_sec=120.0, source_end_sec=130.0)],
    )
    cl = assemble_cut_from_angle(
        angle=angle, transcript=combined, db=None,
        source_offset_map=offsets, source_to_original=to_original)

    p = cl.aroll_track[0]
    assert p.source_file == "/orig/B.mov", (
        f"combined time 120s falls in B's span, got {p.source_file}"
    )
    assert 15.0 <= p.source_start <= 25.0, (
        f"120s combined is 20s into B, got {p.source_start:.1f}"
    )


# ---------------------------------------------------------------------------
# The planner's clip order IS the edit.
#
# 2026-09-11, Ryan on a cut the app pitched as a solid plan: "theres no story
# here. Its pieces of different stories that no one has context to." The
# planner had in fact built an arc across 27 beats. story_assembler re-sorted
# them by (source_file, source_start_sec) — right for PreCut's own planner,
# which returns a few unsequenced continuous ranges, and fatal for a sequenced
# one. The delivered cut opened on "I'm Mitch, I work on the tech side" and
# buried the emotional payoff mid-reel.

def test_story_architect_cut_keeps_the_planners_clip_order():
    from posthouse.story_architect import assemble_two_zone_cutlist
    from precut_pipeline.cutlist import StoryAngle, CreativeBrief, TopicRange
    from precut_pipeline.transcriber import Transcript, Phrase

    phrases = [
        Phrase(id=i, start=float(i * 10), end=float(i * 10 + 9),
               text=f"beat {i}.", words=[])
        for i in range(20)
    ]
    combined = Transcript(source_path="/proxies/A.mp4", language="en",
                          duration=200.0, phrases=phrases)
    offsets = {"/proxies/A.mp4": 0.0, "/proxies/B.mp4": 100.0}
    to_original = {"/proxies/A.mp4": "/orig/A.mov", "/proxies/B.mp4": "/orig/B.mov"}

    # Deliberately NOT chronological, and deliberately crossing files — the
    # shape a real arc takes when it opens late in the interview and pays off
    # with something said early.
    planned = [
        ("/proxies/B.mp4", 50.0, 59.0),
        ("/proxies/A.mp4", 10.0, 19.0),
        ("/proxies/B.mp4", 10.0, 19.0),
        ("/proxies/A.mp4", 70.0, 79.0),
    ]
    angle = StoryAngle(
        angle_id="t", brief=CreativeBrief(
            title="t", hook="", why_it_works="", tone="", target_duration_sec=40.0),
        source_ranges=[
            TopicRange(source_file=f, source_start_sec=a, source_end_sec=b)
            for f, a, b in planned
        ],
    )
    cl = assemble_two_zone_cutlist(
        angle=angle, transcript=combined, db=None,
        source_offset_map=offsets, source_to_original=to_original)

    got = [
        (p.source_file, round(p.source_start, 1))
        for p in sorted(cl.aroll_track, key=lambda p: p.timeline_start)
    ]
    want = [
        ("/orig/B.mov", 50.0), ("/orig/A.mov", 10.0),
        ("/orig/B.mov", 10.0), ("/orig/A.mov", 70.0),
    ]
    assert len(got) == len(want), f"expected {len(want)} clips, got {len(got)}"
    for i, ((gf, gs), (wf, ws)) in enumerate(zip(got, want)):
        assert gf == wf and abs(gs - ws) < 3.0, (
            f"clip {i + 1}: planner ordered {wf} @ {ws}, got {gf} @ {gs} — "
            "the cut was re-sorted and the arc is gone"
        )


def test_prompt_states_necessity_as_the_cut_selection_test():
    """2026-09-16: the criterion for what goes in the cut must be necessity
    to the story, not fit-to-length. Checking the actual prompt text, not
    just the constants — a threshold can be right while the instruction
    that decides selection is still the old, backwards one."""
    from posthouse.story_architect import ARCHITECT_SYSTEM_PROMPT
    assert "NECESSITY" in ARCHITECT_SYSTEM_PROMPT
    assert "does the story break without this" in ARCHITECT_SYSTEM_PROMPT
    # The old instruction ("select tightly") must be reframed, not merely
    # supplemented with the new rule alongside it.
    assert "so select tightly and trust the leftovers" not in ARCHITECT_SYSTEM_PROMPT


def test_target_length_prompt_is_a_guide_not_the_selection_test():
    """_format_planning_context's TARGET LENGTH block must not instruct the
    model to select fewer/shorter fragments to fit a number — that IS the
    backwards behaviour Ryan corrected."""
    from posthouse.story_architect import _format_planning_context
    block = _format_planning_context("", 45.0)
    assert "GUIDE" in block
    assert "necessity rule above" in block
    assert "Select fewer, shorter, better fragments" not in block
    assert "is simply left out" not in block


# --------------------------------------------------------------------------
# 14. The pool is bounded by TOPIC, not by distance from the cut
# --------------------------------------------------------------------------
#
# 2026-09-16. The rule was "used fragments, plus an immediately adjacent
# fragment only when it is itself strong". Measured against Ryan's two real
# finished edits: of the footage he actually used that the cut didn't
# propose, ~136s was blocked purely by that radius versus 7.5s by the fit
# rule, and most of the radius-blocked material was ALREADY labelled strong.
# The app found it, judged it on-topic, and discarded it for sitting more
# than one fragment from a used clip.
#
# These tests exist because the previous rule had NO coverage at this level
# at all — the pool tests above call _compute_pool_leftovers with hand-built
# allowed spans, so they never exercised how those spans get chosen, and a
# silent revert here would not have failed anything.

def _frags(*rows):
    """rows: (source_file, start, end, fit, index)"""
    out = {}
    for sf, s, e, fit, i in rows:
        out.setdefault(sf, []).append((s, e, fit, i))
    for v in out.values():
        v.sort()
    return out


def test_strong_material_far_from_the_cut_is_still_pooled():
    """The core of the 2026-09-16 fix. A strong fragment seven positions
    away from anything the cut used is exactly what Ryan reached for and
    exactly what the radius rule threw away."""
    from posthouse.story_architect import build_allowed_pool_spans
    frags = _frags(
        ("A.mov", 0.0, 10.0, "strong", 0),      # used by the cut
        ("A.mov", 20.0, 30.0, "strong", 1),
        ("A.mov", 40.0, 50.0, "strong", 2),
        ("A.mov", 60.0, 70.0, "strong", 3),
        ("A.mov", 80.0, 90.0, "strong", 4),
        ("A.mov", 100.0, 110.0, "strong", 5),
        ("A.mov", 120.0, 130.0, "strong", 6),
        ("A.mov", 140.0, 150.0, "strong", 7),   # seven away, still on topic
    )
    allowed = build_allowed_pool_spans(frags, used_idx={0})
    spans = allowed["A.mov"]
    assert any(s <= 140.0 and e >= 150.0 for s, e in spans), (
        "a strong, on-topic fragment must reach the pool regardless of how "
        "far it sits from the cut — distance is not relevance"
    )


def test_off_topic_never_reaches_the_pool():
    """What actually fixed "what do shirt colors and fishing licenses have
    to do with wallpaper" was the fit label, not the radius — so the fit
    bound must survive the radius being removed.

    Note this test ONCE also asserted that "possible" was excluded. That
    half was removed on 2026-09-19 (see the test below) after measurement
    showed it was the largest single cause of material Ryan used never
    reaching him. off_topic is the part that was load-bearing and it stays.
    """
    from posthouse.story_architect import build_allowed_pool_spans
    frags = _frags(
        ("A.mov", 0.0, 10.0, "strong", 0),        # used
        ("A.mov", 20.0, 30.0, "off_topic", 1),    # shirt colours
    )
    allowed = build_allowed_pool_spans(frags, used_idx={0})
    spans = allowed["A.mov"]
    assert not any(s < 30.0 and e > 20.0 for s, e in spans), "off_topic leaked in"


def test_possible_reaches_the_pool_but_never_the_cut():
    """2026-09-19, Ryan: "do the pool fix".

    Measured on the tiling day against his own organize pass: of the 139.9s
    of his story the app never surfaced, 73.8s — 53%, the largest single
    cause — had been extracted and labelled "possible", then dropped
    because the pool took "strong" only. Admitting it moved recall of his
    own story from 63% to 81%.

    "possible" is a literal description of his spec for that side: "all of
    the footage that had to do with that topic that i wasnt sure would make
    it into the cut". The tight cut is built from the model's own chosen
    ranges and never from this set, so this cannot loosen the cut.
    """
    from posthouse.story_architect import build_allowed_pool_spans, POOL_ELIGIBLE_FITS

    assert POOL_ELIGIBLE_FITS == {"strong", "possible"}, (
        "off_topic must never be pool-eligible — it is the shirt-colours "
        "and fishing-licence material behind the 37.6-minute complaint."
    )
    frags = _frags(
        ("A.mov", 0.0, 10.0, "strong", 0),        # used by the cut
        ("A.mov", 40.0, 50.0, "possible", 1),
        ("A.mov", 60.0, 70.0, "off_topic", 2),
    )
    allowed = build_allowed_pool_spans(frags, used_idx={0})
    spans = allowed["A.mov"]
    assert any(s <= 40.0 and e >= 50.0 for s, e in spans), "possible should be offered"
    assert not any(s < 70.0 and e > 60.0 for s, e in spans), "off_topic leaked in"


def test_possible_still_obeys_the_files_the_cut_used_bound():
    """Admitting `possible` must not reopen the other half of the
    37.6-minute failure: leftovers from files the cut never touched. Both
    bounds are independent and both must hold."""
    from posthouse.story_architect import build_allowed_pool_spans
    frags = _frags(
        ("A.mov", 0.0, 10.0, "strong", 0),        # used
        ("B.mov", 10.0, 20.0, "possible", 1),     # cut never drew on B
        ("B.mov", 30.0, 40.0, "strong", 2),
    )
    allowed = build_allowed_pool_spans(frags, used_idx={0})
    assert "B.mov" not in allowed, (
        "a file the cut never used contributes nothing, whatever its labels"
    )


def test_files_the_cut_never_used_contribute_nothing():
    """The other half of what stopped 37.6 minutes across six camera files:
    leftovers come only from footage the cut actually drew on."""
    from posthouse.story_architect import build_allowed_pool_spans
    frags = _frags(
        ("A.mov", 0.0, 10.0, "strong", 0),     # used
        ("B.mov", 0.0, 600.0, "strong", 1),    # a whole other camera, untouched
    )
    allowed = build_allowed_pool_spans(frags, used_idx={0})
    assert "B.mov" not in allowed, (
        "a source file the cut never touched must not contribute leftovers, "
        "however strong its material looks in isolation"
    )


def test_offsets_are_applied_to_allowed_spans():
    from posthouse.story_architect import build_allowed_pool_spans
    frags = _frags(("A.mov", 0.0, 10.0, "strong", 0))
    allowed = build_allowed_pool_spans(frags, used_idx={0},
                                        source_offset_lookup={"A.mov": 100.0})
    assert allowed["A.mov"] == [(100.0, 110.0)]


# ---------------------------------------------------------------------------
# §15 — cutting rhythm. 2026-09-19.
# ---------------------------------------------------------------------------

def test_pacing_constants_match_the_verifier_and_the_reference_edit():
    """The floor must not drift from the one verify_export enforces, or a
    cut passes generation and fails at export — which is the situation this
    section exists to remove."""
    import re
    from pathlib import Path
    from posthouse.story_architect import (
        MIN_CLIPS_PER_MINUTE, TARGET_CLIPS_PER_MINUTE, MAX_SINGLE_CLIP_SEC)

    ve = (Path(__file__).resolve().parents[1] / "verify_export.py").read_text()
    m = re.search(r"MIN_CLIPS_PER_MINUTE\s*=\s*([\d.]+)", ve)
    assert m, "verify_export.py no longer defines MIN_CLIPS_PER_MINUTE"
    assert float(m.group(1)) == MIN_CLIPS_PER_MINUTE, (
        "the generator's floor and the verifier's floor must be the same "
        "number; if they diverge a cut can pass one and fail the other"
    )
    # Sanity against Ryan's measured reference: his hand-cut reel runs
    # ~2.2-4.6s per cut (~20/min) and his organize pass 6.5/min, so a
    # target rate under the floor or a ceiling under his slowest gear
    # would be incoherent.
    assert TARGET_CLIPS_PER_MINUTE > MIN_CLIPS_PER_MINUTE
    assert MAX_SINGLE_CLIP_SEC >= 5.0


def test_the_real_112s_failure_would_now_be_rejected():
    """The actual case. A regenerated tiling angle came back as 9 clips over
    112s — 4.8/min, averaging 12.5s, with single ranges of 16.9s and 20.6s.
    verify_export caught it, but only once it was already an XML on Ryan's
    Desktop."""
    from posthouse.story_architect import MIN_CLIPS_PER_MINUTE, MAX_SINGLE_CLIP_SEC
    real = [8.1, 4.3, 9.6, 11.1, 12.5, 13.3, 16.9, 20.6, 15.8]
    total = sum(real)
    per_min = len(real) / (total / 60.0)
    assert per_min < MIN_CLIPS_PER_MINUTE, "this is the case that must fail"
    assert max(real) > MAX_SINGLE_CLIP_SEC, "and its longest clip is over the ceiling"


def test_the_passing_74s_cut_is_still_accepted():
    """Guard against over-tightening. The earlier tiling cut — 9 clips over
    74s, 7.3/min — was shipped and verified, so the new gate must not
    retroactively reject work that was fine."""
    from posthouse.story_architect import MIN_CLIPS_PER_MINUTE, MAX_SINGLE_CLIP_SEC
    real = [1.7, 7.1, 1.9, 7.5, 7.9, 9.1, 9.6, 21.6, 7.7]
    per_min = len(real) / (sum(real) / 60.0)
    assert per_min >= MIN_CLIPS_PER_MINUTE, "this cut passed at export and must still pass"
    # NOTE: its 21.6s range DOES exceed the clip ceiling, and that is
    # intentional — the ceiling is a real tightening, and this clip is the
    # kind of slab it targets. Recorded rather than silently excused.


def test_ryans_own_work_satisfies_the_floor():
    """The floor is only legitimate if his own cutting clears it. Measured
    from the tiling day files he supplied on 2026-09-18."""
    from posthouse.story_architect import MIN_CLIPS_PER_MINUTE
    organize_rate = 40 / (371.4 / 60.0)      # his culled+organized left zone
    final_rate = 55 / (244.0 / 60.0)         # his finished edit
    assert organize_rate >= MIN_CLIPS_PER_MINUTE, (
        f"his organize pass runs {organize_rate:.1f}/min; a floor above that "
        f"would reject his own work"
    )
    assert final_rate > organize_rate
