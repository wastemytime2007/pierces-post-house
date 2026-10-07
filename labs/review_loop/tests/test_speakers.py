"""Speaker timeline from two recorders."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import speakers as rr  # noqa: E402


def _levels(spec, step=0.1):
    """spec: [(seconds, who)] -> each person's recorder level: the talker 10 dB louder on their own recorder, the other one picking up bleed; 'quiet' = both silent."""
    bob, mitch = [], []
    for sec, who in spec:
        for _ in range(int(round(sec / step))):
            if who == "quiet":
                bob.append(-60.0)
                mitch.append(-60.0)
            else:
                bob.append(-22.0 if who == "Bob" else -32.0)
                mitch.append(-22.0 if who == "Mitch" else -32.0)
    return {"Bob": bob, "Mitch": mitch}


def test_whose_recorder_is_louder_is_who_is_talking_and_a_pause_keeps_the_last_speaker():
    runs = rr.speaker_runs(_levels([(3.0, "Mitch"), (0.5, "quiet"), (1.5, "Bob"), (2.0, "Mitch")]))
    assert [(round(a, 1), round(b, 1), w) for a, b, w in runs] == [(0.0, 3.5, "Mitch"), (3.5, 5.0, "Bob"), (5.0, 7.0, "Mitch")]


def test_a_blip_on_the_other_recorder_does_not_cut_the_picture():
    runs = rr.speaker_runs(_levels([(3.0, "Mitch"), (0.2, "Bob"), (3.0, "Mitch")]))
    assert len(runs) == 1 and runs[0][2] == "Mitch"                                        # 0.2 s is a cough, under the 0.4 s minimum


def test_the_picture_switches_a_little_before_the_new_speaker_on_a_whole_frame_and_the_pieces_cover_the_cut():
    runs = [(0.0, 3.0, "Mitch"), (3.0, 4.5, "Bob"), (4.5, 7.0, "Mitch")]
    pcs = rr.pieces_for_cut(runs, 7.0)
    assert [p[2] for p in pcs] == ["Mitch", "Bob", "Mitch"]
    fps = 30000 / 1001
    assert sum(p[1] for p in pcs) == round(7.0 * fps)                                      # nothing lost, nothing added
    assert abs(pcs[1][0] / fps - (3.0 - rr.SWITCH_LEAD)) < 1 / fps                         # cuts to Bob just before he speaks
    assert rr.pieces_for_cut([(0.0, 5.0, "Mitch")], 5.0) == [(0, round(5.0 * fps), "Mitch")]


# ------------------------------------------------------------------ a weak match never overrides the offset a recorder has everywhere else

def _cands(*rows):
    out = {}
    for person, piece, lav, delta, score in rows:
        out.setdefault((person, piece), []).append((Path(lav), delta, score))
    return out


SPANS = [(100.0, 130.0), (130.0, 160.0), (160.0, 190.0)]
LENGTHS = {Path("Bob2.WAV"): 1800.0, Path("Bob1.WAV"): 1800.0}


def test_a_noise_match_just_over_the_threshold_does_not_beat_the_offset_that_matched_everywhere_else():
    import speakers
    c = _cands(("Bob", 0, "Bob1.WAV", -493.0, 26.4),                       # noise: a quiet listener's recorder, an unrelated file scoring just over the threshold
               ("Bob", 1, "Bob2.WAV", 336.1, 140.0), ("Bob", 2, "Bob2.WAV", 336.1, 150.0))
    got = speakers.choose_segments(c, LENGTHS, SPANS)["Bob"]
    assert [(s[2].name, s[3]) for s in got] == [("Bob2.WAV", 336.1)] * 3                # the first piece uses Bob2 at its offset too


def test_a_sharp_match_is_trusted_even_when_it_differs_from_the_anchor():
    import speakers
    c = _cands(("Mitch", 0, "Mitch3.WAV", 1000.0, 200.0), ("Mitch", 1, "Mitch3.WAV", 1000.0, 180.0), ("Mitch", 2, "Mitch4.WAV", 700.0, 90.0))
    got = speakers.choose_segments(c, {Path("Mitch3.WAV"): 1000.0, Path("Mitch4.WAV"): 1500.0}, SPANS)["Mitch"]
    assert [(s[2].name, s[3]) for s in got] == [("Mitch3.WAV", 1000.0), ("Mitch3.WAV", 1000.0), ("Mitch4.WAV", 700.0)]      # a real hand-over to a second recorder is kept


def test_the_anchor_is_not_used_past_the_end_of_its_file():
    import speakers
    c = _cands(("Mitch", 0, "Mitch3.WAV", 1000.0, 200.0), ("Mitch", 1, "Mitch3.WAV", 1000.0, 180.0), ("Mitch", 2, "Mitch4.WAV", 700.0, 30.0))
    got = speakers.choose_segments(c, {Path("Mitch3.WAV"): 1150.0, Path("Mitch4.WAV"): 1500.0}, SPANS)["Mitch"]      # Mitch3 ends 1150 s into its file: camera 160-190 s would be at 1160-1190
    assert got[2][2].name == "Mitch4.WAV"
