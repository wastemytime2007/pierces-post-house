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


def test_the_false_pairs_that_outscored_the_true_ones_are_vetoed_and_the_true_ones_stay():
    pairs = _pairs()
    vetoed = apply_clock_veto(pairs)
    kept = {(Path(p.aroll_file).name[12:18], Path(p.audio_file).stem) for p in pairs if p.is_reliable}
    assert kept == TRUE
    assert {(Path(p.aroll_file).name[12:18], Path(p.audio_file).stem) for p in vetoed} >= {("104358", "DJI_01_20260522_074119"), ("103343", "DJI_01_20260522_071030")}     # 28.0 and 17.8
    assert all(p.score >= 0 for p in pairs)                                                  # scores are never changed: the UI still shows what the matcher said


def test_a_project_whose_clocks_agree_with_none_of_its_matches_is_left_alone():
    """A recorder never set to the right time: no pair is consistent with the clocks, so the clocks are not evidence and nothing is vetoed."""
    pairs = _pairs(shift=36000.0)                                                            # every offset ten hours off the clocks' prediction
    assert apply_clock_veto(pairs) == [] and all(not p.clock_vetoed for p in pairs)


def test_a_saved_state_from_before_the_veto_is_judged_by_it_when_loaded_and_the_cut_gets_only_the_right_recorder():
    from dataclasses import asdict
    state = AudioSyncState(pairs=_pairs())
    saved = state.to_dict()
    for p in saved["pairs"]:
        p.pop("clock_vetoed", None)                                                          # a state written by the app before this change
    loaded = AudioSyncState.from_dict(saved)
    assert sum(p.clock_vetoed for p in loaded.pairs) == 7
    for c, want in (("101009", {"DJI_02_20260522_101626"}), ("104358", {"DJI_02_20260522_104715", "DJI_02_20260522_101626"}), ("094342", {"DJI_02_20260522_094536"})):
        got = find_covering_audio_for_phrase("/v/" + CLIP[c], 700.0 if c == "101009" else 300.0, 710.0 if c == "101009" else 310.0, loaded)
        assert {Path(x.audio_file).stem for x in got} <= want, (c, [Path(x.audio_file).stem for x in got])


def test_pairs_without_clock_stamps_are_never_vetoed():
    p = SyncPair(aroll_file="/v/C0001.MP4", aroll_proxy="/v/p.mp4", audio_file="/v/Bob 1.WAV", offset_sec=5000.0, score=30.0, audio_duration_sec=600.0, computed_at=0.0)
    assert apply_clock_veto([p]) == [] and p.is_reliable
