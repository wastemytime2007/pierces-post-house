"""follow_speaker: the picture splits at each change of speaker and centres on whoever is talking; sound is never touched.

The analysis (who talks when, where each person stands) is stubbed here; framing.py and speakers.py have their own tests. What is under test is the part that was wrong in the real
cuts: the XML it writes, what it refuses, and that the read-back check catches a piece that is not on its speaker.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "reframe"))
sys.path.insert(0, str(HERE.parents[1] / "qa"))

import test_revise as tr  # noqa: E402
from test_revise import FPS, media, xml  # noqa: E402,F401  (fixtures)

import apply_ops  # noqa: E402
import follow_speaker as fs  # noqa: E402
import framing  # noqa: E402
import ops as opsmod  # noqa: E402
import timeline  # noqa: E402
from test_reframe_op import _motion  # noqa: E402

pytestmark = tr.pytestmark

PEOPLE = {"Ann": 0.30, "Ben": 0.70}


@pytest.fixture()
def framed(tmp_path, xml):
    t = ET.parse(xml)
    for ci in t.getroot().find("sequence").findall("media/video/track/clipitem")[:3]:
        ci.append(ET.fromstring(_motion(150, 0)))
    out = tmp_path / "framed.xml"
    t.write(out, encoding="UTF-8", xml_declaration=True)
    return out


def _fake_analyse(runs_for):
    def analyse(cam, clips, mic_dir, cache=None, progress=lambda s: None, **kw):
        return {"people": dict(PEOPLE), "evidence": "stub", "t0": 0.0,
                "clips": {c["idx"]: {"runs": runs_for(c), "pieces": []} for c in clips}}
    return analyse


def alternate(every):
    def runs(c):
        dur, out, t, who = c["src_out"] - c["src_in"], [], 0.0, 0
        while t < dur:
            out.append((t, min(t + every, dur), ("Ann", "Ben")[who % 2]))
            t, who = t + every, who + 1
        return out
    return runs


@pytest.fixture()
def stubbed(monkeypatch):
    def install(runs_for, face_x=None):
        monkeypatch.setattr(framing, "analyse", _fake_analyse(runs_for))
        monkeypatch.setattr(framing, "sample_frames", lambda cam, times, out_dir, width=1600: [Path(f"{t}") for t in times])
        monkeypatch.setattr(framing, "find_faces", lambda imgs: [[{"x": (face_x or PEOPLE)[w], "y": .4, "w": .1, "h": .1} for w in PEOPLE] for _ in imgs])
        monkeypatch.setattr(fs, "mic_dir_of", lambda cut: "/mics")
    return install


OP = [{"note": 1, "op": "follow_speaker", "clips": "all", "why": "t"}]
NOTE = [{"timeline_sec": 3.0, "text": "the framing should follow the speaker"}]


def test_merge_short_takes_a_blink_of_the_other_speaker_into_its_neighbour():
    pieces = [(0, 60, "Ann"), (60, 10, "Ben"), (70, 50, "Ann"), (120, 20, "Ben"), (140, 60, "Ann")]
    assert fs.merge_short(pieces, 30.0, 1.0) == [(0, 200, "Ann")]
    kept = fs.merge_short([(0, 20, "Ann"), (20, 60, "Ben")], 30.0, 1.0)
    assert kept == [(0, 80, "Ben")]                       # a short first piece joins the one after it


def test_the_clip_is_split_at_speaker_changes_and_each_piece_is_centred_on_its_speaker(framed, stubbed, tmp_path):
    stubbed(alternate(2.0))
    cut = timeline.load_cut(framed)
    changes, _ = apply_ops.apply_ops(framed, tmp_path / "out.xml", cut, OP, NOTE)
    assert changes[0].applied, changes[0].summary
    new = timeline.load_cut(tmp_path / "out.xml")
    assert len(new.video) > len(cut.video)
    ok, detail = fs.check_written(new, changes[0].check, __import__("render_preview").source_dims)
    assert ok, detail
    horiz = {round(c.motion[1], 3) for c in new.video if c.motion and c.motion[0] == 150}
    assert len(horiz) == 2 and min(horiz) < 0 < max(horiz)          # one centre per person, on opposite sides of the middle


def test_sound_and_timing_are_untouched(framed, stubbed, tmp_path):
    stubbed(alternate(2.0))
    cut = timeline.load_cut(framed)
    apply_ops.apply_ops(framed, tmp_path / "out.xml", cut, OP, NOTE)
    new = timeline.load_cut(tmp_path / "out.xml")
    assert [(a.tl_start, a.tl_end, a.src_in, a.src_out) for a in new.audio] == [(a.tl_start, a.tl_end, a.src_in, a.src_out) for a in cut.audio]
    assert abs(new.zone_end - cut.zone_end) < 1e-6
    for before in cut.video:                                         # every source second is still shown exactly once, in order
        pieces = [c for c in new.video if c.src_path == before.src_path and before.src_in - 1e-6 <= c.src_in < before.src_out - 1e-6]
        assert abs(pieces[0].src_in - before.src_in) < 1e-3 and abs(pieces[-1].src_out - before.src_out) < 0.05
        assert all(abs(a.src_out - b.src_in) < 0.05 for a, b in zip(pieces, pieces[1:]))


def test_a_blink_of_the_other_speaker_does_not_move_the_picture(framed, stubbed, tmp_path):
    def runs(c):
        dur = c["src_out"] - c["src_in"]
        return [(0, 1.0, "Ann"), (1.0, 1.4, "Ben"), (1.4, dur, "Ann")] if dur > 2 else [(0, dur, "Ann")]
    stubbed(runs)
    cut = timeline.load_cut(framed)
    apply_ops.apply_ops(framed, tmp_path / "out.xml", cut, OP, NOTE)
    new = timeline.load_cut(tmp_path / "out.xml")
    assert len({round(c.motion[1], 3) for c in new.video if c.motion and c.motion[0] == 150}) == 1


def test_a_clip_with_no_speaker_placed_is_left_alone_and_the_summary_says_so(framed, stubbed, tmp_path):
    stubbed(lambda c: [])
    cut = timeline.load_cut(framed)
    changes, _ = apply_ops.apply_ops(framed, tmp_path / "out.xml", cut, OP, NOTE)
    assert "left alone" in changes[0].summary
    new = timeline.load_cut(tmp_path / "out.xml")
    assert [c.motion for c in new.video] == [c.motion for c in cut.video]


def test_without_recordings_it_says_why_and_changes_nothing(framed, monkeypatch, tmp_path):
    monkeypatch.setattr(fs, "mic_dir_of", lambda cut: None)
    monkeypatch.delenv("POSTHOUSE_MIC_DIR", raising=False)
    cut = timeline.load_cut(framed)
    changes, _ = apply_ops.apply_ops(framed, tmp_path / "out.xml", cut, OP, NOTE)
    assert not changes[0].applied and "recordings" in changes[0].summary


def test_the_readback_check_fails_a_piece_that_is_not_on_its_speaker(framed, stubbed, tmp_path):
    stubbed(alternate(2.0))
    cut = timeline.load_cut(framed)
    changes, _ = apply_ops.apply_ops(framed, tmp_path / "out.xml", cut, OP, NOTE)
    t = ET.parse(tmp_path / "out.xml")
    for ci in t.getroot().find("sequence").findall("media/video/track/clipitem")[:3]:
        for h in ci.iter("horiz"):
            h.text = "0"                                            # undo the framing, as if the pass had not run
    t.write(tmp_path / "undone.xml", encoding="UTF-8", xml_declaration=True)
    ok, _detail = fs.check_written(timeline.load_cut(tmp_path / "undone.xml"), changes[0].check, __import__("render_preview").source_dims)
    assert not ok


def test_validate_accepts_only_a_note_that_asks_for_it(framed):
    cut = timeline.load_cut(framed)
    good = [{"timeline_sec": 3.0, "text": "The framing should follow the speaker. Right now it isn't framed on either of the speakers."}]
    bad = [{"timeline_sec": 3.0, "text": "make the pause shorter"}]
    raw = [{"note": 1, "op": "follow_speaker", "clip": "all", "why": "x"}]
    kept = opsmod.validate(raw, good, cut)
    assert kept and kept[0]["op"] == "follow_speaker" and kept[0]["clips"] == "all"
    kept = opsmod.validate(raw, bad, cut)
    assert not [k for k in kept if k["op"] == "follow_speaker"]


def test_who_is_left_needs_both_people_and_a_clear_margin():
    runs = [(i * 20.0, i * 20.0 + 4.0, "Ann" if i % 2 == 0 else "Ben") for i in range(14)]

    def left_when_ann(cam, segs, left, right):
        which = "Ann" if segs[0][0] % 40 < 20 else "Ben"
        n = 40
        return (3.0, 1.0, n) if which == "Ann" else (1.0, 3.0, n)

    r = framing.who_is_left("cam", runs, 0.3, 0.7, motion_fn=left_when_ann)
    assert r and r["left"] == "Ann" and r["right"] == "Ben"
    assert framing.who_is_left("cam", runs, 0.3, 0.7, motion_fn=lambda *a: (1.0, 1.0, 40)) is None        # no difference: no guess
    assert framing.who_is_left("cam", runs, 0.3, 0.7, motion_fn=lambda *a: (1.0, 3.0, 4)) is None         # too few frames: no guess
    assert framing.who_is_left("cam", [(0, 5, "Ann")], 0.3, 0.7, motion_fn=left_when_ann) is None         # one person only


def test_horiz_puts_the_person_mid_frame_and_never_shows_beyond_the_picture():
    import reframe_xml as rx
    mid = framing.horiz_for_person(0.5, 100 * 4 / 3, 1000, 1000)
    assert abs(mid) < 1e-6
    left = framing.horiz_for_person(0.02, 150, 1000, 1000)          # near the left edge: slice flush with the edge, not off it
    hw = framing.window_half_width(150, 1000, 1000)
    assert abs(rx.subject_for_horiz(left, 150, 1000) / 1000 - hw) < 1e-6
