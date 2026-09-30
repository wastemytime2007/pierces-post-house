"""Hermetic tests for caption grouping, timing and placement. No HyperFrames, no media."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import make_captions as mc  # noqa: E402


def W(text, a, b):
    return mc.Word(text, a, b)


def speech(*items):
    return [W(t, a, b) for t, a, b in items]


# geometry of the real note 3 callout (1920x1080 layout space), placed at 12.98s
OVERLAY = {"place_overlay_on_timeline_at_sec": 12.98, "geometry": {
    "box": {"x": 612, "y": 504, "w": 374, "h": 153}, "label": {"x": 239, "y": 807, "w": 880, "h": 150},
    "arrow": {"x": 579, "y": 807, "len": 228, "angle": -32.8, "tip": [799, 665]},
    "t_in": 1.0, "t_out": 4.3, "fade_out": 0.35}}


def test_dashes_become_commas_and_are_counted():
    assert mc.sanitize("Wait — what") == ("Wait, what", 1)
    assert mc.sanitize("ten–twelve") == ("ten, twelve", 1)
    assert mc.sanitize("nothing to do") == ("nothing to do", 0)


def test_clean_words_drops_empty_entries_and_reports_dashes():
    ws, n = mc.clean_words([W(" Yeah, ", 0, 0.4), W("", 0.4, 0.5), W("—", 0.5, 0.6), W("well", 0.6, 0.8)])
    assert [w.text for w in ws] == ["Yeah,", "well"] and n == 1


def test_grouping_breaks_at_sentence_ends_pauses_commas_and_size():
    ws = speech(("Yeah,", 0, .4), ("well", .5, .6), ("something", .6, .9), ("went", .9, 1.1), ("wrong.", 1.1, 1.5),
                ("Alright,", 2.0, 2.4), ("so", 2.4, 2.6), ("it", 2.6, 2.7),
                ("was", 3.6, 3.8), ("what", 3.8, 4.0))            # 0.9s pause before "was"
    g = mc.group_words(ws)
    assert [x["text"] for x in g] == ["Yeah, well something went wrong.", "Alright, so it", "was what"]
    long = speech(*[(f"word{i}", i * 0.3, i * 0.3 + 0.25) for i in range(14)])
    assert all(len(x["words"]) <= 6 for x in mc.group_words(long))
    assert all(len(x["text"]) <= 34 for x in mc.group_words(long))


def test_a_comma_only_breaks_after_a_phrase_of_three_words():
    g = mc.group_words(speech(("So,", 0, .2), ("I", .2, .3), ("tore", .3, .5), ("this,", .5, .7), ("piece", .7, .9), ("off", .9, 1.0)))
    assert [x["text"] for x in g] == ["So, I tore this,", "piece off"]


def test_visibility_covers_each_line_and_lines_never_overlap():
    ws = speech(("One.", 0.0, 0.3), ("Two.", 0.4, 0.7), ("Three.", 0.75, 1.0), ("Four.", 3.0, 3.3))
    g = mc.group_words(ws)
    for x in g:
        assert x["show_start"] <= x["words"][0].start + 1e-9
        assert x["show_end"] >= x["words"][-1].end
    for a, b in zip(g, g[1:]):
        assert a["show_end"] <= b["show_start"] + 1e-9                 # one line on screen at a time
    assert g[-1]["show_end"] == pytest.approx(3.3 + 0.30)              # a lone last line holds 0.3s


def test_estimated_box_grows_with_text_and_is_capped():
    (w1, h1), (w2, h2) = mc.est_box("Okay."), mc.est_box("Yeah, well something went wrong.")
    assert w2 > w1 and h1 == h2
    w3, h3 = mc.est_box("x" * 200)
    assert w3 == mc.MAXW and h3 > h1                                   # wraps to more lines


def test_bottom_by_default_and_top_when_a_callout_is_in_the_way():
    blocked = [mc.blocked_from_overlay(OVERLAY)]
    clear = {"text": "Alright, so it was what I did.", "show_start": 2.0, "show_end": 4.0}
    assert mc.pick_position(clear, blocked, 0.0) == ("bottom", False)
    during = {"text": "Making another mark.", "show_start": 15.0, "show_end": 16.4}
    assert mc.pick_position(during, blocked, 0.0) == ("top", False)    # 12.98 + 1.0 .. 12.98 + 4.65 is the callout's time
    after = {"text": "And that's why I determined this", "show_start": 18.0, "show_end": 20.0}
    assert mc.pick_position(after, blocked, 0.0) == ("bottom", False)


def test_a_window_offset_shifts_caption_time_into_the_cuts_clock():
    blocked = [mc.blocked_from_overlay(OVERLAY)]
    g = {"text": "Making another mark.", "show_start": 2.0, "show_end": 3.4}    # window starts at 13.0, so this is 15.0-16.4
    assert mc.pick_position(g, blocked, 13.0)[0] == "top"


def test_both_bands_blocked_falls_back_to_bottom_and_says_so():
    every = {"t0": 0, "t1": 99, "rects": [(0, 0, 1920, 1080)]}
    assert mc.pick_position({"text": "Hello there", "show_start": 1, "show_end": 2}, [every], 0.0) == ("bottom", True)


def test_html_is_escaped_and_each_word_has_a_base_and_a_highlight_layer():
    out = mc.build_groups_html([{"pos": "top", "words": [W("<b>hi</b>", 0, .2), W("you", .2, .4)]}], 5.0)
    assert "&lt;b&gt;hi&lt;/b&gt;" in out and "<b>hi" not in out
    assert out.count('class="b"') == 2 and out.count('class="h"') == 2 and 'class="cap top clip"' in out


def test_project_has_no_unfilled_placeholders(tmp_path, monkeypatch):
    gs = tmp_path / "gsap.js"
    gs.write_text("/* gsap */")
    monkeypatch.setattr(mc.mo, "gsap_file", lambda: gs)
    groups = mc.group_words(speech(("Hello", 0.1, 0.5), ("there.", 0.5, 0.9)))
    groups[0]["pos"] = "bottom"
    mc.write_project(tmp_path / "p", groups, 3.0, "pill")
    page = (tmp_path / "p" / "index.html").read_text()
    import re
    assert not re.findall(r"__[A-Z]+__", page) and "/*__CFG__*/" not in page
    cfg = json.loads(page.split("const CFG = ")[1].split(";\n")[0])
    assert cfg["groups"][0]["pos"] == "bottom" and len(cfg["groups"][0]["words"]) == 2
    assert 'data-duration="3.0"' in page
    with pytest.raises(KeyError):
        mc.write_project(tmp_path / "q", groups, 3.0, "no-such-style")


# ---- a wording fix from a note, and starred curse words ----

def test_a_fix_replaces_the_line_showing_at_the_moment_and_spreads_the_new_words_over_the_old_span():
    ws = [mc.Word(t, 10.0 + i * 0.3, 10.0 + i * 0.3 + 0.25) for i, t in enumerate("And that's why I determined this".split())]
    ws2 = [mc.Word(t, 14.0 + i * 0.3, 14.0 + i * 0.3 + 0.25) for i, t in enumerate("next line here".split())]
    groups = [{"words": ws, "text": " ".join(w.text for w in ws), "show_start": 9.95, "show_end": 11.8, "pos": "bottom"},
              {"words": ws2, "text": "next line here", "show_start": 13.95, "show_end": 15.0, "pos": "bottom"}]
    out = mc.apply_fixes([dict(g) for g in groups], [{"at": 11.0, "text": "and that's how i determined"}], 0.0)
    g = out[0]
    assert g["text"] == "And that's how I determined" and [w.text for w in g["words"]] == ["And", "that's", "how", "I", "determined"]      # casing follows the old line
    assert g["words"][0].start == pytest.approx(10.0) and g["words"][-1].end == pytest.approx(ws[-1].end)                                # the old spoken span
    assert all(a.end <= b.start + 1e-9 for a, b in zip(g["words"], g["words"][1:])) and g["words"][-1].end > g["words"][0].start
    assert out[1]["text"] == "next line here"                                                                                          # the other line is untouched


def test_a_fix_for_a_moment_with_no_line_showing_is_refused_and_the_window_offset_is_respected():
    w = [mc.Word("a", 1.0, 1.2)]
    g = [{"words": w, "text": "a", "show_start": 0.95, "show_end": 1.5, "pos": "bottom"}]
    with pytest.raises(mc.CaptionError, match="no caption line is showing at 9.00s"):
        mc.apply_fixes([dict(g[0])], [{"at": 9.0, "text": "b"}], 0.0)
    assert mc.apply_fixes([dict(g[0])], [{"at": 21.1, "text": "bee"}], 20.0)[0]["text"] == "bee"            # 21.1 s on the timeline is 1.1 s into a window that starts at 20 s


def test_the_fixed_casing_keeps_punctuation_and_a_standalone_i():
    assert mc.tidy_case("and i think so", "We did.") == "And I think so."
    assert mc.tidy_case('"lowercase stays"', "all lower") == "lowercase stays"
    assert mc.tidy_case("Kept.", "Old?") == "Kept."


def test_listed_words_are_starred_in_the_caption_text_and_other_words_are_not():
    class W:
        def __init__(self, t, s, e):
            self.text, self.start, self.end = t, s, e
    out, _ = mc.clean_words([W("Fucking", 0, 0.3), W("class,", 0.3, 0.6), W("shit.", 0.6, 0.9)])
    assert [w.text for w in out] == ["F******", "class,", "s***."]
    kept, _ = mc.clean_words([W("Fucking", 0, 0.3)], censor=False)
    assert kept[0].text == "Fucking"
