"""Hermetic tests for the overlay planner: mapping, layout and timing. No HyperFrames, no media."""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import make_overlay as mo  # noqa: E402

BOX = {"x0": 0.331, "y0": 0.479, "x1": 0.502, "y1": 0.597}     # the box Ryan drew on the tiling cut
TIMELINE = {"clips": [
    {"idx": 3, "start": 4.0, "end": 11.0, "source": "A.MP4", "src_in": 320.0, "src_out": 327.0},
    {"idx": 4, "start": 11.95, "end": 21.39, "source": "A.MP4", "src_in": 333.6, "src_out": 343.04},
]}
NOTE = {"source": "A.MP4", "source_sec": 335.61, "shapes": [{"type": "rect", "bbox": BOX}]}


def test_locate_finds_the_frame_by_source_position_not_by_the_time_the_note_was_left():
    clip, t = mo.locate(NOTE, TIMELINE)
    assert clip["idx"] == 4 and t == pytest.approx(11.95 + 2.01)


def test_locate_refuses_a_frame_that_is_no_longer_in_the_cut():
    with pytest.raises(mo.OverlayError, match="not in this cut"):
        mo.locate({"source": "A.MP4", "source_sec": 331.0}, TIMELINE)


def test_union_bbox_and_no_drawing():
    u = mo.union_bbox([{"bbox": BOX}, {"bbox": {"x0": 0.1, "y0": 0.9, "x1": 0.2, "y1": 0.95}}])
    assert (u["x0"], u["y0"], u["x1"], u["y1"]) == (0.1, 0.479, 0.502, 0.95)
    with pytest.raises(mo.OverlayError, match="no drawing"):
        mo.union_bbox([])


def test_layout_surrounds_the_drawing_and_keeps_everything_on_screen():
    g = mo.layout(BOX, "The spacer", "A piece of cardboard pulled off the box")
    b = g["box"]
    assert b["x"] < BOX["x0"] * mo.W and b["x"] + b["w"] > BOX["x1"] * mo.W          # padded around the drawing
    assert b["y"] < BOX["y0"] * mo.H and b["y"] + b["h"] > BOX["y1"] * mo.H
    lb = g["label"]
    assert 50 <= lb["x"] and lb["x"] + lb["w"] <= mo.W - 50 and lb["y"] + lb["h"] <= mo.H - 50
    assert lb["y"] > b["y"] + b["h"]                                                 # label sits below a mid-frame box


def test_arrow_runs_from_the_label_edge_to_the_box_edge():
    g = mo.layout(BOX, "The spacer", "A piece of cardboard pulled off the box")
    a, b, lb = g["arrow"], g["box"], g["label"]
    assert a["y"] == lb["y"]                                                         # tail on the label's top edge
    assert lb["x"] + 100 <= a["x"] <= lb["x"] + lb["w"] - 100
    tip_x, tip_y = a["tip"]
    assert b["x"] <= tip_x <= b["x"] + b["w"] and abs(tip_y - (b["y"] + b["h"])) <= 8   # tip on the box's bottom edge
    length = math.hypot(tip_x - a["x"], tip_y - a["y"])
    assert a["len"] == pytest.approx(length - mo.ARROW_HEAD, abs=0.2)


def test_label_goes_above_when_the_box_is_low_in_the_frame():
    low = {"x0": 0.4, "y0": 0.82, "x1": 0.55, "y1": 0.92}
    g = mo.layout(low, "The spacer", "")
    assert g["label"]["y"] + g["label"]["h"] < g["box"]["y"]
    assert g["arrow"]["y"] == g["label"]["y"] + g["label"]["h"]                      # tail on the label's bottom edge
    assert g["arrow"]["angle"] > 0                                                   # arrow points down to the box


def test_tiny_drawing_still_gets_a_readable_box():
    g = mo.layout({"x0": 0.5, "y0": 0.5, "x1": 0.505, "y1": 0.505}, "Title", "")
    assert g["box"]["w"] >= 120 and g["box"]["h"] >= 80


def test_long_subtitle_shrinks_and_an_impossible_one_is_refused():
    assert mo.layout(BOX, "Title", "x" * 52)["subtitle_px"] < 36
    with pytest.raises(mo.OverlayError, match="too long"):
        mo.layout(BOX, "Title", "x" * 200)


def test_plan_times_the_callout_around_the_note_and_stays_inside_the_clip():
    p = mo.plan(NOTE, TIMELINE, "The spacer", "sub")
    assert p["start"] == pytest.approx(p["t_note"] - mo.LEAD)
    assert p["cfg"]["t_in"] == pytest.approx(mo.LEAD)
    assert p["cfg"]["t_out"] == pytest.approx(mo.LEAD + mo.HOLD)
    assert p["total"] == pytest.approx(mo.LEAD + mo.HOLD + mo.FADE_OUT + mo.TAIL)
    assert p["start"] >= 11.95 and p["start"] + p["total"] <= 21.39


def test_plan_shortens_the_lead_at_the_start_of_a_shot_and_the_hold_at_its_end():
    early = dict(NOTE, source_sec=333.9)                                             # 0.3s into the clip
    p = mo.plan(early, TIMELINE, "T", "")
    assert p["start"] == pytest.approx(11.95) and p["cfg"]["t_in"] == pytest.approx(0.3, abs=0.01)
    late = dict(NOTE, source_sec=340.4)                                              # 6.8s in, 2.6s left
    p2 = mo.plan(late, TIMELINE, "T", "")
    assert p2["start"] + p2["total"] <= 21.39 + 1e-6 and p2["cfg"]["t_out"] - p2["cfg"]["t_in"] >= 1.5
    too_late = dict(NOTE, source_sec=342.9)
    with pytest.raises(mo.OverlayError, match="not enough of this shot"):
        mo.plan(too_late, TIMELINE, "T", "")


def test_a_longer_hold_is_used_when_the_shot_has_room_and_clamped_when_it_does_not():
    base = mo.plan(NOTE, TIMELINE, "T", "")
    assert base["hold"] == pytest.approx(mo.HOLD) and base["hold_wanted"] == pytest.approx(mo.HOLD)
    room = mo.plan(NOTE, TIMELINE, "T", "", hold=5.0)                                # frame at 13.96s of a clip that ends at 21.39s
    assert room["cfg"]["t_out"] - room["cfg"]["t_in"] == pytest.approx(5.0, abs=0.01) and room["total"] == pytest.approx(room["cfg"]["t_out"] + mo.FADE_OUT + mo.TAIL, abs=0.01)
    tight = mo.plan(dict(NOTE, source_sec=340.4), TIMELINE, "T", "", hold=9.0)       # 2.6s left after the frame
    assert tight["hold_wanted"] == 9.0 and tight["hold"] < 3.0 and tight["start"] + tight["total"] <= 21.39 + 1e-6


def test_a_remembered_region_stands_in_for_a_drawing():
    drawn = mo.plan(NOTE, TIMELINE, "The spacer", "sub")
    remembered = mo.plan({"source": "A.MP4", "source_sec": 335.61, "region": BOX}, TIMELINE, "The spacer", "sub")
    assert remembered["cfg"]["box"] == drawn["cfg"]["box"] and remembered["cfg"]["label"] == drawn["cfg"]["label"]
