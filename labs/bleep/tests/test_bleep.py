"""Hermetic tests for bleeping curse words: the word list, the spans, silencing the speech in the XML, the bleep layers,
and the checks that measure the result. Whisper is faked with known word times; the audio is synthetic noise."""
import importlib.util
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "review_loop"))
sys.path.insert(0, str(HERE.parents[1] / "safety_net"))

import bleep as bl  # noqa: E402
import layers as ly  # noqa: E402
import timeline  # noqa: E402

_spec = importlib.util.spec_from_file_location("rl_tests", HERE.parent / "review_loop" / "tests" / "test_revise.py")
rl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rl)

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
PATS = bl.load_patterns()
WORDS = [("Hello", 1.0, 1.4), ("Shit,", 12.5, 12.9), ("class", 15.0, 15.4), ("fucking", 20.0, 20.5), ("ok", 25.0, 25.3)]


@pytest.fixture(scope="module")
def cut_media(tmp_path_factory):
    d = tmp_path_factory.mktemp("bleep")
    vid, lav, cam = d / "a.mp4", d / "lav.wav", d / "cam.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=d=60:c=white:r=48000:a=0.3", "-af", "lowpass=f=3000", str(lav)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "5.005", "-i", str(lav), str(cam)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "smptebars=size=320x180:rate=30", "-i", str(cam), "-t", "55",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(vid)], check=True)
    return vid, lav


@pytest.fixture()
def xml(tmp_path, cut_media):
    vid, lav = cut_media
    p = rl.make_xml(tmp_path / "cut.xml", vid, lav)
    root = ET.parse(p).getroot()                                     # a real export declares the sequence's audio rate; the synthetic one does not
    seq = timeline._seq_for_cut(root)
    fmt = ET.SubElement(seq.find("media/audio"), "format")
    ET.SubElement(ET.SubElement(fmt, "samplecharacteristics"), "samplerate").text = "48000"
    seq.find("media/audio").remove(fmt)
    seq.find("media/audio").insert(0, fmt)
    ET.indent(root, space="\t")
    p.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + ET.tostring(root, encoding="unicode") + "\n")
    return p


def test_the_list_matches_whole_words_and_their_endings_and_nothing_else():
    yes = ["shit", "Shit,", "SHIT", "fucking", "Fucked", "motherfucker", "ass", "Ass.", "asshole", "bitch", "bitches", "dick", "cunt", "bullshit", "goddamn", "f***", "s***"]
    no = ["class", "assume", "assist", "Dickens", "passage", "hello", "bass", "grass", "Scunthorpe", "cocktail", "", "..."]
    assert all(bl.is_profane(w, PATS) for w in yes)
    assert not any(bl.is_profane(w, PATS) for w in no)


def test_a_word_added_to_the_list_for_one_run_is_bleeped_and_the_file_is_the_only_default():
    p = bl.load_patterns(extra=["darn"])
    assert bl.is_profane("darn", p) and not bl.is_profane("darn", PATS) and not bl.is_profane("damn", PATS)


def test_censoring_keeps_the_first_letter_and_punctuation():
    assert bl.censor("Fucking, shit and class.", PATS) == "F******, s*** and class."
    assert bl.censor("what the hell", PATS) == "what the hell"


def test_spans_are_padded_merged_and_kept_inside_the_cut():
    hits = [{"word": "a", "start": 10.0, "end": 10.4}, {"word": "b", "start": 10.45, "end": 10.8}, {"word": "c", "start": 0.02, "end": 0.3}, {"word": "d", "start": 29.9, "end": 30.0}]
    sp = bl.spans_of(hits, 30.0)
    assert sp == [(0.0, 0.42), (9.92, 10.92), (29.82, 30.0)]


def test_silencing_splits_the_speech_and_disables_only_the_span_and_the_cut_keeps_its_length(xml, tmp_path):
    before = timeline.load_cut(xml)
    out = tmp_path / "muted.xml"
    info = bl.mute_spans(xml, out, [(12.42, 13.02), (19.92, 20.62)])
    after = timeline.load_cut(out)
    assert after.zone_end == before.zone_end and len(after.video) == len(before.video)                    # the picture and the length are untouched
    lost = sum(a.tl_end - a.tl_start for a in before.audio) - sum(a.tl_end - a.tl_start for a in after.audio)
    assert lost == pytest.approx(0.6 + 0.7, abs=0.05) and info["extra_pieces"] >= 2
    ids = [c.get("id") for c in ET.parse(out).getroot().iter("clipitem")]
    assert len(ids) == len(set(ids)) and sum(i.endswith(bl.MUTED_SUFFIX) for i in ids) >= 2                # unique ids; silenced pieces are marked
    back = tmp_path / "back.xml"
    pieces, layers = bl.strip_previous(out, back)
    restored = timeline.load_cut(back)
    assert pieces >= 2 and layers == 0
    assert sum(a.tl_end - a.tl_start for a in restored.audio) == pytest.approx(sum(a.tl_end - a.tl_start for a in before.audio), abs=0.01)


def test_a_whole_run_finds_the_words_silences_them_lays_bleeps_and_every_check_passes(xml, tmp_path):
    calls = []

    def words_of(wav):
        calls.append(wav)
        return WORDS if len(calls) == 1 else [w for w in WORDS if not bl.is_profane(w[0], PATS)]      # the second listen hears no curse word
    r = bl.bleep(xml, tmp_path / "out", words_of=words_of)
    assert [h["word"] for h in r["hits"]] == ["Shit,", "fucking"] and len(r["spans"]) == 2             # "class" is not bleeped
    assert r["spans"][0] == pytest.approx((12.42, 13.02), abs=0.01)
    bad = [(n, d) for n, ok, d in r["rows"] if ok is False]
    assert not bad, bad
    names = [n for n, _ok, _d in r["rows"]]
    assert {"SPANS-SILENT", "OUTSIDE-UNCHANGED", "ONLY-THE-SPANS-LOST", "NO-LISTED-WORD-LEFT"} <= set(names) and sum(n.startswith("BLEEP-IS-A-TONE") for n in names) == 2
    lay = ly.find_layers(Path(r["xml"]))
    assert sorted(ly.lane_name(l) for l in lay) == ["Bleep", "Bleep"] and all(l.kind == "audio" for l in lay)
    assert [l.start for l in sorted(lay, key=lambda l: l.start)] == pytest.approx([12.42, 19.92], abs=0.02)          # to the nearest video frame


def test_running_it_again_on_its_own_result_changes_nothing(xml, tmp_path):
    r1 = bl.bleep(xml, tmp_path / "one", words_of=lambda w: WORDS, check_transcript=False)
    r2 = bl.bleep(Path(r1["xml"]), tmp_path / "two", words_of=lambda w: WORDS, check_transcript=False)
    assert r2["spans"] == r1["spans"] and not [1 for _n, ok, _d in r2["rows"] if ok is False]
    assert len(ly.find_layers(Path(r2["xml"]))) == 2                                                   # not four: the old bleeps were removed first
    a, b = timeline.load_cut(Path(r1["xml"])), timeline.load_cut(Path(r2["xml"]))
    assert sum(x.tl_end - x.tl_start for x in a.audio) == pytest.approx(sum(x.tl_end - x.tl_start for x in b.audio), abs=0.01)


def test_nothing_to_bleep_writes_no_xml(xml, tmp_path):
    r = bl.bleep(xml, tmp_path / "none", words_of=lambda w: [w_ for w_ in WORDS if w_[0] in ("Hello", "class", "ok")])
    assert r["xml"] is None and r["spans"] == [] and not (tmp_path / "none" / xml.name).exists()


def test_the_checks_fail_when_the_speech_was_not_really_silenced(xml, tmp_path):
    """Negative control: hand verify() an 'after' that is the untouched cut and it must say the spans are not silent."""
    spans = [(12.42, 13.02)]
    clips = bl.make_bleeps(spans, 0.3, tmp_path / "b")
    rows = {n: ok for n, ok, _d in bl.verify(xml, xml, spans, clips, 0.3)}
    assert rows["SPANS-SILENT"] is False and rows["ONLY-THE-SPANS-LOST"] is False


def test_the_checks_fail_when_a_word_is_still_heard_after_bleeping(xml, tmp_path):
    r = bl.bleep(xml, tmp_path / "out", words_of=lambda w: WORDS)                                      # the second listen still hears both
    left = [(n, d) for n, ok, d in r["rows"] if n == "NO-LISTED-WORD-LEFT" and ok is False]
    assert left and "Shit," in left[0][1]


def test_a_bleep_that_is_the_wrong_tone_or_level_is_caught(tmp_path, xml):
    spans = [(12.42, 13.02)]
    clips = bl.make_bleeps(spans, 0.3, tmp_path / "b")
    bl.write_wav(Path(clips[0]["path"]), np.sin(2 * np.pi * 440 * np.arange(int(0.6 * 48000)) / 48000) * 0.3, 48000, 2)          # 440 Hz at the wrong level
    out = tmp_path / "m.xml"
    bl.mute_spans(xml, out, spans)
    rows = {n: ok for n, ok, _d in bl.verify(xml, out, spans, clips, 0.3)}
    assert rows["BLEEP-IS-A-TONE (bleep_1.wav)"] is False


def test_the_outside_check_catches_speech_that_was_silenced_beyond_the_declared_span(xml, tmp_path):
    """Negative control: mute a wider stretch than the spans declare; the speech beside the span is then damaged and must be flagged."""
    declared = [(12.42, 13.02)]
    wide = tmp_path / "wide.xml"
    bl.mute_spans(xml, wide, [(12.0, 13.5)])
    clips = bl.make_bleeps(declared, 0.3, tmp_path / "b")
    rows = {n: ok for n, ok, _d in bl.verify(xml, wide, declared, clips, 0.3)}
    assert rows["OUTSIDE-UNCHANGED"] is False


def _speech_with_burst():
    """16 kHz speech-like noise at -35 dB, with a loud 0.3 s burst at 5.0-5.3 s inside one long 'word'."""
    rng = np.random.default_rng(7)
    x = rng.standard_normal(16000 * 10) * 10 ** (-35 / 20) * 1.0
    x[int(5.0 * 16000):int(5.3 * 16000)] *= 10 ** (12 / 20)
    return x


def test_a_long_word_with_a_loud_burst_inside_is_a_suspect_and_ordinary_words_are_not():
    words = [(f"w{i}", i * 0.5, i * 0.5 + 0.3) for i in range(9)] + [("what", 4.8, 5.3), ("just", 5.3, 5.6)]
    sus = bl.find_suspects(sorted(words, key=lambda w: w[1]), _speech_with_burst())
    assert [s["word"] for s in sus] == ["what"] and sus[0]["start"] == pytest.approx(5.0, abs=0.06) and sus[0]["end"] == pytest.approx(5.3, abs=0.06)
    assert bl.find_suspects([(f"w{i}", i * 0.5, i * 0.5 + 0.3) for i in range(9)], _speech_with_burst()) == []          # short words: nothing flagged
    flat = np.random.default_rng(8).standard_normal(160000) * 10 ** (-35 / 20)
    assert bl.find_suspects([("what", 4.8, 5.3)] + [(f"w{i}", i * 0.5, i * 0.5 + 0.3) for i in range(9)], flat) == []   # a long word with no burst is just a long word


def test_a_suspect_is_only_bleeped_when_a_note_points_at_its_stretch(xml, tmp_path, monkeypatch):
    spans = [(12.42, 13.02)]
    fake = [{"word": "what", "word_start": 12.3, "word_end": 13.0, "start": 12.5, "end": 12.8, "burst_db_over_speech": 11.0}]
    monkeypatch.setattr(bl, "find_suspects", lambda w, s, sr=bl.SR: [dict(f) for f in fake])
    plain = bl.bleep(xml, tmp_path / "plain", words_of=lambda w: [("Hello", 1.0, 1.4)], check_transcript=False)
    assert plain["xml"] is None and plain["suspects"][0]["bleeped"] is False                # reported, not bleeped
    told = bl.bleep(xml, tmp_path / "told", words_of=lambda w: [("Hello", 1.0, 1.4)], check_transcript=False, suspect_windows=[(10.0, 15.0)])
    assert told["xml"] and told["suspects"][0]["bleeped"] is True and len(told["spans"]) == 1
    elsewhere = bl.bleep(xml, tmp_path / "else", words_of=lambda w: [("Hello", 1.0, 1.4)], check_transcript=False, suspect_windows=[(20.0, 25.0)])
    assert elsewhere["xml"] is None                                                         # a note about another stretch does not bleep this one


def test_when_several_suspects_fall_in_the_stretch_a_note_points_at_only_the_strongest_is_bleeped(xml, tmp_path, monkeypatch):
    fake = [{"word": "what", "word_start": 12.3, "word_end": 12.8, "start": 12.5, "end": 12.8, "burst_db_over_speech": 15.0},
            {"word": "just", "word_start": 12.8, "word_end": 13.4, "start": 13.0, "end": 13.2, "burst_db_over_speech": 8.0}]
    monkeypatch.setattr(bl, "find_suspects", lambda w, s, sr=bl.SR: [dict(f) for f in fake])
    r = bl.bleep(xml, tmp_path / "x", words_of=lambda w: [("Hello", 1.0, 1.4)], check_transcript=False, suspect_windows=[(10.0, 15.0)])
    assert [s_["word"] for s_ in r["suspects"] if s_["bleeped"]] == ["what"] and len(r["spans"]) == 1 and r["spans"][0][1] < 13.0
