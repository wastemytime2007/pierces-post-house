"""normalize_pool_ranges: the leftover bin an idea carries is laid on the timeline with no two leftovers over the same footage and none over the cut (2026-10-13)."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "app" / "python_backend"))

from posthouse.story_architect import TopicRange, normalize_pool_ranges  # noqa: E402

F = "0L9A5643.MP4"


def R(a, b, label="x"):
    return TopicRange(source_file=F, source_start_sec=a, source_end_sec=b, topic_label=label, summary="s")


def _spans(rs):
    return [(round(r.source_start_sec, 2), round(r.source_end_sec, 2)) for r in rs]


def test_overlapping_leftovers_become_one_as_on_the_septic_cut():
    """The Septic idea's bin held 624.93-628.77 and 625.53-637.37 over one file: the export check refused every version of the cut."""
    cut = [R(619.03, 624.90), R(637.37, 642.77)]
    pool = [R(624.93, 628.77), R(625.53, 637.37)]
    assert _spans(normalize_pool_ranges(pool, cut)) == [(624.93, 637.37)]


def test_a_leftover_over_the_cut_loses_the_part_the_cut_uses_and_a_sliver_is_dropped():
    cut = [R(10.0, 20.0)]
    assert _spans(normalize_pool_ranges([R(5.0, 15.0)], cut)) == [(5.0, 10.0)]                       # the cut keeps 10-15
    assert _spans(normalize_pool_ranges([R(5.0, 25.0)], cut)) == [(5.0, 10.0), (20.0, 25.0)]         # the cut sits inside it: two leftovers
    assert normalize_pool_ranges([R(19.8, 20.2)], cut) == []                                         # 0.2 s of leftover is not a clip


def test_files_are_kept_apart_and_a_clean_bin_is_unchanged():
    other = TopicRange(source_file="B.MP4", source_start_sec=1.0, source_end_sec=9.0, topic_label="x", summary="s")
    pool = [R(30.0, 40.0), other, R(50.0, 60.0)]
    out = normalize_pool_ranges(pool, [R(0.0, 5.0)])
    assert [(str(r.source_file), round(r.source_start_sec), round(r.source_end_sec)) for r in out] == [("0L9A5643.MP4", 30, 40), ("0L9A5643.MP4", 50, 60), ("B.MP4", 1, 9)]


@pytest.mark.skipif(not list(Path.home().glob("Library/Application Support/Post House/projects/*/plans/idea_*.json")), reason="no saved ideas on this machine")
def test_every_saved_idea_comes_out_with_a_bin_that_has_no_overlaps():
    for f in Path.home().glob("Library/Application Support/Post House/projects/*/plans/idea_*.json"):
        d = json.loads(f.read_text()).get("data", {})
        used = [TopicRange(**{k: r[k] for k in ("source_file", "source_start_sec", "source_end_sec", "topic_label", "summary")}) for r in d.get("source_ranges", []) if "source_file" in r]
        pool = [TopicRange(**{k: r[k] for k in ("source_file", "source_start_sec", "source_end_sec", "topic_label", "summary")}) for r in d.get("pool_ranges", []) if "source_file" in r]
        out = normalize_pool_ranges(pool, used)
        for i, a in enumerate(out):
            for b in out[i + 1:]:
                assert str(a.source_file) != str(b.source_file) or a.source_end_sec <= b.source_start_sec + 1e-6, f"{f.name}: {a} overlaps {b}"
            for u in used:
                assert str(a.source_file) != str(u.source_file) or a.source_end_sec <= u.source_start_sec + 1e-6 or a.source_start_sec >= u.source_end_sec - 1e-6, f"{f.name}: {a} over the cut {u}"
