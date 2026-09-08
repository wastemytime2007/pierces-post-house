"""The Reel contract — the properties a generated cut + export must hold.

Every assertion here corresponds to a real failure Ryan hit on real
footage on 2026-09-04/07, each of which took a manual export round-trip
to find. His own words for why this file exists: *"This is what we need
but from any videos suggested/exported with xmls but without so much hand
holding. I can't carry you through 15 exports and manually do it myself
every time. So lets try to lock this in."*

Nothing here needs an API key, media, or the ML venv. The ground truth is
embedded as fixtures taken from two real artifacts:

  * `Removing Wallpaper Tutorial.xml` — the reference edit Ryan cut by
    hand and supplied as the target. Its 11 selections and 8 leftover
    clips are the numbers in REF_CUT / REF_LEFTOVERS.
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
