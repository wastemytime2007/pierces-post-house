import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import make_audio as ma
import score_music as sm


def test_a_score_becomes_a_composition_plan_with_one_section_per_beat_in_milliseconds():
    plan = sm.to_plan({"global": "warm acoustic; mid tempo", "avoid": "vocals; loud drums",
                       "sections": [{"name": "Title", "seconds": 3.0, "style": "sparse; one note"}, {"name": "Story", "seconds": 19.85, "style": "groove"}]})
    assert plan["positive_global_styles"] == ["warm acoustic", "mid tempo"] and plan["negative_global_styles"] == ["vocals", "loud drums"]
    assert [(s["section_name"], s["duration_ms"]) for s in plan["sections"]] == [("Title", 3000), ("Story", 19850)]
    assert plan["sections"][0]["positive_local_styles"] == ["sparse", "one note"]


REF = {"seconds": 54.0, "bpm": 110.0, "tempo_confidence": 0.3, "tone_centre_hz": 135, "rolloff_hz": 194, "onsets_per_sec": 3.28, "dynamic_spread_db": 4.3, "stereo_width": 0.42, "low_end_fraction": 0.875, "lufs": -26.4, "lra": 3.6}


def test_the_references_measured_feel_goes_into_the_style_in_words_without_naming_any_song():
    s = sm.with_reference({"global": "deep sub bass; kick on the beat", "sections": []}, REF)
    g = s["global"]
    assert "about 110 BPM" in g and "bass-weighted" in g and "busy, rhythmic texture" in g and "strong low end" in g and "kick on the beat" in g


def test_the_take_closest_to_the_reference_is_kept_and_a_passing_take_stops_the_search(tmp_path):
    feats = {"a.mp3": {**REF, "bpm": 140.0, "tone_centre_hz": 400}, "b.mp3": {**REF, "bpm": 110.0, "onsets_per_sec": 3.5, "dynamic_spread_db": 2.0}, "c.mp3": REF}
    made = []

    def gen(score, cache, salt=""):
        name = {"": "a.mp3", "take2": "b.mp3", "take3": "c.mp3"}[salt]
        made.append(name)
        return Path(name), {"cached": True, "file": name, "salt": salt}
    best, info = sm.pick_take({"global": "x", "sections": []}, REF, tmp_path, tries=3, generate=gen, analyse=lambda p: feats[str(p)])
    assert str(best) == "b.mp3" and info["passed"] and info["chosen_take"] == 2 and made == ["a.mp3", "b.mp3"]    # take 2 passes, so take 3 is never generated


def test_a_section_outside_the_apis_three_to_120_seconds_is_refused_before_anything_is_sent():
    for bad in (2.0, 130.0):
        with pytest.raises(ma.AudioError, match="3.0 to 120.0"):
            sm.to_plan({"global": "x", "sections": [{"name": "S", "seconds": bad, "style": "y"}]})
