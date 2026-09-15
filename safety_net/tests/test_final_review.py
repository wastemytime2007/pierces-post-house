"""Hermetic tests for posthouse.final_review.

No API key, no media, no ML venv — synthetic idea JSON + a hand-written
FCP7 XML fixture in the same style already proven to parse correctly in
test_benchmark.py.

The one thing these tests exist to prove correct, above everything else:
STEM matching (not path, not exact basename) across a proxy/original
extension mismatch — the idea references ".../proxies/A005_..._Proxy.mp4"
and the final export references "A005_..._Proxy.mov" (same stem,
different directory AND extension), exactly the real shape produced by
exporter._build_proxy_to_original_map. A naive basename-exact match (what
posthouse.benchmark._group_by_source correctly does for its own different
purpose) would silently find zero matches here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from posthouse.final_review import (
    FinalReviewError,
    diff_idea_against_final,
    load_idea_ranges,
    render_report_markdown,
)


def _idea_json(tmp_path: Path, cut_ranges, pool_ranges, title="Test Idea",
                target=60.0) -> Path:
    data = {
        "idea_id": "idea_test0001",
        "kind": "story_angle",
        "data": {
            "brief": {"title": title, "target_duration_sec": target},
            "source_ranges": cut_ranges,
            "pool_ranges": pool_ranges,
        },
    }
    p = tmp_path / "idea_test0001.json"
    p.write_text(json.dumps(data))
    return p


def _range(stem_path, start, end, label="", summary=""):
    return {
        "source_file": stem_path,
        "source_start_sec": start,
        "source_end_sec": end,
        "topic_label": label,
        "summary": summary,
    }


def _xmeml_clip(idx, name, in_frames, out_frames):
    return f"""
              <clipitem id="clip-{idx}">
                <name>{name}</name>
                <rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate>
                <in>{in_frames}</in>
                <out>{out_frames}</out>
                <file id="file-{idx}">
                  <name>{name}</name>
                  <pathurl>file://localhost/Volumes/T7/{name}</pathurl>
                  <rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate>
                  <duration>100000</duration>
                </file>
              </clipitem>"""


def _final_xml(tmp_path: Path, clips: list[tuple[str, str, float, float]]) -> Path:
    """clips: list of (name, dummy, start_sec, end_sec) at 30fps."""
    body = "".join(
        _xmeml_clip(i, name, round(s * 30), round(e * 30))
        for i, (name, _, s, e) in enumerate(clips)
    )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="4">
  <project>
    <children>
      <sequence id="seq-1">
        <name>Final Edit</name>
        <duration>9000</duration>
        <rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate>
        <media>
          <video>
            <track>{body}
            </track>
          </video>
        </media>
      </sequence>
    </children>
  </project>
</xmeml>
"""
    p = tmp_path / "final.xml"
    p.write_text(xml)
    return p


# ---------------------------------------------------------------------------

def test_load_idea_ranges_reads_real_shape(tmp_path):
    p = _idea_json(
        tmp_path,
        cut_ranges=[_range("/proxies/A005_Proxy.mp4", 10.0, 20.0, "hook")],
        pool_ranges=[_range("/proxies/A005_Proxy.mp4", 50.0, 60.0)],
    )
    title, target, cut, pool = load_idea_ranges(p)
    assert title == "Test Idea"
    assert target == 60.0
    assert len(cut) == 1 and cut[0].topic_label == "hook"
    assert cut[0].source_stem == "a005_proxy"  # stripped extension, lowercased
    assert len(pool) == 1


def test_load_idea_ranges_rejects_empty_cut(tmp_path):
    p = _idea_json(tmp_path, cut_ranges=[], pool_ranges=[])
    with pytest.raises(FinalReviewError):
        load_idea_ranges(p)


def test_stem_matches_across_proxy_to_original_extension_change(tmp_path):
    """The one thing this module exists to get right. Idea says .mp4 in a
    proxies/ folder; final export says .mov with no proxies/ folder at
    all — the real, confirmed shape of a resolved original camera file."""
    idea = _idea_json(
        tmp_path,
        cut_ranges=[_range(
            "/Volumes/T7/Longform Intv_Proxies/proxies/A005_A001_Proxy.mp4",
            10.0, 20.0, "hook", "Bob is a workaholic",
        )],
        pool_ranges=[],
    )
    final = _final_xml(tmp_path, [
        ("A005_A001_Proxy.mov", "", 10.0, 20.0),
    ])
    review = diff_idea_against_final(idea, final)
    assert len(review.cut_kept) == 1
    assert review.cut_kept[0].topic_label == "hook"
    assert review.cut_kept[0].coverage_frac == pytest.approx(1.0)
    assert not review.cut_dropped


def test_kept_requires_real_overlap_not_a_sliver(tmp_path):
    """A half-second of incidental overlap must not count as kept — that
    would credit the plan for content Ryan didn't actually use."""
    idea = _idea_json(
        tmp_path,
        cut_ranges=[_range("A005_Proxy.mp4", 0.0, 30.0, "long range")],
        pool_ranges=[],
    )
    # Final only grazes the very start: 0.3s of a 30s proposed range.
    final = _final_xml(tmp_path, [("A005_Proxy.mov", "", 0.0, 0.3)])
    review = diff_idea_against_final(idea, final)
    assert review.cut_dropped and review.cut_dropped[0].topic_label == "long range"
    assert not review.cut_kept


def test_full_classification_kept_dropped_pool_and_added(tmp_path):
    idea = _idea_json(
        tmp_path,
        cut_ranges=[
            _range("A005_Proxy.mp4", 100.0, 110.0, "kept-beat"),
            _range("A005_Proxy.mp4", 200.0, 210.0, "dropped-beat"),
        ],
        pool_ranges=[
            _range("A005_Proxy.mp4", 300.0, 320.0),
        ],
    )
    final = _final_xml(tmp_path, [
        ("A005_Proxy.mov", "", 100.0, 110.0),   # matches the kept cut range exactly
        # dropped-beat (200-210) never appears in the final at all
        ("A005_Proxy.mov", "", 305.0, 315.0),   # inside the pool range -> pulled from pool
        ("A004_Proxy.mov", "", 50.0, 60.0),     # a stem never mentioned anywhere -> added
    ])
    review = diff_idea_against_final(idea, final)

    assert [v.topic_label for v in review.cut_kept] == ["kept-beat"]
    assert [v.topic_label for v in review.cut_dropped] == ["dropped-beat"]

    assert len(review.pool_used) == 1
    pu = review.pool_used[0]
    assert pu.source_stem == "a005_proxy"
    assert pu.start_sec == pytest.approx(305.0) and pu.end_sec == pytest.approx(315.0)

    assert len(review.added_from_elsewhere) == 1
    add = review.added_from_elsewhere[0]
    assert add.source_stem == "a004_proxy"
    assert add.start_sec == pytest.approx(50.0) and add.end_sec == pytest.approx(60.0)


def test_clip_extending_past_a_kept_range_is_reported_as_extra_not_lost(tmp_path):
    """A final clip that covers a proposed range AND runs longer than it
    must not make that extra stretch invisible — it's genuinely new
    footage Ryan chose to include, reported as added-from-elsewhere."""
    idea = _idea_json(
        tmp_path,
        cut_ranges=[_range("A005_Proxy.mp4", 100.0, 110.0, "beat")],
        pool_ranges=[],
    )
    final = _final_xml(tmp_path, [("A005_Proxy.mov", "", 100.0, 130.0)])
    review = diff_idea_against_final(idea, final)
    assert review.cut_kept and review.cut_kept[0].coverage_frac == pytest.approx(1.0)
    assert len(review.added_from_elsewhere) == 1
    add = review.added_from_elsewhere[0]
    assert add.start_sec == pytest.approx(110.0)
    assert add.end_sec == pytest.approx(130.0)


def test_duration_not_available_without_a_video(tmp_path):
    idea = _idea_json(
        tmp_path,
        cut_ranges=[_range("A005_Proxy.mp4", 0.0, 10.0)],
        pool_ranges=[],
    )
    final = _final_xml(tmp_path, [("A005_Proxy.mov", "", 0.0, 10.0)])
    review = diff_idea_against_final(idea, final)
    assert review.final_duration_sec is None
    assert review.planned_cut_duration_sec == pytest.approx(10.0)


def test_report_renders_without_error_and_names_every_bucket(tmp_path):
    idea = _idea_json(
        tmp_path,
        cut_ranges=[
            _range("A005_Proxy.mp4", 0.0, 10.0, "kept"),
            _range("A005_Proxy.mp4", 20.0, 30.0, "dropped"),
        ],
        pool_ranges=[_range("A005_Proxy.mp4", 40.0, 50.0)],
    )
    final = _final_xml(tmp_path, [
        ("A005_Proxy.mov", "", 0.0, 10.0),
        ("A005_Proxy.mov", "", 45.0, 48.0),
        ("A004_Proxy.mov", "", 0.0, 5.0),
    ])
    review = diff_idea_against_final(idea, final)
    text = render_report_markdown(review)
    for heading in ("Kept from the proposed cut", "Dropped from the proposed cut",
                    "Pulled from the pool", "Added from elsewhere"):
        assert heading in text
