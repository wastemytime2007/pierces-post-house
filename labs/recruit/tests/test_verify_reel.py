import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import verify_reel as vr


def test_a_cut_passes_when_its_opening_and_closing_phrases_are_found_at_its_edges():
    ok, before, after, r1, r2 = vr.edge_check("I am also very loyal to my guys. We have a few incidents. I will stand up to them, even if they're not.",
                                             "I am also very loyal to my guys", "even if they're not")
    assert ok and before == 0 and after == 0 and r1 > 0.9 and r2 > 0.9


def test_one_misheard_word_does_not_fail_a_phrase_but_stray_words_do():
    assert vr.edge_check("I go, you are real. I'm like, yep, I'm here.", "oh, you are real", "yeah, I'm here")[0]                    # 'oh' heard as 'I go'
    ok, before, after, _a, _b = vr.edge_check("Even if they're not, I'm going to stand up for them.", "I am also very loyal to my guys", "even if they're not")
    assert not ok                                                                                    # the opening is not even there
    ok, before, after, _a, _b = vr.edge_check("I am also very loyal to my guys. Even if they're not, I'm going to stand up", "I am also very loyal to my guys", "even if they're not")
    assert not ok and after == 5                                                                     # a cut that ran five words ("I'm going to stand up") into the next sentence
    assert not vr.edge_check("", "a b", "c d")[0]


def test_the_xml_reader_reports_frames_files_and_whether_audio_has_a_sourcetrack(tmp_path):
    x = tmp_path / "r.xml"
    x.write_text('''<?xml version="1.0"?><xmeml version="4"><sequence><name>Reel</name><duration>90</duration><rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate><media>
<video><track><clipitem><name>a.mp4</name><enabled>TRUE</enabled><start>0</start><end>60</end><in>100</in><out>160</out><file id="f1"><name>a.mp4</name><pathurl>file://localhost/x/a%20b.mp4</pathurl></file></clipitem>
<clipitem><name>a.mp4</name><enabled>TRUE</enabled><start>60</start><end>90</end><in>400</in><out>430</out><file id="f1"/></clipitem></track></video>
<audio><track><clipitem><name>a.mp4</name><enabled>TRUE</enabled><start>0</start><end>60</end><in>100</in><out>160</out><file id="f1"/><sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack></clipitem></track></audio></media></sequence></xmeml>''')
    d = vr.read_xml(x)
    assert d["timebase"] == 30 and d["ntsc"] == "TRUE" and d["duration"] == 90
    assert [(v["start"], v["end"], v["in"], v["out"]) for v in d["v"]] == [(0, 60, 100, 160), (60, 90, 400, 430)]
    assert d["v"][1]["file"] == "/x/a b.mp4"                                                         # a later clip refers to the file by id; the path is decoded
    assert d["a"][0]["sourcetrack"] is True and d["v"][0]["file"] == d["a"][0]["file"]


def test_a_cut_at_the_bottom_of_a_pause_or_a_dip_between_words_is_at_a_valley_and_one_in_the_middle_of_a_word_is_not():
    import numpy as np
    sr = vr.sa.SR
    sig = np.full(int(sr * 8), 0.0015, dtype=np.float32)                                # a pause everywhere ...
    sig[int(2.5 * sr):int(5.0 * sr)] = 0.05                                             # ... except speech from 2.5 s to 5.0 s
    a, b, ok = vr.edges_at_valleys(sig, pre=2.2, dur=3.1)                               # starts and ends in the pause around the words
    assert ok and a <= 6.0 and b <= 6.0
    a, b, ok = vr.edges_at_valleys(sig, pre=2.51, dur=2.0)                              # starts 10 ms after the speech began: the onset is clipped
    assert not ok and a > 6.0
    cont = np.full(int(sr * 6), 0.05, dtype=np.float32)                                 # continuous speech with one shallow dip at 3.0 s
    cont[int(2.97 * sr):int(3.03 * sr)] = 0.02
    a, b, ok = vr.edges_at_valleys(cont, pre=3.0, dur=1.5)                              # the cut-in is at the dip
    assert a <= 6.0
