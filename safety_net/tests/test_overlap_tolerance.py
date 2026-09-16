"""The overlap tolerance in verify_export.py is ONE FRAME, and must stay there.

Why this file exists. On 2026-09-16 the Runnells tiling export failed
XML-POOL-NOT-IN-CUT with "2 leftover range(s) already used in the cut". The
real overlap was ONE FRAME -- 0.0167s at 60fps -- where the assembler expands
a cut range slightly for handles and the fragment immediately before it
happens to be in the leftovers pool. The old tolerance was a flat 0.01s,
which is smaller than a frame at 60fps, so an exactly-abutting pair tripped
a check meant to catch duplicated *content*.

Loosening a safety check is how real defects get through later, so the new
tolerance is deliberately derived from the timebase (1.5 frames) rather than
widened to a comfortable number of seconds. These tests pin both ends of
that: the artifact must pass, and every defect the check was built for must
still fail.

The failure this check exists to catch, in Ryan's words about a Mitch
Interview export that passed every check at the time: "this isnt useable at
all" -- clips replaying whole seconds of each other's source, and leftovers
duplicating the cut wholesale.
"""
from __future__ import annotations

import pytest

TIMEBASES = (24.0, 25.0, 30.0, 50.0, 60.0)


def _overlaps(a1, b1, a2, b2, tb):
    """Mirror of verify_export.py's _overlaps, parameterised by timebase."""
    tol = 1.5 * (1.0 / tb)
    return a1 < b2 - tol and b1 > a2 + tol


@pytest.mark.parametrize("tb", TIMEBASES)
def test_exactly_abutting_ranges_are_not_an_overlap(tb):
    """The common, correct case: one clip ends where the next begins."""
    assert not _overlaps(100.0, 200.0, 200.0, 300.0, tb)


@pytest.mark.parametrize("tb", TIMEBASES)
def test_a_one_frame_touch_is_not_an_overlap(tb):
    """The actual Runnells artifact. A single shared boundary frame is a
    rounding consequence of handle expansion, not duplicated footage."""
    frame = 1.0 / tb
    assert not _overlaps(100.0, 200.0 + frame, 200.0, 300.0, tb)


@pytest.mark.parametrize("tb", TIMEBASES)
def test_two_frames_is_still_reported(tb):
    """The tolerance must not creep. Two frames is already a finding --
    this is the line that keeps 'one frame' from becoming 'a second'."""
    frame = 1.0 / tb
    assert _overlaps(100.0, 200.0 + 2 * frame, 200.0, 300.0, tb)


@pytest.mark.parametrize("tb", TIMEBASES)
@pytest.mark.parametrize("seconds", [0.5, 1.0, 5.0, 20.0])
def test_real_duplication_is_always_reported(tb, seconds):
    """Sub-second through Mitch-class. None of these may ever pass."""
    assert _overlaps(100.0, 200.0 + seconds, 200.0, 300.0, tb)


@pytest.mark.parametrize("tb", TIMEBASES)
def test_a_fully_duplicated_range_is_reported(tb):
    """A leftover that IS a cut clip, byte for byte."""
    assert _overlaps(200.0, 300.0, 200.0, 300.0, tb)


@pytest.mark.parametrize("tb", TIMEBASES)
def test_disjoint_ranges_are_never_reported(tb):
    assert not _overlaps(10.0, 20.0, 200.0, 300.0, tb)


def test_tolerance_is_derived_from_the_timebase_not_hardcoded():
    """The whole point. A flat tolerance is wrong at some frame rate: 0.01s
    is under one frame at 60fps (0.0167s), which is what broke. Assert the
    source really computes it from tb rather than carrying a constant."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "verify_export.py").read_text()
    assert "frame = 1.0 / tb" in src, (
        "verify_export.py must derive its overlap tolerance from the "
        "sequence timebase; a hardcoded seconds value is wrong at some "
        "frame rate and that is the bug this file documents."
    )
    assert "a1 < b2 - 0.01" not in src, (
        "the old flat 0.01s tolerance is back -- it is smaller than one "
        "frame at 60fps and fails exactly-abutting clips."
    )
