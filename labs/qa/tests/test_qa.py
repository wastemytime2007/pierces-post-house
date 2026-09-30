"""Hermetic tests for the QA pass. Synthetic media only."""
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
LABS = HERE.parent
for sub in ("review_loop", "overlay", "audio"):
    sys.path.insert(0, str(LABS / sub))
sys.path.insert(0, str(LABS.parent / "safety_net"))
sys.path.insert(0, str(HERE))

import apply_ops  # noqa: E402
import place_overlay as po  # noqa: E402
import qa_pass as qa  # noqa: E402
import timeline  # noqa: E402

_spec = importlib.util.spec_from_file_location("rl_tests", LABS / "review_loop" / "tests" / "test_revise.py")
rl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rl)

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _built(tmp_path, media_pair, ops, notes):
    vid, lav = media_pair
    old_xml = rl.make_xml(tmp_path / "old.xml", vid, lav)
    old = timeline.load_cut(old_xml)
    new_xml = tmp_path / "new.xml"
    changes, _delta = apply_ops.apply_ops(old_xml, new_xml, old, ops, notes)
    items = [{"note": c.note, "note_time": notes[c.note - 1]["timeline_sec"], "applied": c.applied, "summary": c.summary, "v2_time": c.v2_time,
              "why": c.why, "op": c.op, "removed": [c.removed[0], c.removed[1]] if c.removed else None,
              "extended_sec": c.check.get("ext") if c.check and c.check.get("kind") == "quiet_at" else None} for c in changes]
    return old_xml, old, new_xml, timeline.load_cut(new_xml), items


@pytest.fixture(scope="module")
def pair(tmp_path_factory):
    d = tmp_path_factory.mktemp("qa_media")
    import subprocess
    lav, vid, cam = d / "lav.wav", d / "a.mp4", d / "cam.wav"
    ff = lambda *a: subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True)
    ff("-f", "lavfi", "-i", "anoisesrc=d=60:c=white:r=48000:a=0.5", "-af", "volume=enable='between(t,10,12)':volume=0", str(lav))
    ff("-ss", "5.005", "-i", str(lav), str(cam))
    ff("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30", "-i", str(cam), "-t", "55", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(vid))
    return vid, lav


def _run(tmp_path, pair, ops, notes, **kw):
    old_xml, old, new_xml, new, items = _built(tmp_path, pair, ops, notes)
    return qa.qa(notes, ops, kw.pop("items", items), old, new, kw.pop("after_xml", new_xml), tmp_path / "qa", **kw), items, old_xml, new_xml


def test_a_tightened_pause_is_verified_by_measuring_it_before_and_after(tmp_path, pair):
    notes = [{"timeline_sec": 5.5, "text": "awkward pause, tighten it"}]
    (results, glob, unreq, report), *_ = _run(tmp_path, pair, [{"note": 1, "op": "tighten_pause", "at": 5.5, "why": "x"}], notes)
    r = results[0]
    assert r.status == qa.VERIFIED, [x.detail for x in r.rows]
    d = r.rows[0].detail
    assert "gone from the new one" in d and "measured" in d and "no silence of 0.25s or more remains" in d
    assert unreq == [] and all(ok is not False for _n, ok, _d in glob)


def test_a_removed_range_and_a_trim_are_verified_and_nothing_unasked_changed(tmp_path, pair):
    notes = [{"timeline_sec": 3.5, "text": "cut 3 to 5 seconds"}, {"timeline_sec": 15.0, "text": "trim 2 seconds off the start of this clip"}]
    ops = [{"note": 1, "op": "remove_range", "start": 3.0, "end": 5.0}, {"note": 2, "op": "trim_start", "clip": 2, "seconds": 2.0}]
    (results, glob, unreq, _rep), *_ = _run(tmp_path, pair, ops, notes)
    assert [r.status for r in results] == [qa.VERIFIED, qa.VERIFIED], [[x.detail for x in r.rows] for r in results]
    assert "now starts 2.00s later" in results[1].rows[0].detail
    assert unreq == []


def test_a_ledger_that_claims_it_applied_something_the_new_file_does_not_contain_is_a_failure(tmp_path, pair):
    notes = [{"timeline_sec": 3.5, "text": "cut 3 to 5 seconds"}]
    ops = [{"note": 1, "op": "remove_range", "start": 3.0, "end": 5.0}]
    old_xml, old, new_xml, new, items = _built(tmp_path, pair, ops, notes)
    assert items[0]["applied"]                                            # the ledger says it was done...
    results, *_ = qa.qa(notes, ops, items, old, old, old_xml, tmp_path / "qa")   # ...but the "new" version is the old one
    assert results[0].status == qa.FAILED and "still in the new cut" in results[0].rows[0].detail


def test_a_removal_that_should_have_been_a_shorter_cut_is_caught_when_the_pause_is_still_there(tmp_path, pair):
    notes = [{"timeline_sec": 5.5, "text": "tighten the pause"}]
    ops = [{"note": 1, "op": "tighten_pause", "at": 5.5}]
    old_xml, old, new_xml, new, items = _built(tmp_path, pair, ops, notes)
    items[0]["removed"] = None                                            # a record with no span: only the measurement remains
    results, *_ = qa.qa(notes, ops, items, old, old, old_xml, tmp_path / "qa")
    assert results[0].status == qa.FAILED and "measured" in results[0].rows[0].detail      # the silence is still 2.00s on the "new" version


def test_unsupported_notes_are_reported_not_done_with_their_reason(tmp_path, pair):
    notes = [{"timeline_sec": 8.0, "text": "make it warmer"}, {"timeline_sec": 9.0, "text": "something with no operation"}]
    ops = [{"note": 1, "op": "unsupported", "reason": "colour needs a grade, which this cannot do"}]
    old_xml, old, new_xml, new, items = _built(tmp_path, pair, [{"note": 1, "op": "remove_range", "start": 3.0, "end": 5.0}], [{"timeline_sec": 4.0, "text": "cut 3 to 5"}])
    results, *_ = qa.qa(notes, ops, [], old, new, new_xml, tmp_path / "qa")
    assert [r.status for r in results] == [qa.NOT_DONE, qa.NOT_DONE]
    assert "colour needs a grade" in results[0].rows[0].detail and results[1].rows == []


def test_changes_nobody_asked_for_are_listed(tmp_path, pair):
    notes = [{"timeline_sec": 3.5, "text": "cut 3 to 5 seconds"}, {"timeline_sec": 20.5, "text": "cut 20 to 21 seconds"}]
    ops = [{"note": 1, "op": "remove_range", "start": 3.0, "end": 5.0}, {"note": 2, "op": "remove_range", "start": 20.0, "end": 21.0}]
    old_xml, old, new_xml, new, items = _built(tmp_path, pair, ops, notes)
    (results, glob, unreq, _r) = qa.qa(notes, ops, items[:1], old, new, new_xml, tmp_path / "qa")           # the second removal is not in the record
    assert len(unreq) >= 1 and "left the cut with no note asking for it" in unreq[0]
    (results, glob, unreq, _r) = qa.qa(notes, ops, items, old, new, new_xml, tmp_path / "qa2")
    assert unreq == []                                                                                      # attributed once it is in the record


def test_an_old_record_without_removed_spans_is_unmeasured_and_says_to_rerun(tmp_path, pair):
    notes = [{"timeline_sec": 3.5, "text": "cut 3 to 5 seconds"}]
    ops = [{"note": 1, "op": "remove_range", "start": 3.0, "end": 5.0}]
    old_xml, old, new_xml, new, items = _built(tmp_path, pair, ops, notes)
    for i in items:
        i.pop("removed"); i.pop("extended_sec"); i.pop("op")
    results, glob, unreq, _r = qa.qa(notes, ops, items, old, new, new_xml, tmp_path / "qa")
    assert results[0].status == qa.UNMEASURED and "re-run revise.py" in results[0].rows[0].detail
    assert unreq == []


def test_the_report_has_a_before_and_after_frame_per_note_and_notes_the_missing_frame(tmp_path, pair):
    notes = [{"timeline_sec": 15.0, "text": "cut 15 to 16 seconds, dead air"}, {"timeline_sec": 15.5, "text": "and the same spot"}]
    ops = [{"note": 1, "op": "remove_range", "start": 15.0, "end": 16.0}, {"note": 2, "op": "unsupported", "reason": "x"}]
    (results, glob, unreq, report), *_ = _run(tmp_path, pair, ops, notes)
    page = report.read_text()
    assert page.count("data:image/jpeg;base64,") >= 2 and "before, 00:15.0" in page and "cut 15 to 16 seconds" in page
    assert results[0].t_new is not None or "not in the new version" in page
    assert results[1].t_new is None and "the frame this note was left on is not in the new version" in page   # note 2 sat inside the removed second
    assert (tmp_path / "qa" / "qa.json").exists() and (tmp_path / "qa" / "frames" / "note1_before.jpg").exists()


def test_a_note_with_a_drawing_is_done_by_a_callout_on_its_frame_and_a_dropped_callout_is_not_done(tmp_path, pair):
    vid, lav = pair
    base = rl.make_xml(tmp_path / "old.xml", vid, lav)
    import subprocess
    f = tmp_path / "ov"
    f.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red@0.8:s=320x60:r=60000/1001:d=2,format=yuva444p10le,pad=320:180:0:0:color=black@0.0",
                    "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", str(f / "overlay.mov")], check=True)
    (f / "placement.json").write_text(json.dumps({"overlay_path": str(f / "overlay.mov"), "duration_sec": 2.0, "anchor": {"source": "a.mp4", "source_sec": 22.0, "lead_sec": 0.5}}))
    layered = tmp_path / "layered.xml"
    po.place(base, layered, f)
    old, new = timeline.load_cut(base), timeline.load_cut(layered)
    note = {"timeline_sec": 12.0, "source": "a.mp4", "source_sec": 22.0, "shapes": [{"type": "arrow"}], "text": "put an arrow here"}
    ops = [{"note": 1, "op": "unsupported", "reason": "adding graphics is not something the timeline step can do"}]
    results, *_ = qa.qa([note], ops, [], old, new, layered, tmp_path / "qa", overlays=[f])
    assert results[0].status == qa.VERIFIED and results[0].rows[0].op == "callout" and "placed on this note's frame" in results[0].rows[0].detail
    results, *_ = qa.qa([note], ops, [], old, new, base, tmp_path / "qa2", overlays=[f])                    # the callout is not on the new XML
    assert results[0].status == qa.NOT_DONE and "dropped" in results[0].rows[0].detail


def test_a_seam_claim_needs_a_cut_at_that_time_before_any_words_are_trusted(tmp_path, pair):
    notes = [{"timeline_sec": 12.0, "text": "clip 2 should start at the stepping words"}]
    ops = [{"note": 1, "op": "start_at_words", "clip": 2, "words": "stepping words"}]
    old_xml = rl.make_xml(tmp_path / "old.xml", *pair)
    old = timeline.load_cut(old_xml)
    seam = old.video[1].tl_start                                     # a real clip boundary
    item = {"note": 1, "applied": True, "v2_time": seam, "summary": "x", "op": "start_at_words", "removed": None, "extended_sec": None}
    r, *_ = qa.qa(notes, ops, [item], old, old, old_xml, tmp_path / "qa")
    assert r[0].status == qa.UNMEASURED and "a clip starts at the seam" in r[0].rows[0].detail      # a cut is there; no preview to listen to
    item["v2_time"] = seam + 1.3                                     # mid-clip: the same words could be spoken here, but there is no cut
    r, *_ = qa.qa(notes, ops, [item], old, old, old_xml, tmp_path / "qa2")
    assert r[0].status == qa.FAILED and "no clip starts at the seam time" in r[0].rows[0].detail


def test_the_worst_row_decides_a_notes_status(tmp_path, pair):
    r = qa.NoteResult(1, 1.0, "x", rows=[qa.Row(1, "a", qa.VERIFIED, ""), qa.Row(1, "b", qa.NOT_DONE, ""), qa.Row(1, "c", qa.UNMEASURED, "")])
    assert r.status == qa.NOT_DONE
    r.rows.append(qa.Row(1, "d", qa.FAILED, ""))
    assert r.status == qa.FAILED
    assert qa.NoteResult(2, 1.0, "y").status == qa.NOT_DONE                                                 # no rows at all: nothing was done


def _callout(root: Path, name: str, secs: float) -> Path:
    import subprocess
    f = root / name
    f.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=red@0.8:s=320x60:r=60000/1001:d={secs},format=yuva444p10le,pad=320:180:0:0:color=black@0.0",
                    "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", str(f / "overlay.mov")], check=True)
    (f / "placement.json").write_text(json.dumps({"overlay_path": str(f / "overlay.mov"), "duration_sec": secs,
                                                  "anchor": {"source": "a.mp4", "source_sec": 22.0, "lead_sec": 0.5}}))
    return f


def test_a_longer_callout_is_verified_by_its_time_on_screen_before_and_after(tmp_path, pair):
    base = rl.make_xml(tmp_path / "old.xml", *pair)
    short, long_ = _callout(tmp_path, "short", 2.0), _callout(tmp_path, "long", 3.5)
    before, after = tmp_path / "before_v3.xml", tmp_path / "after_v3.xml"
    po.place(base, before, short)
    po.place(base, after, long_)
    old, new = timeline.load_cut(before), timeline.load_cut(after)
    t = timeline.load_cut(before).video[1].tl_start + 2.5                                     # while the callout is up
    note = {"timeline_sec": t, "text": "keep the bubble up longer"}
    for seconds, want in ((None, qa.VERIFIED), (1.5, qa.VERIFIED), (3.0, qa.FAILED)):
        ops = [{"note": 1, "op": "extend_graphic", "seconds": seconds}]
        results, *_ = qa.qa([note], ops, [{"note": 1, "applied": False, "summary": "graphics step"}], old, new, after, tmp_path / f"qa{seconds}", before_xml=before)
        assert results[0].status == want, (seconds, results[0].rows[0].detail)
    assert "on screen 3.50s, was 2.00s (+1.50s" in results[0].rows[0].detail and "asked for +3.00s" in results[0].rows[0].detail


def test_an_unchanged_callout_fails_and_a_missing_before_version_is_unmeasured(tmp_path, pair):
    base = rl.make_xml(tmp_path / "old.xml", *pair)
    short = _callout(tmp_path, "short", 2.0)
    same = tmp_path / "same_v3.xml"
    po.place(base, same, short)
    cut = timeline.load_cut(same)
    note = {"timeline_sec": cut.video[1].tl_start + 2.5, "text": "keep the bubble up longer"}
    ops = [{"note": 1, "op": "extend_graphic", "seconds": None}]
    results, *_ = qa.qa([note], ops, [], cut, cut, same, tmp_path / "qa1", before_xml=same)
    assert results[0].status == qa.FAILED and "+0.00s" in results[0].rows[0].detail                   # nothing changed: not verified
    results, *_ = qa.qa([note], ops, [], cut, cut, same, tmp_path / "qa2")
    assert results[0].status == qa.UNMEASURED and "--before-xml" in results[0].rows[0].detail
