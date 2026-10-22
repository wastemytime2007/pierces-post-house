"""A Get Creative batch can run for an hour while the person is in Premiere: when it ends, a macOS notification says how it went. The words come from the batch's own record."""
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "app" / "python_backend"))


def _ct():
    import creator_tools
    return creator_tools


def test_message_when_everything_finished():
    ct = _ct()
    title, text = ct.finished_message({"items": [{"status": "done"}, {"status": "skipped"}]})
    assert title == "Get Creative finished" and text == "All 2 sequences finished. Ready for review."


def test_message_names_the_ones_that_need_a_look():
    ct = _ct()
    title, text = ct.finished_message({"items": [{"status": "done"}, {"status": "failed"}, {"status": "done"}]})
    assert text == "2 of 3 finished, 1 need a look."


def test_message_when_stopped_early():
    ct = _ct()
    title, text = ct.finished_message({"items": [{"status": "done"}, {"status": "waiting"}]})
    assert title == "Get Creative stopped" and "1 of 2" in text


def test_message_with_no_record_does_not_raise():
    ct = _ct()
    assert ct.finished_message(None)[0] == "Get Creative finished"


def test_notify_passes_the_text_to_osascript_quoted(monkeypatch):
    ct = _ct()
    calls = []

    class R:
        returncode = 0

    monkeypatch.setattr(ct.sys, "platform", "darwin")
    monkeypatch.setattr(ct.subprocess, "run", lambda *a, **k: (calls.append(a[0]) or R()))
    assert ct.notify("Get Creative finished", 'He said "done"') is True
    assert calls[0][:2] == ["osascript", "-e"] and 'with title "Get Creative finished"' in calls[0][2] and '\\"done\\"' in calls[0][2]


def test_notify_failure_is_quiet(monkeypatch):
    ct = _ct()
    monkeypatch.setattr(ct.sys, "platform", "darwin")

    def boom(*a, **k):
        raise OSError("no osascript")

    monkeypatch.setattr(ct.subprocess, "run", boom)
    assert ct.notify("t", "x") is False
