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
