"""Hermetic tests for bleeps edited on the review page: the edits replace the automatic spans exactly, the result is measured, and the page is rebuilt
with the editor's data (a live preview with the speech whole, the loudness envelope, the bleep level)."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "review_loop"))
sys.path.insert(0, str(HERE.parents[1] / "safety_net"))

import apply_edits as ae  # noqa: E402
import bleep as bl  # noqa: E402
import layers as ly  # noqa: E402
import timeline  # noqa: E402
from build_review import build  # noqa: E402
from test_bleep import cut_media, xml  # noqa: E402,F401  (the synthetic cut and its fixture)

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
WORDS = [("Hello", 1.0, 1.4), ("Shit,", 12.4, 12.9), ("ok", 25.0, 25.3)]


@pytest.fixture()
def auto(xml, tmp_path):
    """The automatic result: one bleep over the 'Shit,' at 12.32-13.02 (the word at 12.4-12.9 padded 0.08 s before and 0.12 s after)."""
    r = bl.bleep(xml, tmp_path / "auto", words_of=lambda w: WORDS, check_transcript=False)
    assert len(r["spans"]) == 1
    return Path(r["xml"])


def _edits(tmp_path, spans):
    p = tmp_path / "bleep_edits.json"
    p.write_text(json.dumps({"schema": "bleep_edits.v0", "spans": [{"start": a, "end": b} for a, b in spans]}))
    return p


def _bleeps(xml_path):
    return sorted((round(l.start, 2), round(l.end, 2)) for l in ly.find_layers(xml_path) if ly.lane_name(l) == "Bleep")


def test_the_edited_spans_replace_the_automatic_ones_exactly_and_are_measured(auto, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    r = ae.apply(auto, _edits(tmp_path, [(12.7, 13.3), (20.0, 20.4)]), out)
    assert [(round(a, 2), round(b, 2)) for a, b in r["spans"]] == [(12.7, 13.3), (20.0, 20.4)]
    got = _bleeps(r["xml"])
    fps = timeline.load_cut(r["xml"]).fps
    assert len(got) == 2 and abs(got[0][0] - 12.7) <= 1 / fps and abs(got[0][1] - 13.3) <= 1 / fps and abs(got[1][0] - 20.0) <= 1 / fps     # to the nearest frame
    assert all(ok is not False for _n, ok, _d in r["rows"]) and {"SPANS-SILENT", "OUTSIDE-UNCHANGED", "ONLY-THE-SPANS-LOST"} <= {n for n, _o, _d in r["rows"]}
    assert not any(abs(a - 12.32) < 0.05 for a, _b in got)                                            # the automatic (padded) bleep is gone, not merged back in
    muted = bl.muted_spans_in(r["xml"])
    assert [(round(a, 1), round(b, 1)) for a, b in muted] == [(12.7, 13.3), (20.0, 20.4)]             # the speech is silent exactly where the bleeps are


def test_an_empty_edit_takes_every_bleep_out_and_the_speech_comes_back_whole(auto, xml, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    r = ae.apply(auto, _edits(tmp_path, []), out)
    assert _bleeps(r["xml"]) == [] and bl.muted_spans_in(r["xml"]) == []
    before, after = timeline.load_cut(xml), timeline.load_cut(r["xml"])
    assert sum(a.tl_end - a.tl_start for a in after.audio) == pytest.approx(sum(a.tl_end - a.tl_start for a in before.audio), abs=0.01)


def test_spans_outside_the_cut_or_too_short_are_refused_with_the_reason(auto, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(ae.EditError, match="outside the cut"):
        ae.apply(auto, _edits(tmp_path, [(200.0, 201.0)]), out)
    with pytest.raises(ae.EditError, match="shorter than 0.05"):
        ae.apply(auto, _edits(tmp_path, [(12.0, 12.04)]), out)
    assert not (out / auto.name).exists()                                                             # nothing is written when it refuses


def test_the_page_carries_the_editor_data_a_live_preview_and_a_script_that_parses(auto, tmp_path):
    page = build(auto, tmp_path / "rv", height=180)
    d = json.loads(re.search(r"const DATA = (\{.*?\});\n", page.read_text(), re.S).group(1))
    assert d["preview_live"] == "preview_live.mp4" and (tmp_path / "rv" / "preview_live.mp4").exists() and (tmp_path / "rv" / "preview_live_clean.mp4").exists()
    assert [(round(b["start"], 1), round(b["end"], 1)) for b in d["live_bleeps"]] == [(12.3, 13.0)]
    assert d["envelope_hz"] == 100 and abs(len(d["envelope"]) - d["duration"] * 100) < 3 and max(d["envelope"]) > 20
    assert 0.02 < d["bleep_level"] < 0.5 and d["xml"] == str(auto)
    # in the live preview the speech is whole where the bleep is; in the baked one it is the tone
    def rms(p, a, b):
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(a), "-t", str(b - a), "-i", str(tmp_path / "rv" / p), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], capture_output=True)
        x = np.frombuffer(r.stdout, dtype=np.float32).astype(float)
        sp = np.abs(np.fft.rfft(x * np.hanning(len(x))))
        hz = np.fft.rfftfreq(len(x), 1 / 16000)
        return float(np.sqrt((x ** 2).mean())), float(sp[(hz > 950) & (hz < 1050)].sum() / sp.sum())
    live_level, live_tone = rms("preview_live.mp4", 12.6, 12.9)
    baked_level, baked_tone = rms("preview_full.mp4", 12.6, 12.9)
    assert live_tone < 0.2 and baked_tone > 0.9                                                       # noise-like speech against a pure 1 kHz tone
    js = re.search(r"<script>(.*?)</script>", page.read_text(), re.S).group(1)
    f = tmp_path / "page.js"
    f.write_text(js)
    if shutil.which("node"):
        r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[:300]
    assert "id=\"bleepPanel\"" in page.read_text() or "bleepPanel" in page.read_text()


def test_a_page_with_no_bleeps_has_no_editor_and_no_extra_preview(xml, tmp_path):
    page = build(xml, tmp_path / "plain", height=180)
    d = json.loads(re.search(r"const DATA = (\{.*?\});\n", page.read_text(), re.S).group(1))
    assert "preview_live" not in d and "live_bleeps" not in d and not (tmp_path / "plain" / "preview_live.mp4").exists()


def test_a_cut_with_no_bleeps_can_still_be_opened_for_editing_so_missed_ones_can_be_added(xml, tmp_path):
    page = build(xml, tmp_path / "e", height=180, editable_bleeps=True)
    d = json.loads(re.search(r"const DATA = (\{.*?\});\n", page.read_text(), re.S).group(1))
    assert d["live_bleeps"] == [] and d["preview_live"] == "preview_live.mp4" and (tmp_path / "e" / "preview_live.mp4").exists()
    assert d["envelope_hz"] == 100 and d["bleep_level"] > 0.02 and d["xml"] == str(xml)               # the level an automatic bleep would have had, and the path apply_edits needs
    edits = tmp_path / "e.json"
    edits.write_text(json.dumps({"sequence": "Cut A", "spans": [{"start": 12.7, "end": 13.3}]}))
    out = tmp_path / "out"
    out.mkdir()
    before = {"origin": "automatic", "spans": [], "hits": [], "words": [["what", 12.4, 13.1, 0.9]], "loud": [[12.5, 12.9]]}
    (tmp_path / "bleep.json").write_text(json.dumps(before))                                           # the automatic scan found nothing; the bleep he adds is a missed word
    r = ae.apply(xml, edits, out, auto_json=tmp_path / "bleep.json")
    assert [x["kind"] for x in r["learned"]["records"]] == ["added"] and r["learned"]["records"][0]["context"]["in_loud_stretch"] is True


def test_the_page_says_what_the_scan_did_so_no_bleeps_is_not_mistaken_for_a_failed_scan(xml, tmp_path):
    scan = tmp_path / "bleep.json"
    scan.write_text(json.dumps({"words_heard": 155, "spans": [], "hits": [], "words": [], "suspects": [{"word": "So,", "word_start": 28.3, "word_end": 28.84, "tier": "likely"}]}))
    page = build(xml, tmp_path / "s", height=180, editable_bleeps=True, scan_json=scan)
    d = json.loads(re.search(r"const DATA = (\{.*?\});\n", page.read_text(), re.S).group(1))
    assert d["scan"] == {"words_heard": 155, "bleeped": 0, "listed_found": 0, "flagged": [{"start": 28.3, "end": 28.84, "word": "So,", "tier": "likely"}]}
    assert "found no curse words, so there are no bleeps yet" in page.read_text()                              # the wording the page shows
    assert "scan" not in json.loads(re.search(r"const DATA = (\{.*?\});\n", build(xml, tmp_path / "t", height=180, editable_bleeps=True).read_text(), re.S).group(1))   # no scan record, no note
