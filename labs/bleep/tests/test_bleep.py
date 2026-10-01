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


def test_the_checks_fail_when_a_word_is_still_audible_after_bleeping_and_ignore_one_written_over_silence(xml, tmp_path):
    calls = []

    def still_audible(wav):                                                                          # first listen: the real hit; second listen: the word again, over sound at 5 s
        calls.append(1)
        return [("Shit,", 12.5, 12.9)] if len(calls) == 1 else [("Shit,", 5.0, 5.4)]
    r = bl.bleep(xml, tmp_path / "a", words_of=still_audible)
    left = [(n, d) for n, ok, d in r["rows"] if n == "NO-LISTED-WORD-LEFT" and ok is False]
    assert left and "Shit," in left[0][1] and "audible" in left[0][1]
    over_silence = []

    def phantom(wav):                                                                                # the second listen writes the word over the silenced stretch itself
        over_silence.append(1)
        return [("Shit,", 12.5, 12.9)]
    r2 = bl.bleep(xml, tmp_path / "b", words_of=phantom)
    row = next((n, ok, d) for n, ok, d in r2["rows"] if n == "NO-LISTED-WORD-LEFT")
    assert row[1] is True and "written over silence" in row[2] and "0% audible" in row[2]
    assert bl.audible_share(np.zeros(16000 * 3), 1.0, 2.0) == 0.0 and bl.audible_share(_flat(3), 1.0, 2.0) == 1.0

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
    monkeypatch.setattr(bl, "find_suspects", lambda w, s, sr=bl.SR, **kw: [dict(f) for f in fake])
    plain = bl.bleep(xml, tmp_path / "plain", words_of=lambda w: [("Hello", 1.0, 1.4)], check_transcript=False, flag_suspects=True)
    assert plain["xml"] is None and plain["suspects"][0]["bleeped"] is False                # reported, not bleeped
    told = bl.bleep(xml, tmp_path / "told", words_of=lambda w: [("Hello", 1.0, 1.4)], check_transcript=False, suspect_windows=[(10.0, 15.0)])
    assert told["xml"] and told["suspects"][0]["bleeped"] is True and len(told["spans"]) == 1
    elsewhere = bl.bleep(xml, tmp_path / "else", words_of=lambda w: [("Hello", 1.0, 1.4)], check_transcript=False, suspect_windows=[(20.0, 25.0)])
    assert elsewhere["xml"] is None                                                         # a note about another stretch does not bleep this one


def test_when_several_suspects_fall_in_the_stretch_a_note_points_at_only_the_strongest_is_bleeped(xml, tmp_path, monkeypatch):
    fake = [{"word": "what", "word_start": 12.3, "word_end": 12.8, "start": 12.5, "end": 12.8, "burst_db_over_speech": 15.0},
            {"word": "just", "word_start": 12.8, "word_end": 13.4, "start": 13.0, "end": 13.2, "burst_db_over_speech": 8.0}]
    monkeypatch.setattr(bl, "find_suspects", lambda w, s, sr=bl.SR, **kw: [dict(f) for f in fake])
    r = bl.bleep(xml, tmp_path / "x", words_of=lambda w: [("Hello", 1.0, 1.4)], check_transcript=False, suspect_windows=[(10.0, 15.0)])
    assert [s_["word"] for s_ in r["suspects"] if s_["bleeped"]] == ["what"] and len(r["spans"]) == 1 and r["spans"][0][1] < 13.0


# ---- the strict detector, click snapping, requests from notes ----

def _flat(n=10, db=-35):
    return np.random.default_rng(9).standard_normal(16000 * n) * 10 ** (db / 20)


def test_the_strict_detector_needs_two_independent_signals_and_names_them():
    base = [(f"w{i}", i * 0.5, i * 0.5 + 0.3) for i in range(9)]
    speech = _flat()
    # one signal alone (Whisper unsure of a short word) is not enough
    one = base + [("by", 4.6, 4.85)]
    details = [(w, s_, e_, 0.95) for w, s_, e_ in base] + [("by", 4.6, 4.85, 0.3)]
    assert bl.find_suspects(sorted(one, key=lambda x: x[1]), speech, details=sorted(details, key=lambda x: x[1])) == []
    # unsure + the second model heard something else: two signals, flagged 'possible'
    other = [(w, s_, e_) for w, s_, e_ in base] + [("through", 4.6, 4.85)]
    sus = bl.find_suspects(sorted(one, key=lambda x: x[1]), speech, details=sorted(details, key=lambda x: x[1]), other=sorted(other, key=lambda x: x[1]))
    assert [x["word"] for x in sus] == ["by"] and sus[0]["signals"] == ["unsure", "models disagree"] and sus[0]["tier"] == "possible" and sus[0]["score"] == 2
    assert (sus[0]["start"], sus[0]["end"]) == (4.6, 4.85)                                          # no burst: the span is the word
    # stretched + burst + unsure + disagreeing = all four, 'likely'
    words = base + [("what", 4.8, 5.3)]
    sp = _speech_with_burst()
    det = [(w, s_, e_, 0.95) for w, s_, e_ in base] + [("what", 4.8, 5.3, 0.2)]
    oth = base + [("the", 4.8, 5.3)]
    four = bl.find_suspects(sorted(words, key=lambda x: x[1]), sp, details=sorted(det, key=lambda x: x[1]), other=sorted(oth, key=lambda x: x[1]))
    assert four[0]["signals"] == ["stretched", "burst", "unsure", "models disagree"] and four[0]["tier"] == "likely"
    assert four[0]["start"] == pytest.approx(5.0, abs=0.06)                                         # the burst's span when there is one
    # a word both models agree on, that Whisper is sure of, is never a suspect however it sounds
    sure = bl.find_suspects(sorted(words, key=lambda x: x[1]), _flat(), details=sorted([(w, s_, e_, 0.99) for w, s_, e_ in words], key=lambda x: x[1]), other=sorted(words, key=lambda x: x[1]))
    assert sure == []


def test_a_higher_bar_flags_fewer_stretches():
    base = [(f"w{i}", i * 0.5, i * 0.5 + 0.3) for i in range(9)]
    words = sorted(base + [("by", 4.6, 4.85)], key=lambda x: x[1])
    details = sorted([(w, s_, e_, 0.95) for w, s_, e_ in base] + [("by", 4.6, 4.85, 0.3)], key=lambda x: x[1])
    other = sorted(base + [("through", 4.6, 4.85)], key=lambda x: x[1])
    assert len(bl.find_suspects(words, _flat(), details=details, other=other, min_signals=2)) == 1
    assert bl.find_suspects(words, _flat(), details=details, other=other, min_signals=3) == []


def test_a_clicked_spot_snaps_to_the_spoken_stretch_and_never_bleeps_a_whole_sentence():
    x = np.random.default_rng(3).standard_normal(16000 * 10) * 10 ** (-60 / 20)                     # quiet room
    x[int(4.0 * 16000):int(4.4 * 16000)] += np.random.default_rng(4).standard_normal(int(0.4 * 16000)) * 10 ** (-25 / 20)       # a 0.4 s word
    a, b = bl.snap_voiced(x, 4.2)
    assert a == pytest.approx(4.0, abs=0.05) and b == pytest.approx(4.4, abs=0.05)                  # the word, not wider
    a, b = bl.snap_voiced(x, 4.25)                                                                  # off centre still finds it
    assert a == pytest.approx(4.0, abs=0.05) and b == pytest.approx(4.4, abs=0.05)
    a, b = bl.snap_voiced(x, 8.0)                                                                   # nothing voiced near the click: plus or minus 0.3 s of it
    assert (a, b) == (7.7, 8.3)
    run = np.random.default_rng(5).standard_normal(16000 * 10) * 10 ** (-25 / 20)                   # continuous speech: capped to 0.3 s either side of the click
    a, b = bl.snap_voiced(run, 5.0)
    assert b - a <= 0.61 and a <= 5.0 <= b


def test_a_note_turns_into_the_most_exact_request_it_can():
    ops = [{"note": i, "op": "bleep_word"} for i in (1, 2, 3, 4)]
    notes = [{"timeline_sec": 29.0, "text": "bleep", "target": {"lane": "Suspects", "label": "x", "start": 28.9, "end": 29.3}},
             {"timeline_sec": 27.5, "text": "bleep here", "target": {"lane": "Clips", "label": "c", "start": 26.4, "end": 33.3, "clicked": True}},
             {"timeline_sec": 29.8, "text": "bleep the curse word", "target": {"lane": "Clips", "label": "c", "start": 26.4, "end": 33.3}},
             {"timeline_sec": 10.0, "text": "bleep the swear word"}]
    r = bl.requests_from_notes(ops, notes)
    assert r[0] == {"kind": "span", "start": 28.9, "end": 29.3, "label": "x"} and r[1] == {"kind": "at", "t": 27.5}
    assert r[2] == {"kind": "window", "start": 9.0, "end": 11.0} and len(r) == 3           # the clip-wide request held the click, so it is the same ask made loosely and is dropped


def test_a_confirmed_suspect_and_a_clicked_spot_are_bleeped_exactly_there(xml, tmp_path, monkeypatch):
    fake = [{"word": "what", "word_start": 12.3, "word_end": 13.0, "start": 12.5, "end": 12.8, "burst_db_over_speech": 0.0, "signals": ["unsure", "models disagree"], "score": 2, "tier": "possible"}]
    monkeypatch.setattr(bl, "find_suspects", lambda w, s, sr=bl.SR, **kw: [dict(f) for f in fake])
    none = lambda w: [("Hello", 1.0, 1.4)]                                                          # noqa: E731
    confirmed = bl.bleep(xml, tmp_path / "a", words_of=none, check_transcript=False, flag_suspects=True, requests=[{"kind": "span", "start": 12.5, "end": 12.8, "label": "what"}])
    assert len(confirmed["spans"]) == 1 and confirmed["spans"][0] == pytest.approx((12.42, 12.92), abs=0.02) and confirmed["suspects"][0]["bleeped"] is True
    clicked = bl.bleep(xml, tmp_path / "b", words_of=none, check_transcript=False, flag_suspects=True, requests=[{"kind": "at", "t": 20.2}])
    assert len(clicked["spans"]) == 1 and clicked["spans"][0][0] < 20.2 < clicked["spans"][0][1] and clicked["suspects"][0]["bleeped"] is False
    bad = [(n, d) for n, ok, d in clicked["rows"] if ok is False]
    assert not bad, bad                                                                             # silence, tone, level and the rest all hold for a clicked spot


def test_a_loud_stretched_word_the_transcript_is_sure_of_is_emphasis_not_a_suspect():
    """Nine of fourteen flags on the real cut were this. With the transcript's uncertainty available, acoustic signals alone do not count."""
    words = sorted([(f"w{i}", i * 0.5, i * 0.5 + 0.3) for i in range(9)] + [("what", 4.8, 5.3)], key=lambda x: x[1])
    sure = [(w, s_, e_, 0.96) for w, s_, e_ in words]
    sp = _speech_with_burst()
    assert [x["word"] for x in bl.find_suspects(words, sp)] == ["what"]                                   # with no transcript evidence at all, the acoustic rule still flags it
    assert bl.find_suspects(words, sp, details=sure, other=words) == []                                    # sure and agreed: emphasis
    unsure = [(w, s_, e_, 0.2 if w == "what" else 0.96) for w, s_, e_ in words]
    got = bl.find_suspects(words, sp, details=unsure, other=words)
    assert [x["word"] for x in got] == ["what"] and got[0]["signals"] == ["stretched", "burst", "unsure"]   # the same word, once Whisper doubts it


def test_a_vague_bleep_request_that_holds_an_exact_one_is_dropped_and_a_no_never_bleeps():
    sys.path.insert(0, str(HERE.parent / "review_loop"))
    import ops as opsmod
    clip = {"lane": "Clips", "label": "c", "start": 26.43, "end": 33.3}
    notes = [{"timeline_sec": 28.35, "text": "bleep this word", "target": dict(clip, clicked=True)},
             {"timeline_sec": 29.86, "text": "Lets bleep the curse word here", "target": clip},
             {"timeline_sec": 50.0, "text": "bleep the swear word", "target": {"lane": "Clips", "label": "d", "start": 46.0, "end": 54.0}}]
    ops = [{"note": i, "op": "bleep_word"} for i in (1, 2, 3)]
    assert bl.requests_from_notes(ops, notes) == [{"kind": "at", "t": 28.35}, {"kind": "window", "start": 46.0, "end": 54.0}]     # note 2 said the same thing loosely
    cut = type("C", (), {"video": [object()], "zone_end": 70.0})()
    sus = {"lane": "Suspects", "label": "possible curse word?", "start": 26.3, "end": 26.54, "clicked": True}
    no = [{"timeline_sec": 26.49, "text": t_, "target": sus} for t_ in ("no", "No, that's not a curse word", "don't bleep it", "yes", "bleep it")]
    got = opsmod.validate([{"note": i, "op": "bleep_word"} for i in range(1, 6)], no, cut)
    assert [o["op"] for o in got] == ["unsupported", "unsupported", "unsupported", "bleep_word", "bleep_word"]
    assert "the note says no" in got[0]["reason"] and "nothing is bleeped" in got[1]["reason"]


def test_the_evaluation_scores_hits_false_alarms_and_misses_against_ryans_labels():
    import json
    import evaluate as ev
    truth = json.loads((HERE / "ground_truth.json").read_text())
    assert truth["curse_words"][0]["range"] == [29.44, 29.70] and len(truth["rejected_suspects"]) == 3         # his labels are in the repo and parse
    flagged = [{"start": 26.3, "end": 26.54}, {"start": 31.5, "end": 31.98}, {"start": 66.8, "end": 67.1}]      # what the strict detector flagged on the real cut
    r = ev.score(flagged, truth)
    assert r["false_alarms"] == 3 and r["found"] == 0 and r["missed"] == ["fuck"] and r["recall"] == 0.0 and r["precision"] == 0.0   # the documented negative result
    better = ev.score(flagged + [{"start": 29.30, "end": 29.70}], truth)
    assert better["found"] == 1 and better["recall"] == 1.0 and better["precision"] == 0.25                     # a flag on the word counts; the three false alarms still count against it
    assert ev.score([], truth)["precision"] is None                                                            # nothing flagged, nothing to be precise about


# ---- times a person gives are used exactly; a hidden curse word is revealed by silencing the loud stretch ----

def test_times_a_person_gives_are_used_exactly_with_no_padding(xml, tmp_path):
    none = lambda w: [("Hello", 1.0, 1.4)]                                                         # noqa: E731
    r = bl.bleep(xml, tmp_path / "x", words_of=none, check_transcript=False, requests=[{"kind": "exact", "start": 12.9, "end": 13.2}])
    assert len(r["spans"]) == 1 and r["spans"][0] == pytest.approx((12.9, 13.2), abs=0.001)           # a transcript hit would have been padded 0.08 before and 0.12 after
    bad = [(n, d) for n, ok, d in r["rows"] if ok is False]
    assert not bad, bad
    both = bl.bleep(xml, tmp_path / "y", words_of=lambda w: [("Shit,", 20.0, 20.4)], check_transcript=False, requests=[{"kind": "exact", "start": 12.9, "end": 13.2}])
    assert both["spans"][0] == pytest.approx((12.9, 13.2), abs=0.001) and both["spans"][1] == pytest.approx((19.92, 20.52), abs=0.01)   # exact stays exact next to a padded hit


def test_a_bleep_note_that_states_times_is_taken_at_its_word():
    ops = [{"note": i, "op": "bleep_word"} for i in (1, 2, 3)]
    notes = [{"timeline_sec": 10.0, "text": "bleep 28.9-29.2 please"}, {"timeline_sec": 5.0, "text": "bleep the swear at 12 to 12.4 s"},
             {"timeline_sec": 7.0, "text": "bleep the curse word from 10 to 30"}]                                 # 20 s is not a word: ignored, falls back to the moment
    r = bl.requests_from_notes(ops, notes)
    assert r[0] == {"kind": "exact", "start": 28.9, "end": 29.2} and r[1] == {"kind": "exact", "start": 12.0, "end": 12.4}
    assert r[2] == {"kind": "window", "start": 6.0, "end": 8.0}


def _read16k(wav):
    import wave
    with wave.open(str(wav)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float) / 32767


def test_a_curse_word_whisper_hides_is_revealed_by_silencing_part_of_the_loud_stretch_and_listening_again(tmp_path):
    """The fake listener behaves like Whisper did on the real cut, on the short window reveal gives it: the curse word is written only when the front
    of the loud stretch is silent and its tail is still audible. (Times in the window are relative to its start, 2 s before the stretch.)"""
    sp = _speech_with_burst()                                                                        # a 12 dB burst at 5.0-5.3 s in a synthetic room
    calls = []

    def listen(wav):
        x = _read16k(wav)
        calls.append(len(x) / 16000)
        base = [("Hello", 0.2, 0.6), ("what", 1.8, 2.3), ("just", 2.3, 2.6)]
        front_silent = np.abs(x[int(2.0 * 16000):int(2.2 * 16000)]).max() < 1e-3
        tail_heard = np.abs(x[int(2.26 * 16000):int(2.3 * 16000)]).max() >= 1e-3
        return base + [("fuck", 2.05, 2.25)] if front_silent and tail_heard else base
    got = bl.reveal(sp, [(5.0, 5.3)], listen, PATS, tmp_path)
    assert len(got) == 1 and got[0]["word"] == "fuck" and got[0]["revealed"] is True
    assert got[0]["word_start"] == pytest.approx(5.05, abs=0.01)
    assert got[0]["start"] == pytest.approx(5.05, abs=0.01) and got[0]["end"] == pytest.approx(5.25, abs=0.01)      # the word's own span, not widened to the stretch it hid behind
    assert len(calls) == 3 and all(c < 5 for c in calls)                                             # three partial masks, each on a short window, not the whole file
    assert bl.reveal(sp, [(5.0, 5.3)], lambda wav: [("Hello", 0.2, 0.6)], PATS, tmp_path) == []     # silencing reveals nothing: nothing is added
    assert bl.reveal(sp, [], listen, PATS, tmp_path) == []


def test_each_stretch_is_tried_alone_because_silencing_two_together_can_hide_the_word_completely(tmp_path):
    sp = _speech_with_burst()
    seen = []

    def listen(wav):
        x = _read16k(wav)
        zero_runs = int((x == 0.0).sum())                                                          # only masked samples are exactly zero
        seen.append(zero_runs)
        return [("Hello", 0.2, 0.6)]
    bl.reveal(sp, [(5.0, 5.3), (7.0, 7.3)], listen, PATS, tmp_path)
    assert len(seen) == 6 and max(seen) < int(0.3 * 16000)                                           # never more than one stretch silenced in any one listen


def test_a_whole_run_with_the_reveal_bleeps_the_hidden_word_and_every_check_passes(xml, tmp_path, monkeypatch):
    words = [("Hello", 1.0, 1.4), ("what", 12.4, 13.2), ("just", 13.2, 13.7), ("ok", 20.0, 20.3)]

    def listen(wav):
        """Full file: the words as written. Short window (before the stretch at 12.6, so 2 s earlier): the curse word appears only when its front is silent and its tail audible."""
        x = _read16k(wav)
        if len(x) / 16000 > 10:
            return words
        silent = lambda a, b: np.abs(x[int(a * 16000):int(b * 16000)]).max() < 1e-3                # noqa: E731
        t0 = 12.6 - 2.0
        front = silent(12.6 - t0, 12.85 - t0) and not silent(13.1 - t0, 13.25 - t0)
        return [("what", 12.4 - t0, 13.2 - t0), ("fuck", 13.0 - t0, 13.25 - t0)] if front else [("what", 12.4 - t0, 13.2 - t0)]
    monkeypatch.setattr(bl, "find_suspects", lambda w, s, sr=bl.SR, **kw: [{"word": "what", "word_start": 12.4, "word_end": 13.2, "start": 12.6, "end": 12.95,
                                                                           "burst_db_over_speech": 12.0, "signals": ["stretched", "burst"], "score": 2, "tier": "possible"}])
    r = bl.bleep(xml, tmp_path / "x", words_of=listen, reveal_with=listen)
    assert [h["word"].split(" ")[0] for h in r["hits"]] == ["fuck"] and "was hidden" in r["hits"][0]["word"]
    assert len(r["spans"]) == 1 and r["spans"][0][0] <= 13.0 and r["spans"][0][1] >= 13.25           # the word, as Whisper timed it, is covered
    bad = [(n, d) for n, ok, d in r["rows"] if ok is False]
    assert not bad, bad
    assert any(n == "SPANS-SILENT" for n, _ok, _d in r["rows"])


def test_times_a_person_gives_override_the_tools_own_hit_where_they_overlap(xml, tmp_path):
    """Ryan: the bleep 'is too long, it only needs to be' a shorter stretch. His times replace the tool's span there instead of being merged back into it."""
    listen = lambda w: [("Hello", 1.0, 1.4), ("Shit,", 12.4, 13.2), ("Fuck", 20.0, 20.4)]                # noqa: E731
    r = bl.bleep(xml, tmp_path / "x", words_of=listen, check_transcript=False, requests=[{"kind": "exact", "start": 12.7, "end": 13.3}])
    assert r["spans"][0] == pytest.approx((12.7, 13.3), abs=0.001)                                      # not (12.32, 13.32): the tool's padded hit is gone
    assert [h["word"] for h in r["overridden"]] == ["Shit,"] and r["spans"][1] == pytest.approx((19.92, 20.52), abs=0.01)   # a hit elsewhere is untouched
    bad = [(n, d) for n, ok, d in r["rows"] if ok is False]
    assert not bad, bad
    far = bl.bleep(xml, tmp_path / "y", words_of=listen, check_transcript=False, requests=[{"kind": "exact", "start": 5.0, "end": 5.4}])
    assert far["overridden"] == [] and len(far["spans"]) == 3                                           # times that overlap nothing the tool found simply add a bleep


def test_ordinary_words_are_not_flagged_unless_asked_for_and_only_definitive_words_are_bleeped(xml, tmp_path, monkeypatch):
    """Ryan: 'why is it pulling normal words like so, real, now, because, just, when ... just apply bleeps to definitive curse words and let me add to any that may be missed.'"""
    fake = [{"word": "so", "word_start": 12.3, "word_end": 12.8, "start": 12.5, "end": 12.8, "burst_db_over_speech": 12.0, "signals": ["stretched", "burst"], "score": 2, "tier": "possible"}]
    monkeypatch.setattr(bl, "find_suspects", lambda w, s, sr=bl.SR, **kw: [dict(f) for f in fake])
    r = bl.bleep(xml, tmp_path / "x", words_of=lambda w: [("Hello", 1.0, 1.4), ("Shit,", 20.0, 20.4)], check_transcript=False)
    assert r["suspects"] == [] and [h["word"] for h in r["hits"]] == ["Shit,"]                              # no flags, and only the listed word is bleeped
    r2 = bl.bleep(xml, tmp_path / "y", words_of=lambda w: [("Hello", 1.0, 1.4)], check_transcript=False, flag_suspects=True)
    assert [x["word"] for x in r2["suspects"]] == ["so"] and r2["xml"] is None                               # asked for, they are listed; still nothing is bleeped from them


def test_a_word_end_stretched_over_a_pause_is_pulled_back_to_where_the_sound_stops_and_nothing_else_is_touched():
    """Ryan's DeWalt video: Whisper put a 0.3 s curse word's end 0.8 s late; the sound was over by 36.6 s."""
    x = np.random.default_rng(11).standard_normal(16000 * 40) * 10 ** (-50 / 20)                              # a quiet room
    x[int(36.28 * 16000):int(36.6 * 16000)] += np.random.default_rng(12).standard_normal(int(0.32 * 16000)) * 10 ** (-22 / 20)          # the word: 0.32 s, 28 dB above the room
    a, b = bl.trim_to_sound(x, 36.28, 37.42)
    assert a == 36.28 and b == pytest.approx(36.6 + bl.TRIM_TAIL_SEC, abs=0.04)                                 # start kept, end pulled back to the sound plus a soft-release margin
    assert bl.trim_to_sound(x, 36.28, 36.75) == (36.28, 36.75)                                                   # under 0.25 s to save: left alone
    loud = np.random.default_rng(13).standard_normal(16000 * 10) * 10 ** (-25 / 20)
    assert bl.trim_to_sound(loud, 2.0, 3.0) == (2.0, 3.0)                                                        # continuous sound to the end: nothing to trim
    assert bl.trim_to_sound(x, 36.28, 36.4) == (36.28, 36.4)                                                     # a very short span is never trimmed
    a2, b2 = bl.trim_to_sound(x, 36.28, 37.42)
    assert b2 <= 37.42 and a2 >= 36.28                                                                           # never extends


def test_unsure_words_become_places_to_listen_again_but_listed_confident_short_and_already_loud_ones_do_not():
    """Ryan's DeWalt/Milwaukee cut: a hidden 'ass' sat under 'acting' (0.18 s, probability 0.48), which the loud-and-stretched rule never tried."""
    pats = bl.load_patterns()
    detail = [("acting", 18.28, 18.46, 0.48), ("fine", 18.74, 19.1, 1.00), ("um", 19.2, 19.25, 0.2), ("fuck", 20.0, 20.3, 0.3), ("so", 21.0, 21.2, 0.4), ("hmm", 22.0, 22.4, 0.3)]
    got = bl.unsure_regions(detail, pats, loud=[(21.0, 21.5)])
    assert got == [(18.28, 18.46), (22.0, 22.4)]                 # unsure and long enough; not 'fine' (sure), 'um' (0.05 s), 'fuck' (listed already), or 'so' (inside a loud stretch)
    assert bl.unsure_regions(None, pats, []) == []


def test_a_word_revealed_under_only_one_way_of_silencing_is_not_kept_for_an_unsure_word_but_is_kept_when_two_agree(tmp_path):
    pats = bl.load_patterns()
    speech = np.zeros(16000 * 6)

    def words_after(n_hits):
        calls = {"n": 0}

        def words_of(_wav):
            calls["n"] += 1
            return [("ass", 2.2, 2.4)] if calls["n"] <= n_hits else [("acting", 2.2, 2.4)]
        return words_of
    one = bl.reveal(speech, [(2.28, 2.43)], words_after(1), pats, tmp_path)
    two = bl.reveal(speech, [(2.28, 2.43)], words_after(2), pats, tmp_path)
    assert [r["votes"] for r in one] == [1] and [r["votes"] for r in two] == [2]
    assert bl.REVEAL_UNSURE_VOTES == 2
