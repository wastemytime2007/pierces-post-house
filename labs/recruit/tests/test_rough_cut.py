import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rough_cut as rc


def _words(text, start=10.0, step=0.4):
    return [(w, start + i * step, start + i * step + 0.3) for i, w in enumerate(text.split())]


def test_a_phrase_is_found_among_the_cameras_words_even_when_one_word_is_heard_differently():
    words = _words("so I made the representation to the buyer and I was too lazy to go check it that's on me")
    i, j, r = rc.find_phrase("I was too lazy to go check it", words)
    assert words[i][0] == "I" and words[j][0] == "it" and r > 0.9
    i, j, r = rc.find_phrase("I was too lazy to go chuck it", words)                                       # a mishearing still lands on the right words
    assert words[i][0] == "I" and words[j][0] == "it"
    assert rc.find_phrase("quarterly tax filing deadlines", words) is None                                 # nothing like it: refused, not guessed


def test_the_closing_words_are_searched_only_after_the_opening():
    words = _words("it is on me and I said it is on me again")
    i, j, _ = rc.find_phrase("it is on me", words)
    a, b, _ = rc.find_phrase("it is on me", words, start_index=j + 1)
    assert a > i and b > j                                                                                  # the second occurrence, not the first again


def _speech(t0, total, words, loud=0.2, quiet=0.001):
    """A signal at rms level `loud` inside each (start, end) in `words` and `quiet` elsewhere; returns the samples."""
    sr = rc.SR
    x = np.full(int(sr * total), quiet, dtype=np.float32)
    for a, b in words:
        x[int((a - t0) * sr):int((b - t0) * sr)] = loud
    return x


def test_the_cut_ends_where_speech_actually_stops_not_at_a_quiet_spot_inside_the_last_word():
    t0 = 100.0
    # the last word is drawn out: Whisper says it ends at 103.0 but the sound runs to 103.30, with a 60 ms dip (a stop consonant, shorter than a real pause) at 103.00-103.06
    x = _speech(t0, 6, [(101.1, 103.0), (103.06, 103.30)])
    rms, fs = rc.energy(x)
    t_in, t_out = rc.snap(101.1, 103.0, rms, t0, fs)
    assert t_out >= 103.30                                                          # not inside the drawn-out end (a minimum-energy search would have picked the dip)
    assert t_in < 101.1 - 0.05                                                      # a lead before the first word


def test_a_cut_never_runs_into_the_next_word_and_never_back_into_the_previous_one():
    t0 = 100.0
    # words: previous 100.2-100.9, ours 101.0-102.0, next starts 102.15 (a 0.15 s gap: shorter than a quiet run of 0.09 s plus its pad)
    x = _speech(t0, 5, [(100.2, 100.9), (101.0, 102.0), (102.15, 103.0)])
    rms, fs = rc.energy(x)
    t_in, t_out = rc.snap(101.0, 102.0, rms, t0, fs, prev_end=100.9, next_start=102.15)
    assert 102.0 < t_out <= 102.13                                                  # after our last word, before the next one starts
    assert 100.92 <= t_in < 100.99                                                  # after the previous word ends, before ours begins
    t_in2, t_out2 = rc.snap(101.0, 102.0, np.full(len(rms), 0.2, dtype=np.float32), t0, fs, prev_end=100.9, next_start=102.1)
    assert 102.0 < t_out2 <= 102.08 and 100.92 <= t_in2 < 100.99                    # no quiet at all: still outside the words, still inside the neighbours


def test_overlapping_cuts_of_one_source_meet_in_the_gap_between_their_words_and_keep_their_words():
    a = {"id": "A", "role": "first", "source_original": "/s.mp4", "in_sec": 10.0, "out_sec": 14.0, "first_word_at": 10.3, "last_word_end": 13.4}
    b = {"id": "B", "role": "second", "source_original": "/s.mp4", "in_sec": 13.8, "out_sec": 20.0, "first_word_at": 14.0, "last_word_end": 19.5}
    notes = rc.resolve_overlaps([a, b])
    assert len(notes) == 1 and a["out_sec"] <= b["in_sec"] + 1e-6                   # they touch or leave a gap: no repeated source
    assert a["out_sec"] >= a["last_word_end"] and b["in_sec"] <= b["first_word_at"]  # neither lost a word
    c = {"id": "C", "role": "other camera", "source_original": "/other.mp4", "in_sec": 10.0, "out_sec": 14.0, "first_word_at": 10.3, "last_word_end": 13.4}
    assert rc.resolve_overlaps([c, dict(c, id="D", source_original="/another.mp4")]) == []   # different sources never overlap


def test_two_cuts_that_repeat_the_same_words_are_refused_not_trimmed():
    hook = {"id": "H", "role": "Hook", "source_original": "/s.mp4", "in_sec": 50.0, "out_sec": 51.5, "first_word_at": 50.2, "last_word_end": 51.3}
    end = {"id": "E", "role": "Owning it", "source_original": "/s.mp4", "in_sec": 47.0, "out_sec": 51.6, "first_word_at": 47.2, "last_word_end": 51.3}
    with pytest.raises(rc.CutError, match="repeat the same words"):
        rc.resolve_overlaps([hook, end])


def test_frame_rounding_goes_outward_so_a_word_is_never_clipped_by_the_frame_grid():
    t = 12.3456
    assert rc.frame_floor(t) <= t <= rc.frame_ceil(t)
    assert (rc.frame_ceil(t) - rc.frame_floor(t)) * rc.FPS == pytest.approx(1.0, abs=1e-6)                  # a time inside a frame spans exactly one frame
    on_grid = 100 / rc.FPS
    assert rc.frame_floor(on_grid) == pytest.approx(on_grid) and rc.frame_ceil(on_grid) == pytest.approx(on_grid)


def test_a_cut_whose_words_are_not_in_the_moments_transcript_is_refused_with_its_name():
    row = {"text": "we pay our vendors very quickly", "speech_start": 5.0, "speech_end": 9.0, "clip": "c"}
    with pytest.raises(rc.CutError, match="not in that moment's transcript"):
        rc.resolve_cut({"unit": "May15", "id": "X1", "from": "we always pay late", "to": "late"}, row, None, {"c": {"analysis": "/x", "original": "/x"}}, None)


def test_recorder_time_maps_to_camera_time_by_the_sync_offset():
    row = {"speech_start": 1157.0}
    synced = {"synced": True, "camera": "cam", "camera_start": 935.4}
    d = rc.lav_to_camera_delta(row, synced)
    assert d == pytest.approx(-221.6)
    assert 1160.25 + d == pytest.approx(938.65)                                       # a word 3.25 s into the moment on the recorder is 3.25 s after camera_start


def test_the_whole_hard_phrase_case_the_camera_misheard_is_found_on_the_recorders_words():
    recorder = _words("And if you don't have that in your heart it's probably not the right fit to operate. I tell everyone")
    camera = _words("And if you don't have that in your heart it's probably not going to like you. I tell everyone")
    assert rc.find_phrase("not the right fit to operate", recorder) is not None
    assert rc.find_phrase("not the right fit to operate", camera) is None             # why weekend words are timed on the recorder


def test_a_number_with_a_percent_sign_or_a_dollar_sign_is_also_found_in_its_spoken_form():
    assert "a hundred percent" in " ".join(rc.variants("That's 100% my fault"))
    assert "88 dollars" in " ".join(rc.variants("we're burning $88 a day"))
    assert rc.variants("no numbers here") == ["no numbers here"]
    words = _words("okay well I should have checked it that's a hundred percent my fault yes I'm upset")
    i, j, r = rc.find_phrase("That's 100% my fault", words)
    assert words[i][0] == "that's" and words[j][0] == "fault" and r > 0.9


def test_the_first_good_match_wins_over_a_cleaner_one_later():
    words = _words("okay well I should have checked it and that was on me and then later I said go check it again")
    i, j, r = rc.find_phrase("check it", words)
    assert words[i][0] == "checked" and r >= rc.FIRST_OK                                               # the nearest occurrence, though 'check it' later is an exact match
    i2, j2, _ = rc.find_phrase("check it", words, start_index=j + 1)
    assert words[i2][0] == "check" and i2 > i                                                          # asked to look after the first, it finds the second
    assert rc.find_phrase("completely different", words) is None


def test_quiet_is_relative_to_the_recording_so_a_noisy_camera_with_a_small_range_still_finds_its_dips():
    t0 = 0.0
    sr = rc.SR
    rec = np.full(int(sr * 6), 0.05, dtype=np.float32)                       # a camera: speech at about -26 dB, a noise floor at about -44 dB (an 18 dB range)
    rec[int(1.0 * sr):int(2.0 * sr)] = 0.006                                 # a dip between words
    rms, fs = rc.energy(rec)
    thr = rc.quiet_threshold(rms, int(2.0 / fs), int(5.0 / fs))
    assert 0.006 < thr < 0.05                                                # the dip counts as quiet, the speech does not
    rec2 = np.full(int(sr * 6), 0.02, dtype=np.float32)
    rec2[int(1.0 * sr):int(2.0 * sr)] = 0.0014                               # a chest-worn recorder: silence at -57 dB under speech at -34 dB
    r2, _ = rc.energy(rec2)
    thr2 = rc.quiet_threshold(r2, int(2.0 / fs), int(5.0 / fs))
    assert 0.0014 < thr2 < 0.01 and thr2 < thr                               # a cleaner recording gets a stricter bar


def test_snap_reports_the_quiet_intervals_so_a_shared_boundary_can_go_in_the_silence():
    t0 = 100.0
    x = _speech(t0, 5, [(100.0, 100.9), (101.1, 102.0)])                     # a 0.2 s silence between two words
    rms, fs = rc.energy(x)
    d = rc.snap_detail(101.1, 102.0, rms, t0, fs, prev_end=100.9)
    lo, hi = d["lead_quiet"]
    assert lo >= 100.88 and hi <= 101.12 and hi - lo >= 0.15                               # the whole silence before our first word
    assert d["tail_quiet"] is not None and d["tail_quiet"][0] >= 101.98                    # the silence after our last word begins where the word ends


def test_a_gap_of_only_a_tenth_of_a_second_still_gets_a_boundary_in_the_middle_of_its_silence():
    t0 = 100.0
    x = _speech(t0, 5, [(100.0, 100.90), (101.0, 102.0)])                                  # 0.1 s of silence between words: a 90 ms run could never be found
    rms, fs = rc.energy(x)
    a_det = rc.snap_detail(100.0, 100.90, rms, t0, fs, next_start=101.0)
    b_det = rc.snap_detail(101.0, 102.0, rms, t0, fs, prev_end=100.90)
    a = {"id": "A", "role": "a", "source_original": "/s", "in_sec": 99.9, "out_sec": 101.2, "first_word_at": 100.0, "last_word_end": 100.90, "tail_quiet": list(a_det["tail_quiet"])}
    b = {"id": "B", "role": "b", "source_original": "/s", "in_sec": 100.7, "out_sec": 103.0, "first_word_at": 101.0, "last_word_end": 102.0, "lead_quiet": list(b_det["lead_quiet"])}
    rc.resolve_overlaps([a, b])
    assert 100.92 <= a["out_sec"] <= 100.98 and a["out_sec"] == b["in_sec"]                # in the middle of the tenth of a second of silence


def test_the_shared_boundary_goes_in_the_silence_not_at_the_midpoint_of_the_word_times():
    a = {"id": "A", "role": "first", "source_original": "/s.mp4", "in_sec": 10.0, "out_sec": 12.0, "first_word_at": 10.3, "last_word_end": 11.55, "tail_quiet": [11.60, 11.85]}
    b = {"id": "B", "role": "second", "source_original": "/s.mp4", "in_sec": 11.5, "out_sec": 15.0, "first_word_at": 11.9, "last_word_end": 14.5, "lead_quiet": [11.65, 11.88]}
    rc.resolve_overlaps([a, b])
    assert a["out_sec"] == b["in_sec"] and 11.65 <= a["out_sec"] <= 11.85 and a["out_sec"] >= a["last_word_end"] and b["in_sec"] <= b["first_word_at"]
    assert a["out_sec"] * rc.FPS == pytest.approx(round(a["out_sec"] * rc.FPS), abs=0.01)   # on a whole frame (stored to 0.1 ms)


def test_continuous_speech_is_cut_in_the_valley_between_words_not_on_a_word_timestamp():
    t0 = 100.0
    sr = rc.SR
    x = np.full(int(sr * 6), 0.2, dtype=np.float32)                                    # speech all the way through, no silence anywhere ...
    x[int(2.62 * sr):int(2.70 * sr)] = 0.07                                            # ... but a shallow dip (-9 dB) between 'two' and "we're" at 102.62-102.70
    rms, fs = rc.energy(x)
    d = rc.snap_detail(102.80, 105.0, rms, t0, fs, prev_end=102.45)                    # Whisper puts the first word's start at 102.80, later than where it really begins
    assert 102.60 <= d["in"] <= 102.72                                                 # the cut-in goes in the dip, not at 102.80 minus a fixed amount


def test_text_a_recogniser_invents_over_trailing_silence_is_dropped():
    assert rc.doubted({"no_speech_prob": 0.9, "avg_logprob": -1.4}) is True
    assert rc.doubted({"no_speech_prob": 0.9, "avg_logprob": -0.4}) is False           # unsure it is speech, but sure of the words: kept
    assert rc.doubted({"no_speech_prob": 0.1, "avg_logprob": -1.4}) is False
    assert rc.doubted({}) is False


def _tone(sr, t0, t1, n_total, amp=0.2):
    import numpy as np
    x = np.zeros(int(n_total * sr))
    t = np.arange(int(t0 * sr), int(t1 * sr)) / sr
    x[int(t0 * sr):int(t1 * sr)] = amp * np.sin(2 * np.pi * 220 * t)
    return x


def test_a_word_timed_across_a_pause_starts_where_the_speech_does_not_where_the_timestamp_says():
    """Whisper timed 'if' from the end of 'is' (0.40 s) to its own end (1.80 s); the recording is silent from 0.45 to 1.50 s and the word is at 1.50. A cut placed from the timestamp begins a second early."""
    import numpy as np
    sr = rc.SR
    x = _tone(sr, 0.20, 0.40, 3.0) + _tone(sr, 1.50, 1.80, 3.0) + _tone(sr, 2.0, 2.4, 3.0)
    x += np.random.default_rng(0).standard_normal(len(x)) * 0.0005
    rms, fs = rc.energy(x, sr)
    first = ("if", 0.40, 1.80)                                  # stretched across the pause
    last = ("know", 2.0, 2.4)
    f_start, l_end = rc.refine_edges(first, last, rms, 0.0, fs)
    assert 1.45 <= f_start <= 1.56 and l_end == 2.4              # speech resumes at 1.5 s
    d = rc.snap_detail(f_start, l_end, rms, 0.0, fs, prev_end=0.40, next_start=None)
    assert 1.3 <= d["in"] <= 1.5                                 # the cut starts in the pause, just ahead of the word, not at 0.4
    # an ordinary word (no pause inside its span) is left alone
    ok_first = ("if", 1.50, 1.80)
    assert rc.refine_edges(ok_first, last, rms, 0.0, fs)[0] == 1.50


def test_a_last_word_timed_across_a_pause_ends_where_the_speech_does():
    import numpy as np
    sr = rc.SR
    x = _tone(sr, 0.5, 0.9, 4.0) + _tone(sr, 1.0, 1.3, 4.0)       # 'you should' then 'know' is spoken, then silence; Whisper ends 'know' at 3.2 s
    x += np.random.default_rng(1).standard_normal(len(x)) * 0.0005
    rms, fs = rc.energy(x, sr)
    f_start, l_end = rc.refine_edges(("you", 0.5, 0.9), ("know", 1.0, 3.2), rms, 0.0, fs)
    assert f_start == 0.5 and 1.25 <= l_end <= 1.4               # it ends where the sound does, not 1.9 s of silence later


def test_a_trailing_s_is_quiet_by_level_but_it_is_the_word_so_the_cut_ends_after_it():
    """'ass': a vowel at -20 dB, then an /s/ (high-frequency noise) 18 dB under it, then silence, then the next speaker. By level alone the /s/ is silence and the cut ends before it."""
    import numpy as np
    sr = rc.SR
    rng = np.random.default_rng(3)
    n = int(3.0 * sr)
    x = np.convolve(rng.standard_normal(n), np.ones(24) / 24, mode="same") * 0.003    # room noise: low-frequency heavy, as a real room is (1 to 7% of its energy above 3.5 kHz)
    t = np.arange(int(0.6 * sr), int(1.1 * sr)) / sr
    x[int(0.6 * sr):int(1.1 * sr)] += 0.1 * np.sin(2 * np.pi * 180 * t)             # the vowel, 0.6 to 1.1 s
    s_len = int(0.12 * sr)
    noise = rng.standard_normal(s_len)
    hi = np.diff(noise, n=2, prepend=0.0, append=0.0)[:s_len]                       # twice-differenced noise: nearly all its energy is above 3.5 kHz
    x[int(1.1 * sr):int(1.1 * sr) + s_len] += hi / hi.std() * 0.0014                 # the /s/, 1.10 to 1.22 s: a few dB over the room and far under the vowel, as on the real recording (about -38 dB against -41 to -45 and -20)
    x[int(1.6 * sr):int(2.1 * sr)] += 0.1 * np.sin(2 * np.pi * 150 * np.arange(int(0.5 * sr)) / sr)   # the next speaker
    rms, fs = rc.energy(x, sr)
    hf = rc.hf_share(x, sr)
    assert hf[int(1.15 / fs)] > rc.HF_SHARE and hf[int(2.5 / fs)] < 0.1              # the /s/ is high-frequency; the room is not
    lifted = rc.without_fricatives(rms, hf)
    assert lifted[int(1.15 / fs)] == rms.max() and lifted[int(0.8 / fs)] == rms[int(0.8 / fs)]      # fricative frames are lifted; the vowel frames are left alone
    fixed = rc.snap_detail(0.6, 1.1, lifted, 0.0, fs, None, 1.6)                      # Whisper ends 'ass' at the vowel (1.10 s)
    assert 1.22 <= fixed["out"] < 1.58                                                # the cut ends after the /s/ (1.22 s) and before the next speaker (1.6 s)
