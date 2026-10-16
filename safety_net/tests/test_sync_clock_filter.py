"""The windowed "rescue" of weak sync pairs skips pairs whose recordings cannot overlap in time.

Ryan, 2026-10-15: a new project (Runnells Wall Removal) sat 20+ minutes in the rescue pass with the progress stuck at 36. Its 4 clips (9:43-10:55) were matched against 9 recorder files, six of them
recorded 6:39-8:11; the rescue tried ten windows on every weak pair, hours-away ones included. These are that project's real file names and durations.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app" / "python_backend"))

from precut_pipeline.audio_sync import clocks_rule_out_overlap  # noqa: E402

CLIPS = ["DJI_20260522094342_0001_D.MP4", "DJI_20260522101009_0002_D.MP4", "DJI_20260522103343_0003_D.MP4", "DJI_20260522104358_0004_D.MP4"]
AUDIO = [("DJI_01_20260522_063939.WAV", 30.8), ("DJI_01_20260522_063952.WAV", 30.8), ("DJI_01_20260522_071030.WAV", 30.8), ("DJI_01_20260522_071042.WAV", 30.8), ("DJI_01_20260522_074119.WAV", 11.0),
         ("DJI_01_20260522_074131.WAV", 10.9), ("DJI_02_20260522_094536.WAV", 30.8), ("DJI_02_20260522_101626.WAV", 30.8), ("DJI_02_20260522_104715.WAV", 7.1)]
CLIP_SEC = 683.2


def _pair(clip, audio, minutes):
    return SimpleNamespace(aroll_file="/v/" + clip, aroll_proxy="/v/proxies/x.mp4", audio_file="/v/Source Audio/" + audio, audio_duration_sec=minutes * 60)


def test_the_morning_recorder_files_and_one_far_afternoon_file_are_ruled_out_and_the_pairs_that_really_sync_are_tried():
    kept, skipped = set(), set()
    for c in CLIPS:
        for a, m in AUDIO:
            key = (c[12:18], a[10:21])                                                       # (clip start hhmmss, recorder file date_hhmm...)
            (skipped if clocks_rule_out_overlap(_pair(c, a, m), camera_duration_sec=CLIP_SEC) else kept).add((c[12:18], a))
    morning = {x for x in skipped if x[1].startswith(("DJI_01_",))}
    assert len(morning) == 6 * 4                                                             # every pair with a morning recorder file (6:39-8:11) against clips shot from 9:43
    assert skipped - morning == {("094342", "DJI_02_20260522_104715.WAV")}                    # and the 10:47 file against the 9:43 clip, 52 minutes apart
    assert kept >= {("094342", "DJI_02_20260522_094536.WAV"), ("101009", "DJI_02_20260522_101626.WAV"), ("103343", "DJI_02_20260522_101626.WAV"), ("104358", "DJI_02_20260522_104715.WAV")}   # pairs that really sync
    assert len(kept) == 11                                                                   # 36 pairs, 25 ruled out: only these 11 are tried


def test_a_pair_the_clocks_cannot_judge_is_never_skipped():
    p = _pair(CLIPS[0], "Bob 1.WAV", 30)                                                     # no clock stamp in the recorder's name
    assert clocks_rule_out_overlap(p, camera_duration_sec=CLIP_SEC) is None
    p = _pair("IMG_0001.MP4", AUDIO[0][0], 30)                                               # none in the camera's
    assert clocks_rule_out_overlap(p, camera_duration_sec=CLIP_SEC) is None
    p = _pair(CLIPS[0], AUDIO[0][0], 0)                                                       # no duration known
    assert clocks_rule_out_overlap(p, camera_duration_sec=CLIP_SEC) is None


def test_a_recorder_clock_a_few_minutes_off_is_still_tried():
    p = _pair("DJI_20260522094342_0001_D.MP4", "DJI_02_20260522_091500.WAV", 20)             # ends 9:35, the clip starts 9:43:42: 8 minutes apart, inside the 30 minute margin
    assert clocks_rule_out_overlap(p, camera_duration_sec=CLIP_SEC) is None
    p = _pair("DJI_20260522094342_0001_D.MP4", "DJI_02_20260522_080000.WAV", 20)             # ends 8:20: 83 minutes apart
    assert "min apart" in clocks_rule_out_overlap(p, camera_duration_sec=CLIP_SEC)


def test_a_recorder_whose_clock_is_hours_off_is_tried_where_it_really_is_once_its_own_offset_is_known():
    """2026-10-16: DJI_01 (Bob's recorder) sits 3 h 3 min 7 s from the camera's clock on every strong pair. By the file names alone its 6:39 file is hours from the 9:43 clip and is skipped; placed by its
    own learned offset it starts at 9:42 and overlaps it."""
    p = _pair(CLIPS[0], "DJI_01_20260522_063939.WAV", 30.8)
    assert clocks_rule_out_overlap(p, camera_duration_sec=CLIP_SEC)                           # by the names alone: ruled out
    assert clocks_rule_out_overlap(p, camera_duration_sec=CLIP_SEC, clock_offset_sec=10987.5) is None


def test_the_learned_offset_of_each_recorder_comes_from_its_strong_pairs_only():
    from precut_pipeline.audio_sync import learned_clock_offsets
    from test_sync_clock_veto import _pairs
    got = learned_clock_offsets(_pairs())
    assert abs(got["DJI_01_"] - 10987.5) < 3 and abs(got["DJI_02_"] - 63.5) < 3
