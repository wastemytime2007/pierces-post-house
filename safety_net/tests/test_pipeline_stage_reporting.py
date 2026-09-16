"""Every pipeline stage must report whether it failed.

Found 2026-09-16 on the Runnells tiling ingest. `transcript_flagging`
emitted `stage_complete` with nothing but the stage name, while every other
stage emitted success/skipped/failed counts. So a run in which ALL FIVE
files failed --- CLI cost mode was off, so there was no LLM client and every
call raised --- produced an event stream indistinguishable from a clean run.
The per-file `status: "failed"` events were there, but the summary the UI
and every log-reader keys on said nothing, and the pipeline reported
`ok: true`.

That is the same class of defect as the silent-sequence and 12:44-Reel
exports in STATUS 2026-09-07: the work didn't happen, and nothing said so.

This test is deliberately STATIC rather than a run of the stage. Running it
needs a project, transcripts, and a live LLM client, which is exactly the
setup whose absence caused the bug --- a hermetic test would have to stub the
failure away. Parsing the source catches any FUTURE stage added without a
failure count, which a test of this one stage would not.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

PIPELINE = (
    Path(__file__).resolve().parents[2]
    / "app" / "python_backend" / "pipeline.py"
)

# audio_sync is a real exception, not an oversight. It is not a per-file
# loop -- it correlates every A-roll against every audio file and reports a
# different, richer shape (pair_count / reliable_count / group_count /
# rescued_count). There is no "this file failed" to count. Any OTHER stage
# claiming an exemption should be argued for here rather than added quietly.
STAGES_WITHOUT_FAILURE_COUNTS = {"audio_sync"}


def _stage_complete_payloads():
    """Every dict literal in pipeline.py whose "type" is "stage_complete"."""
    tree = ast.parse(PIPELINE.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = {
            k.value for k in node.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        }
        if "type" not in keys:
            continue
        for k, v in zip(node.keys, node.values):
            if (isinstance(k, ast.Constant) and k.value == "type"
                    and isinstance(v, ast.Constant)
                    and v.value == "stage_complete"):
                stage = None
                for kk, vv in zip(node.keys, node.values):
                    if (isinstance(kk, ast.Constant) and kk.value == "stage"
                            and isinstance(vv, ast.Constant)):
                        stage = vv.value
                yield stage, keys, node.lineno


def test_pipeline_py_exists():
    assert PIPELINE.exists(), f"expected the forked pipeline at {PIPELINE}"


def test_every_stage_complete_reports_a_failure_count():
    payloads = list(_stage_complete_payloads())
    assert payloads, "found no stage_complete emits -- did pipeline.py move?"

    offenders = [
        (stage, lineno) for stage, keys, lineno in payloads
        if stage not in STAGES_WITHOUT_FAILURE_COUNTS and "failed" not in keys
    ]
    assert not offenders, (
        "these stage_complete events carry no `failed` count, so a run where "
        "every file failed looks identical to a clean one: "
        + ", ".join(f"{s or '<dynamic>'} (pipeline.py:{n})" for s, n in offenders)
    )


def test_transcript_flagging_specifically_reports_counts():
    """The stage that taught us this. Named so a regression points at the
    original failure rather than at an abstract rule."""
    for stage, keys, _ in _stage_complete_payloads():
        if stage == "transcript_flagging":
            missing = {"success", "skipped", "failed"} - keys
            assert not missing, (
                f"transcript_flagging's stage_complete is missing {sorted(missing)}"
            )
            return
    pytest.fail("no stage_complete emit found for transcript_flagging")
