"""The Claude agent that takes the notes the rule-based editor left (creator_tools.agent_edit). The agent itself is not started here: the runner is injected, and writes what the toolbox's `apply`
would have written (the state file of versions) and returns the agent's last message."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app" / "python_backend"))
import creator_tools as ct  # noqa: E402

LEFT = [{"text": "AI: Clip 2 starts mid-sentence. Start at 'Okay.'", "kind": "story", "reason": "made, then its own check failed"},
        {"text": "AI: Clip 3 may start in the middle of a sound.", "kind": "edge-start", "reason": "the clip already starts where the sound begins"},
        {"text": "AI: The number comes with no context.", "kind": "story", "reason": "could not tell which line the note means"}]


def folder_with_review(tmp_path, monkeypatch):
    monkeypatch.setattr(ct, "AGENT_WORK_ROOT", tmp_path / "agent_work")
    f = tmp_path / "V5"
    f.mkdir()
    (f / "ai_review.json").write_text(json.dumps({"notes": [{"text": LEFT[0]["text"], "timeline_sec": 15.7}]}))
    return f


def test_the_agent_gets_the_notes_a_toolbox_and_a_narrow_permission_list_and_each_version_it_makes_is_reported(tmp_path, monkeypatch):
    f = folder_with_review(tmp_path, monkeypatch)
    events, seen = [], {}

    def runner(prompt, work, env, on_line, cancelled):
        seen["work"] = work
        seen["prompt"], seen["notes"] = prompt, json.loads((work / "review_notes.json").read_text())["notes"]
        on_line("The agent is reading the cut")
        (work / "agent_state.json").write_text(json.dumps([{"from": "x.xml", "xml": str(tmp_path / "x_v6.xml"), "folder": str(tmp_path / "V6"), "applied": 1, "notes": 1, "items": [], "url": "http://x/v6"}]))
        return 'I made the first.\n{"made": [{"note": 1, "what": "started clip 2 at Okay."}], "not_made": [{"note": 2, "why": "nothing earlier to add: silence precedes it"}], "final_xml": null, "needs": ""}'

    monkeypatch.setattr(ct, "ai_review", lambda *a, **k: {"notes": [], "checks": [], "summary": ""})                                   # the review of the new version is not what is tested here
    res = ct.agent_edit(str(tmp_path / "x.xml"), str(f), "V5", LEFT, events.append, lambda: False, "root.xml", "open on the door", runner=runner)
    assert seen["work"].parent == tmp_path / "agent_work"                                                       # not inside the project folder: its name can be one the Write tool refuses
    assert "agent_tools.py" in seen["prompt"] and "open on the door" in seen["prompt"] and LEFT[1]["reason"] in seen["prompt"]
    assert [n["text"] for n in seen["notes"]] == [n["text"] for n in LEFT] and seen["notes"][0]["timeline_sec"] == 15.7          # the time comes from the review the note was in
    applied = [e for e in events if e["type"] == "notes_applied"]
    assert [e["label"] for e in applied] == ["V6"] and applied[0]["xml"].endswith("x_v6.xml") and applied[0]["root"] == "root.xml"
    assert res["best"] == "V6" and res["status"] == "left"
    assert [x["text"] for x in res["left"]] == [LEFT[1]["text"], LEFT[2]["text"]]                              # note 1 was made
    assert res["left"][0]["reason"] == "nothing earlier to add: silence precedes it" and res["left"][1]["reason"] == LEFT[2]["reason"]      # the agent's reason, else the editor's own
    assert events[-1]["type"] == "auto_edit_done"


def test_an_agent_that_makes_nothing_says_so_and_a_stopped_one_says_it_was_stopped(tmp_path, monkeypatch):
    f = folder_with_review(tmp_path, monkeypatch)
    none = ct.agent_edit(str(tmp_path / "x.xml"), str(f), "V5", LEFT, None, lambda: False, None, "", runner=lambda *a: '{"made": [], "not_made": [{"note": 1, "why": "the footage does not say it"}]}')
    assert none["best"] == "V5" and none["versions"] == [] and "no new version" in none["summary"] and none["left"][0]["reason"] == "the footage does not say it"
    stopped = ct.agent_edit(str(tmp_path / "x.xml"), str(f), "V5", LEFT, None, lambda: True, None, "", runner=lambda *a: "")
    assert stopped["status"] == "stopped" and "stopped" in stopped["summary"]


def test_the_last_json_object_is_found_after_prose_and_nested_braces():
    assert ct._last_json('text {"a": 1} more text {"made": [{"note": 1}], "x": {"y": 2}} ') == {"made": [{"note": 1}], "x": {"y": 2}}
    assert ct._last_json("no json here") == {}


def test_the_agent_may_run_only_its_toolbox_and_listening_tools_and_write_only_in_its_own_folder(tmp_path):
    allowed = ct.agent_tools_allowed(tmp_path / "agent")
    assert not any(a in ("Bash", "Bash(python3 *)") for a in allowed)
    assert any(a.startswith("Bash(python3 ") and a.endswith("agent_tools.py *)") for a in allowed)
    assert f"Write(/{tmp_path / 'agent'}/**)" in allowed and "Write" not in allowed and "Edit" not in allowed      # two slashes: absolute
