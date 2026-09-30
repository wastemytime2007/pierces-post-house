"""Hermetic tests for learning from Ryan's bleep edits: what is recorded, what the model changes (small, bounded, from the defaults), and the loop through apply_edits."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "review_loop"))
sys.path.insert(0, str(HERE.parents[1] / "safety_net"))

import apply_edits as ae  # noqa: E402
import bleep as bl  # noqa: E402
import learn as lr  # noqa: E402
from test_bleep import _speech_with_burst, cut_media, xml  # noqa: E402,F401

BEFORE = {"origin": "automatic", "spans": [[10.0, 11.0], [20.0, 21.0], [30.0, 31.0]],
          "hits": [{"word": "shit", "start": 10.1, "end": 10.8, "source": "transcript"},
                   {"word": "fuck (was hidden; revealed by silencing 20.05-20.35s)", "start": 20.2, "end": 20.7, "source": "revealed"},
                   {"word": "damn", "start": 30.1, "end": 30.8, "source": "transcript"}],
          "revealed": [{"word": "fuck", "word_start": 20.2, "word_end": 20.7, "start": 20.2, "end": 20.7, "revealed": True, "masked": [[20.05, 20.35]]}],
          "words": [["what", 39.6, 40.2, 0.9], ["the", 40.2, 40.4, 0.9]], "loud": [[39.7, 40.1]]}


def test_edits_are_classified_against_what_the_tool_guessed():
    edits = {"sequence": "Cut A", "spans": [{"start": 10.0, "end": 11.0}, {"start": 20.3, "end": 20.9}, {"start": 39.8, "end": 40.1}]}    # kept, moved, (30-31 removed), one added inside the stretched "what"
    recs = lr.records_from_edits(edits, BEFORE, "Cut A")
    by = {(r["kind"], r["source"]): r for r in recs}
    assert set(by) == {("kept", "transcript"), ("adjusted", "revealed"), ("deleted", "transcript"), ("added", "manual")}
    adj = by[("adjusted", "revealed")]
    assert adj["word_timing"] == {"start": 20.2, "end": 20.7} and adj["final"] == {"start": 20.3, "end": 20.9} and adj["auto"] == {"start": 20.0, "end": 21.0}
    assert by[("kept", "transcript")]["word_timing"] == {"start": 10.1, "end": 10.8}                       # the word's own timing, not the padded span
    assert by[("deleted", "transcript")]["final"] is None and by[("deleted", "transcript")]["word"] == "damn"
    add = by[("added", "manual")]
    assert add["context"]["in_loud_stretch"] is True and add["context"]["inside_word"] == "what" and add["context"]["hiding_word_duration"] == 0.6   # what Whisper wrote where he put it


def test_the_same_edits_applied_twice_count_once_and_a_hand_edited_baseline_teaches_nothing(tmp_path):
    edits = {"sequence": "Cut A", "spans": [{"start": 20.3, "end": 20.9}]}
    recs = lr.records_from_edits(edits, BEFORE, "Cut A")
    assert lr.save(recs) == len(recs) and lr.save(recs) == 0 and len(lr.load_records()) == len(recs)
    assert lr.records_from_edits(edits, dict(BEFORE, origin="edits"), "Cut A") == []                         # already his own edit: nothing automatic to compare with


def test_padding_moves_a_quarter_of_the_way_after_one_edit_and_stays_inside_its_bounds(tmp_path):
    one = [{"kind": "adjusted", "source": "revealed", "auto": None, "final": {"start": 29.44, "end": 29.70}, "word_timing": {"start": 29.38, "end": 29.58}, "context": {}}]
    m = lr.fit(one)["pads"]["revealed"]
    assert m["before"] == pytest.approx((1 * -0.06 + 3 * 0.08) / 4, abs=0.001) and m["after"] == pytest.approx(0.12, abs=0.001) and m["n"] == 1     # Ryan's real edit
    assert lr.fit(one)["pads"]["transcript"]["n"] == 0 and lr.fit(one)["pads"]["transcript"]["before"] == bl.PAD_BEFORE                        # other sources stay at the defaults
    ten = one * 10
    assert lr.fit(ten)["pads"]["revealed"]["before"] == pytest.approx((10 * -0.06 + 0.24) / 13, abs=0.001)                                    # ten edits: most of the way
    wild = [{"kind": "adjusted", "source": "transcript", "auto": None, "final": {"start": 5.0, "end": 9.0}, "word_timing": {"start": 6.0, "end": 6.2}, "context": {}}] * 50
    p = lr.fit(wild)["pads"]["transcript"]
    assert p["before"] == lr.PAD_BEFORE_RANGE[1] and p["after"] == lr.PAD_AFTER_RANGE[1]                                                      # never past the bounds
    assert lr.fit([])["pads"]["revealed"]["before"] == bl.PAD_BEFORE                                                                          # nothing learned, the defaults


def test_the_reach_changes_only_after_three_missed_words_and_only_downward():
    def miss(d):
        return {"kind": "added", "source": "manual", "auto": None, "final": {"start": 1, "end": 1.5}, "word_timing": None, "context": {"hiding_word_duration": d, "in_loud_stretch": True}}
    assert lr.fit([miss(0.3), miss(0.32)])["detection"]["suspect_min_sec"] is None                          # two misses: not enough to change anything
    three = lr.fit([miss(0.3), miss(0.35), miss(0.5)])["detection"]
    assert three["suspect_min_sec"] == pytest.approx(0.27, abs=0.001) and three["misses"] == 3
    assert lr.fit([miss(0.6), miss(0.7), miss(0.9)])["detection"]["suspect_min_sec"] is None                # words longer than today's reach would raise it: never raised
    assert lr.fit([miss(0.05), miss(0.06), miss(0.07)])["detection"]["suspect_min_sec"] == 0.25             # and never below 0.25 s


def test_yes_and_no_on_a_flagged_stretch_are_recorded_as_reliability():
    notes = {"sequence": "Cut A", "notes": [
        {"text": "no", "target": {"lane": "Suspects", "label": 'possible curse word? heard as "by" (unsure, models disagree)', "start": 26.3, "end": 26.54}},
        {"text": "yes", "target": {"lane": "Suspects", "label": 'likely curse word? heard as "what" (stretched, burst)', "start": 28.9, "end": 29.2}},
        {"text": "a note somewhere else", "target": {"lane": "Clips", "start": 1, "end": 2}}]}
    recs = lr.records_from_notes(notes, "Cut A")
    assert [r["kind"] for r in recs] == ["rejected_suspect", "confirmed_suspect"] and recs[0]["word"] == "by" and recs[0]["context"]["signals"] == ["unsure", "models disagree"]
    m = lr.fit(recs)["sources"]["suspect"]
    assert m == {"kept": 1, "wrong": 1, "precision": 0.5}


def test_the_bleep_tool_uses_what_was_learned_and_nothing_else_changes(tmp_path, monkeypatch):
    hit = {"source": "revealed", "start": 29.38, "end": 29.58}
    assert bl.spans_of([hit], 73.0) == [(29.3, 29.7)]                                                        # no model: the defaults
    d = tmp_path / "learned"
    d.mkdir()
    monkeypatch.setenv("POSTHOUSE_BLEEP_LEARNING", str(d))
    lr.fit([{"kind": "adjusted", "source": "revealed", "auto": None, "final": {"start": 29.44, "end": 29.70}, "word_timing": {"start": 29.38, "end": 29.58}, "context": {}}])
    assert bl.spans_of([hit], 73.0) == [(29.335, 29.7)]                                                      # the revealed padding moved, as learned
    assert bl.spans_of([{"source": "transcript", "start": 29.38, "end": 29.58}], 73.0) == [(29.3, 29.7)]     # a transcript hit is still on the defaults
    assert bl.spans_of([{"start": 29.38, "end": 29.58}], 73.0) == [(29.3, 29.7)]                              # and a hit with no source is treated as a transcript hit
    lr.fit([{"kind": "added", "source": "manual", "auto": None, "final": {"start": 1, "end": 1.5}, "word_timing": None, "context": {"hiding_word_duration": d_}} for d_ in (0.3, 0.3, 0.3)])
    words = [(f"w{i}", i * 0.5, i * 0.5 + 0.25) for i in range(9)] + [("what", 4.8, 5.1)]                      # a 0.3 s stretched word with a burst in it
    assert [s["word"] for s in bl.find_suspects(words, _speech_with_burst())] == ["what"]                     # now examined (two signals: stretched and burst)
    monkeypatch.setenv("POSTHOUSE_BLEEP_LEARNING", str(tmp_path / "none"))
    assert bl.find_suspects(words, _speech_with_burst()) == []                                                # with nothing learned it is not


def test_applying_edits_teaches_the_model_through_apply_edits_and_says_so(xml, tmp_path, monkeypatch):
    learn_dir = tmp_path / "L"
    monkeypatch.setenv("POSTHOUSE_BLEEP_LEARNING", str(learn_dir))
    r = bl.bleep(xml, tmp_path / "auto", words_of=lambda w: [("Hello", 1.0, 1.4), ("Shit,", 12.4, 12.9)], check_transcript=False)
    bj = json.loads((tmp_path / "auto" / "bleep.json").read_text())
    assert bj["origin"] == "automatic" and bj["hits"][0]["source"] == "transcript" and bj["words"][0][0] == "Hello" and r["spans"][0] == pytest.approx((12.32, 13.02), abs=0.01)
    edits = tmp_path / "e.json"
    edits.write_text(json.dumps({"sequence": "Cut A", "spans": [{"start": 12.45, "end": 12.95}]}))              # he trimmed the start (12.32 to 12.45) and the end (13.02 to 12.95)
    out = tmp_path / "out"
    out.mkdir()
    res = ae.apply(Path(r["xml"]), edits, out)
    assert res["learned"]["new"] == 1 and res["learned"]["records"][0]["kind"] == "adjusted" and res["learned"]["records"][0]["source"] == "transcript"
    m = json.loads((learn_dir / "model.json").read_text())["pads"]["transcript"]
    assert m["n"] == 1 and m["before"] == pytest.approx((1 * (12.4 - 12.45) + 3 * 0.08) / 4, abs=0.001) and m["after"] == pytest.approx((1 * (12.95 - 12.9) + 3 * 0.12) / 4, abs=0.001)
    again = ae.apply(Path(r["xml"]), edits, tmp_path / "out2")                                                     # the same edits on the same automatic result: nothing new
    assert again["learned"]["new"] == 0
    third = ae.apply(res["xml"], edits, tmp_path / "out3")                                                          # edits on his own edit: no automatic baseline
    assert third["learned"] is None or third["learned"]["records"] == []
    assert len(lr.load_records()) == 1


def _rec(kind, word=None, source="transcript", **extra):
    r = {"kind": kind, "source": source, "word": word, "auto": None, "final": None, "word_timing": None, "context": {}}
    r.update(extra)
    return r


def test_a_word_removed_twice_and_never_kept_is_skipped_and_one_removal_or_any_keep_prevents_it():
    assert lr.fit([_rec("deleted", "hell")])["words"]["skip"] == {}                                        # once is not enough
    assert lr.fit([_rec("deleted", "hell"), _rec("deleted", "Hell,")])["words"]["skip"] == {"hell": 2}
    assert lr.fit([_rec("deleted", "hell"), _rec("deleted", "hell"), _rec("kept", "hell", final={"start": 1, "end": 2}, word_timing={"start": 1, "end": 2})])["words"]["skip"] == {}   # he kept it once: no
    assert lr.fit([_rec("deleted", "x", source="suspect"), _rec("deleted", "x", source="suspect")])["words"]["skip"] == {}                    # only words Whisper wrote, never a guessed stretch


def test_an_ordinary_word_he_adds_a_bleep_over_twice_is_learned_but_a_stretched_hiding_word_is_not():
    before = dict(BEFORE, spans=[], hits=[], words=[["crap", 5.0, 5.3, 0.95], ["what", 8.0, 8.7, 0.9]], loud=[])
    edits = {"sequence": "Cut B", "spans": [{"start": 5.05, "end": 5.3}, {"start": 8.1, "end": 8.5}]}
    recs = lr.records_from_edits(edits, before, "Cut B")
    assert [r.get("added_word") for r in recs] == ["crap", None]                                          # "crap" (0.3 s) is an ordinary word; "what" (0.7 s) is a stretched one
    assert lr.fit(recs)["words"]["add"] == {}                                                              # added once: not yet
    assert lr.fit([recs[0], dict(recs[0], id="z"), recs[1]])["words"]["add"] == {"crap": 2}                # twice: learned; the stretched "what" never is


def test_the_bleep_tool_skips_a_learned_skip_word_and_bleeps_a_learned_added_word_and_says_so(xml, tmp_path, monkeypatch):
    d = tmp_path / "L"
    d.mkdir()
    monkeypatch.setenv("POSTHOUSE_BLEEP_LEARNING", str(d))
    (d / "model.json").write_text(json.dumps({"pads": {}, "words": {"skip": {"shit": 2}, "add": {"crap": 2}}}))
    assert bl.is_profane("crap", bl.load_patterns()) and not bl.is_profane("crap", bl.load_patterns(path=bl.LIST_FILE))      # learned, without touching the list file itself
    r = bl.bleep(xml, tmp_path / "x", words_of=lambda w: [("Hello", 1.0, 1.4), ("Shit,", 12.4, 12.9), ("crap", 20.0, 20.4)], check_transcript=False)
    assert [h["word"] for h in r["hits"]] == ["crap"] and [h["word"] for h in r["skipped"]] == ["Shit,"]
    assert "skipped" in json.loads((tmp_path / "x" / "bleep.json").read_text())
    monkeypatch.setenv("POSTHOUSE_BLEEP_LEARNING", str(tmp_path / "none"))
    r2 = bl.bleep(xml, tmp_path / "y", words_of=lambda w: [("Shit,", 12.4, 12.9), ("crap", 20.0, 20.4)], check_transcript=False)
    assert [h["word"] for h in r2["hits"]] == ["Shit,"] and r2["skipped"] == []                              # with nothing learned: the list as written


def test_forgetting_a_learned_word_removes_what_taught_it(tmp_path, monkeypatch):
    lr.save([dict(_rec("deleted", "hell"), id="a1", cut="c", at="2026-01-01"), dict(_rec("deleted", "hell"), id="a2", cut="c", at="2026-01-01"), dict(_rec("deleted", "other"), id="a3", cut="c", at="2026-01-01")])
    assert lr.fit()["words"]["skip"] == {"hell": 2}
    monkeypatch.setattr(sys, "argv", ["learn.py", "forget", "Hell"])
    lr.main()
    assert [r["word"] for r in lr.load_records()] == ["other"] and lr.fit()["words"]["skip"] == {}
