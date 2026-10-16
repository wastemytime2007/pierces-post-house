"""A sync match the file-name clocks rule out is not used, whatever it scored.

Ryan, 2026-10-15: "its output was unusable and had no purpose". The first cut of a new project (4 DJI clips shot 10:10-10:55, recorder files from 6:39 to 10:54) played THREE recorder files at once under
every shot, two of them recorded at 7:10 in the morning: the matcher had scored those false pairs 12.8, 17.8 and 28.0, above the true ones (14-18). The true pairs sit about 63 s from what the clocks
in the file names predict; the false ones are hours away. These are that project's real pairs and scores.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app" / "python_backend"))

from precut_pipeline.audio_sync import (  # noqa: E402
    AudioSyncState, SyncPair, apply_clock_veto, find_covering_audio_for_phrase,
)

CLIP = {"094342": "DJI_20260522094342_0001_D.MP4", "101009": "DJI_20260522101009_0002_D.MP4", "103343": "DJI_20260522103343_0003_D.MP4", "104358": "DJI_20260522104358_0004_D.MP4"}
# (clip, recorder file, score, offset found, promoted by consistency)
REAL = [("094342", "DJI_02_20260522_094536", 18.1, 177.1, False), ("101009", "DJI_01_20260522_071030", 12.8, 209.0, False), ("101009", "DJI_01_20260522_071042", 11.3, 219.9, False),
        ("101009", "DJI_02_20260522_101626", 7.7, 440.4, True), ("103343", "DJI_01_20260522_071030", 17.8, -1205.4, False), ("103343", "DJI_02_20260522_101626", 4.8, -974.0, True),
        ("104358", "DJI_01_20260522_074119", 28.0, 28.7, False), ("104358", "DJI_01_20260522_074131", 20.3, 39.6, False), ("104358", "DJI_02_20260522_104715", 18.0, 260.1, False),
        ("104358", "DJI_01_20260522_071042", 17.3, -1809.9, False), ("104358", "DJI_02_20260522_101626", 14.1, -1589.5, False), ("094342", "DJI_01_20260522_063939", 9.8, -54.3, False)]
TRUE = {("094342", "DJI_02_20260522_094536"), ("101009", "DJI_02_20260522_101626"), ("103343", "DJI_02_20260522_101626"), ("104358", "DJI_02_20260522_104715"), ("104358", "DJI_02_20260522_101626")}


def _pairs(shift=0.0):
    return [SyncPair(aroll_file="/v/" + CLIP[c], aroll_proxy="/v/proxies/x.mp4", audio_file=f"/v/Source Audio/{a}.WAV", offset_sec=o + shift, score=s, audio_duration_sec=1800.0, computed_at=0.0,
                     promoted_via_consistency=pr) for c, a, s, o, pr in REAL]


DJI_01_STRONG = {("101009", "DJI_01_20260522_071030"), ("101009", "DJI_01_20260522_071042"), ("103343", "DJI_01_20260522_071030"), ("104358", "DJI_01_20260522_074119"),
                 ("104358", "DJI_01_20260522_074131"), ("104358", "DJI_01_20260522_071042")}


def test_a_recorder_whose_strong_pairs_share_one_clock_offset_keeps_them_even_when_it_is_hours_from_the_camera_clock():
    """2026-10-16: the pairs this test used to call false (12.8, 17.8, 28.0 at 7:10 am under 10:10 am footage) are Bob's own recorder, DJI_01, whose clock sits 3 h 3 min 7 s from the camera's on EVERY
    strong pair, to within 2 s. Its line "That door stops right where that piece of trim was added" is on it and the app had paired the clip with the other person's recorder only."""
    pairs = _pairs()
    assert apply_clock_veto(pairs) == []
    kept = {(Path(p.aroll_file).name[12:18], Path(p.audio_file).stem) for p in pairs if p.is_reliable}
    assert kept == TRUE | DJI_01_STRONG
    assert all(p.score >= 0 for p in pairs)                                                  # scores are never changed: the UI still shows what the matcher said


def test_a_pair_of_the_same_recorder_that_does_not_share_its_clock_offset_is_still_ruled_out():
    pairs = _pairs()
    odd = SyncPair(aroll_file="/v/" + CLIP["094342"], aroll_proxy="/v/proxies/x.mp4", audio_file="/v/Source Audio/DJI_01_20260522_071030.WAV", offset_sec=-784.3, score=12.0,
                   audio_duration_sec=1850.0, computed_at=0.0)
    pairs.append(odd)
    vetoed = apply_clock_veto(pairs)
    assert vetoed == [odd] and odd.clock_vetoed and not odd.is_reliable


def test_a_project_whose_clocks_agree_with_none_of_its_matches_is_left_alone():
    """A recorder never set to the right time: no pair is consistent with the clocks, so the clocks are not evidence and nothing is vetoed."""
    pairs = _pairs(shift=36000.0)                                                            # every offset ten hours off the clocks' prediction
    assert apply_clock_veto(pairs) == [] and all(not p.clock_vetoed for p in pairs)


def test_a_saved_state_from_before_the_veto_is_judged_by_it_when_loaded_and_the_cut_gets_each_persons_own_recorder():
    from dataclasses import asdict
    state = AudioSyncState(pairs=_pairs())
    saved = state.to_dict()
    for p in saved["pairs"]:
        p.pop("clock_vetoed", None)                                                          # a state written by the app before this change
    loaded = AudioSyncState.from_dict(saved)
    assert sum(p.clock_vetoed for p in loaded.pairs) == 0                                     # every pair agrees with its own recorder's clock offset
    for c, want in (("101009", {"DJI_02_20260522_101626", "DJI_01_20260522_071030", "DJI_01_20260522_071042"}), ("104358", {"DJI_02_20260522_104715", "DJI_02_20260522_101626", "DJI_01_20260522_074119", "DJI_01_20260522_074131", "DJI_01_20260522_071042"}),
                    ("094342", {"DJI_02_20260522_094536", "DJI_01_20260522_063939"})):
        got = find_covering_audio_for_phrase("/v/" + CLIP[c], 700.0 if c == "101009" else 300.0, 710.0 if c == "101009" else 310.0, loaded)
        assert {Path(x.audio_file).stem for x in got} <= want, (c, [Path(x.audio_file).stem for x in got])


def test_pairs_without_clock_stamps_are_never_vetoed():
    p = SyncPair(aroll_file="/v/C0001.MP4", aroll_proxy="/v/p.mp4", audio_file="/v/Bob 1.WAV", offset_sec=5000.0, score=30.0, audio_duration_sec=600.0, computed_at=0.0)
    assert apply_clock_veto([p]) == [] and p.is_reliable
