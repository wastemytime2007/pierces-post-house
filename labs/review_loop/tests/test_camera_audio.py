"""use_camera_audio: where the separate recorder is silent or far quieter than the camera's own sound under a clip, the camera's sound is played instead.

Ryan, 2026-10-15 (Runnells Wall Removal): "AI: The voice track here (DJI_02_20260522_101626.WAV) is silent. The wrong recorder may have been synced to this footage." was left as 'needs different words (a cleaner take
or a change to the captions), which no cut of this footage can give'. The camera heard the speech (-33 dB) and the recorder did not (-56 dB), and the camera's sound was switched off in the cut.
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
NOTE = {"timeline_sec": 15.0, "clip": 2, "text": "I can't hear him on this clip, the sound drops out"}


def _levels(monkeypatch, lav_db, cam_db):
    monkeypatch.setattr(opsmod, "level_db", lambda path, t, dur=0.03: lav_db if str(path).endswith("lav.wav") else cam_db)


def _real_camera_audio(xml):
    """The fixture's switched-off camera audio counts file frames; a real export (PreCut's) counts the sequence's frames like every other audio clip, which is what the loader requires once one is on."""
    import xml.etree.ElementTree as ET
    root = ET.parse(xml).getroot()
    for c in root.find("sequence").findall("media/audio/track")[0].findall("clipitem"):
        s, e, i = int(c.findtext("start")), int(c.findtext("end")), int(c.findtext("in"))
        c.find("in").text, c.find("out").text = str(i * 2), str(i * 2 + (e - s))
    ET.ElementTree(root).write(xml, encoding="unicode", xml_declaration=False)


def _run(xml, tmp_path, op):
    _real_camera_audio(xml)
    cut = timeline.load_cut(xml)
    ops = opsmod.validate([op], [NOTE], cut)
    assert ops[0]["op"] == "use_camera_audio", ops
    out = tmp_path / "v2.xml"
    changes, delta = apply_ops.apply_ops(xml, out, cut, ops, [NOTE])
    return cut, out, changes, delta


def test_a_silent_recorder_is_replaced_by_the_cameras_own_sound_under_that_clip_only(tmp_path, xml, monkeypatch):
    _levels(monkeypatch, -60.0, -25.0)
    cut, out, changes, delta = _run(xml, tmp_path, {"note": 1, "op": "use_camera_audio", "clip": 2, "why": "x"})
    assert changes[0].applied, changes[0].summary
    new = timeline.load_cut(out)
    c2 = new.video[1]
    under = [a for a in new.audio if a.tl_start - 0.02 <= (c2.tl_start + c2.tl_end) / 2 < a.tl_end]
    assert under and all(a.src_path == c2.src_path for a in under)                            # the camera's file is what plays under clip 2
    for i in (0, 2):                                                                          # the other clips keep their recorder
        c = new.video[i]
        live = [a for a in new.audio if a.tl_start - 0.02 <= (c.tl_start + c.tl_end) / 2 < a.tl_end]
        assert live and all(a.src_path.endswith("lav.wav") for a in live)
    assert delta == 0 and new.zone_end == pytest.approx(cut.zone_end, abs=0.03)
    rows = revise.verify(cut, xml, out, delta)
    assert not [(n, d) for n, ok, d in rows if ok is False], rows


def test_a_recorder_that_is_not_clearly_worse_is_left_alone(tmp_path, xml, monkeypatch):
    _levels(monkeypatch, -30.0, -25.0)
    cut, out, changes, delta = _run(xml, tmp_path, {"note": 1, "op": "use_camera_audio", "clip": 2, "why": "x"})
    assert not changes[0].applied and "not clearly worse" in changes[0].summary and delta == 0
    assert tr._items(out, "audio", 1) == tr._items(xml, "audio", 1)


def test_when_the_camera_hears_nothing_either_nothing_is_changed(tmp_path, xml, monkeypatch):
    _levels(monkeypatch, -70.0, -65.0)
    _cut, _out, changes, _d = _run(xml, tmp_path, {"note": 1, "op": "use_camera_audio", "clip": 2, "why": "x"})
    assert not changes[0].applied and "silent under clip 2 too" in changes[0].summary


def test_the_reader_takes_the_note_and_the_reviewers_own_fix_and_refuses_a_note_that_is_not_about_the_voice(xml):
    cut = timeline.load_cut(xml)
    mk = lambda text: opsmod.validate([{"note": 1, "op": "use_camera_audio", "clip": 2}], [{"timeline_sec": 15.0, "clip": 2, "text": text}], cut)[0]["op"]
    for text in ("I can't hear him on this clip", "the audio drops out here", "his voice is silent for the whole shot", "no sound on this one"):
        assert mk(text) == "use_camera_audio", text
    assert mk("make the title bigger") == "unsupported"
    got = opsmod.validate(opsmod.from_suggestions([{"timeline_sec": 15.0, "clip": 2, "text": "AI: silent", "suggested_op": {"op": "use_camera_audio", "clip": 2}}], cut),
                          [{"timeline_sec": 15.0, "clip": 2, "text": "AI: silent"}], cut)[0]
    assert got["op"] == "use_camera_audio" and got["trusted"] is True


def test_the_reviewer_attaches_the_fix_when_the_camera_hears_what_the_recorder_does_not(monkeypatch):
    import ai_review as air
    import numpy as np
    v = [timeline.VideoClip(1, 0.0, 4.0, "/x/cam.mp4", 10.0, 14.0), timeline.VideoClip(2, 4.0, 8.0, "/x/cam.mp4", 30.0, 34.0)]
    a = [timeline.AudioClip(0.0, 4.0, "/x/lav.wav", 50.0, 54.0, 1), timeline.AudioClip(4.0, 8.0, "/x/lav.wav", 70.0, 74.0, 1)]
    cut = timeline.Cut("t", 30.0, 1080, 1920, 8.0, v, a)
    level = {("/x/lav.wav", 50.0): 0.05, ("/x/lav.wav", 70.0): 0.0001, ("/x/cam.mp4", 10.0): 0.1, ("/x/cam.mp4", 30.0): 0.05}     # linear RMS: the second recorder piece is ~ -80 dB, the camera ~ -26

    def pcm_fn(path, start, dur):
        return np.full(1600, level.get((path, round(start, 1)), 0.1), dtype=np.float32)
    f = air.source_audio_findings(cut, pcm_fn)
    fixes = [n.get("suggested_op") for n in f.notes if n["kind"] in ("audio-silent", "audio-quiet")]
    assert fixes == [{"op": "use_camera_audio", "clip": 2}]                                   # only the silent piece, only with the camera that hears speech
    assert "camera's own sound" in [n for n in f.notes if n["kind"] == "audio-silent"][0]["text"]
