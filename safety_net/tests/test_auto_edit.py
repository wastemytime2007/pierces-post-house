"""The AI editor's own loop (creator_tools.auto_edit): when it stops, which version it names, what it will not retry.

The reviewer (ai_review) and the editor (apply_notes) are replaced by scripted fakes so each scenario is exact; the real ones are covered by their own tests and by the real run on the Septic cut.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "app" / "python_backend"))
import creator_tools as ct  # noqa: E402

ct.use_labs()
import timeline  # noqa: E402


def note(key, fix=True, kind="edge-end", text=None):
    n = {"timeline_sec": 1.0, "clip": 1, "source": "a.mp4", "source_sec": 1.0, "where": key, "text": text or f"AI: {key}", "shapes": [], "ai": True, "kind": kind, "key": key}
    if fix:
        n["suggested_op"] = {"op": "extend_end", "clip": 1, "max_sec": 3.0}
    return n


def review_of(notes, hook_ok=True):
    return {"sequence": "S", "notes": notes, "checks": [{"name": "HOOK", "ok": hook_ok, "detail": ""}], "summary": "s", "unverified_quotes_dropped": 0}


class World:
    """Scripted reviews by version label and scripted editor results by round."""

    def __init__(self, tmp, reviews, edits, durations=None):
        self.tmp, self.reviews, self.edits, self.durations = tmp, reviews, edits, durations or {}
        self.review_calls, self.apply_calls, self.events = [], [], []

    def ai_review(self, xml, folder, on_stage=None, story=True):
        label = Path(xml).stem
        self.review_calls.append(label)
        return self.reviews[label]

    def apply_notes(self, xml, notes, out=None, height=540, on_stage=None, ops=None):
        self.apply_calls.append((Path(xml).stem, [n["key"] for n in __import__("json").loads(Path(notes).read_text())["notes"]]))
        e = self.edits[len(self.apply_calls) - 1]
        if isinstance(e, Exception):
            raise e
        if e is None:
            return {"folder": str(self.tmp), "items": [{"note": 1, "applied": False, "summary": "the editor could not do this"}], "applied": 0, "notes": 1, "xml": None, "page": None, "qa": None, "message": "nothing applied"}
        nxt, applied = e
        d = self.tmp / nxt
        d.mkdir(exist_ok=True)
        items = [{"note": i + 1, "applied": ok, "summary": "done" if ok else "no"} for i, ok in enumerate(applied)]
        return {"folder": str(d), "items": items, "applied": sum(applied), "notes": len(applied), "xml": str(d / f"{nxt}.xml"), "page": str(d / "review.html"), "url": "u", "qa": {"notes": []}, "message": ""}

    def fake_cut(self, stem):
        d = self.durations.get(stem, 60.0)
        return SimpleNamespace(zone_end=d, video=[SimpleNamespace(idx=i, tl_start=(i - 1) * d / 4, tl_end=i * d / 4) for i in range(1, 5)])

    def run(self, monkeypatch, cancelled=None, max_rounds=4):
        monkeypatch.setattr(ct, "ai_review", self.ai_review)
        monkeypatch.setattr(ct, "apply_notes", self.apply_notes)
        monkeypatch.setattr(timeline, "load_cut", lambda p: self.fake_cut(Path(p).stem))
        (self.tmp / "V1").mkdir(exist_ok=True)
        return ct.auto_edit(str(self.tmp / "V1.xml"), str(self.tmp / "V1"), "V1", max_rounds, self.events.append, cancelled)


def test_it_fixes_what_it_can_reviews_again_and_names_the_clean_version_as_ready(tmp_path, monkeypatch):
    w = World(tmp_path, {"V1": review_of([note("a"), note("b")]), "V2": review_of([])}, [("V2", [True, True])])
    out = w.run(monkeypatch)
    assert out["status"] == "clean" and out["best"] == "V2" and out["left"] == []
    assert w.apply_calls == [("V1", ["a", "b"])] and w.review_calls == ["V1", "V2"]
    assert [r["applied"] for r in out["rounds"]] == [2] and "Ready for your review: V2" in out["summary"]
    types = [e["type"] for e in w.events]
    assert types[0] == "auto_edit_started" and types[-1] == "auto_edit_done" and "notes_applied" in types and types.count("ai_review_done") == 2
    assert all(e.get("auto") for e in w.events if e["type"] in ("ai_review_done", "notes_applied"))              # the tab can tell these from a manual submit


def test_notes_with_no_fix_are_never_submitted_and_come_back_as_what_is_left(tmp_path, monkeypatch):
    story = note("story point", fix=False, kind="story", text="AI: the ending is abrupt")
    w = World(tmp_path, {"V1": review_of([note("a"), story]), "V2": review_of([story])}, [("V2", [True])])
    out = w.run(monkeypatch)
    assert w.apply_calls == [("V1", ["a"])]                                                                    # only the fixable one went to the editor
    assert out["status"] == "left" and out["best"] == "V2"
    assert out["left"][0]["text"] == "AI: the ending is abrupt" and "decision or new words" in out["left"][0]["reason"]
    assert "1 thing left that the editor could not fix by itself" in out["summary"]


def test_a_fix_the_editor_could_not_make_is_parked_and_not_tried_again(tmp_path, monkeypatch):
    w = World(tmp_path, {"V1": review_of([note("a"), note("b")]), "V2": review_of([note("b")])}, [("V2", [True, False])])
    out = w.run(monkeypatch)
    assert len(w.apply_calls) == 1                                                                             # V2 still has "b", but "b" is parked
    assert out["status"] == "left" and out["left"][0]["reason"] == "no"


def test_when_the_editor_applies_nothing_the_loop_stops_and_says_what_it_could_not_do(tmp_path, monkeypatch):
    w = World(tmp_path, {"V1": review_of([note("a")])}, [None])
    out = w.run(monkeypatch)
    assert out["status"] == "left" and out["best"] == "V1" and out["left"][0]["reason"] == "the editor could not do this" and len(out["versions"]) == 1


def test_a_round_that_leaves_more_to_fix_ends_the_loop_and_the_earlier_version_is_named(tmp_path, monkeypatch):
    w = World(tmp_path, {"V1": review_of([note("a")]), "V2": review_of([note("x"), note("y"), note("z")])}, [("V2", [True])])
    out = w.run(monkeypatch)
    assert out["status"] == "worse" and out["best"] == "V1" and [v["label"] for v in out["versions"]] == ["V1", "V2"]   # V2 is kept to look at, V1 is the one to review
    assert "V1 is the one to review" in out["summary"] and len(w.apply_calls) == 1


def test_the_loop_stops_at_the_round_limit_with_the_latest_improvement_named(tmp_path, monkeypatch):
    revs = {"V1": review_of([note("a"), note("b"), note("c")]), "V2": review_of([note("b"), note("c")]), "V3": review_of([note("c")]), "V4": review_of([note("d")])}
    w = World(tmp_path, revs, [("V2", [True, True, True]), ("V3", [True, True])])
    out = w.run(monkeypatch, max_rounds=2)
    assert out["status"] == "limit" and out["best"] == "V3" and len(w.apply_calls) == 2


def test_the_cut_is_not_allowed_below_60_percent_of_its_length(tmp_path, monkeypatch):
    w = World(tmp_path, {"V1": review_of([note("a")]), "V2": review_of([])}, [("V2", [True])], durations={"V1": 60.0, "V2": 30.0})
    out = w.run(monkeypatch)
    assert out["status"] == "short" and out["best"] == "V1" and "below 60%" in out["summary"]


def test_stop_is_honoured_before_the_next_revision(tmp_path, monkeypatch):
    w = World(tmp_path, {"V1": review_of([note("a")])}, [])
    out = w.run(monkeypatch, cancelled=lambda: True)
    assert out["status"] == "stopped" and w.apply_calls == [] and out["best"] == "V1"


def test_a_revision_that_fails_its_own_checks_ends_the_loop_with_the_last_good_version(tmp_path, monkeypatch):
    w = World(tmp_path, {"V1": review_of([note("a")])}, [ct.ToolError("The revision did not pass its own checks, so no revised cut was kept.")])
    out = w.run(monkeypatch)
    assert out["status"] == "failed" and out["best"] == "V1" and "did not pass its own checks" in out["message"]
    assert any(e["type"] == "notes_failed" and e.get("auto") for e in w.events)


def test_an_already_clean_cut_is_reported_clean_without_touching_it(tmp_path, monkeypatch):
    w = World(tmp_path, {"V1": review_of([])}, [])
    out = w.run(monkeypatch)
    assert out["status"] == "clean" and out["best"] == "V1" and w.apply_calls == [] and "nothing left to fix" in out["summary"]


def test_the_score_counts_what_is_left_to_fix():
    assert ct.review_score(review_of([])) == 0
    assert ct.review_score(review_of([note("a"), note("b")])) == 2
    assert ct.review_score(review_of([note("h", kind="audio-hole")])) == 2
    assert ct.review_score(review_of([], hook_ok=False)) == 2
    assert ct.review_score({"notes": [], "checks": [{"name": "SYNC", "ok": False, "detail": ""}]}) == 3
    assert ct.review_score({"notes": [], "checks": [{"name": "SYNC", "ok": None, "detail": ""}]}) == 0               # the open 29.97-vs-30 question is not a problem to fix


def fixnote(key, op, clip, kind=None, **kw):
    n = note(key, kind=kind or ("story" if op in ("drop_clip", "start_at_words") else "edge-end"))
    n["suggested_op"] = {"op": op, "clip": clip, **kw}
    return n


def cut_of_clips(*durations):
    t, clips = 0.0, []
    for i, d in enumerate(durations, start=1):
        clips.append(SimpleNamespace(idx=i, tl_start=t, tl_end=t + d))
        t += d
    return SimpleNamespace(zone_end=t, video=clips)


def test_fixes_that_clash_are_not_submitted_together():
    cut = cut_of_clips(*([5] * 12))
    notes = [fixnote("drop2", "drop_clip", 2), fixnote("e2", "extend_end", 2), fixnote("s2", "extend_start", 2),                  # clip 2 is going: its edge fixes are moot
             fixnote("w3", "start_at_words", 3, words="x"), fixnote("s3", "extend_start", 3, kind="edge-start"), fixnote("e3", "extend_end", 3),    # clip 3 starts later: no earlier start
             fixnote("e1", "extend_end", 1)]
    got = ct._compatible(notes, cut, floor_sec=0.0, parked={})
    assert [n["key"] for n in got] == ["drop2", "w3", "e3", "e1"]


def test_story_drops_may_not_take_the_video_below_the_floor_or_below_two_clips():
    cut = cut_of_clips(*([5] * 8))                                                                                # 40 s, 12 clips a minute
    parked = {}
    notes = [fixnote("d1", "drop_clip", 1), fixnote("d2", "drop_clip", 2), fixnote("d3", "drop_clip", 3)]
    got = ct._compatible(notes, cut, floor_sec=33.0, parked=parked)                                              # floor 33 s: one drop fits (35 s), the second would leave 30 s
    assert [n["key"] for n in got] == ["d1"] and set(parked) == {"d2", "d3"} and "too much" in parked["d2"]
    two = ct._compatible([fixnote("a", "drop_clip", 1), fixnote("b", "drop_clip", 2)], cut_of_clips(1, 1, 1), floor_sec=0.0, parked={})
    assert [n["key"] for n in two] == ["a"]                                                                       # never fewer than two clips


def test_when_story_fixes_clash_with_the_edge_fixes_the_editor_retries_with_only_the_edge_fixes(tmp_path, monkeypatch):
    seen = []

    def apply_notes(xml, notes, out=None, height=540, on_stage=None, ops=None):
        keys = [n["key"] for n in __import__("json").loads(Path(notes).read_text())["notes"]]
        seen.append(keys)
        if len(keys) > 1:
            raise ct.ToolError("REFUSING: cannot extend the end of a clip whose end is also being trimmed by another note")
        d = tmp_path / "V2"
        d.mkdir(exist_ok=True)
        return {"folder": str(d), "items": [{"note": 1, "applied": True, "summary": "ok"}], "applied": 1, "notes": 1, "xml": str(d / "V2.xml"), "page": "p", "url": "u", "qa": {"notes": []}, "message": ""}

    story = fixnote("story-fix", "start_at_words", 1, words="x")
    edge = fixnote("edge-fix", "extend_end", 2)
    reviews = {"V1": review_of([story, edge]), "V2": review_of([])}
    monkeypatch.setattr(ct, "ai_review", lambda xml, folder, on_stage=None, story=True: reviews[Path(xml).stem])
    monkeypatch.setattr(ct, "apply_notes", apply_notes)
    monkeypatch.setattr(timeline, "load_cut", lambda p: cut_of_clips(20, 20, 20))
    (tmp_path / "V1").mkdir()
    events = []
    out = ct.auto_edit(str(tmp_path / "V1.xml"), str(tmp_path / "V1"), "V1", 4, events.append)
    assert seen == [["story-fix", "edge-fix"], ["edge-fix"]] and out["status"] == "clean" and out["best"] == "V2"
    assert any(e["type"] == "notes_failed" and "only the edge fixes" in e["message"] for e in events)


def test_a_tools_failure_text_is_its_own_refusal_not_progress_bars_and_warnings():
    text = ("/x/whisper/transcribe.py:132: UserWarning: FP16 is not supported on CPU; using FP32 instead\n  warnings.warn(\"FP16\")\n  0%|          | 0/660 [00:00<?, ?frames/s]\n"
            "100%|██████████| 660/660 [00:01<00:00, 355.81frames/s]\nREFUSING: cannot extend the end of a clip whose end is also being trimmed by another note; resolve the two notes first\n")
    assert ct._tail(text) == "REFUSING: cannot extend the end of a clip whose end is also being trimmed by another note; resolve the two notes first"
    assert ct._tail("a\nb\nc") == "a\nb\nc"


def test_drops_that_would_leave_too_few_clips_a_minute_for_the_export_check_are_refused():
    cut = cut_of_clips(10, 10, 10, 10, 10, 10)                                              # 6 clips in 60 s = 6 a minute: already at the limit
    parked = {}
    got = ct._compatible([fixnote("d1", "drop_clip", 1)], cut, floor_sec=0.0, parked=parked)
    assert got == [] and "6 a minute" in parked["d1"]
    long_cut = cut_of_clips(*([5] * 12))                                                    # 12 clips in 60 s = 12 a minute: dropping one leaves 11 in 55 s = 12 a minute: fine
    assert len(ct._compatible([fixnote("d1", "drop_clip", 1)], long_cut, floor_sec=0.0, parked={})) == 1


def test_a_clashing_batch_records_the_real_refusal_as_the_reason(tmp_path, monkeypatch):
    def apply_notes(xml, notes, out=None, height=540, on_stage=None, ops=None):
        keys = [n["key"] for n in __import__("json").loads(Path(notes).read_text())["notes"]]
        if len(keys) > 1:
            raise ct.ToolError("The revision did not pass its own checks.\n  [FAIL] verify_export CUT-GRANULARITY  4 clips in 41s (6/min, need >=6)")
        return {"folder": str(tmp_path), "items": [{"note": 1, "applied": False, "summary": "no"}], "applied": 0, "notes": 1, "xml": None, "page": None, "qa": None, "message": "x"}

    story_fix, edge_fix = fixnote("story-fix", "start_at_words", 1, words="x"), fixnote("edge-fix", "extend_end", 2)
    monkeypatch.setattr(ct, "ai_review", lambda xml, folder, on_stage=None, story=True: review_of([story_fix, edge_fix]))
    monkeypatch.setattr(ct, "apply_notes", apply_notes)
    monkeypatch.setattr(timeline, "load_cut", lambda p: cut_of_clips(20, 20, 20))
    (tmp_path / "V1").mkdir()
    out = ct.auto_edit(str(tmp_path / "V1.xml"), str(tmp_path / "V1"), "V1", 4, lambda e: None)
    why = {l["text"]: l["reason"] for l in out["left"]}
    assert "CUT-GRANULARITY" in why["AI: story-fix"] and why["AI: story-fix"].startswith("tried together with the other fixes and refused")
