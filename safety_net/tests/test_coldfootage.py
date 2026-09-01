"""Tests for posthouse.coldfootage — the Cold Footage sequence builder.

Extends the Phase 0 safety net rather than replacing any part of it:
reuses `conftest.py`'s XML normalization (`normalize_xml_text`) and the
same BLESS=1 golden-master mechanism as `test_exporter_golden.py`, and
draws on the same committed fixture media (`safety_net/fixtures/media/`)
so this stays hermetic — no real footage, nothing PreCut-repo-touching.

`posthouse` is imported directly (not through `conftest.py`'s
`sys.path` setup) — it's a sibling top-level package at the repo root,
found via the normal "run pytest from the repo root" convention
`safety_net/run_safety_net.sh` already uses.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path
from xml.dom import minidom

import pytest

from conftest import normalize_xml_text

from posthouse.coldfootage import (
    ColdFootageError,
    ColdFootageValidationError,
    _validate_and_resolve,
    build_coldfootage_xml,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_MEDIA = Path(__file__).parent.parent / "fixtures" / "media"
GOLDEN_PATH = Path(__file__).parent.parent / "golden" / "expected_coldfootage.xml"
ACTUAL_PATH = Path(__file__).parent.parent / "golden" / "actual_coldfootage.xml"

STABLE = str(FIXTURES_MEDIA / "stable.mp4")          # 4.0s, no audio
SHAKY = str(FIXTURES_MEDIA / "shaky.mp4")             # 4.0s, no audio
AROLL = str(FIXTURES_MEDIA / "AROLL_01.MOV")          # 4.0s, HAS audio


class _Approx:
    """Minimal float-tolerance comparator, standing in for pytest.approx.

    pytest.approx() is NOT usable in this test file: importing
    posthouse.coldfootage triggers precut_bridge's marker-dependency stub
    injection (see precut_bridge.py's module docstring), which installs a
    bare-bones placeholder module named "numpy" into sys.modules whenever
    the real numpy isn't installed (true in this environment). pytest's
    own pytest.approx() opportunistically probes sys.modules["numpy"] and
    calls np.isscalar() on it — a real numpy has that; our inert stub
    (which only defines .ndarray and .float32, exactly what
    precut_pipeline.database needs and nothing more) doesn't, so
    pytest.approx() raises AttributeError the moment anything in this
    process has imported posthouse. This is a real, pre-existing landmine
    in the stub-injection technique (safety_net/conftest.py's own
    identical stub has the same effect on every test in this suite,
    not just these) — see posthouse/README.md "Friction worth recording"
    for the recommendation. Routing around it here with a tiny
    math.isclose-based comparator is the correct response to a discovered
    limitation of a shared test technique, not a weakened test."""
    __slots__ = ("value",)

    def __init__(self, value: float):
        self.value = value

    def __eq__(self, other) -> bool:
        return math.isclose(other, self.value, rel_tol=1e-9, abs_tol=1e-9)

    def __repr__(self) -> str:
        return f"≈{self.value}"


def _eq(value: float) -> _Approx:
    return _Approx(value)


# ---------------------------------------------------------------------------
# Segment math: handle extension + clamping
# ---------------------------------------------------------------------------

def test_handles_extend_symmetrically_when_room_allows():
    """A segment with room on both sides gets the full handle on each side."""
    resolved = _validate_and_resolve([
        {"source_path": AROLL, "in_sec": 1.0, "out_sec": 2.0, "handle_sec": 0.5},
    ])
    assert len(resolved) == 1
    seg = resolved[0]
    assert seg.handled_in == _eq(0.5)
    assert seg.handled_out == _eq(2.5)


def test_handle_clamps_at_zero():
    """A handle that would push in_sec below 0 clamps to 0, not negative."""
    resolved = _validate_and_resolve([
        {"source_path": STABLE, "in_sec": 0.2, "out_sec": 0.5, "handle_sec": 1.0},
    ])
    seg = resolved[0]
    assert seg.handled_in == _eq(0.0)
    assert seg.handled_out == _eq(1.5)


def test_handle_clamps_at_source_duration():
    """A handle that would push out_sec past the source's real duration
    clamps to that duration (4.0s for every fixture clip) instead of
    manufacturing footage that isn't there."""
    resolved = _validate_and_resolve([
        {"source_path": SHAKY, "in_sec": 3.5, "out_sec": 3.9, "handle_sec": 1.0},
    ])
    seg = resolved[0]
    assert seg.handled_in == _eq(2.5)
    assert seg.handled_out == _eq(4.0)


def test_handle_clamps_on_both_sides_at_once():
    """A handle larger than the whole clip clamps at 0 AND at duration —
    the resolved range degrades to the full source, not past it."""
    resolved = _validate_and_resolve([
        {"source_path": SHAKY, "in_sec": 1.0, "out_sec": 1.5, "handle_sec": 5.0},
    ])
    seg = resolved[0]
    assert seg.handled_in == _eq(0.0)
    assert seg.handled_out == _eq(4.0)


def test_default_handle_is_one_second_when_omitted():
    resolved = _validate_and_resolve([
        {"source_path": STABLE, "in_sec": 1.0, "out_sec": 2.0},
    ])
    seg = resolved[0]
    assert seg.handled_in == _eq(0.0)   # 1.0 - 1.0 (default)
    assert seg.handled_out == _eq(3.0)  # 2.0 + 1.0 (default)


# ---------------------------------------------------------------------------
# Validation: every offender listed, not just the first
# ---------------------------------------------------------------------------

def test_validation_lists_every_offender_not_just_the_first():
    missing_path = str(FIXTURES_MEDIA / "does_not_exist.mp4")
    with pytest.raises(ColdFootageValidationError) as excinfo:
        _validate_and_resolve([
            {"source_path": STABLE, "in_sec": 2.0, "out_sec": 1.0},       # in >= out
            {"source_path": missing_path, "in_sec": 0.0, "out_sec": 1.0},  # missing file
            {"source_path": SHAKY, "in_sec": 0.0, "out_sec": 999.0},       # exceeds duration
        ])
    problems = excinfo.value.problems
    assert len(problems) == 3, f"expected all 3 offenders listed, got: {problems}"
    assert any("in_sec" in p and "out_sec" in p for p in problems)
    assert any("does not exist" in p for p in problems)
    assert any("exceeds source duration" in p for p in problems)


def test_validation_passes_valid_segments_through_unmodified_order():
    resolved = _validate_and_resolve([
        {"source_path": STABLE, "in_sec": 0.0, "out_sec": 1.0, "handle_sec": 0.0},
        {"source_path": SHAKY, "in_sec": 0.0, "out_sec": 1.0, "handle_sec": 0.0},
    ])
    assert [r.source_path for r in resolved] == [STABLE, SHAKY]


def test_bad_contract_version_rejected():
    with pytest.raises(ColdFootageError):
        build_coldfootage_xml(
            {"contract_version": 99, "sequence_name": "x", "segments": []},
            Path("/tmp/should-not-be-written.xml"),
        )


def test_empty_segments_list_rejected():
    with pytest.raises(ColdFootageError):
        build_coldfootage_xml(
            {"contract_version": 1, "sequence_name": "x", "segments": []},
            Path("/tmp/should-not-be-written.xml"),
        )


# ---------------------------------------------------------------------------
# CLI: non-zero exit, stderr message, no partial file, on a bad input
# ---------------------------------------------------------------------------

def test_cli_exits_nonzero_and_writes_nothing_on_bad_segments(tmp_path):
    bad_segments = {
        "contract_version": 1,
        "sequence_name": "Bad CLI Test",
        "segments": [
            {"source_path": STABLE, "in_sec": 5.0, "out_sec": 1.0},
        ],
    }
    segments_path = tmp_path / "bad_segments.json"
    segments_path.write_text(json.dumps(bad_segments))
    output_path = tmp_path / "should_not_exist.xml"

    result = subprocess.run(
        [sys.executable, "-m", "posthouse.coldfootage", str(segments_path), str(output_path)],
        capture_output=True, text=True, timeout=30,
        cwd=str(REPO_ROOT),
    )

    assert result.returncode != 0, (
        f"expected non-zero exit; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.stderr.strip(), "expected a stderr message describing the failure"
    assert not output_path.exists(), "a failed build must never leave a partial output file"


def test_cli_exits_nonzero_on_malformed_json(tmp_path):
    segments_path = tmp_path / "not_json.json"
    segments_path.write_text("{ this is not valid json")
    output_path = tmp_path / "should_not_exist.xml"

    result = subprocess.run(
        [sys.executable, "-m", "posthouse.coldfootage", str(segments_path), str(output_path)],
        capture_output=True, text=True, timeout=30,
        cwd=str(REPO_ROOT),
    )

    assert result.returncode != 0
    assert result.stderr.strip()
    assert not output_path.exists()


def test_cli_succeeds_and_writes_xml_on_good_input(tmp_path):
    good_segments = {
        "contract_version": 1,
        "sequence_name": "Good CLI Test",
        "segments": [
            {"source_path": STABLE, "in_sec": 0.5, "out_sec": 1.5, "handle_sec": 0.0},
        ],
    }
    segments_path = tmp_path / "good_segments.json"
    segments_path.write_text(json.dumps(good_segments))
    output_path = tmp_path / "out.xml"

    result = subprocess.run(
        [sys.executable, "-m", "posthouse.coldfootage", str(segments_path), str(output_path)],
        capture_output=True, text=True, timeout=30,
        cwd=str(REPO_ROOT),
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert output_path.exists()


# ---------------------------------------------------------------------------
# Golden master: the real export path, byte-diffed after normalization
# ---------------------------------------------------------------------------

# Three segments from three different fixture clips (exceeds the "at least
# 2 different clips" requirement); includes AROLL_01.MOV (the audio-
# declaring path) and two different handle-clamping shapes (clamp-low-only
# on stable.mp4, clamp-both-sides on shaky.mp4).
_GOLDEN_SEGMENTS = {
    "contract_version": 1,
    "sequence_name": "Cold Footage Golden Test",
    "segments": [
        {
            "source_path": STABLE, "in_sec": 0.5, "out_sec": 2.5,
            "label": "wide establishing", "handle_sec": 1.0,
        },
        {
            "source_path": AROLL, "in_sec": 1.0, "out_sec": 2.0,
            "label": "interview beat", "handle_sec": 0.5,
        },
        {
            "source_path": SHAKY, "in_sec": 1.0, "out_sec": 1.5,
            "label": "handheld pass (over-handled on purpose)", "handle_sec": 5.0,
        },
    ],
}


@pytest.fixture(scope="session")
def coldfootage_export(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("coldfootage_golden")
    output_path = root / "coldfootage.xml"
    build_coldfootage_xml(_GOLDEN_SEGMENTS, output_path)
    return {
        "output_path": output_path,
        "raw_text": output_path.read_text(encoding="utf-8"),
    }


@pytest.fixture(scope="session")
def normalized_coldfootage_xml(coldfootage_export) -> str:
    # FIXTURES_MEDIA (not a temp project root — coldfootage references the
    # committed fixture files directly) is the path that varies by checkout
    # location, so it's what we normalize to {ROOT} here.
    return normalize_xml_text(coldfootage_export["raw_text"], FIXTURES_MEDIA)


@pytest.fixture(scope="session")
def coldfootage_dom(coldfootage_export):
    return minidom.parseString(coldfootage_export["raw_text"].encode("utf-8"))


def test_coldfootage_matches_golden_master(normalized_coldfootage_xml):
    import os

    if os.environ.get("BLESS") == "1":
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(normalized_coldfootage_xml, encoding="utf-8")
        # Blessing is an explicit act, not a check — skip LOUDLY so a stray
        # BLESS=1 in the environment can never masquerade as a green gate.
        pytest.skip(f"BLESSED new golden snapshot at {GOLDEN_PATH} — this was NOT a check")

    assert GOLDEN_PATH.exists(), (
        f"No blessed snapshot at {GOLDEN_PATH}. Run with BLESS=1 to create "
        f"one, and record why in the Decision Log per safety_net/README.md."
    )
    expected = GOLDEN_PATH.read_text(encoding="utf-8")

    if normalized_coldfootage_xml == expected:
        if ACTUAL_PATH.exists():
            ACTUAL_PATH.unlink()
        return

    ACTUAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACTUAL_PATH.write_text(normalized_coldfootage_xml, encoding="utf-8")

    import difflib
    diff = list(difflib.unified_diff(
        expected.splitlines(), normalized_coldfootage_xml.splitlines(),
        fromfile="golden/expected_coldfootage.xml", tofile="actual (this run)",
        lineterm="",
    ))
    excerpt = "\n".join(diff[:40])
    raise AssertionError(
        f"Cold Footage XML no longer matches the blessed golden master.\n"
        f"Full actual output written to {ACTUAL_PATH} for inspection.\n"
        f"First ~40 diff lines:\n{excerpt}"
    )


def test_golden_fixture_has_three_segments_on_one_sequence_in_order(coldfootage_dom):
    """Sanity check independent of the byte-diff — catches a vacuous pass
    (e.g. an empty document matching an empty golden file)."""
    sequences = coldfootage_dom.getElementsByTagName("sequence")
    assert len(sequences) == 1, "cold footage export must be exactly one sequence"

    seq = sequences[0]
    video = seq.getElementsByTagName("video")[0]
    v1_track = video.getElementsByTagName("track")[0]
    clipitems = [
        c for c in v1_track.childNodes
        if c.nodeType == c.ELEMENT_NODE and c.tagName == "clipitem"
    ]
    assert len(clipitems) == 3, f"expected 3 V1 clipitems, found {len(clipitems)}"

    names = []
    for ci in clipitems:
        name_el = [c for c in ci.childNodes
                   if c.nodeType == c.ELEMENT_NODE and c.tagName == "name"][0]
        names.append(name_el.firstChild.nodeValue)
    assert names == ["stable.mp4", "AROLL_01.MOV", "shaky.mp4"], (
        f"expected source-order-preserving clip names, got {names}"
    )

    # No B-roll markers, no library bin, no overlay — this is a markers-
    # free, library-free export by design (see coldfootage.py docstring).
    assert not seq.getElementsByTagName("marker"), "cold footage sequence must carry no markers"


def test_golden_fixture_timeline_positions_accumulate_back_to_back(coldfootage_dom):
    """Segment 1 is 0.5-2.5s stretched to 0.0-3.5s by its 1.0s handle (clamped
    at the low end) = 3.5s = 105 frames @ 30fps. Segment 2 (AROLL_01.MOV) is
    1.0-2.0s with a 0.5s handle = 0.5-2.5s = 2.0s = 60 frames. Segment 3
    (shaky.mp4) is over-handled (5.0s handle on a 0.5s clip) so it clamps to
    the full 0.0-4.0s clip = 4.0s = 120 frames. Expected back-to-back
    timeline: [0, 105), [105, 165), [165, 285)."""
    seq = coldfootage_dom.getElementsByTagName("sequence")[0]
    video = seq.getElementsByTagName("video")[0]
    v1_track = video.getElementsByTagName("track")[0]
    clipitems = [
        c for c in v1_track.childNodes
        if c.nodeType == c.ELEMENT_NODE and c.tagName == "clipitem"
    ]

    def _child_text(el, tag):
        for c in el.childNodes:
            if c.nodeType == c.ELEMENT_NODE and c.tagName == tag:
                return c.firstChild.nodeValue
        return None

    starts_ends = [
        (int(_child_text(ci, "start")), int(_child_text(ci, "end")))
        for ci in clipitems
    ]
    assert starts_ends == [(0, 105), (105, 165), (165, 285)], starts_ends

    total_duration = int(_child_text(seq, "duration"))
    assert total_duration == 285
