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
