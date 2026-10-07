"""Reopening a cut shows its review as it was left (versions, AI reviews, the editor's conclusion) instead of starting over.

The recording is driven by the events the backend already emits; the loading is checked on its own, and the whole thing through two separate backend runs (close and reopen).
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
BACKEND = REPO / "app" / "python_backend" / "backend.py"
sys.path.insert(0, str(REPO / "app" / "python_backend"))
import creator_tools as ct  # noqa: E402


def version(tmp, name, page=True):
    d = tmp / name
    d.mkdir(exist_ok=True)
    (d / f"{name}.xml").write_text("<xmeml/>")
    if page:
        (d / "review.html").write_text("<html/>")
    return str(d / f"{name}.xml"), str(d)


def events(root, v2=None):
    rx, rf = root
    ev = [{"type": "review_built", "xml": rx, "folder": rf, "url": "x", "sequence": "S", "clips": 6, "duration": 56.0},
          {"type": "ai_review_done", "xml": rx, "tag": "V1", "root": rx, "notes": [{"text": "AI: a", "kind": "edge-end"}], "checks": [{"name": "HOOK", "ok": False, "detail": "weak"}], "summary": "tip", "unverified_quotes_dropped": 0}]
    if v2:
        ev.append({"type": "notes_applied", "xml": v2[0], "folder": v2[1], "root": rx, "label": "V2", "applied": 3, "notes": 5, "qa": {"notes": [{"note": 1, "status": "VERIFIED"}]}})
        ev.append({"type": "ai_review_done", "xml": v2[0], "tag": "V2", "root": rx, "notes": [], "checks": [], "summary": ""})
        ev.append({"type": "auto_edit_done", "root": rx, "status": "clean", "best": "V2", "summary": "Ready for your review: V2", "left": [], "versions": [{"label": "V1", "score": 5}, {"label": "V2", "score": 0}], "rounds": []})
    return ev


def test_a_session_is_recorded_as_the_events_happen_and_read_back_with_its_pages_served_again(tmp_path):
    root, v2 = version(tmp_path, "cut"), version(tmp_path, "cut_v2")
    for e in events(root, v2):
        ct.record_event(e)
    s = ct.load_session(root[0])
    assert [v["label"] for v in s["versions"]] == ["V1", "V2"]
    assert all(v["url"].startswith("http://127.0.0.1:") and v["url"].endswith("/review.html") for v in s["versions"])
    assert s["versions"][1]["applied"] == 3 and s["versions"][1]["qa"]["notes"][0]["status"] == "VERIFIED"
    assert s["ai"]["V1"]["notes"][0]["text"] == "AI: a" and "V2" in s["ai"]
    assert s["auto"]["best"] == "V2" and s["auto"]["status"] == "clean" and s["auto"]["summary"].startswith("Ready for your review")
    summary = ct.session_summary(root[0])
    assert summary["versions"] == 2 and summary["best"] == "V2" and summary["latest"] == "V2" and summary["left"] == 0


def test_a_version_whose_files_are_gone_is_left_out_and_a_missing_first_version_means_no_session(tmp_path):
    root, v2 = version(tmp_path, "cut"), version(tmp_path, "cut_v2")
    for e in events(root, v2):
        ct.record_event(e)
    shutil.rmtree(v2[1])
    s = ct.load_session(root[0])
    assert [v["label"] for v in s["versions"]] == ["V1"] and "V2" not in s["ai"]
    Path(root[1], "review.html").unlink()
    assert ct.load_session(root[0]) is None                                                    # the review page of V1 is gone: build the cut again rather than show half a session
    assert ct.load_session(tmp_path / "nothing.xml") is None


def test_a_rebuild_starts_the_session_over_and_the_same_version_is_never_listed_twice(tmp_path):
    root, v2 = version(tmp_path, "cut"), version(tmp_path, "cut_v2")
    for e in events(root, v2):
        ct.record_event(e)
    ct.record_event({"type": "notes_applied", "xml": v2[0], "folder": v2[1], "root": root[0], "label": "V2", "applied": 1, "notes": 1, "qa": None})       # the same V2 again
    assert [v["label"] for v in ct.load_session(root[0])["versions"]] == ["V1", "V2"] and ct.load_session(root[0])["versions"][1]["applied"] == 1
    ct.record_event({"type": "review_built", "xml": root[0], "folder": root[1], "url": "x", "sequence": "S", "clips": 6, "duration": 56.0})
    s = ct.load_session(root[0])
    assert [v["label"] for v in s["versions"]] == ["V1"] and s["ai"] == {} and s["auto"] is None


def test_a_cut_reviewed_before_sessions_were_saved_is_rebuilt_from_the_folders_it_left(tmp_path):
    """Today's V1 to V5 were made before any session file existed. The versions are nested folders with their own ledger, QA and AI review; they are read back and saved."""
    root = tmp_path / "Cut.xml"
    root.write_text("<xmeml/>")
    review = tmp_path / "Cut - review"
    review.mkdir()
    (review / "review.html").write_text("<html/>")
    (review / "ai_review.json").write_text(json.dumps({"notes": [{"text": "AI: a"}], "checks": [{"name": "HOOK", "ok": False, "detail": "weak"}], "summary": "tip", "unverified_quotes_dropped": 0}))
    folder = tmp_path
    for n, (applied, total) in ((2, (3, 5)), (3, (1, 1))):
        d = folder / f"Cut_v{n} - revised"
        (d / "qa").mkdir(parents=True)
        (d / f"Cut_v{n}.xml").write_text("<xmeml/>")
        (d / "review.html").write_text("<html/>")
        (d / "changes.json").write_text(json.dumps({"items": [{"note": i + 1, "applied": i < applied} for i in range(total)]}))
        (d / "qa" / "qa.json").write_text(json.dumps({"notes": [{"note": 1, "status": "VERIFIED", "text": "AI: a", "rows": []}], "checks": [{"name": "LENGTH", "ok": True, "detail": "ok"}], "unrequested": []}))
        (d / "ai_review.json").write_text(json.dumps({"notes": [], "checks": [], "summary": "", "unverified_quotes_dropped": 0}))
        folder = d
    refused = folder / "Cut_v4 - revised"                                                       # a V4 whose revision was refused by its own checks: files exist, but no QA result
    refused.mkdir()
    (refused / "Cut_v4.xml").write_text("<xmeml/>")
    (refused / "review.html").write_text("<html/>")
    assert not ct.session_path(root).exists()
    s = ct.load_session(root)
    assert [v["label"] for v in s["versions"]] == ["V1", "V2", "V3"]                           # V4 is not shown as a version
    assert (s["versions"][1]["applied"], s["versions"][1]["notes"]) == (3, 5) and (s["versions"][2]["applied"], s["versions"][2]["notes"]) == (1, 1)
    assert s["versions"][1]["qa"]["notes"][0]["status"] == "VERIFIED" and s["versions"][1]["qa"]["whole_cut"][0]["name"] == "LENGTH"        # the ledger the tab shows
    assert s["ai"]["V1"]["notes"][0]["text"] == "AI: a" and set(s["ai"]) == {"V1", "V2", "V3"} and s["auto"] is None
    assert ct.session_path(root).exists()                                                                                                    # saved: the next open reads the file
    assert ct.session_summary(root)["versions"] == 3
    ct.session_path(root).unlink()
    (tmp_path / "Cut - review" / "review.html").unlink()
    assert ct.load_session(root) is None                                                                                                     # no saved session and no first review page: simply not reviewed


def test_events_that_name_no_cut_or_are_not_session_events_write_nothing(tmp_path):
    root = version(tmp_path, "cut")
    ct.record_event({"type": "ai_review_done", "xml": root[0], "tag": "V1", "notes": []})                  # no root
    ct.record_event({"type": "notes_applied", "xml": None, "root": root[0]})                               # nothing was made
    ct.record_event({"type": "log", "root": root[0]})
    assert not ct.session_path(root[0]).exists()


def test_the_exports_list_carries_each_cuts_session_summary(tmp_path):
    proj = tmp_path / "proj"
    cuts = proj / "cuts"
    cuts.mkdir(parents=True)
    root, v2 = version(cuts, "A cut"), version(cuts, "A cut_v2")
    other = cuts / "Another.xml"
    other.write_text("<xmeml/>")
    ct.remember_export(proj, root[0])
    ct.remember_export(proj, str(other))
    for e in events(root, v2):
        ct.record_event(e)
    rows = {r["name"]: r for r in ct.list_exports(proj)}
    assert rows["A cut.xml"]["session"]["versions"] == 2 and rows["A cut.xml"]["session"]["best"] == "V2"
    assert rows["Another.xml"]["session"] is None


# ------------------------------------------------------------------ through the real backend, two separate runs

pytestmark_e2e = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _talk(commands, want, timeout=240):
    import os
    env = {"PATH": "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin", "PRECUT_ROOT": str(REPO / "app" / "python_backend"), "HOME": os.environ.get("HOME", "")}
    p = subprocess.Popen([sys.executable, str(BACKEND)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
    out = []
    try:
        for c in commands:
            p.stdin.write(json.dumps(c) + "\n")
        p.stdin.flush()
        t0 = time.time()
        for line in p.stdout:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            out.append(ev)
            if ev.get("type") in want or time.time() - t0 > timeout:
                break
    finally:
        p.kill()
    return out


@pytestmark_e2e
def test_closing_and_reopening_shows_the_review_as_it_was_left_without_rebuilding(tmp_path):
    v = tmp_path / "Clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000:duration=12", "-f", "lavfi", "-i", "testsrc2=s=180x320:r=30:d=12",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(v)], check=True)
    ct.use_labs()
    import xml_from_media
    xml = str(xml_from_media.make(v, tmp_path / "cut.xml"))
    notes = tmp_path / "n.json"
    notes.write_text(json.dumps({"notes": [{"timeline_sec": 4.0, "clip": 1, "text": "Take out 3 to 5 seconds, it drags", "shapes": []}]}))
    ops = tmp_path / "ops.json"
    ops.write_text(json.dumps([{"note": 1, "op": "remove_range", "start": 3.0, "end": 5.0, "why": "stated"}]))

    # first run: open the cut (no session yet, so it is built), then submit changes
    first = _talk([{"type": "open_review", "xml": xml, "height": 320}], {"review_built", "review_failed"})
    assert "review_started" in [e["type"] for e in first] and first[-1]["type"] == "review_built", first[-2:]
    second = _talk([{"type": "apply_notes", "xml": xml, "notes": str(notes), "ops": str(ops), "root": xml, "label": "V2", "height": 320}], {"notes_applied", "notes_failed"})
    assert second[-1]["type"] == "notes_applied", second[-2:]

    # a different run of the app (closed and reopened): the saved session comes straight back, nothing is built
    again = _talk([{"type": "open_review", "xml": xml}], {"review_session_loaded", "review_built", "review_failed"}, timeout=60)
    assert [e["type"] for e in again if e["type"] in ("review_started", "review_built")] == []            # not rebuilt
    s = again[-1]
    assert s["type"] == "review_session_loaded" and [x["label"] for x in s["versions"]] == ["V1", "V2"]
    assert s["versions"][1]["applied"] == 1 and s["versions"][1]["qa"]["notes"][0]["status"] == "VERIFIED"
    assert s["auto_running"] is False and all(x["url"].startswith("http://127.0.0.1:") for x in s["versions"])

    # starting over is an explicit choice
    fresh = _talk([{"type": "open_review", "xml": xml, "fresh": True, "height": 320}], {"review_built", "review_failed"})
    assert "review_started" in [e["type"] for e in fresh] and fresh[-1]["type"] == "review_built"
    after = _talk([{"type": "open_review", "xml": xml}], {"review_session_loaded", "review_built"}, timeout=60)[-1]
    assert [x["label"] for x in after["versions"]] == ["V1"]                                                # the fresh build reset the session
