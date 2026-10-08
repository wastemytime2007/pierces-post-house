import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import make_overlay as mo
import make_title as mt
sys.path.insert(0, str(HERE.parent / "captions"))

TL = {"clips": [{"idx": 1, "start": 0.0, "end": 5.0, "source": "cam.mp4", "source_path": "/x/cam.mp4", "src_in": 100.0, "src_out": 105.0},
                {"idx": 2, "start": 5.0, "end": 12.0, "source": "cam.mp4", "source_path": "/x/cam.mp4", "src_in": 200.0, "src_out": 207.0}], "audio": []}
SPEC = {"title": {"anchor": {"source": "cam.mp4", "source_sec": 100.5}, "small": "THE SUB SAID", "big": "“DONE”", "joke": "(THE BACKYARD DISAGREED)", "builds": [0.0, 0.5, 1.0], "hold": 2.7},
        "labels": [{"anchor": {"source": "cam.mp4", "source_sec": 200.0}, "text": "The closing", "word_step": 0.2, "until": "clip_end"}]}


def _spec(**over):
    import copy
    s = copy.deepcopy(SPEC)
    for k, v in over.items():
        s[k] = v
    return s


def test_every_element_gets_its_time_on_this_timeline_from_its_source_frame():
    p = mt.plan(SPEC, TL, 12.0)
    assert p["title"]["on"] == [0.5, 1.0, 1.5] and p["title"]["off"] == 3.2                       # the anchor frame 100.5 s is 0.5 s into the cut, the builds are offsets from it
    lb = p["labels"][0]
    assert lb["on"] == 5.0 and lb["word_times"] == [5.0, 5.2] and lb["words"] == ["THE", "CLOSING"]
    assert lb["off"] == pytest.approx(12.0 - mt.LABEL_CLEAR)                                      # clears just before its clip ends
    # the same spec on a LATER cut where that clip moved: the elements follow their frames
    moved = {"clips": [{**TL["clips"][1], "start": 0.0, "end": 7.0}, {**TL["clips"][0], "start": 7.0, "end": 12.0}], "audio": []}
    q = mt.plan(SPEC, moved, 12.0)
    assert q["title"]["on"][0] == 7.5 and q["labels"][0]["on"] == 0.0


def test_a_moment_that_is_not_in_the_cut_is_refused():
    bad = _spec(labels=[{"anchor": {"source": "cam.mp4", "source_sec": 150.0}, "text": "Nope"}])
    with pytest.raises(mo.OverlayError, match="is not in this cut"):
        mt.plan(bad, TL, 12.0)


def test_a_label_too_short_to_read_or_whose_last_word_would_come_after_it_clears_is_refused():
    short = _spec(labels=[{"anchor": {"source": "cam.mp4", "source_sec": 206.5}, "text": "Whose job", "until": "clip_end"}])      # 0.5 s left in its clip
    with pytest.raises(mt.TitleError, match="too short to read"):
        mt.plan(short, TL, 12.0)
    slow = _spec(labels=[{"anchor": {"source": "cam.mp4", "source_sec": 200.0}, "text": "one two three four five six", "word_step": 0.3, "until": 1.2}])
    with pytest.raises(mt.TitleError, match="last word would come on"):
        mt.plan(slow, TL, 12.0)


def test_elements_that_would_overlap_or_run_past_the_cut_are_refused():
    overlap = _spec(labels=[{"anchor": {"source": "cam.mp4", "source_sec": 103.0}, "text": "Too early", "until": 1.5}])           # starts at 3.0 s, while the title (0.5 to 3.2) is up
    with pytest.raises(mt.TitleError, match="on screen together"):
        mt.plan(overlap, TL, 12.0)
    with pytest.raises(mt.TitleError, match="past the end of the cut"):
        mt.plan(SPEC, TL, 3.0)


def test_a_spec_with_missing_or_unordered_parts_is_refused_before_anything_renders():
    with pytest.raises(mt.TitleError, match="neither a title, labels nor lower thirds"):
        mt.validate_spec({})
    with pytest.raises(mt.TitleError, match="three offsets"):
        mt.validate_spec(_spec(title={**SPEC["title"], "builds": [0.0, 1.0, 0.5]}))
    with pytest.raises(mt.TitleError, match="longer than the last build"):
        mt.validate_spec(_spec(title={**SPEC["title"], "hold": 0.8}))
    with pytest.raises(mt.TitleError, match="needs 'text' and 'anchor'"):
        mt.validate_spec(_spec(labels=[{"text": " "}]))


def test_the_layout_scales_from_the_frame_so_the_same_spec_reads_at_any_size():
    g = mt.layout(1080, 1920)
    assert (g["label_px"], g["big_px"], g["small_px"]) == (78, 248, 70) and g["label_y"] == round(1920 * 0.69)
    big = mt.layout(2160, 3840)
    assert all(abs(big[k] - 2 * g[k]) <= 1 for k in ("label_px", "big_px", "small_px", "joke_px"))  # a 4K portrait frame gets twice the type (to the pixel)
    wide = mt.layout(1920, 1080)
    assert wide["label_px"] == round(1080 * 9 / 16 * 0.072)                                         # landscape is sized from the short side


def test_the_project_written_has_one_span_per_word_and_events_for_every_element_and_no_placeholders_left(tmp_path, monkeypatch):
    gs = tmp_path / "gsap.min.js"
    gs.write_text("// gsap")
    monkeypatch.setattr(mo, "gsap_file", lambda: gs)
    p = mt.plan(SPEC, TL, 12.0)
    info = mt.write_project(tmp_path / "proj", p, 1080, 1920, 12.0)
    page = (tmp_path / "proj" / "index.html").read_text()
    assert "__" not in page.replace("__timelines", "")                                              # every placeholder filled
    assert page.count('class="hf-word"') == 2 and 'width: 1080px' in page and 'data-height="1920"' in page
    sels = [e["sel"] for e in info["events"]]
    assert "#field" in sels and {"#t_small", "#t_big", "#t_joke"} <= set(sels) and "#l0w0" in sels and "#l0w1" in sels
    on_off = {e["sel"]: (e["on"], e["off"]) for e in info["events"]}
    assert on_off["#t_big"] == (1.0, 3.2) and on_off["#l0w1"][0] == 5.2                             # hard on at its build, off with the rest of the title; a word at its own time
    assert json.loads(json.dumps(info["events"])) == info["events"]


# ------------------------------------------------------------------ a stylised lower third (name and role)

LT_SPEC = {"lower_thirds": [{"anchor": {"source": "cam.mp4", "source_sec": 102.0}, "name": "Bob", "sub": "Builder / Founder", "until": 3.2},
                            {"anchor": {"source": "cam.mp4", "source_sec": 201.0}, "name": "Mitch", "sub": "", "until": 3.0}]}


def test_a_lower_third_gets_its_time_from_its_source_frame_and_clears_after_its_hold():
    p = mt.plan(LT_SPEC, TL, 12.0)
    a, b = p["lower_thirds"]
    assert (a["on"], a["off"]) == (2.0, 5.0 - mt.LABEL_CLEAR) or a["off"] == pytest.approx(5.0 - mt.LABEL_CLEAR)      # asked for 3.2 s but its clip ends at 5 s: it clears just before the cut
    assert (b["on"], b["off"]) == (6.0, 9.0) and b["name"] == "Mitch" and b["sub"] == ""


def test_a_lower_third_too_short_to_read_or_over_another_element_is_refused():
    short = {"lower_thirds": [{"anchor": {"source": "cam.mp4", "source_sec": 104.2}, "name": "Bob", "until": 3.0}]}                    # 0.8 s left in its clip
    with pytest.raises(mt.TitleError, match="too short to read"):
        mt.plan(short, TL, 12.0)
    clash = _spec(lower_thirds=[{"anchor": {"source": "cam.mp4", "source_sec": 101.0}, "name": "Bob", "until": 2.0}])                 # while the title (0.5 to 3.2) is up
    with pytest.raises(mt.TitleError, match="on screen together"):
        mt.plan(clash, TL, 12.0)
    with pytest.raises(mt.TitleError, match="needs 'name' and 'anchor'"):
        mt.validate_spec({"lower_thirds": [{"name": " "}]})


def test_the_lower_third_sits_above_the_caption_band_and_its_box_is_recorded_for_the_captions():
    g = mt.layout(1080, 1920)
    assert g["lt_y"] == round(1920 * 0.55) and g["lt_y"] + g["lt_h"] < 1920 * 0.70                              # clear of where the captions sit
    assert mt.layout(1080, 1920, None, 0.5)["lt_y"] == 960
    w_long, _ = mt.lt_box(g, "Mitch", "Operator / CEO")
    w_short, _ = mt.lt_box(g, "Bob", "")
    assert w_long > w_short > g["lt_bar"]                                                                       # the panel is as wide as its longer line


def test_the_page_builds_the_panel_with_its_name_rule_and_role_and_a_wipe_event(tmp_path):
    p = mt.plan(LT_SPEC, TL, 12.0)
    info = mt.write_project(tmp_path / "proj", p, 1080, 1920, 12.0)
    page = (tmp_path / "proj" / "index.html").read_text()
    assert 'class="lt-name"' in page and ">Bob<" in page and "Builder / Founder" in page and 'class="lt-rule"' in page
    assert page.count('class="hf-lt clip"') == 2 and page.count('class="lt-sub"') == 1                           # a lower third with no role draws no empty role line
    assert [e["kind"] for e in info["events"] if e.get("kind")] == ["lt", "lt"]
    assert all(lt["w"] > 0 and lt["h"] > 0 for lt in p["lower_thirds"])


def test_captions_keep_clear_of_a_lower_third_only_while_it_is_on():
    import make_captions as mc
    mc.use_layout(1080, 1920)
    p = mt.plan(LT_SPEC, TL, 12.0)
    mt.write_project(Path(__import__("tempfile").mkdtemp()) / "x", p, 1080, 1920, 12.0)
    pl = {"kind": "title_layer", "place_overlay_on_timeline_at_sec": 0.0, "plan": p, "layout": mt.layout(1080, 1920)}
    blocked = mc.blocked_for(pl)
    assert [round(b["t0"], 2) for b in blocked] == [1.95, 5.95]
    live = {"text": "and all three houses", "show_start": 2.5, "show_end": 3.5}
    assert mc.pick_position(live, blocked, 0.0) == ("bottom", False)                                            # the band under the lower third is free: the captions do not jump to the top
