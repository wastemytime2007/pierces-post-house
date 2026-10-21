"""The framing rules (labs/reframe/framing_rules.json, docs/reference/FRAMING_RULES.md) must agree with themselves and with the arithmetic they quote."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
RULES = json.loads((HERE / "framing_rules.json").read_text())
DOC = (HERE.parent.parent / "docs" / "reference" / "FRAMING_RULES.md").read_text()


def test_the_zones_nest_and_the_face_fits_inside_them():
    p = RULES["portrait"]
    w, h = p["output"]
    hard, pref = p["safe_px"]["hard"], p["safe_px"]["preferred"]
    assert all(pref[k] >= hard[k] for k in hard), "the preferred zone is never looser than the hard one"
    eye_min, eye_max = (p["eye_line_fraction_from_top"][k] * h for k in ("min", "max"))
    face = p["face_height_fraction"]["max"] * h
    assert eye_min > pref["top"], "the eyes sit below the top no-go band"
    head_top = eye_max - 0.4 * face                       # the eye line is about 40% down a face
    assert head_top >= p["head_top_min_px"]["value"], "even the lowest eyes with the biggest face keep the head clear of the top band"
    chin = eye_min + 0.6 * face
    assert chin < p["caption_band_start_y_px"]["value"], "the chin ends above where captions start"
    assert p["caption_band_start_y_px"]["value"] < h - pref["bottom"], "captions start above the bottom no-go band"
    assert p["lower_third_top_fraction"]["value"] * h > eye_max + 0.6 * face, "a lower third starts below the chin"


def test_the_window_fraction_and_the_fill_scales_are_the_arithmetic_the_doc_quotes():
    w, h = RULES["portrait"]["output"]
    src = RULES["resolution"]["sources"]
    assert abs((src["uhd"][1] * 9 / 16) / src["uhd"][0] - RULES["portrait"]["window_fraction_of_16x9_width"]["value"]) < 0.001
    fill = {k: round(100 * h / s[1]) for k, s in src.items()}          # percent that fills the frame's height
    assert fill == {"mitch_red": 44, "bob_red": 59, "uhd": 89, "proxy": 178}
    for pct, free in ((44, "2.25x"), (59, "1.69x"), (89, "1.12x")):
        assert f"{pct}%" in DOC and free in DOC, f"the doc's table carries {pct}% and {free}"
    assert fill["proxy"] > RULES["resolution"]["scale_soft_max_pct"]["value"], "a proxy is already past the softness limit: no punch-in"


def test_the_numbers_already_in_the_app_are_the_ones_the_rules_say():
    root = HERE.parent
    follow = (root / "review_loop" / "follow_speaker.py").read_text()
    punch = (root / "review_loop" / "punch_in.py").read_text()
    finish = (root / "review_loop" / "finish_cut.py").read_text()
    assert f"FRAME_MIN_SEC = {RULES['motion']['min_speaker_change_sec_to_move_frame']['value']}" in follow
    assert f"PUNCH = {RULES['punch_in']['factor']['value']}" in punch
    assert f"SOFT_MAX_SCALE = {RULES['resolution']['scale_soft_max_pct']['value']:.1f}" in punch
    assert f"MIN_PUNCH = {RULES['punch_in']['min_punch_factor']['value'] if 'min_punch_factor' in RULES['punch_in'] else RULES['resolution']['min_punch_factor']['value']:.2f}" in punch
    assert f"TAG_Y = {RULES['portrait']['lower_third_top_fraction']['value']}" in finish
    lo, hi = RULES["punch_in"]["sourced_range"]["min"], RULES["punch_in"]["sourced_range"]["max"]
    assert lo <= RULES["punch_in"]["factor"]["value"] <= hi


def test_every_rule_says_how_solid_it_is():
    allowed = {"sourced", "ryan", "derived", "mine"}

    def walk(node, path):
        if isinstance(node, dict):
            if "basis" in node:
                assert node["basis"].split(" ")[0] in allowed, f"{path}: basis starts with one of {allowed}"
            for k, v in node.items():
                if k != "basis":
                    walk(v, f"{path}.{k}")
    walk(RULES, "rules")
