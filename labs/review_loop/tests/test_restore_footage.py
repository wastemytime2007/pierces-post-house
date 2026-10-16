"""restore_footage: a line of the recording the cut no longer has is put back, with its picture and its recorder, where it was said.

Ryan, 2026-10-13: "The editor removed the part of the video where bob mentions 'if there arent any curbs youre probably on a septic system' which was really valuable to a viewer. I asked the
editor to put that back in and it couldnt." Same synthetic media as test_revise (camera 0-55 s; the cut holds camera 0-10, 20-30, 30-40 s; the selects pool holds 45-50 and 50-55 s; the lav is the
camera's sound 5.005 s later). The transcript is faked: the line's words, where they were said.
"""
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "qa"))

import apply_ops  # noqa: E402
import ops as opsmod  # noqa: E402
import revise  # noqa: E402
import test_revise as tr  # noqa: E402
import timeline  # noqa: E402
from test_revise import FPS, media, xml  # noqa: E402,F401  (fixtures)

pytestmark = tr.pytestmark

@pytest.fixture(autouse=True)
def _model_confirms_by_default(monkeypatch):
    """No test here reaches the real model: the confirmation of a chosen line says yes unless a test says otherwise."""
    monkeypatch.setattr(opsmod, "_confirm_line", lambda wanted, line, client=None: True)


NOTE = {"timeline_sec": 12.0, "clip": 2, "text": "the line 'the septic tank outlet sits behind the garage' should not have been cut. Put it back in."}
WORDS = "the septic tank outlet sits behind the garage"


def _transcript(monkeypatch, phrases):
    monkeypatch.setattr(opsmod, "_transcript_phrases", lambda path: phrases)


LINE = [{"start": 11.0, "end": 11.9, "text": "So the other thing is the weather."},
        {"start": 12.4, "end": 15.0, "text": "The septic tank outlet sits behind the garage."},
        {"start": 16.0, "end": 18.0, "text": "And that is all about the roof."}]


def _natural_pool(xml):
    """The fixture's pool plays at quarter speed (600 timeline frames for 150 source frames); a real export's pool plays at the cut's own speed, so it is made so here."""
    s = xml.read_text()
    a, b = "<start>4000</start><end>4600</end><in>1350</in><out>1500</out>", "<start>4600</start><end>5200</end><in>1500</in><out>1650</out>"
    assert a in s and b in s
    xml.write_text(s.replace(a, "<start>4000</start><end>4300</end><in>1350</in><out>1500</out>").replace(b, "<start>4300</start><end>4600</end><in>1500</in><out>1650</out>"))


def _put_back(xml, tmp_path, words=WORDS, before=None):
    cut = timeline.load_cut(xml)
    ops = opsmod.validate([{"note": 1, "op": "restore_footage", "words": words, "before": before}], [NOTE], cut)
    assert ops[0]["op"] == "restore_footage", ops
    out = tmp_path / "v2.xml"
    changes, delta = apply_ops.apply_ops(xml, out, cut, ops, [NOTE])
    return cut, out, changes, delta


def test_the_line_is_put_back_after_the_clip_that_ends_nearest_before_it_and_the_rest_moves_right(tmp_path, xml, monkeypatch):
    _transcript(monkeypatch, LINE)
    cut, out, changes, delta = _put_back(xml, tmp_path)
    assert changes[0].applied, changes[0].summary
    new = timeline.load_cut(out)
    k = changes[0].check
    assert len(new.video) == len(cut.video) + 1
    assert 2.4 < k["t1"] - k["t0"] < 3.0 and abs(delta - (k["t1"] - k["t0"])) < 0.05
    put = new.video[1]                                                               # clip 1 ends at camera 10 s, the line is at 12.4-15 s: it goes right after clip 1
    assert put.src_in == pytest.approx(k["t0"], abs=0.05) and put.src_out == pytest.approx(k["t1"], abs=0.05)
    assert put.tl_start == pytest.approx(cut.video[0].tl_end, abs=0.02)
    assert [round(c.src_in) for c in new.video] == [0, round(k["t0"]), 20, 30]        # the clips after it are the same clips, later
    assert new.video[2].tl_start == pytest.approx(cut.video[1].tl_start + delta, abs=0.03)
    assert new.zone_end == pytest.approx(cut.zone_end + delta, abs=0.05)
    assert "after clip 1" in changes[0].summary


def test_the_recorder_under_it_is_the_same_recording_at_the_same_offset(tmp_path, xml, monkeypatch):
    _transcript(monkeypatch, LINE)
    cut, out, changes, delta = _put_back(xml, tmp_path)
    new = timeline.load_cut(out)
    put = new.video[1]
    lav = next(a for a in new.audio if abs(a.tl_start - put.tl_start) < 0.05 and a.src_path.endswith("lav.wav"))
    assert lav.src_in - put.src_in == pytest.approx(5.005, abs=0.03)                  # the lav's offset to the camera is the one every other piece has
    rows = revise.verify(cut, xml, out, delta, restored=[(changes[0].check["path"], changes[0].check["t0"], changes[0].check["t1"])])
    assert not [(n, d) for n, ok, d in rows if ok is False], rows


def test_the_footage_check_would_catch_it_without_being_told_it_was_asked_for(tmp_path, xml, monkeypatch):
    _transcript(monkeypatch, LINE)
    cut, out, changes, delta = _put_back(xml, tmp_path)
    rows = dict((n, ok) for n, ok, _d in revise.verify(cut, xml, out, delta))
    assert rows["NO-NEW-FOOTAGE"] is False                                            # negative control: footage the cut did not have is seen


def test_everything_has_a_unique_id_and_one_file_body_each(tmp_path, xml, monkeypatch):
    import xml.etree.ElementTree as ET
    _transcript(monkeypatch, LINE)
    _cut, out, *_ = _put_back(xml, tmp_path)
    root = ET.parse(out).getroot()
    ids = [c.get("id") for c in root.iter("clipitem")]
    assert len(ids) == len(set(ids))
    assert sorted(f.get("id") for f in root.iter("file") if len(list(f)) > 0) == ["f1", "f2"]
    assert any("-restore" in i for i in ids)


def test_it_goes_before_the_clip_the_note_names(tmp_path, xml, monkeypatch):
    _transcript(monkeypatch, LINE)
    cut, out, changes, delta = _put_back(xml, tmp_path, before=3)
    new = timeline.load_cut(out)
    assert [round(c.src_in) for c in new.video][:3] == [0, 20, round(changes[0].check["t0"])] and "before clip 3" in changes[0].summary


def test_a_line_the_cut_already_has_is_not_put_back_twice(tmp_path, xml, monkeypatch):
    _transcript(monkeypatch, [{"start": 21.0, "end": 24.0, "text": "The septic tank outlet sits behind the garage."}])
    cut, out, changes, delta = _put_back(xml, tmp_path)
    assert not changes[0].applied and "already in the cut" in changes[0].summary and "clip 2" in changes[0].summary and delta == 0


def test_a_line_that_is_not_in_the_footage_says_so_and_changes_nothing(tmp_path, xml, monkeypatch):
    _transcript(monkeypatch, [{"start": 12.0, "end": 14.0, "text": "We closed on the house in March."}, {"start": 16.0, "end": 18.0, "text": "Nobody was home at the time."}])
    cut, out, changes, delta = _put_back(xml, tmp_path)
    assert not changes[0].applied and "closest line" in changes[0].summary or "could not" in changes[0].summary
    assert delta == 0


def test_no_transcript_is_said_plainly(tmp_path, xml, monkeypatch):
    _transcript(monkeypatch, None)
    cut, out, changes, delta = _put_back(xml, tmp_path)
    assert not changes[0].applied and "no transcript" in changes[0].summary


def test_a_line_that_sits_in_the_selects_pool_takes_its_footage_out_of_the_pool(tmp_path, xml, monkeypatch):
    """The pool holds camera 45-50 s and 50-55 s; the line at 49-52 s runs across both: the first gives up its end, the second its front, so the pool never repeats the cut."""
    _transcript(monkeypatch, [{"start": 49.0, "end": 52.0, "text": "The septic tank outlet sits behind the garage."}])
    _natural_pool(xml)
    cut, out, changes, delta = _put_back(xml, tmp_path)
    assert changes[0].applied, changes[0].summary
    k = changes[0].check
    pool = tr._items(out, "video")[-2:]
    assert pool[0][3] <= round(k["t0"] * 30) + 1                                      # the first pool clip now ends where the line begins (file frames at 30 fps)
    assert pool[1][2] >= round(k["t1"] * 30) - 1                                      # the second starts where it ends
    rows = revise.verify(cut, xml, out, delta, restored=[(k["path"], k["t0"], k["t1"])])
    assert not [(n, d) for n, ok, d in rows if ok is False], rows
    assert "selects pool" in changes[0].summary


def test_a_line_inside_one_pool_clip_is_left_for_a_decision_by_hand(tmp_path, xml, monkeypatch):
    _transcript(monkeypatch, [{"start": 46.0, "end": 48.5, "text": "The septic tank outlet sits behind the garage."}])
    _natural_pool(xml)
    cut, out, changes, delta = _put_back(xml, tmp_path)
    assert not changes[0].applied and "selects pool" in changes[0].summary and delta == 0
    assert tr._items(out, "video") == tr._items(xml, "video")                         # nothing was changed


def test_the_reader_is_not_trusted_with_words_nobody_gave_or_with_a_graphic(xml):
    cut = timeline.load_cut(xml)
    mk = lambda text, words, **kw: opsmod.validate([{"note": 1, "op": "restore_footage", "words": words, **kw}], [{"timeline_sec": 3.0, "text": text, "clip": 1}], cut)[0]
    ok = mk("Put the line 'the septic tank outlet sits behind the garage' back in", WORDS)
    assert ok["op"] == "restore_footage" and ok["words"] == WORDS
    assert mk("Put that back in", "the septic tank outlet sits behind the garage")["op"] == "unsupported"          # the words are the model's, not the note's
    assert mk("Put it back in", "it")["op"] == "unsupported"                                                          # nothing to search for
    assert mk("Put the curbs part back in", "the curbs part")["op"] == "restore_footage"                              # one distinctive word is enough to ask the search
    assert mk("the septic tank outlet looks great", WORDS)["op"] == "unsupported"                                      # the note does not ask for anything to be put back
    assert mk("put the title card back, the septic tank outlet", "septic tank outlet", before=None)["op"] in ("unsupported", "restore_footage")
    g = opsmod.validate([{"note": 1, "op": "restore_footage", "words": "septic tank outlet"}], [{"timeline_sec": 3.0, "text": "put the septic tank outlet card back", "clip": 1, "target": {"lane": "Card", "label": "x", "start": 1, "end": 2}}], cut)[0]
    assert g["op"] == "unsupported"                                                                                    # left on a graphic: not footage
    q = mk("Bob says 'the septic tank outlet sits behind the garage' put that back in", WORDS)
    assert q["op"] == "restore_footage"                                                                                # quote marks on the first and last word of a quoted line do not stop it


def _septic_cut(monkeypatch, ph):
    monkeypatch.setattr(opsmod, "_transcript_phrases", lambda path: ph)
    monkeypatch.setattr(opsmod.words_mod, "valley", lambda path, lo, hi: (lo + hi) / 2)
    c = timeline.VideoClip(1, 0.0, 3.0, "/x/0L9A5643.MP4", 20.0, 23.0)
    return timeline.Cut("t", 30.0, 1080, 1920, 3.0, [c], [timeline.AudioClip(0.0, 3.0, "/x/lav.wav", 50.0, 53.0, 1)])


QUOTE = "if there arent any curbs youre probably on a septic system"
PH = [{"start": float(i), "end": i + 0.9, "text": t} for i, t in enumerate(
    ["we talked about the septic system", "the septic system is old", "so with the septic systems we wait", "a new septic system costs money",
     "you know, if you don't have curbs in storm sewer water, you're probably out of substances.", "it has sewer", "septic septic system"])]


def test_the_search_weighs_a_rare_word_over_a_common_one_and_takes_a_clear_winner_without_asking(monkeypatch):
    """In a recording about a septic system, 'septic' and 'system' name nothing; 'curbs' said once does. A transcript that mishears the common words still finds the line."""
    cands = opsmod._line_candidates(PH, opsmod._distinctive(QUOTE))
    assert cands[0]["span"] == (4, 4) and "curbs" in cands[0]["held"]
    asked = []
    monkeypatch.setattr(opsmod, "_pick_line", lambda *a, **k: asked.append(1))
    r = opsmod.locate_restore(_septic_cut(monkeypatch, PH), QUOTE)
    assert r["heard"].startswith("you know, if you don't have curbs") and not asked


def test_one_word_is_never_taken_on_trust_the_model_confirms_it_and_may_say_none(monkeypatch):
    """'weather' is 0.83 like 'water' to the matcher: a note about the weather must not bring back the curbs line because it says "storm sewer water". One known word, or only common words, goes to the model."""
    cut = _septic_cut(monkeypatch, PH)
    seen = []

    def model(note_words, options, client=None):                                      # a stand-in for the model: it picks the option that really has the note's word, else none
        seen.append(note_words)
        w = "curbs" if "curbs" in note_words else note_words.split()[-1]
        return next((i for i, o in enumerate(options) if w in o.lower()), None)
    monkeypatch.setattr(opsmod, "_pick_line", model)
    assert opsmod.locate_restore(cut, "the curbs part")["heard"].startswith("you know, if you don't have curbs")
    assert opsmod.locate_restore(cut, "curbs thing about the zoning commissioner")["heard"].startswith("you know, if you don't have curbs")       # 'thing', 'zoning', 'commissioner' are not in the recording at all
    n = len(seen)
    r = opsmod.locate_restore(cut, "the part about the weather")
    assert len(seen) == n + 1 and "could not tell which line" in r["reason"]            # asked, and the answer was none: nothing is put back
    opsmod.locate_restore(cut, "septic system")
    assert len(seen) == n + 2                                                           # only common words: asked too


def test_when_two_lines_are_as_likely_the_model_chooses_and_none_is_an_answer(monkeypatch):
    ph = PH[:4] + [{"start": 4.0, "end": 4.9, "text": "you know, if you don't have curbs you're probably fine."}, {"start": 5.0, "end": 5.9, "text": "if there are curbs you're probably on city sewer."}]
    asked = {}

    def pick(note_words, options, client=None):
        asked["options"] = options
        return next(i for i, o in enumerate(options) if "city sewer" in o)
    monkeypatch.setattr(opsmod, "_pick_line", pick)
    cut = _septic_cut(monkeypatch, ph)
    r = opsmod.locate_restore(cut, QUOTE)
    assert "options" in asked and r["heard"].endswith("city sewer.")                    # the words alone could not tell the lines apart, so the model was asked
    monkeypatch.setattr(opsmod, "_pick_line", lambda *a, **k: None)
    assert "could not tell which line" in opsmod.locate_restore(cut, QUOTE)["reason"]


def test_qa_reads_the_line_back_from_the_new_version_and_fails_when_it_is_not_there(tmp_path, xml, monkeypatch):
    import qa_pass
    _transcript(monkeypatch, LINE)
    cut, out, changes, delta = _put_back(xml, tmp_path)
    new = timeline.load_cut(out)
    k = changes[0].check
    item = {"applied": True, "summary": "x", "restored": {kk: k[kk] for kk in ("path", "t0", "t1", "heard")}}
    o = {"note": 1, "op": "restore_footage", "words": WORDS}
    good = qa_pass.check_op(o, item, NOTE, cut, new, {}, None, {})
    assert good.status == qa_pass.VERIFIED and "holds the line" in good.detail
    bad = qa_pass.check_op(o, item, NOTE, cut, cut, {}, None, {})
    assert bad.status == qa_pass.FAILED                                                # the old cut again: the line is not on it
    none = qa_pass.check_op(o, {"applied": False, "summary": "could not find that line"}, NOTE, cut, cut, {}, None, {})
    assert none.status in (qa_pass.NOT_DONE, qa_pass.FAILED) and "not applied" in none.detail


def test_a_story_problem_the_footage_may_answer_becomes_a_line_to_add_not_a_note_that_nothing_can_be_done():
    """Ryan, 2026-10-15: 'Nothing in it says a doorway was covered up' was left as 'needs different words, which no cut of this footage can give'. The recordings are 45 minutes long and transcribed: a line
    that says it may be there. The reviewer now asks for it; the search finds it, a model confirms it (or says none), and the editor says so either way."""
    import ai_review as air
    cut = timeline.Cut("t", 30.0, 1080, 1920, 8.0, [timeline.VideoClip(1, 0.0, 4.0, "/x/a.mp4", 10.0, 14.0), timeline.VideoClip(2, 4.0, 8.0, "/x/a.mp4", 30.0, 34.0)], [])
    got = air._checked_fix({"op": "restore_footage", "words": "that the old doorway was covered up and framed in", "before": 2}, cut, [])
    assert got == {"op": "restore_footage", "words": "that the old doorway was covered up and framed in", "before": 2}
    assert air._checked_fix({"op": "restore_footage", "words": "x"}, cut, []) is None                       # too little to search for
    assert air._checked_fix({"op": "restore_footage", "words": "the doorway was covered", "before": 9}, cut, [])["before"] is None      # a clip that is not there
    notes = [{"timeline_sec": 2.0, "clip": 1, "text": "AI: nothing says a doorway was covered up", "suggested_op": got}]
    plan = opsmod.validate(opsmod.from_suggestions(notes, cut), notes, cut)
    assert plan[0]["op"] == "restore_footage" and plan[0]["trusted"] is True and plan[0]["before"] == 2 and "doorway" in plan[0]["words"]
    bad = [{"timeline_sec": 2.0, "clip": 1, "text": "AI: x", "suggested_op": {"op": "restore_footage", "words": "it"}}]
    assert opsmod.validate(opsmod.from_suggestions(bad, cut), bad, cut)[0]["op"] == "unsupported"


def test_a_projects_transcripts_are_found_wherever_the_project_lives(tmp_path, monkeypatch):
    """Ryan's Runnells Wall Removal lives on an external drive, next to its footage: 'there is no transcript for DJI_20260522101009_0002_D.MP4' although the project's own transcripts folder holds it."""
    import json
    app = tmp_path / "app"
    (app / "projects").mkdir(parents=True)
    drive = tmp_path / "drive" / "Runnells Wall Removal" / "transcripts"
    drive.mkdir(parents=True)
    (drive / "DJI_0002_D.json").write_text(json.dumps({"source_path": "/Volumes/x/proxies/DJI_0002_D.mp4", "phrases": [{"start": 1.0, "end": 2.0, "text": "a doorway", "words": []}]}))
    (app / "known_projects.json").write_text(json.dumps({"Runnells Wall Removal": str(tmp_path / "drive" / "Runnells Wall Removal"), "gone": str(tmp_path / "nowhere")}))
    monkeypatch.setenv("POSTHOUSE_APP_DIR", str(app))
    monkeypatch.delenv("POSTHOUSE_PROJECTS", raising=False)
    got = opsmod._transcript_phrases("/Volumes/x/DJI_0002_D.MP4")                          # the original (.MP4) of the proxy (.mp4) the transcript was made from
    assert got and got[0]["text"] == "a doorway"
    assert opsmod._transcript_phrases("/Volumes/x/DJI_9999_D.MP4") is None


def test_a_line_the_model_picked_is_kept_only_if_a_second_question_says_it_really_says_it(monkeypatch):
    """2026-10-15: for 'a line saying the old door opening is being measured' the pick was "And doors not open." Best of several is not the same as right."""
    ph = PH[:4] + [{"start": 4.0, "end": 4.9, "text": "you know, if you don't have curbs you're probably fine."}, {"start": 5.0, "end": 5.9, "text": "if there are curbs you're probably on city sewer."}]
    cut = _septic_cut(monkeypatch, ph)                                                      # two lines as likely as each other: the model is asked to pick
    monkeypatch.setattr(opsmod, "_pick_line", lambda note_words, options, client=None: next(i for i, o in enumerate(options) if "city sewer" in o))
    asked = []
    monkeypatch.setattr(opsmod, "_confirm_line", lambda wanted, line, client=None: asked.append(line) or False)
    r = opsmod.locate_restore(cut, QUOTE)
    assert "has no line that says it" in r["reason"] and "city sewer" in asked[0]
    monkeypatch.setattr(opsmod, "_confirm_line", lambda wanted, line, client=None: True)
    assert opsmod.locate_restore(cut, QUOTE)["heard"].endswith("city sewer.")


def test_a_line_that_holds_only_some_of_the_described_words_is_confirmed_even_when_the_words_alone_pick_it(monkeypatch):
    cut = _septic_cut(monkeypatch, PH)                                                      # the curbs line wins by its rare word alone and holds two of the four words searched for
    asked = []
    monkeypatch.setattr(opsmod, "_confirm_line", lambda wanted, line, client=None: asked.append(line) or False)
    r = opsmod.locate_restore(cut, QUOTE)
    assert "has no line that says it" in r["reason"] and asked and "curbs" in asked[0]
    monkeypatch.setattr(opsmod, "_confirm_line", lambda wanted, line, client=None: True)
    assert "reason" not in opsmod.locate_restore(cut, QUOTE)


def test_a_stretch_named_by_its_seconds_is_put_back_without_a_search_and_can_play_the_cameras_own_sound(tmp_path, xml, monkeypatch):
    """2026-10-16, wall project: Bob says "the door stops right where that piece of trim was added", the transcript hears a quiet line as "And doors not open", so no search by words finds it, and
    the recorders do not hear it at all (-58 dB), the camera does (-31 dB). A trusted op may name the stretch by its seconds in the recording and ask for the camera's sound."""
    import xml.etree.ElementTree as ET
    monkeypatch.setattr(opsmod, "_transcript_phrases", lambda path: pytest.fail("a stretch named by its seconds must not be searched for"))
    cut = timeline.load_cut(xml)
    cam = cut.video[0].src_path
    raw = {"note": 1, "op": "restore_footage", "words": "the septic tank outlet sits behind the garage", "start": 12.4, "end": 15.0, "file": cam, "camera_audio": True, "trusted": True}
    ops = opsmod.validate([raw], [NOTE], cut)
    assert ops[0]["start"] == 12.4 and ops[0]["camera_audio"] is True
    out = tmp_path / "v2.xml"
    changes, delta = apply_ops.apply_ops(xml, out, cut, ops, [NOTE])
    assert changes[0].applied, changes[0].summary
    k = changes[0].check
    assert 2.4 < k["t1"] - k["t0"] < 3.0 and "camera's own sound plays under it" in changes[0].summary
    audio = [c for tr_ in ET.parse(out).getroot().findall(".//sequence/media/audio/track") for c in tr_.findall("clipitem") if "-restore" in (c.get("id") or "")]
    assert audio
    on = [c for c in audio if c.findtext("enabled") == "TRUE"]
    off = [c for c in audio if c.findtext("enabled") == "FALSE"]
    assert on and off and all("sync" not in (c.get("id") or "") for c in on) and all("sync" in (c.get("id") or "") for c in off)      # the camera's clip is on, the separate recorder's is off
    bad = dict(raw, start=12.4, end=50.0)
    assert opsmod.validate([bad], [NOTE], cut) == [] or opsmod.validate([bad], [NOTE], cut)[0]["op"] == "unsupported"                      # over 30 s is refused
