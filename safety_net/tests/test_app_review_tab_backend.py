"""The app's backend, started as the app starts it (python backend.py, JSON lines on stdin), builds a review page from an export XML using the BUNDLED labs.

Proves the integration end to end for the first creator-workflow screen: the command goes in, a `review_built` event comes out naming a real review.html and a preview
whose frame count matches the cut. Synthetic media only (a 6 s test pattern with a tone).
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

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("appreview")
    v = d / "Finished Video.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000:duration=6", "-f", "lavfi", "-i", "testsrc2=s=180x320:r=30:d=6",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(v)], check=True)
    sys.path.insert(0, str(REPO / "app" / "python_backend"))
    import creator_tools
    creator_tools.use_labs()
    import xml_from_media
    xml = xml_from_media.make(v, d / "cut.xml")
    return d, Path(xml)


def _talk(commands, want, timeout=180, extra_env=None):
    """Send commands to a fresh backend; return every event up to and including the first whose type is in `want`."""
    env = {"PATH": "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin", "PRECUT_ROOT": str(REPO / "app" / "python_backend"), **(extra_env or {})}
    p = subprocess.Popen([sys.executable, str(BACKEND)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
    events = []
    try:
        for c in commands:
            p.stdin.write(json.dumps(c) + "\n")
        p.stdin.flush()
        t0 = time.time()
        for line in p.stdout:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                events.append({"type": "NOT-JSON", "line": line.strip()})
                continue
            events.append(ev)
            if ev.get("type") in want or time.time() - t0 > timeout:
                break
    finally:
        p.kill()
    return events


def test_build_review_through_the_backend_makes_a_page_and_a_frame_exact_preview(media):
    d, xml = media
    evs = _talk([{"type": "build_review", "job_id": "r1", "xml": str(xml), "height": 320}], {"review_built", "review_failed"})
    last = evs[-1]
    assert last["type"] == "review_built", evs[-3:]
    leaked = [e["line"] for e in evs if e["type"] == "NOT-JSON" and ("[PASS]" in e["line"] or "[FAIL]" in e["line"] or "[SKIP]" in e["line"])]
    assert not leaked, f"a tool's check rows reached the event channel: {leaked[:2]}"
    assert Path(last["page"]).is_file() and last["page"].endswith("review.html")
    assert Path(last["preview"]).is_file() and last["clips"] == 1 and last["duration"] == pytest.approx(6.0, abs=0.1)
    frames = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", last["preview"]],
                            capture_output=True, text=True).stdout.strip()
    assert int(frames) == round(6.0 * 30)                                              # frame-exact at the cut's own rate (the review_loop guarantee survives bundling)


def test_a_missing_xml_is_reported_in_words_not_a_traceback(tmp_path):
    evs = _talk([{"type": "build_review", "job_id": "r2", "xml": str(tmp_path / "nope.xml")}], {"review_failed"}, timeout=60)
    assert evs[-1]["type"] == "review_failed" and "is not there" in evs[-1]["message"]


def test_apply_notes_through_the_backend_revises_the_cut_and_qa_measures_it(tmp_path):
    """notes + a reviewed plan in; a shorter v2 XML, its review page and a QA ledger out, through the bundled tools. The plan removes 3 to 5 s of a 12 s video."""
    v = tmp_path / "Clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000:duration=12", "-f", "lavfi", "-i", "testsrc2=s=180x320:r=30:d=12",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(v)], check=True)
    sys.path.insert(0, str(REPO / "app" / "python_backend"))
    import creator_tools
    creator_tools.use_labs()
    import timeline
    import xml_from_media
    xml = xml_from_media.make(v, tmp_path / "cut.xml")
    notes = tmp_path / "review_notes.json"
    notes.write_text(json.dumps({"notes": [{"timeline_sec": 4.0, "clip": 1, "text": "Take out 3 to 5 seconds, it drags", "shapes": []}]}))
    ops = tmp_path / "ops.json"
    ops.write_text(json.dumps([{"note": 1, "op": "remove_range", "start": 3.0, "end": 5.0, "why": "stated"}]))
    evs = _talk([{"type": "apply_notes", "job_id": "n1", "xml": str(xml), "notes": str(notes), "ops": str(ops), "out": str(tmp_path / "revised"), "height": 320}],
                {"notes_applied", "notes_failed"}, timeout=240)
    last = evs[-1]
    assert last["type"] == "notes_applied", evs[-3:]
    assert Path(last["xml"]).is_file() and Path(last["page"]).is_file() and last["applied"] == 1
    assert any(e["type"] == "notes_stage" for e in evs)
    assert not [e for e in evs if e["type"] == "NOT-JSON" and "[PASS]" in e["line"]]
    qa = last["qa"]
    assert [n["status"] for n in qa["notes"]] == ["VERIFIED"], qa["notes"]
    assert Path(qa["report"]).is_file()
    assert timeline.load_cut(Path(last["xml"])).zone_end == pytest.approx(10.0, abs=0.1)


def _short_cut(tmp_path):
    v = tmp_path / "Clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000:duration=12", "-f", "lavfi", "-i", "testsrc2=s=180x320:r=30:d=12",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(v)], check=True)
    sys.path.insert(0, str(REPO / "app" / "python_backend"))
    import creator_tools
    creator_tools.use_labs()
    import xml_from_media
    return creator_tools, xml_from_media.make(v, tmp_path / "cut.xml")


def test_the_review_server_does_byte_ranges_and_serves_nothing_outside_its_folder(tmp_path):
    """A WebView cannot seek a video it is served without ranges; and the server must not be a way to read other files."""
    import http.client
    import urllib.parse
    sys.path.insert(0, str(REPO / "app" / "python_backend"))
    import creator_tools
    folder = tmp_path / "rev"
    folder.mkdir()
    (folder / "review.html").write_text("<html>page</html>")
    blob = bytes(range(100))
    (folder / "preview.mp4").write_bytes(blob)
    (tmp_path / "secret.txt").write_text("not for the page")
    url = creator_tools.serve_review(folder)
    u = urllib.parse.urlparse(url)
    base = u.path.rsplit("/", 1)[0]

    def get(path, rng=None):
        c = http.client.HTTPConnection(u.hostname, u.port, timeout=10)
        c.request("GET", path, headers={"Range": rng} if rng else {})
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read()

    st, h, body = get(base + "/preview.mp4")
    assert st == 200 and body == blob and h["Accept-Ranges"] == "bytes"
    st, h, body = get(base + "/preview.mp4", "bytes=10-19")
    assert st == 206 and body == blob[10:20] and h["Content-Range"] == "bytes 10-19/100"
    st, h, body = get(base + "/preview.mp4", "bytes=90-")
    assert st == 206 and body == blob[90:] and h["Content-Range"] == "bytes 90-99/100"
    st, h, body = get(base + "/preview.mp4", "bytes=-5")
    assert st == 206 and body == blob[-5:]
    assert get(base + "/preview.mp4", "bytes=500-600")[0] == 416
    assert get(base + "/../secret.txt")[0] == 404 and get(base + "/%2e%2e/secret.txt")[0] == 404
    assert get("/r/deadbeef00/review.html")[0] == 404 and get("/etc/passwd")[0] == 404
    assert get(base + "/review.html")[2] == b"<html>page</html>"


def test_export_checks_the_xml_and_only_a_passing_one_is_opened(tmp_path):
    creator_tools, xml = _short_cut(tmp_path)
    import os
    os.environ["POSTHOUSE_NO_OPEN"] = "1"                      # report what would open; open nothing
    try:
        ok = creator_tools.export_xml(str(xml))
        assert ok["verified"] is True and ok["failed"] == [] and ok["opened"] is False
        assert ok["app"] is None or "Premiere" in ok["app"]
        bad = tmp_path / "bad.xml"
        bad.write_text("<xmeml version='4'><sequence id='s'><name>x</name></sequence></xmeml>")
        refused = creator_tools.export_xml(str(bad))
        assert refused["verified"] is False and refused["failed"] and refused["opened"] is False and refused["app"] is None
        assert creator_tools.export_xml(str(xml), open_in_premiere=False)["app"] is None
    finally:
        os.environ.pop("POSTHOUSE_NO_OPEN", None)


def test_an_export_the_app_just_made_is_remembered_and_listed_newest_first_and_a_deleted_one_is_not(tmp_path):
    """The export dialog lets the XML go anywhere, so the project records each export; the Review tab starts from the newest one."""
    sys.path.insert(0, str(REPO / "app" / "python_backend"))
    import creator_tools
    proj = tmp_path / "proj"
    proj.mkdir()
    a, b, c = (tmp_path / n for n in ("a.xml", "b.xml", "c.xml"))
    for f in (a, b, c):
        f.write_text("<xmeml/>")
    creator_tools.remember_export(proj, str(a))
    creator_tools.remember_export(proj, str(b))
    creator_tools.remember_export(proj, str(a))                       # exported again: moves to the front, no duplicate
    assert [e["name"] for e in creator_tools.list_exports(proj)] == ["a.xml", "b.xml"]
    b.unlink()
    assert [e["name"] for e in creator_tools.list_exports(proj)] == ["a.xml"]
    (proj / "exports").mkdir()
    (proj / "exports" / "old.xml").write_text("<xmeml/>")             # an XML already in the project's own exports folder is found too
    assert {e["name"] for e in creator_tools.list_exports(proj)} == {"a.xml", "old.xml"}
    assert creator_tools.list_exports(tmp_path / "nothing") == []


def test_ai_review_through_the_backend_returns_notes_the_page_can_carry_and_saves_them_beside_the_page(tmp_path):
    """The mechanical checks only (story=False: no model call). A 12 s sine with one cut at 3 and 5 s: the edges are measured on the voice under them."""
    creator_tools, xml = _short_cut(tmp_path)
    folder = tmp_path / "rev"
    evs = _talk([{"type": "ai_review", "xml": str(xml), "folder": str(folder), "tag": "V1", "story": False}], {"ai_review_done", "ai_review_failed"}, timeout=240)
    last = evs[-1]
    assert last["type"] == "ai_review_done", evs[-3:]
    assert last["tag"] == "V1" and {c["name"] for c in last["checks"]} >= {"CUT-EDGES", "SOURCE-AUDIO"}
    assert all({"timeline_sec", "clip", "text", "shapes"} <= set(n) and n["text"].startswith("AI: ") for n in last["notes"])
    assert json.loads((folder / "ai_review.json").read_text())["schema"] == "ai_review.v0-draft"
    assert any(e["type"] == "ai_review_stage" for e in evs)


def test_export_through_the_backend_reports_the_check(tmp_path):
    creator_tools, xml = _short_cut(tmp_path)
    evs = _talk([{"type": "export_xml", "xml": str(xml)}], {"xml_exported", "xml_export_failed"}, timeout=60, extra_env={"POSTHOUSE_NO_OPEN": "1"})
    assert evs[-1]["type"] == "xml_exported" and evs[-1]["verified"] is True and evs[-1]["opened"] is False


def test_the_pages_own_notes_are_applied_and_the_next_version_opens_beside_the_xml(tmp_path):
    """What the Review tab does: the page hands over its notes object, the app saves it beside that page, revises, and returns the next version's page URL."""
    creator_tools, xml = _short_cut(tmp_path)
    review = tmp_path / "cut - review"
    review.mkdir()
    payload = {"schema": "review_notes.v0-draft", "sequence": "Clip", "notes": [{"timeline_sec": 4.0, "clip": 1, "text": "Take out 3 to 5 seconds, it drags", "shapes": []}]}
    ops = tmp_path / "ops.json"
    ops.write_text(json.dumps([{"note": 1, "op": "remove_range", "start": 3.0, "end": 5.0, "why": "stated"}]))
    evs = _talk([{"type": "apply_notes", "job_id": "p1", "xml": str(xml), "notes_payload": payload, "review_folder": str(review), "ops": str(ops), "height": 320}],
                {"notes_applied", "notes_failed"}, timeout=240)
    last = evs[-1]
    assert last["type"] == "notes_applied", evs[-3:]
    assert json.loads((review / "review_notes.json").read_text())["notes"][0]["text"].startswith("Take out")
    assert Path(last["folder"]).name == "cut_v2 - revised" and Path(last["folder"]).parent == tmp_path
    assert last["url"].startswith("http://127.0.0.1:") and last["url"].endswith("/review.html")
    assert [n["status"] for n in last["qa"]["notes"]] == ["VERIFIED"]


def test_a_notes_file_that_is_not_review_notes_is_refused_in_words(tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text("{}")
    xml = tmp_path / "c.xml"
    xml.write_text("<xmeml/>")
    evs = _talk([{"type": "apply_notes", "job_id": "n2", "xml": str(xml), "notes": str(bad)}], {"notes_failed"}, timeout=60)
    assert evs[-1]["type"] == "notes_failed" and "not a review_notes.json" in evs[-1]["message"]
