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


def _line(text, start):
    ws = [mc.Word(t, start + i * 0.3, start + i * 0.3 + 0.25) for i, t in enumerate(text.split())]
    return {"words": ws, "text": text, "show_start": start - 0.05, "show_end": ws[-1].end + 0.05, "pos": "bottom"}


def test_a_saved_fix_finds_its_line_by_the_words_it_changed_not_by_the_time_and_never_lands_on_another_line():
    """2026-10-12: a fix saved at 22.59 s turned "So the idea is with" into "$23,000" after the cut had changed and that time was a different line."""
    groups = [_line("this septic, it costs $23", 17.0), _line("000 is the quote we", 19.0), _line("So the idea is with", 22.0)]
    fix = {"at": 22.59, "text": "$23,000", "was": "this septic, it costs $23"}                            # the line it changed is now at 17 s, the time points at another line
    dropped = []
    out = mc.apply_fixes([dict(g) for g in groups], [fix], 0.0, dropped)
    assert [g["text"] for g in out] == ["$23,000", "000 is the quote we", "So the idea is with"] and dropped == []     # found by its words
    gone = {"at": 22.59, "text": "$23,000", "was": "a line that is no longer in the cut"}
    dropped = []
    out = mc.apply_fixes([dict(g) for g in groups], [gone], 0.0, dropped)
    assert [g["text"] for g in out] == [g["text"] for g in groups] and dropped == [gone]                       # the line is gone: the fix is dropped, nothing else is touched


def test_two_lines_with_the_same_words_get_the_fix_on_the_one_nearest_its_time():
    groups = [_line("so the idea is", 5.0), _line("something else", 8.0), _line("so the idea is", 12.0)]
    out = mc.apply_fixes([dict(g) for g in groups], [{"at": 12.4, "text": "the plan is", "was": "so the idea is"}], 0.0, [])
    assert out[0]["text"] == "so the idea is" and out[1]["text"] == "something else" and out[2]["text"].lower() == "the plan is"       # the second "so the idea is" (at 12 s) took the fix


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


def test_captions_keep_clear_of_a_title_cards_words_and_a_name_tag(monkeypatch):
    mc.use_layout(1080, 1920) if hasattr(mc, "use_layout") else None
    title_layer = {"kind": "title_layer", "place_overlay_on_timeline_at_sec": 0.0, "plan": {
        "title": {"on": [0.0, 0.2, 0.4], "off": 2.4, "small": "The septic you don't replace yet", "big": "", "joke": ""},
        "labels": [{"on": 3.0, "off": 5.5, "text": "Bob"}]}}
    blocked = mc.blocked_for(title_layer)
    assert [round(b["t1"], 1) for b in blocked] == [2.4, 5.5]                                   # one window for the card, one for the tag
    mid = {"text": "So we bought three houses", "show_start": 1.0, "show_end": 2.0}
    tag = {"text": "and all three", "show_start": 3.5, "show_end": 4.5}
    clear = {"text": "later", "show_start": 7.0, "show_end": 8.0}
    # a caption sits at the bottom, clear of the card's text band and of the tag's lower-left box, so none of them has to move
    for g in (mid, tag, clear):
        pos, collided = mc.pick_position(g, blocked, 0.0)
        assert not collided
    assert all(r[3] > 0 and r[2] > 0 for b in blocked for r in b["rects"])


def test_two_readings_that_agree_partly_are_a_warning_and_two_that_hardly_agree_are_a_failure():
    """Ryan, 2026-10-15: 65% agreement on crew talk threw away the whole finish; it is a warning now, and only near-invention (under 50%) is a failure."""
    import verify_captions as vc
    assert vc.independent_verdict(0.92, 0.04) is True
    assert vc.independent_verdict(0.65, 0.04) is None                      # the screenshot's case: 65%, median 40 ms
    assert vc.independent_verdict(0.40, 0.04) is False
    assert vc.independent_verdict(0.90, 0.80) is False                     # agreeing words that start far apart in time are not the same captions


def test_a_two_word_line_spoken_in_a_blink_is_joined_to_a_neighbour_not_left_as_a_caption():
    """WK_04, 2026-10-21: "of it." timed at 0.06 s was a line of its own; its fade-out began before its fade-in, it stayed on screen to the end and the render check failed 49 times."""
    ws = speech(("I", 0, .1), ("did", .1, .25), ("not", .25, .4), ("think", .4, .6), ("so.", .6, .9),
                ("of", .92, .98), ("it.", .98, .98),
                ("Then", 1.0, 1.2), ("we", 1.2, 1.3), ("left.", 1.3, 1.6))
    g = mc.group_words(ws)
    assert [w.text for x in g for w in x["words"]] == [w.text for w in ws], "no word lost, none reordered"
    assert all(x["words"][-1].end - x["words"][0].start >= mc.FRAGMENT_SEC for x in g), [x["text"] for x in g]
    assert all(x["show_end"] - x["show_start"] >= 0.26 for x in g), "every line is on screen at least as long as the fades that show and hide it"


def test_a_tiny_line_no_neighbour_takes_by_width_is_joined_to_the_nearer_one_anyway():
    a = speech(("extraordinarily", 0, .8), ("comprehensive", .8, 1.5))
    tiny = speech(("of", 1.52, 1.56), ("it.", 1.56, 1.57))
    b = speech(("documentation", 1.9, 2.6), ("everywhere.", 2.6, 3.2))
    out = mc._fold_fragments([a, tiny, b], max_words=6, max_chars=10, gap=0.4)
    assert len(out) == 2 and [w.text for w in out[0]][-2:] == ["of", "it."], "the nearer (previous) line took it"


def test_the_template_never_fades_a_line_out_before_it_has_faded_in_and_hides_it_for_certain():
    t = (Path(__file__).resolve().parents[1] / "caption_template.html").read_text()
    assert "Math.max(g.show_end - 0.12, g.show_start + 0.14)" in t
    assert "tl.set(id, { opacity: 0 }, Math.max(g.show_end, g.show_start + 0.26))" in t
