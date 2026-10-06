import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import render_reel as rr


def test_the_person_who_moves_more_is_the_speaker_and_a_close_call_is_unclear():
    assert rr.side_from_motion(1.0, 5.0) == "right"
    assert rr.side_from_motion(4.0, 1.5) == "left"
    assert rr.side_from_motion(2.0, 2.2) == "unclear"


def test_the_vertical_crop_is_nine_by_sixteen_inside_the_frame_and_follows_the_subject():
    w, h, x, y = rr.crop_box(3840, 2160, 0.665, tight=False)
    assert (w, h, y) == (1216, 2160, 0) and abs(w / h - 9 / 16) < 0.002 and abs((x + w / 2) - 0.665 * 3840) < 2
    w, h, x, y = rr.crop_box(3840, 2160, 0.665, tight=True)
    assert h < 2160 and abs(w / h - 9 / 16) < 0.003 and y + h <= 2160                      # the punched-in crop is smaller and still 9:16
    _w, _h, x, _y = rr.crop_box(3840, 2160, 0.99, tight=False)
    assert x + _w == 3840                                                                  # a subject at the edge cannot push the crop out of the frame


def test_a_label_is_built_a_word_at_a_time_and_never_doubles_up():
    states = rr.label_states("What he told the buyer", 10.0, 14.0)
    assert len(states) == 5
    windows = [(a, b) for _img, a, b in states]
    assert all(b > a for a, b in windows) and all(windows[i][1] <= windows[i + 1][0] + 1e-9 for i in range(len(windows) - 1))
    assert windows[-1][1] < 14.0                                                           # it clears before the next beat starts
    assert all(img.size == (rr.OUT_W, rr.OUT_H) for img, _a, _b in states)


def test_the_title_card_builds_in_three_steps_with_no_overlap():
    states = rr.title_states("THE SUB SAID", "DONE", "(THE BACKYARD DISAGREED)", 1.4, 4.1)
    assert [round(a, 2) for _i, a, _b in states] == [1.4, 1.85, 2.45] and states[-1][2] == 4.1


def test_the_music_bed_gain_puts_it_a_set_distance_under_the_voice():
    assert rr.music_gain_db(-15.0, -20.0, under=6.0) == -1.0                               # voice -15, music -20, want -21: turn it down 1 dB
    assert rr.music_gain_db(-15.0, -30.0, under=6.0) == 9.0
