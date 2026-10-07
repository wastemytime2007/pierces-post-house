"""AI review: what it flags, what it leaves alone, and that nothing the model says is shown unless it is in the real words.

Synthetic media only. The 'voice' is a 300 Hz tone in two bursts (0.5-1.5 s and 3.0-4.0 s) with near-silence between, so which cut edges land in speech is known exactly.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import ai_review as ar  # noqa: E402
import words as words_mod  # noqa: E402
from timeline import AudioClip, Cut, VideoClip  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def voice(tmp_path_factory):
    d = tmp_path_factory.mktemp("air")
    sr = 16000
    t = np.arange(int(6 * sr)) / sr
    x = (np.random.default_rng(1).normal(0, 0.002, t.size)).astype(np.float32)           # a quiet room
    for a, b in ((0.5, 1.5), (3.0, 4.0)):
        m = (t >= a) & (t < b)
        env = np.minimum(1.0, np.minimum((t[m] - a) / 0.03, (b - t[m]) / 0.03))           # 30 ms attack and release
        x[m] += (0.4 * np.sin(2 * np.pi * 300 * t[m]) * env).astype(np.float32)
    raw = d / "voice.f32"
    x.tofile(raw)
    wav = d / "voice.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", str(raw), str(wav)], check=True)
    return str(wav)


def cut_of(voice, pieces):
    """pieces: [(src_in, src_out)] played back to back; the voice file stands for the camera and for the recorder."""
    cut = Cut("Test cut", 30.0, 1080, 1920, 0.0)
    t = 0.0
    for i, (a, b) in enumerate(pieces, start=1):
        cut.video.append(VideoClip(i, t, t + (b - a), voice, a, b))
        cut.audio.append(AudioClip(t, t + (b - a), voice, a, b, 1))
        t += b - a
    cut.zone_end = t
    return cut


NO_WORDS = lambda path, start, dur: []  # noqa: E731


def test_a_cut_that_ends_inside_the_voice_is_flagged_and_one_that_ends_in_silence_is_not(voice):
    bad = ar.edge_findings(cut_of(voice, [(0.3, 1.0), (2.9, 4.5)]), NO_WORDS)
    assert bad.ok is False and len(bad.notes) == 1
    n = bad.notes[0]
    assert n["clip"] == 1 and "cut off the end" in n["text"] and n["text"].startswith("AI: ") and n["ai"] is True and n["shapes"] == []
    assert n["timeline_sec"] == pytest.approx(0.65, abs=0.1)                                # on the clip's end (0.7 s into the cut)

    good = ar.edge_findings(cut_of(voice, [(0.3, 1.7), (2.9, 4.5)]), NO_WORDS)
    assert good.ok is True and good.notes == []


def test_a_clip_that_starts_in_the_middle_of_a_burst_is_flagged_as_a_start(voice):
    f = ar.edge_findings(cut_of(voice, [(0.3, 1.7), (3.5, 4.5)]), NO_WORDS)
    assert f.ok is False and [(n["clip"], "start in the middle" in n["text"]) for n in f.notes] == [(2, True)]


def test_a_continuation_in_the_same_file_is_not_an_edge(voice):
    edges = ar.edges_of(cut_of(voice, [(0.3, 1.0), (1.0, 1.7)]))
    assert [(round(t, 2), k) for t, k, _c in edges] == [(0.0, "start"), (1.4, "end")]        # only the start and the end of the cut


def test_a_word_across_the_edge_counts_only_where_the_audio_is_not_silent(voice):
    straddles = lambda path, start, dur: [words_mod.W("hello", 0.6, 1.1)]  # noqa: E731
    in_speech = ar.edge_findings(cut_of(voice, [(0.3, 1.0), (2.9, 4.5)]), straddles)
    assert 'inside the word "hello"' in in_speech.notes[0]["text"]
    in_silence = ar.edge_findings(cut_of(voice, [(0.3, 1.7), (2.9, 4.5)]), lambda p, s, d: [words_mod.W("late", 1.5, 1.9)])
    assert in_silence.ok is True                                                             # Whisper's word times are only good to about 0.1 s: silence at the edge wins


def test_the_seam_uses_the_voice_piece_that_ends_there_not_the_neighbour(voice):
    """Two recorders: the piece before the seam is the quiet one and the piece after it is loud. The end edge must be measured on the piece that ends there."""
    cut = cut_of(voice, [(0.3, 1.7), (2.9, 4.5)])
    cut.audio[1].src_in, cut.audio[1].src_out = 0.5, 1.5 + 0.1                                # the second clip's recorder is mid-burst from its first frame
    f = ar.edge_findings(cut, NO_WORDS)
    assert not any(n["clip"] == 1 and "cut off the end" in n["text"] for n in f.notes)


def test_voice_holes_and_silent_voice_tracks_are_reported_and_a_bleep_is_not_a_hole(voice, tmp_path):
    cut = cut_of(voice, [(0.3, 1.7), (2.9, 4.5)])
    cut.audio = [cut.audio[0]]                                                                # nothing under the second clip
    f = ar.source_audio_findings(cut)
    assert f.ok is False and "no voice" in f.detail and any("audio has a hole" in n["text"] for n in f.notes)
    covered = ar.source_audio_findings(cut, covered=[(1.4, 3.0)])
    assert covered.ok is True                                                                 # a bleep layer covers it on purpose

    silent = tmp_path / "silent.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "3", str(silent)], check=True)
    cut2 = cut_of(voice, [(0.3, 1.7)])
    cut2.audio[0].src_path = str(silent)
    cut2.audio[0].src_in, cut2.audio[0].src_out = 0.0, 1.4
    s = ar.source_audio_findings(cut2)
    assert s.ok is False and any("is silent" in n["text"] for n in s.notes)
    assert "silent.wav 100%" in s.detail


def test_sync_a_matching_recorder_passes_a_shifted_one_fails_and_the_ntsc_pattern_is_an_open_question(voice):
    cut = cut_of(voice, [(0.3, 1.7), (2.9, 4.5)])
    cut.audio[0].src_path = cut.audio[1].src_path = voice + ".lav"                         # a separate recorder (the path differs from the camera file's)
    ok = ar.sync_findings(cut, lambda v, a: 0.004)
    assert ok.ok is True and "lines up" in ok.detail
    off = ar.sync_findings(cut, lambda v, a: 0.5)
    assert off.ok is False and "out of line" in off.detail and "clip 1 by +0.50 s" in off.detail
    for v in cut.video:
        v.src_in += 722.0                                                                     # where the clip sits in a 12-minute camera file
    ntsc = ar.sync_findings(cut, lambda v, a: -0.73)                                          # 0.1% of 722 s
    assert ntsc.ok is None and "open question" in ntsc.detail and "Premiere" in ntsc.detail
    not_ntsc = ar.sync_findings(cut, lambda v, a: -0.31)                                      # a lag at that position that is NOT 0.1%
    assert not_ntsc.ok is False
    assert ar.sync_findings(cut_of(voice, [(0.3, 1.7)]), lambda v, a: 0.9).ok is None        # camera audio is the voice: nothing to line up


def test_the_ntsc_signature_is_tight_and_only_applies_far_into_a_file():
    import verify_preview as vp
    assert vp.ntsc_explained(0.722, 722.9) and vp.ntsc_explained(-0.757, 759.2)
    assert not vp.ntsc_explained(0.722, 300.0)                                                # that lag would not be 0.1% of 300 s
    assert not vp.ntsc_explained(0.05, 40.0) and not vp.ntsc_explained(0.5, 10.0)             # too small or too early in the file to be this


def fake_client(reply):
    return SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: SimpleNamespace(content=[SimpleNamespace(text=reply)])))


def test_the_models_quotes_are_checked_against_the_real_words_and_invented_ones_are_dropped(voice):
    cut = cut_of(voice, [(0.3, 1.7), (2.9, 4.5)])
    heard = lambda path, start, dur: [words_mod.W(t, start + a, start + b) for t, a, b in (("So", 0.3, 0.5), ("the", 0.5, 0.6), ("septic", 0.6, 0.9), ("tank", 0.9, 1.2))]  # noqa: E731
    words = ar.transcript_of(cut, heard)
    assert [w[2] for w in words][:4] == ["So", "the", "septic", "tank"]
    reply = "```json\n" + json.dumps({"story": "A septic tip.", "hook": "weak", "ending": "abrupt", "problems": [
        {"quote": "the septic tank", "note": "Opens on a half sentence. Start on the point."},
        {"quote": "the drain field is fine", "note": "Nothing says this."},                  # not in the transcript
        {"quote": "septic", "note": ""}]}) + "\n```"                                          # no note text
    rows, meta = ar.story_findings(cut, words, fake_client(reply))
    story, hook, ending = rows
    assert story.detail == "A septic tip." and hook.ok is False and ending.ok is False
    assert len(story.notes) == 1 and meta["dropped"] == 2
    n = story.notes[0]
    assert n["where"] == "the septic tank" and n["text"].startswith("AI: Opens on a half sentence") and n["timeline_sec"] == pytest.approx(0.5, abs=0.05)                  # "the" is heard at source 0.8 s; the clip starts at source 0.3 s
    assert n["clip"] == 1


def test_a_story_that_works_adds_no_notes_and_no_words_means_not_judged(voice):
    cut = cut_of(voice, [(0.3, 1.7)])
    words = [(0.0, 0.2, "Hello", cut.video[0])]
    rows, _ = ar.story_findings(cut, words, fake_client('{"story": "Hello.", "hook": "good", "ending": "clean", "problems": []}'))
    assert rows[0].notes == [] and rows[1].ok is True and rows[2].ok is True
    nothing, _ = ar.story_findings(cut, [], None)
    assert nothing[0].ok is None and "not judged" in nothing[0].detail


def test_review_end_to_end_on_an_xml_keeps_the_edge_findings_when_the_model_cannot_run(voice, tmp_path):
    import xml_from_media
    v = tmp_path / "Clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=180x320:r=30:d=6", "-i", voice, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(v)], check=True)
    xml = xml_from_media.make(v, tmp_path / "cut.xml")
    boom = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(RuntimeError("claude CLI not found"))))
    res = ar.review(xml, client=boom, words_fn=lambda path, start, dur: [words_mod.W("hi", start + 0.6, start + 0.9)])
    names = {c["name"]: c for c in res["checks"]}
    assert set(names) >= {"CUT-EDGES", "SOURCE-AUDIO", "STORY"}
    assert names["STORY"]["ok"] is None and "claude CLI not found" in names["STORY"]["detail"]
    assert res["schema"] == "ai_review.v0-draft" and res["not_covered"] and isinstance(res["notes"], list)


def test_a_reply_with_prose_around_the_json_is_still_read():
    assert ar._json_object('Sure. {"story": "x", "problems": []} Hope that helps')["story"] == "x"
    with pytest.raises(ValueError):
        ar._json_object("no json here")
