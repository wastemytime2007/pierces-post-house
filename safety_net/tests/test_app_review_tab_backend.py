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


def _talk(commands, want, timeout=180):
    """Send commands to a fresh backend; return every event up to and including the first whose type is in `want`."""
    env = {"PATH": "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin", "PRECUT_ROOT": str(REPO / "app" / "python_backend")}
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


def test_a_notes_file_that_is_not_review_notes_is_refused_in_words(tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text("{}")
    xml = tmp_path / "c.xml"
    xml.write_text("<xmeml/>")
    evs = _talk([{"type": "apply_notes", "job_id": "n2", "xml": str(xml), "notes": str(bad)}], {"notes_failed"}, timeout=60)
    assert evs[-1]["type"] == "notes_failed" and "not a review_notes.json" in evs[-1]["message"]
