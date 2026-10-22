"""The Premiere link: an XML opens in a NEW Premiere project named after it and saved beside it.

Premiere is not here, so the panel is played by a thread that does what jsx/host.jsx does (claim the job, answer in a .result file). What these prove is the app's side of the protocol:
the project path, the job file, the heartbeat rules, the answers, and that a panel that is missing, old, or unable never leaves the person waiting or a stale job to run later.
What they cannot prove is that Premiere itself accepts the calls: that is for a run in Premiere.
"""
import importlib.util
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _tools():
    # Loaded by path: other tests put PreCut's modules on the path, and a plain import could find a different one
    spec = importlib.util.spec_from_file_location("posthouse_app_creator_tools", REPO / "app" / "python_backend" / "creator_tools.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["posthouse_app_creator_tools"] = mod
    spec.loader.exec_module(mod)
    return mod


def _heartbeat(jobs: Path, v=2, age=0.0):
    jobs.mkdir(parents=True, exist_ok=True)
    (jobs / "link.txt").write_text(f"v={v}\nt={int((time.time() - age) * 1000)}\napp=26.0\n")


def _xml(tmp_path, name="Runnells - 2 stories.xml"):
    d = tmp_path / "exports"
    d.mkdir(exist_ok=True)
    x = d / name
    x.write_text("<xmeml/>")
    return x


def _panel(jobs: Path, answer: str, seen: list):
    """What the panel does for one job: take it, answer it."""
    def run():
        end = time.time() + 10
        while time.time() < end:
            for j in jobs.glob("*.job"):
                kv = dict(l.split("=", 1) for l in j.read_text().splitlines() if "=" in l)
                seen.append(kv)
                (jobs / (j.stem + ".result")).write_text(answer.format(**kv))
                j.unlink()
                return
            time.sleep(0.05)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


@pytest.fixture(autouse=True)
def _no_real_premiere(monkeypatch):
    # Never launch anything: `open` calls are recorded instead
    calls = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: calls.append(a[0]) or type("R", (), {"returncode": 0})())
    monkeypatch.delenv("POSTHOUSE_NO_OPEN", raising=False)
    return calls


def test_the_project_is_named_after_the_xml_beside_it_and_never_over_another(tmp_path):
    tools = _tools()
    x = _xml(tmp_path)
    first = tools.project_path_for(x)
    assert first == x.with_suffix(".prproj") and first.parent == x.parent
    first.write_text("x")
    second = tools.project_path_for(x)
    assert second != first and second.suffix == ".prproj" and second.parent == x.parent


def test_the_heartbeat_must_be_fresh_and_from_a_panel_that_has_the_inbox(tmp_path):
    tools = _tools()
    jobs = tmp_path / "jobs"
    assert not tools.premiere_link_alive(jobs)                      # no file: no panel
    _heartbeat(jobs)
    assert tools.premiere_link_alive(jobs)
    _heartbeat(jobs, age=60)
    assert not tools.premiere_link_alive(jobs)                      # Premiere closed a minute ago
    _heartbeat(jobs, v=1)
    assert not tools.premiere_link_alive(jobs)                      # the panel from before the inbox existed
    (jobs / "link.txt").write_text("garbage")
    assert not tools.premiere_link_alive(jobs)


def test_a_running_panel_makes_the_named_project(tmp_path):
    tools = _tools()
    jobs = tmp_path / "jobs"
    _heartbeat(jobs)
    x = _xml(tmp_path)
    seen = []
    t = _panel(jobs, "ok=1\nproject={project}\n", seen)
    out = tools.open_in_new_project(str(x), jobs_dir=jobs, start_wait=1, job_wait=10)
    t.join()
    assert out["mode"] == "project" and out["project"] == str(x.with_suffix(".prproj"))
    assert seen == [{"xml": str(x), "project": str(x.with_suffix(".prproj"))}]
    assert not list(jobs.glob("*.job")) and not list(jobs.glob("*.result"))     # nothing left behind


def test_no_panel_means_a_plain_open_and_the_person_is_told(tmp_path, _no_real_premiere):
    tools = _tools()
    x = _xml(tmp_path)
    out = tools.open_in_new_project(str(x), jobs_dir=tmp_path / "jobs", start_wait=0.2, job_wait=1, running_wait=0.2)
    assert out["mode"] == "plain" and "did not answer" in out["message"]
    assert not list((tmp_path / "jobs").glob("*.job"))                           # no job left to run on its own later
    assert any(str(x) in c for c in _no_real_premiere)                           # the plain open happened


def test_a_panel_that_cannot_do_it_falls_back_and_says_why_and_leaves_no_job(tmp_path, _no_real_premiere):
    tools = _tools()
    jobs = tmp_path / "jobs"
    _heartbeat(jobs)
    x = _xml(tmp_path)
    t = _panel(jobs, "error=Premiere would not make the project\n", [])
    out = tools.open_in_new_project(str(x), jobs_dir=jobs, start_wait=1, job_wait=10)
    t.join()
    assert out["mode"] == "plain" and "would not make the project" in out["message"]
    assert any(str(x) in c for c in _no_real_premiere)


def test_a_panel_that_takes_the_job_and_never_answers_does_not_hang_forever(tmp_path):
    tools = _tools()
    jobs = tmp_path / "jobs"
    _heartbeat(jobs)
    x = _xml(tmp_path)
    t0 = time.time()
    out = tools.open_in_new_project(str(x), jobs_dir=jobs, start_wait=1, job_wait=1.0)
    assert out["mode"] == "plain" and "no answer" in out["message"] and time.time() - t0 < 10
    assert not list(jobs.glob("*.job"))


def test_only_an_xml_that_exists_is_accepted(tmp_path):
    tools = _tools()
    with pytest.raises(tools.ToolError):
        tools.open_in_new_project(str(tmp_path / "nope.xml"), jobs_dir=tmp_path / "jobs")
    notxml = tmp_path / "a.txt"
    notxml.write_text("x")
    with pytest.raises(tools.ToolError):
        tools.open_in_new_project(str(notxml), jobs_dir=tmp_path / "jobs")


def test_premiere_already_running_without_a_panel_gives_up_in_seconds_not_minutes(tmp_path, monkeypatch):
    # A panel loads when Premiere starts: if Premiere was already running, waiting the long start-up time would only leave the person staring at "Opening..."
    tools = _tools()
    monkeypatch.setattr(tools, "premiere_running", lambda: True)
    x = _xml(tmp_path)
    t0 = time.time()
    out = tools.open_in_new_project(str(x), jobs_dir=tmp_path / "jobs", start_wait=60, job_wait=1, running_wait=0.5)
    assert out["mode"] == "plain" and time.time() - t0 < 10


def test_premiere_not_running_is_given_the_long_wait_to_load_its_panel(tmp_path, monkeypatch):
    tools = _tools()
    monkeypatch.setattr(tools, "premiere_running", lambda: False)
    jobs = tmp_path / "jobs"
    x = _xml(tmp_path)

    def premiere_starts_and_its_panel_reports_in():
        time.sleep(1.5)
        _heartbeat(jobs)
        _panel(jobs, "ok=1\nproject={project}\n", [])

    threading.Thread(target=premiere_starts_and_its_panel_reports_in, daemon=True).start()
    out = tools.open_in_new_project(str(x), jobs_dir=jobs, start_wait=20, job_wait=10, running_wait=0.2)
    assert out["mode"] == "project"
