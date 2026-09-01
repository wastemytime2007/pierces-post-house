"""Golden-master test for the FCP7 multi-timeline exporter.

The synthetic project (media copy-in, SQLite B-roll index, CutLists,
ExportRequests, and the actual call to export_multi_timeline) is built once
per test session by the `synthetic_project` fixture in conftest.py — see
that file for the full layout and the deliberate scope decisions (no lav
sync, exactly one overlay style).

This file's only job is: normalize the output and diff it against the
blessed snapshot at safety_net/golden/expected_multi.xml.

To (re)bless the snapshot:  BLESS=1 python -m pytest safety_net/tests/test_exporter_golden.py
Re-blessing requires a Decision Log entry (see safety_net/README.md) —
a change to this file's expected output is a change to what Premiere will
see, not a test tweak.
"""
from __future__ import annotations

import difflib
import os
from pathlib import Path

GOLDEN_PATH = Path(__file__).parent.parent / "golden" / "expected_multi.xml"
ACTUAL_PATH = Path(__file__).parent.parent / "golden" / "actual_multi.xml"


def test_multi_timeline_export_matches_golden_master(normalized_xml):
    if os.environ.get("BLESS") == "1":
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(normalized_xml)
        # Blessing always "passes" — it's an explicit act, not a check.
        return

    assert GOLDEN_PATH.exists(), (
        f"No blessed snapshot at {GOLDEN_PATH}. Run with BLESS=1 to create "
        f"one, and record why in the Decision Log per safety_net/README.md."
    )
    expected = GOLDEN_PATH.read_text()

    if normalized_xml == expected:
        if ACTUAL_PATH.exists():
            ACTUAL_PATH.unlink()  # clean up a stale failure artifact
        return

    ACTUAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACTUAL_PATH.write_text(normalized_xml)

    diff = list(difflib.unified_diff(
        expected.splitlines(), normalized_xml.splitlines(),
        fromfile="golden/expected_multi.xml", tofile="actual (this run)",
        lineterm="",
    ))
    excerpt = "\n".join(diff[:40])
    raise AssertionError(
        f"Exported XML no longer matches the blessed golden master.\n"
        f"Full actual output written to {ACTUAL_PATH} for inspection.\n"
        f"First ~40 diff lines:\n{excerpt}"
    )


def test_golden_master_covers_two_sequences_and_the_library_bin(exported_dom):
    """Sanity check on the fixture itself, independent of the byte-diff
    above — if this fails, the synthetic project construction broke in a
    way that could make the golden-master diff pass vacuously (e.g. an
    empty document matching an empty golden file)."""
    sequences = exported_dom.getElementsByTagName("sequence")
    assert len(sequences) >= 2, "golden master must cover at least two sequences"

    bin_names = {
        b.getElementsByTagName("name")[0].firstChild.nodeValue
        for b in exported_dom.getElementsByTagName("bin")
        if b.getElementsByTagName("name") and b.getElementsByTagName("name")[0].firstChild
    }
    assert "B-Roll" in bin_names, "library bin must be present and enabled"
    broll_bin = next(
        b for b in exported_dom.getElementsByTagName("bin")
        if b.getElementsByTagName("name")[0].firstChild.nodeValue == "B-Roll"
    )
    library_clips = [
        c for c in broll_bin.getElementsByTagName("clip")
    ]
    assert len(library_clips) == 5, (
        f"expected 5 B-roll library master clips, found {len(library_clips)}"
    )
