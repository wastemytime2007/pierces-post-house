import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_selects as bs
import find_moments as fm


def _srt(path: Path, rows):
    def t(x):
        return f"{int(x // 3600):02d}:{int(x % 3600 // 60):02d}:{x % 60:06.3f}".replace(".", ",")
    path.write_text("".join(f"{i}\n{t(a)} --> {t(b)}\n{txt}\n\n" for i, (a, b, txt) in enumerate(rows, 1)))


@pytest.fixture
def clips(tmp_path):
    _srt(tmp_path / "clipA.srt", [(0, 5, "So tell me about the job."), (5, 12, "I need bodies that are willing to work."), (12, 20, "If you are just here for a paycheck please go find a job."),
                                  (20, 30, "I am looking for people that are looking for a career."), (30, 40, "Stay here, retire here.")])
    _srt(tmp_path / "clipB.srt", [(0, 6, "Completely unrelated talk about the weather."), (6, 12, "More talk about lunch.")])
    return fm.load_clips(tmp_path)


def test_srt_is_read_with_times_and_text(clips):
    assert [round(s["end"], 1) for s in clips["clipA"]][:2] == [5.0, 12.0] and clips["clipA"][1]["text"].startswith("I need bodies")
    assert sorted(clips) == ["clipA", "clipB"]


def test_a_moment_is_found_by_approximate_anchors_with_the_transcripts_own_words_and_the_clip_it_is_in(clips):
    m = {"id": "S1", "audience": "x", "start_anchor": "I need bodies that are willing to work", "end_anchor": "looking for people looking for a career"}
    r = fm.locate(m, clips, pad=1.0)
    assert r["found"] and r["clip"] == "clipA" and r["speech_start"] == 5.0 and r["speech_end"] == 30.0 and r["start"] == 4.0 and r["end"] == 31.0
    assert r["text"].startswith("I need bodies") and r["text"].endswith("a career.")                       # copied from the transcript, not retyped


def test_a_moment_is_never_guessed_missing_start_missing_end_end_before_start_or_too_long_all_say_why(clips):
    base = {"id": "X", "audience": "x"}
    r = fm.locate({**base, "start_anchor": "quarterly tax filing deadlines", "end_anchor": "a career"}, clips)
    assert not r["found"] and "start words were not found" in r["reason"]
    r = fm.locate({**base, "start_anchor": "I need bodies that are willing to work", "end_anchor": "zebras crossing the highway at midnight"}, clips)
    assert not r["found"] and "end words were not found" in r["reason"]
    r = fm.locate({**base, "start_anchor": "Stay here, retire here", "end_anchor": "tell me about the job"}, clips)        # the end is before the start
    assert not r["found"]
    long_ = {"clipL": [{"start": 0, "end": 5, "text": "the opening words of the moment"}, {"start": 400, "end": 405, "text": "and these are the closing words of it"}]}
    r = fm.locate({**base, "start_anchor": "the opening words of the moment", "end_anchor": "the closing words of it"}, long_)
    assert not r["found"] and "over the" in r["reason"]


def test_a_headline_is_timed_to_the_segment_it_begins_in_and_is_none_if_it_is_not_in_the_transcript(clips):
    segs = clips["clipA"]
    assert bs.headline_time("I am looking for people that are looking for a career.", segs) == 20.0
    assert bs.headline_time("a paycheck please go find a job", segs) == 12.0
    assert bs.headline_time("a line nobody said", segs) is None


def test_the_log_lines_are_in_the_shape_the_verifiers_parser_reads_a_claim_not_a_source_declaration():
    spec = Path.home() / ".claude" / "skills" / "verified-quotes" / "scripts" / "verify_quotes.py"
    if not spec.exists():
        pytest.skip("the verified-quotes skill is not installed here")
    mod = importlib.util.spec_from_file_location("vq", spec)
    vq = importlib.util.module_from_spec(mod)
    mod.loader.exec_module(vq)
    text = "\n".join(bs.claim_lines("clipA", 20.0, "I am looking for people that are looking for a career.") + bs.claim_lines("clipB", 7.0, "More talk about lunch."))
    claims = vq.parse_log_text(text)
    assert [c[3] for c in claims] == ["I am looking for people that are looking for a career.", "More talk about lunch."]
    # the shape that LOOKED right and silently checked nothing: the file named on the claim line itself
    assert vq.parse_log_text('- [00:00:20] `clipA.srt`: "I am looking for people"') == []


def test_a_moment_is_marked_used_only_when_the_finished_video_really_says_it(tmp_path):
    final = tmp_path / "final.srt"
    _srt(final, [(0, 5, "We never have a rain day."), (5, 9, "I am looking for people that are looking for a career.")])
    assert bs.used_in(final, "I am looking for people that are looking for a career.", "x") == "the finished video says this line"
    assert bs.used_in(final, "nothing like it", "well we never have a rain day i am looking for people that are looking for") == "the finished video uses a passage from this moment"
    assert bs.used_in(final, "an unrelated headline", "completely different words about weather and lunch and other things entirely") is None
    assert bs.used_in(None, "x", "y") is None


def test_a_moment_synced_to_a_camera_is_cut_from_that_camera_at_the_matched_time_and_otherwise_from_its_own_recording():
    r = {"id": "W9", "clip": "wknd_Mitch4", "start": 645.0, "end": 671.0, "speech_start": 646.5, "speech_end": 669.5}
    sources = {"DJI_cam": "/cams/DJI_cam.MP4", "wknd_Mitch4": "/mics/wknd_Mitch4.WAV"}
    synced = {"W9": {"id": "W9", "synced": True, "camera": "DJI_cam", "camera_start": 424.9, "score": 89.9}}
    src, a, b, audio_only, how = bs.choose_media(r, sources, synced)
    assert src == Path("/cams/DJI_cam.MP4") and audio_only is False and "synced by audio" in how
    assert a == pytest.approx(424.9 - 1.5) and b - a == pytest.approx(26.0)                          # same length as the transcript clip, the 1.5 s of padding kept
    src, a, b, audio_only, how = bs.choose_media(r, sources, {"W9": {"id": "W9", "synced": False}})
    assert src == Path("/mics/wknd_Mitch4.WAV") and audio_only is True and (a, b) == (645.0, 671.0) and "no camera match" in how
    assert bs.choose_media(r, {}, synced)[0] is None                                                   # no source file known: nothing is cut
    assert bs.choose_media(r, {"wknd_Mitch4": "/mics/x.WAV"}, None)[4] == ""                         # no sync step run at all: no claim about a camera match


def test_the_speaker_clue_is_worded_as_a_clue_and_unclear_is_said_plainly():
    assert bs.mic_note({"mic": "unclear"}).startswith("Speaker unclear")
    n = bs.mic_note({"mic": "Bob", "mic_gap": 0.33})
    assert "Likely Bob" in n and "0.33" in n and "A clue, not a fact" in n
    assert "only their microphone had it" in bs.mic_note({"mic": "Mitch", "mic_gap": None})
    assert bs.mic_note({}) == ""


def test_the_speaker_clue_names_a_person_only_when_one_microphone_is_clearly_more_confident():
    import pick_mic as pm
    who, gap = pm.choose({"Bob": {"conf": -0.25}, "Mitch": {"conf": -0.61}})
    assert who == "Bob" and gap == pytest.approx(0.36)
    who, gap = pm.choose({"Bob": {"conf": -0.20}, "Mitch": {"conf": -0.21}})
    assert who == "unclear" and gap == pytest.approx(0.01)                                             # too close to call
    assert pm.choose({"Mitch": {"conf": -0.2}}) == ("Mitch", None)                                      # only one person's mic had it
    assert pm.choose({"Bob": {"conf": None}}) == ("unclear", None)


def test_clean_transcription_drops_silence_hallucinations_and_third_repeats_but_keeps_real_speech(tmp_path):
    import json
    import transcribe_clean as tc
    segs = [
        {"start": 0, "end": 2, "text": " Real words here.", "no_speech_prob": 0.05, "avg_logprob": -0.3},
        {"start": 2, "end": 4, "text": " Thank you.", "no_speech_prob": 0.9, "avg_logprob": -1.5},                 # Whisper itself thinks this was silence
        {"start": 4, "end": 6, "text": " Quiet but real.", "no_speech_prob": 0.7, "avg_logprob": -0.4},              # unsure it is speech but confident in the words: kept
        {"start": 6, "end": 8, "text": " Same line.", "no_speech_prob": 0.1, "avg_logprob": -0.3},
        {"start": 8, "end": 10, "text": " Same line.", "no_speech_prob": 0.1, "avg_logprob": -0.3},
        {"start": 10, "end": 12, "text": " Same line.", "no_speech_prob": 0.1, "avg_logprob": -0.3},                  # the third time in a row: a loop
        {"start": 12, "end": 14, "text": "   ", "no_speech_prob": 0.1, "avg_logprob": -0.3},
    ]
    kept, dropped = tc.filter_segments(segs)
    assert [s["text"].strip() for s in kept] == ["Real words here.", "Quiet but real.", "Same line.", "Same line."] and dropped == 2
    tc.write_outputs(tmp_path, "clip", "/x/clip.wav", kept, dropped)
    j = json.loads((tmp_path / "clip.json").read_text())
    assert j["dropped_as_silence_or_loop"] == 2 and j["segments"][0]["avg_logprob"] == -0.3                       # the confidence pick_mic.py reads is kept
    srt = (tmp_path / "clip.srt").read_text()
    assert srt.startswith("1\n00:00:00,000 --> 00:00:02,000\nReal words here.")
