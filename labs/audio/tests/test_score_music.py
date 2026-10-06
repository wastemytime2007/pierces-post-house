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


def test_a_section_outside_the_apis_three_to_120_seconds_is_refused_before_anything_is_sent():
    for bad in (2.0, 130.0):
        with pytest.raises(ma.AudioError, match="3.0 to 120.0"):
            sm.to_plan({"global": "x", "sections": [{"name": "S", "seconds": bad, "style": "y"}]})
