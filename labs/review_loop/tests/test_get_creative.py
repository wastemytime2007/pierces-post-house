"""Get Creative on many sequences: one after another in the background, progress on disk, each version recorded in its own cut's session as it is made (Ryan, 2026-10-21: "leave and come back to
all seqs having had the uh um removal, music, graphics, etc"). The slow steps (render, transcribe, music) are stood in for; what is tested is the batch: order, isolation, skipping, stopping, records."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "app" / "python_backend"))
import creator_tools as ct  # noqa: E402


@pytest.fixture()
def seqs(tmp_path):
    out = []
    for n in ("A", "B", "C"):
        p = tmp_path / f"{n}.xml"
        p.write_text("<xmeml/>")
        out.append(str(p))
    return out


class Fake:
    """Stands in for the slow tools and keeps a log of what the batch asked of them."""

    def __init__(self, monkeypatch, fail_on=None, fillers=True, layers=()):
        self.calls, self.events, self.sessions, self.items = [], [], {}, []
        self.fail_on, self.fillers, self.layers = fail_on, fillers, set(layers)
        ct.use_labs()
        import finish_cut as fc
        import fillers
        import timeline
        monkeypatch.setattr(ct, "build_review", self.build_review)
        monkeypatch.setattr(ct, "apply_notes", self.apply_notes)
        monkeypatch.setattr(ct, "finish_cut", self.finish_cut)
        monkeypatch.setattr(ct, "load_session", lambda x: self.sessions.get(str(x)))
        monkeypatch.setattr(fillers, "find", lambda cut: [{"start": 1, "end": 1.3, "words": ["um"]}] if self.fillers else [])
        monkeypatch.setattr(timeline, "load_cut", lambda p: object())
        monkeypatch.setattr(fc, "layers_present", lambda p: {"on_screen": Path(p).stem in self.layers, "music": False, "bleep": False})

    def emit(self, ev):
        self.events.append(ev)
        if ev["type"] == "review_built":
            self.sessions[ev["xml"]] = {"versions": [{"label": "V1", "xml": ev["xml"]}]}
        if ev["type"] == "notes_applied":
            self.sessions[ev["root"]]["versions"].append({"label": ev["label"], "xml": ev["xml"]})

    def build_review(self, xml):
        self.calls.append(("review", Path(xml).stem))
        return {"xml": xml, "folder": str(Path(xml).parent / (Path(xml).stem + " - review")), "url": "http://x"}

    def apply_notes(self, xml, notes, out, height, stage, ops, auto):
        self.calls.append(("fillers", Path(xml).stem, auto))
        return {"xml": str(Path(xml).with_name(Path(xml).stem + "_ums.xml")), "folder": "f"}

    def finish_cut(self, xml, out, captions, music, bleep, sfx_at, height, stage, graphics, sfx, keep_going=False):
        self.keep_going = keep_going
        self.calls.append(("finish", Path(xml).stem, captions, music, bleep, graphics))
        if self.fail_on and Path(xml).stem.startswith(self.fail_on):
            raise ct.ToolError("The music failed its own check")
        return {"xml": str(Path(xml).with_name(Path(xml).stem + "_creative.xml")), "folder": "f", "items": self.items}


def test_every_sequence_in_turn_review_then_ums_then_the_layers_each_a_version_of_its_own(monkeypatch, seqs):
    f = Fake(monkeypatch)
    res = ct.get_creative(seqs, None, f.emit)
    assert res["state"] == "done" and res["counts"]["done"] == 3
    assert [c[:2] for c in f.calls] == [("review", "A"), ("fillers", "A"), ("finish", "A_ums"), ("review", "B"), ("fillers", "B"), ("finish", "B_ums"), ("review", "C"), ("fillers", "C"), ("finish", "C_ums")], "one after another, in order; each finish runs on the version with the ums already out"
    assert f.calls[2][2:] == (True, True, True, True), "captions, music, bleep and graphics are all on by default"
    assert [v["label"] for v in f.sessions[seqs[0]]["versions"]] == ["V1", "V2", "V3"], "V2 the ums out, V3 the creative layers"
    assert f.sessions[seqs[0]]["versions"][2]["xml"].endswith("A_ums_creative.xml"), "the layers go on the version with the ums already cut out"


def test_progress_is_on_disk_and_survives_a_closed_app(monkeypatch, seqs):
    f = Fake(monkeypatch)
    ct.get_creative(seqs, None, f.emit)
    saved = ct.read_creative(seqs[0])
    assert saved["state"] == "done" and [i["status"] for i in saved["items"]] == ["done"] * 3
    assert all(i["final_xml"].endswith("_creative.xml") for i in saved["items"]), "the finished version of each is named"
    assert (Path(seqs[0]).parent / "get_creative.json").is_file() and ct.read_creative(Path(seqs[0]).parent / "nothing.xml") is not None


def test_one_sequence_failing_does_not_stop_the_others_and_says_why(monkeypatch, seqs):
    f = Fake(monkeypatch, fail_on="B")
    res = ct.get_creative(seqs, None, f.emit)
    assert [i["status"] for i in res["items"]] == ["done", "failed", "done"]
    assert "music failed" in res["items"][1]["message"]
    assert res["counts"] == {"done": 2, "skipped": 0, "failed": 1, "waiting": 0}
    assert ("finish", "C_ums", True, True, True, True) in f.calls, "C was still worked on after B failed"


def test_a_sequence_that_already_has_layers_is_left_alone_not_doubled(monkeypatch, seqs):
    f = Fake(monkeypatch, layers={"B"})
    res = ct.get_creative(seqs, None, f.emit)
    assert [i["status"] for i in res["items"]] == ["done", "skipped", "done"]
    assert "already has" in res["items"][1]["message"] and not any(c[1] == "B" and c[0] in ("fillers", "finish") for c in f.calls)


def test_no_ums_heard_means_no_fillers_pass_and_it_says_so(monkeypatch, seqs):
    f = Fake(monkeypatch, fillers=False)
    res = ct.get_creative(seqs[:1], None, f.emit)
    assert not any(c[0] == "fillers" for c in f.calls) and "no ums or uhs heard" in res["items"][0]["message"]
    assert [v["label"] for v in f.sessions[seqs[0]]["versions"]] == ["V1", "V2"], "the layers are then V2"


def test_the_options_decide_the_steps(monkeypatch, seqs):
    f = Fake(monkeypatch)
    ct.get_creative(seqs[:1], {"fillers": False, "music": False, "bleep": False}, f.emit)
    assert [c[0] for c in f.calls] == ["review", "finish"] and f.calls[1][2:] == (True, False, False, True), "no ums pass; captions and graphics only"


def test_stopping_ends_the_batch_after_the_cut_in_progress_and_what_is_done_stays_done(monkeypatch, seqs):
    f = Fake(monkeypatch)
    state = {"n": 0}

    def stop():
        state["n"] += 1
        return state["n"] > 1                                  # asked after the first sequence started
    res = ct.get_creative(seqs, None, f.emit, stop)
    assert res["state"] == "stopped" and [i["status"] for i in res["items"]] == ["done", "waiting", "waiting"]
    assert ct.read_creative(seqs[0])["state"] == "stopped"


def test_events_carry_what_the_tab_needs_to_show_progress(monkeypatch, seqs):
    f = Fake(monkeypatch)
    ct.get_creative(seqs[:2], None, f.emit)
    kinds = [e["type"] for e in f.events]
    assert kinds[0] == "creative_started" and kinds[-1] == "creative_batch_done"
    prog = [e for e in f.events if e["type"] == "creative_progress"]
    assert prog and all({"index", "total", "name", "step"} <= set(e) for e in prog) and {e["index"] for e in prog} == {1, 2}
    assert [e["status"] for e in f.events if e["type"] == "creative_item"] == ["done", "done"]


def test_missing_xml_or_nothing_to_do_is_refused_up_front(monkeypatch, tmp_path):
    with pytest.raises(ct.ToolError):
        ct.get_creative([], None)
    with pytest.raises(ct.ToolError):
        ct.get_creative([str(tmp_path / "gone.xml")], None)


def test_the_batch_events_build_each_cuts_session_through_the_real_recorder(monkeypatch, seqs):
    """The backend saves the session from the review_built / notes_applied events as they go out. Fed to the real recorder, each sequence ends with V1, V2 (ums out) and V3 (layers) on disk, which is
    what lets the tab show them after it was closed."""
    f = Fake(monkeypatch)
    monkeypatch.setattr(ct, "load_session", lambda x: ct._read_session(x))          # the real reader, but no served pages needed here

    def emit(ev):
        f.emit(ev)
        ct.record_event(ev)
    ct.get_creative(seqs[:2], None, emit)
    for x in seqs[:2]:
        s = ct._read_session(x)
        assert [v["label"] for v in s["versions"]] == ["V1", "V2", "V3"], s
        assert s["versions"][2]["xml"].endswith("_ums_creative.xml")


def test_the_batchs_review_and_version_events_are_tagged_so_the_tab_leaves_the_review_on_screen_alone(monkeypatch, seqs):
    f = Fake(monkeypatch)
    ct.get_creative(seqs[:1], None, f.emit)
    session_events = [e for e in f.events if e["type"] in ("review_built", "notes_applied")]
    assert session_events and all(e.get("batch") is True for e in session_events)
    tab = (REPO / "app" / "src" / "screens" / "tabs" / "ReviewTab.jsx").read_text()
    assert tab.count("if (ev.batch) return;") == 2, "both of the tab's event handlers ignore the batch's events"


def test_a_layer_that_could_not_be_made_is_named_with_its_reason_and_the_sequence_still_counts_as_done(monkeypatch, seqs):
    f = Fake(monkeypatch)
    f.items = [{"op": "captions", "applied": True, "failed": False, "summary": "captions placed"},
               {"op": "music", "applied": False, "failed": True, "summary": "not added: the music service is out of credits (191 left, this cut needs 450): top up the ElevenLabs account and run it again"}]
    res = ct.get_creative(seqs[:1], None, f.emit)
    it = res["items"][0]
    assert it["status"] == "done" and "added captions" in it["message"] and "music not added: the music service is out of credits" in it["message"]
    assert f.keep_going is True, "the batch always asks the finish to keep going"


def test_music_taken_from_the_cache_or_the_artlist_library_is_named_in_the_message(monkeypatch, seqs):
    f = Fake(monkeypatch)
    f.items = [{"op": "music", "applied": True, "failed": False,
                "summary": "music from your Artlist library (Aves - Bumpin' - No Backing Vocals), because the music could not be generated (the music service is out of credits (191 left, this cut needs 200): top up), with a sound effect on each graphic (2)"}]
    res = ct.get_creative(seqs[:1], None, f.emit)
    msg = res["items"][0]["message"]
    assert res["items"][0]["status"] == "done" and "music is music from your Artlist library (Aves - Bumpin' - No Backing Vocals) (it could not be generated)" in msg, msg
