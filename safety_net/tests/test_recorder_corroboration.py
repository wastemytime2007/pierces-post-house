"""One recorder, one clock: corroborating a lav across its own file chunks.

2026-09-16, Runnells tiling day. Ryan: *"tile day should be able to sync. If
theres source audio it should be syncable."*

He was right and the audio was there. The mic ran continuously for 67.4
minutes across a 67.2-minute shoot, and the recorder split it into three
~30-minute chunks. The correlator found the CORRECT offset for every camera
file with speech on it -- checked against the filename clocks, accurate to
between 0.0 and 1.5 seconds -- and four of five were discarded.

Neither existing branch could save them:
  (a) wants a pair >= SCORE_TIMELINE_ATTACH in the SAME audio file; only the
      third chunk had one.
  (b) wants MUTUAL_CORROBORATION_MIN (3) agreeing pairs in the same file;
      each chunk had exactly two.
Chunking the recording had chunked its evidence with it.

Branch (c) normalises each pair by its own audio file's filename start, so
`camera_start + offset - audio_start` is ONE constant per recorder for the
whole shoot. Real numbers from that day: the five true pairs land inside
1.5s of each other (10934.86-10936.35); the ten false ones scatter over
7216-12652, nearest miss 333s away. Score could not separate them at all --
true pairs ran 3.21-25.67, false ones 3.52-6.06, fully interleaved.

The safety properties that must hold, and that these tests pin:
  * nothing attaches unless a pair from the SAME RECORDER cleared
    SCORE_TIMELINE_ATTACH on its own, so this adds evidence rather than
    lowering the bar set on 2026-09-04;
  * two recorders in one folder never vouch for each other (the "Bob 2 and
    Bob 3... different points in time" failure);
  * projects with unstamped audio filenames are untouched -- verified
    against the real wallpaper project, whose files are named `Bob 1.WAV`
    and which shows 0 changed pairs.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

# Must be the FORK's audio_sync, not the protected donor at $PRECUT_ROOT.
# A bare `import precut_pipeline.audio_sync` picks whichever copy is first
# on sys.path, which made an earlier version of this file pass alone and
# fail in the suite while reading a checkout that has none of this code.
# See conftest.py.
from conftest import load_fork_module as _load_fork_module

A = _load_fork_module("audio_sync")


def _ts(s: str) -> float:
    return datetime.strptime(s, "%Y%m%d%H%M%S").timestamp()


class FakePair:
    """Enough of AudioSyncPair for offset_is_corroborated."""

    def __init__(self, cam_stamp, audio_name, offset_sec, score):
        self.aroll_file = f"/x/Osmo/DJI_{cam_stamp}_0001_D.MP4"
        self.audio_file = f"/x/Source Audio/{audio_name}"
        self.offset_sec = offset_sec
        self.score = score
        self.promoted_via_consistency = False
        self.audio_duration_sec = 1849.57


class FakeState:
    def __init__(self, pairs):
        self.pairs = pairs
        self.groups = []


# A recorder whose clock sits DELTA seconds behind the camera's.
DELTA = 10936.35


def _pair(cam_stamp, audio_name, audio_stamp, score, err=0.0):
    """Build a pair that is TRUE (agrees with DELTA) apart from `err`."""
    offset = (_ts(audio_stamp) + DELTA) - _ts(cam_stamp) + err
    return FakePair(cam_stamp, audio_name, offset, score)


def test_the_real_tiling_day_shape_now_corroborates():
    """Two chunks with two weak-but-true pairs each, one strong anchor in a
    third chunk. This is the exact configuration that failed."""
    anchor = _pair("20260630100221", "DJI_01_20260630_070831.WAV",
                   "20260630070831", 25.67)
    weak = [
        _pair("20260630091331", "DJI_01_20260630_060652.WAV", "20260630060652", 9.34),
        _pair("20260630093329", "DJI_01_20260630_060652.WAV", "20260630060652", 5.29),
        _pair("20260630093934", "DJI_01_20260630_063742.WAV", "20260630063742", 3.67),
        _pair("20260630100221", "DJI_01_20260630_063742.WAV", "20260630063742", 3.21),
    ]
    st = FakeState([anchor] + weak)
    for p in weak:
        assert A.offset_is_corroborated(p, st), (
            f"score {p.score} pair agreeing with the recorder-wide clock "
            f"should corroborate"
        )


def test_a_recorder_without_enough_agreement_vouches_for_nothing():
    """SUPERSEDED IN PART, 2026-09-22 -- read the change, not just the test.

    This asserted the 2026-09-04 bar in its original form: *a recorder that
    never matched anything convincingly vouches for nothing, however
    self-consistent*, and its fixture was three weak pairs agreeing across
    two chunks with no pair at SCORE_TIMELINE_ATTACH. Branch (d) now accepts
    exactly that fixture, on purpose -- see this module's branch (d) section
    for the measurement. The windows project proved the old form too strong:
    it threw away eight true pairs, including the two highest-scoring pairs
    in the project, because the shoot's ceiling was 15.93 and 18 was the
    only door.

    What survives is the property the bar was really protecting, restated as
    a quantity of evidence rather than as one pair's score: agreement below
    MUTUAL_CORROBORATION_MIN vouches for nothing. Two pairs is a coincidence
    a shoot can produce; three across a recorder's own clock is not.
    """
    weak = [
        _pair("20260630091331", "DJI_01_20260630_060652.WAV", "20260630060652", 9.34),
        _pair("20260630093934", "DJI_01_20260630_063742.WAV", "20260630063742", 3.67),
    ]
    st = FakeState(weak)
    for p in weak:
        assert not A.offset_is_corroborated(p, st), (
            "two agreeing pairs and no anchor must still corroborate nothing"
        )


def test_a_pair_that_disagrees_with_the_clock_is_still_rejected():
    """The false matches on that day missed by 333s and up."""
    anchor = _pair("20260630100221", "DJI_01_20260630_070831.WAV",
                   "20260630070831", 25.67)
    liar = _pair("20260630093934", "DJI_01_20260630_060652.WAV",
                 "20260630060652", 6.06, err=333.0)
    st = FakeState([anchor, liar])
    assert not A.offset_is_corroborated(liar, st)


def test_two_recorders_never_vouch_for_each_other():
    """The 'Bob 2 and Bob 3... those are different points in time' failure.
    A strong pair on unit 01 must not corroborate unit 02, even when their
    numbers happen to line up."""
    anchor = _pair("20260630100221", "DJI_01_20260630_070831.WAV",
                   "20260630070831", 25.67)
    other_unit = _pair("20260630093934", "DJI_02_20260630_063742.WAV",
                       "20260630063742", 3.67)
    st = FakeState([anchor, other_unit])
    assert A._recorder_id(anchor.audio_file) != A._recorder_id(other_unit.audio_file)
    assert not A.offset_is_corroborated(other_unit, st)


def test_pure_noise_stays_out_even_when_it_agrees():
    """MUTUAL_CORROBORATION_SCORE_FLOOR still applies to branch (c)."""
    anchor = _pair("20260630100221", "DJI_01_20260630_070831.WAV",
                   "20260630070831", 25.67)
    noise = _pair("20260630093934", "DJI_01_20260630_063742.WAV",
                  "20260630063742", A.MUTUAL_CORROBORATION_SCORE_FLOOR - 0.5)
    st = FakeState([anchor, noise])
    assert not A.offset_is_corroborated(noise, st)


def test_unstamped_audio_filenames_disable_the_rule_entirely():
    """The wallpaper project names its files `Bob 1.WAV`. No timestamp means
    no recorder-wide clock, so branch (c) cannot run -- which is why that
    project shows 0 changed pairs."""
    assert A._audio_clock_start("/x/Source Audio/Bob 1.WAV") is None
    assert A._recorder_id("/x/Source Audio/Bob 1.WAV") is None

    anchor = _pair("20260630100221", "DJI_01_20260630_070831.WAV",
                   "20260630070831", 25.67)
    unstamped = FakePair("20260630093934", "Bob 2.WAV", 12345.0, 5.0)
    st = FakeState([anchor, unstamped])
    assert not A.offset_is_corroborated(unstamped, st)


def test_audio_stamp_parser_handles_the_dji_split_format():
    """`DJI_01_20260630_060652.WAV` -- the underscore between date and time
    is exactly why the camera's 14-contiguous-digit parser misses it."""
    assert A._camera_clock_start("/x/DJI_01_20260630_060652.WAV") is None
    assert A._audio_clock_start("/x/DJI_01_20260630_060652.WAV") == _ts("20260630060652")
    assert A._recorder_id("/x/DJI_01_20260630_060652.WAV") == "DJI_01_"


def test_camera_stamp_parser_still_works_on_camera_names():
    assert A._camera_clock_start("/x/DJI_20260630091331_0002_D.MP4") == _ts("20260630091331")


@pytest.mark.parametrize("err", [0.0, 1.5, 2.9])
def test_agreement_inside_tolerance_is_accepted(err):
    anchor = _pair("20260630100221", "DJI_01_20260630_070831.WAV",
                   "20260630070831", 25.67)
    near = _pair("20260630091331", "DJI_01_20260630_060652.WAV",
                 "20260630060652", 5.0, err=err)
    st = FakeState([anchor, near])
    assert A.offset_is_corroborated(near, st)


@pytest.mark.parametrize("err", [4.0, 30.0, 333.0, 1000.0])
def test_agreement_outside_tolerance_is_refused(err):
    anchor = _pair("20260630100221", "DJI_01_20260630_070831.WAV",
                   "20260630070831", 25.67)
    far = _pair("20260630091331", "DJI_01_20260630_060652.WAV",
                "20260630060652", 5.0, err=err)
    st = FakeState([anchor, far])
    assert not A.offset_is_corroborated(far, st)


# ---------------------------------------------------------------------------
# Branch (d): recorder-wide mutual corroboration, no strong anchor required.
#
# 2026-09-22, the windows project (Runnells 05-28 + 06-02). Ryan: "None of
# the syncing came through." Four of nineteen camera files got audio.
#
# That shoot's top correlation score was 15.93 -- it never produced an 18 --
# so branches (a) and (c), which both need a pair at SCORE_TIMELINE_ATTACH,
# could not fire at all, and (b) rescued only the single audio chunk that
# happened to hold three agreeing pairs by itself. Meanwhile the clock
# invariant was as clean as it has ever been: the 06-02 pairs cluster over
# 93.72-95.53 (span 1.81s) and the 05-28 pairs over 80.18-80.62 (span
# 0.45s), with the nearest false pair 84s out and most of them 400s+.
# Whether the evidence lands in one chunk or four is a property of how the
# recorder splits files, not of whether the offsets are right.
# ---------------------------------------------------------------------------


def test_three_agreeing_pairs_across_chunks_need_no_strong_anchor():
    """The windows shape: a recorder whose ceiling is below 18, whose true
    pairs are spread one or two per chunk. Branch (b) cannot see them and
    (a)/(c) have nothing to anchor on."""
    pairs = [
        _pair("20260602094903", "DJI_01_20260602_094815.WAV", "20260602094815", 7.90),
        _pair("20260602100718", "DJI_01_20260602_101906.WAV", "20260602101906", 7.63),
        _pair("20260602112955", "DJI_01_20260602_112044.WAV", "20260602112044", 15.31),
    ]
    st = FakeState(pairs)
    assert max(p.score for p in pairs) < A.SCORE_TIMELINE_ATTACH, (
        "the point of this fixture is that nothing clears the anchor bar"
    )
    for p in pairs:
        assert A.offset_is_corroborated(p, st), (
            f"score {p.score} pair agreeing with two others on the same "
            f"recorder's clock should corroborate without an anchor"
        )


def test_two_agreeing_pairs_are_still_not_enough():
    """MUTUAL_CORROBORATION_MIN is 3 for branch (d) exactly as for (b).
    Two measurements agreeing is a coincidence a shoot can produce."""
    pairs = [
        _pair("20260602094903", "DJI_01_20260602_094815.WAV", "20260602094815", 7.90),
        _pair("20260602100718", "DJI_01_20260602_101906.WAV", "20260602101906", 7.63),
    ]
    st = FakeState(pairs)
    for p in pairs:
        assert not A.offset_is_corroborated(p, st)


def test_a_disagreeing_pair_is_not_carried_by_the_cluster():
    """The cluster vouches for its own members, not for everything on the
    recorder. On the real project the nearest false pair missed by 84s."""
    good = [
        _pair("20260602094903", "DJI_01_20260602_094815.WAV", "20260602094815", 7.90),
        _pair("20260602100718", "DJI_01_20260602_101906.WAV", "20260602101906", 7.63),
        _pair("20260602112955", "DJI_01_20260602_112044.WAV", "20260602112044", 15.31),
    ]
    liar = _pair("20260602103109", "DJI_01_20260602_104955.WAV",
                 "20260602104955", 5.80, err=84.0)
    st = FakeState(good + [liar])
    assert A.offset_is_corroborated(good[0], st)
    assert not A.offset_is_corroborated(liar, st)


def test_branch_d_respects_the_recorder_boundary():
    """Three agreeing pairs on unit 01 must not corroborate unit 02."""
    good = [
        _pair("20260602094903", "DJI_01_20260602_094815.WAV", "20260602094815", 7.90),
        _pair("20260602100718", "DJI_01_20260602_101906.WAV", "20260602101906", 7.63),
        _pair("20260602112955", "DJI_01_20260602_112044.WAV", "20260602112044", 15.31),
    ]
    other_unit = _pair("20260602103109", "DJI_02_20260602_104955.WAV",
                       "20260602104955", 6.50)
    st = FakeState(good + [other_unit])
    assert not A.offset_is_corroborated(other_unit, st)


def test_branch_d_keeps_the_noise_floor():
    """Below MUTUAL_CORROBORATION_SCORE_FLOOR a correlation carries no
    usable offset, however well it agrees -- one floor, not two."""
    good = [
        _pair("20260602094903", "DJI_01_20260602_094815.WAV", "20260602094815", 7.90),
        _pair("20260602100718", "DJI_01_20260602_101906.WAV", "20260602101906", 7.63),
        _pair("20260602112955", "DJI_01_20260602_112044.WAV", "20260602112044", 15.31),
    ]
    noise = _pair("20260602103109", "DJI_01_20260602_104955.WAV",
                  "20260602104955", A.MUTUAL_CORROBORATION_SCORE_FLOOR - 0.5)
    st = FakeState(good + [noise])
    assert not A.offset_is_corroborated(noise, st)


def test_two_shoot_days_on_one_recorder_do_not_merge():
    """One recorder has one clock offset PER DAY, not one forever. The real
    windows project carries both: 05-28 sits at ~80s and 06-02 at ~95s on
    the same DJI_01_. Each day must corroborate itself and neither may
    borrow the other's members -- which is why this compares pairs to each
    other rather than to a single per-recorder constant."""
    day2 = [
        _pair("20260602094903", "DJI_01_20260602_094815.WAV", "20260602094815", 7.90),
        _pair("20260602100718", "DJI_01_20260602_101906.WAV", "20260602101906", 7.63),
        _pair("20260602112955", "DJI_01_20260602_112044.WAV", "20260602112044", 15.31),
    ]
    # Same recorder, 5 days earlier, clock 15s further off: a lone pair.
    day1 = _pair("20260528080119", "DJI_01_20260528_075951.WAV",
                 "20260528075951", 15.93, err=-15.0)
    st = FakeState(day2 + [day1])
    assert A.offset_is_corroborated(day2[0], st)
    assert not A.offset_is_corroborated(day1, st), (
        "a day with only one measurement must not be carried by another "
        "day's cluster on the same recorder"
    )
