"""Tests for posthouse.benchmark — the Phase 3 scoring harness.

Three groups:

1. Round-trip the answer-key parser against XML this repo itself can
   manufacture (``posthouse.coldfootage.build_coldfootage_xml`` on the
   committed fixture media) — exact range recovery within one frame.
2. A hand-written, minimal Premiere-style xmeml fixture exercising the
   real-world quirks the parser must tolerate: file-by-id reference
   reuse, percent-encoded pathurl with spaces, an NTSC 29.97 timebase, a
   gap clipitem with no file, and a nested bin/sequence.
3. Scoring semantics on plain ``Range`` objects (no XML/JSON involved) —
   identical sets, disjoint sets, the handle-tolerance boundary exactly
   and beyond, predicted-range merging, per-ruleset breakdown, and the
   basename fallback for a source remounted at a different path.

Avoids ``pytest.approx()`` for the same reason
``safety_net/tests/test_coldfootage.py`` does (see its ``_Approx``
docstring): importing ``posthouse.coldfootage`` in this same test module
installs an inert placeholder ``numpy`` module (via
``precut_bridge``'s marker-dependency stub injection) that lacks
``np.isscalar``, which ``pytest.approx()`` probes and crashes on. A tiny
``math.isclose``-based helper routes around it.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

from posthouse.coldfootage import build_coldfootage_xml
from posthouse.precut_bridge import import_precut
from posthouse.benchmark import (
    AnswerKeyParseError,
    CullsLoadError,
    Range,
    load_culls,
    parse_answer_key_xml,
    score,
    write_report,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_MEDIA = Path(__file__).parent.parent / "fixtures" / "media"

STABLE = str(FIXTURES_MEDIA / "stable.mp4")            # 4.0s, no audio
SHAKY = str(FIXTURES_MEDIA / "shaky.mp4")               # 4.0s, no audio
AROLL = str(FIXTURES_MEDIA / "AROLL_01.MOV")            # 4.0s, HAS audio


def _close(a: float, b: float, tol: float = 0.05) -> bool:
    return math.isclose(a, b, abs_tol=tol)


def _precut_importable() -> bool:
    """True when a real PreCut checkout is reachable (PRECUT_ROOT points
    at a real precut_pipeline). Used to skip the one test in this module
    that legitimately needs PreCut (item 8 of the code review: the CLI
    itself needs neither PRECUT_ROOT nor ffprobe, so its own test should
    not require them either)."""
    try:
        import_precut("precut_pipeline")
        return True
    except Exception:
        return False


_PRECUT_AVAILABLE = _precut_importable()


# ---------------------------------------------------------------------------
# Group 1 — round trip through posthouse.coldfootage's own writer
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _PRECUT_AVAILABLE,
    reason="requires a working PRECUT_ROOT checkout (precut_pipeline not importable)",
)
def test_roundtrip_through_coldfootage_writer_recovers_exact_ranges(tmp_path):
    segments = {
        "contract_version": 1,
        "sequence_name": "Answer Key Roundtrip",
        "segments": [
            {"source_path": STABLE, "in_sec": 0.5, "out_sec": 2.5, "handle_sec": 0.0},
            {"source_path": AROLL, "in_sec": 1.0, "out_sec": 3.0, "handle_sec": 0.0},
            {"source_path": SHAKY, "in_sec": 0.25, "out_sec": 1.75, "handle_sec": 0.0},
        ],
    }
    xml_path = tmp_path / "answer_key.xml"
    build_coldfootage_xml(segments, xml_path)

    ranges = parse_answer_key_xml(xml_path)
    assert len(ranges) == 3

    by_basename = {r.source_basename: r for r in ranges}
    assert set(by_basename) == {"stable.mp4", "AROLL_01.MOV", "shaky.mp4"}

    # One frame at any plausible fixture fps (24-30) is well under 0.05s.
    assert _close(by_basename["stable.mp4"].in_sec, 0.5)
    assert _close(by_basename["stable.mp4"].out_sec, 2.5)
    assert _close(by_basename["AROLL_01.MOV"].in_sec, 1.0)
    assert _close(by_basename["AROLL_01.MOV"].out_sec, 3.0)
    assert _close(by_basename["shaky.mp4"].in_sec, 0.25)
    assert _close(by_basename["shaky.mp4"].out_sec, 1.75)


def test_parse_rejects_missing_file(tmp_path):
    with pytest.raises(AnswerKeyParseError):
        parse_answer_key_xml(tmp_path / "does_not_exist.xml")


def test_parse_rejects_malformed_xml(tmp_path):
    bad = tmp_path / "bad.xml"
    bad.write_text("<xmeml><project>not closed")
    with pytest.raises(AnswerKeyParseError):
        parse_answer_key_xml(bad)


# ---------------------------------------------------------------------------
# Group 2 — hand-written minimal Premiere-style xmeml fixture
# ---------------------------------------------------------------------------

_PREMIERE_STYLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="4">
  <project>
    <name>Runnells Day 1</name>
    <children>
      <bin>
        <name>Nested Bin</name>
        <children>
          <sequence id="nested-seq-1">
            <name>Nested Seq</name>
            <duration>90</duration>
            <rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate>
            <media>
              <video>
                <track>
                  <clipitem id="nested-clipitem-1">
                    <name>NestedClip.mov</name>
                    <rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate>
                    <in>30</in>
                    <out>90</out>
                    <file id="nested-file-1">
                      <name>NestedClip.mov</name>
                      <pathurl>file://localhost/Volumes/Other/NestedClip.mov</pathurl>
                      <rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate>
                    </file>
                  </clipitem>
                </track>
              </video>
            </media>
          </sequence>
        </children>
      </bin>
      <sequence id="sequence-1">
        <name>Runnells Day 1 Selects</name>
        <duration>300</duration>
        <rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate>
        <media>
          <video>
            <track>
              <clipitem id="clipitem-1">
                <name>Clip A.mov</name>
                <rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate>
                <in>150</in>
                <out>450</out>
                <file id="file-1">
                  <name>Clip A.mov</name>
                  <pathurl>file://localhost/Volumes/My%20Drive/Clip%20A.mov</pathurl>
                  <rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate>
                </file>
              </clipitem>
              <clipitem id="clipitem-gap">
                <name>Gap</name>
                <rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate>
                <in>0</in>
                <out>30</out>
              </clipitem>
            </track>
          </video>
          <audio>
            <track>
              <clipitem id="clipitem-1-audio">
                <name>Clip A.mov</name>
                <rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate>
                <in>150</in>
                <out>450</out>
                <file id="file-1"/>
              </clipitem>
            </track>
          </audio>
        </media>
      </sequence>
    </children>
  </project>
</xmeml>
"""


@pytest.fixture
def premiere_style_xml(tmp_path) -> Path:
    path = tmp_path / "premiere_style.xml"
    path.write_text(_PREMIERE_STYLE_XML, encoding="utf-8")
    return path


def test_premiere_quirks_all_handled(premiere_style_xml):
    ranges = parse_answer_key_xml(premiere_style_xml)

    # Gap clipitem contributes nothing; video+audio linked pair (same
    # file-by-id, same in/out) dedupes to ONE range; the nested bin's
    # sequence contributes its own, separate range.
    assert len(ranges) == 2

    by_basename = {r.source_basename: r for r in ranges}
    assert set(by_basename) == {"Clip A.mov", "NestedClip.mov"}

    clip_a = by_basename["Clip A.mov"]
    assert clip_a.source_path == "/Volumes/My Drive/Clip A.mov"  # percent-decoded
    # NTSC 29.97: 150/29.97 = 5.005..., 450/29.97 = 15.015...
    assert _close(clip_a.in_sec, 150 / (30 * 1000 / 1001))
    assert _close(clip_a.out_sec, 450 / (30 * 1000 / 1001))

    nested = by_basename["NestedClip.mov"]
    assert nested.source_path == "/Volumes/Other/NestedClip.mov"
    assert _close(nested.in_sec, 1.0)
    assert _close(nested.out_sec, 3.0)


# ---------------------------------------------------------------------------
# Group 3 — scoring semantics on plain Range objects
# ---------------------------------------------------------------------------

def test_identical_sets_score_perfect():
    truth = [Range("clipA.mov", 0.0, 10.0)]
    predicted = [Range("clipA.mov", 0.0, 10.0)]
    result = score(predicted, truth, handle_tolerance_sec=1.0)
    assert result.overall.precision == 1.0
    assert result.overall.recall == 1.0
    assert result.overall.f1 == 1.0


def test_disjoint_sets_score_zero():
    truth = [Range("clipA.mov", 0.0, 10.0)]
    predicted = [Range("clipA.mov", 20.0, 30.0)]
    result = score(predicted, truth, handle_tolerance_sec=1.0)
    assert result.overall.precision == 0.0
    assert result.overall.recall == 0.0


def test_overcover_by_exactly_the_tolerance_keeps_precision_at_one():
    truth = [Range("clipA.mov", 5.0, 10.0)]
    predicted = [Range("clipA.mov", 4.0, 11.0)]  # +1s each side
    result = score(predicted, truth, handle_tolerance_sec=1.0)
    assert result.overall.precision == 1.0
    assert result.overall.recall == 1.0


def test_overcover_beyond_tolerance_lowers_precision_by_the_right_amount():
    truth = [Range("clipA.mov", 5.0, 10.0)]
    predicted = [Range("clipA.mov", 3.0, 12.0)]  # +2s each side, tol is 1s
    result = score(predicted, truth, handle_tolerance_sec=1.0)
    # dilated truth = (4, 11); predicted = (3, 12), predicted_sec = 9;
    # overlap with dilated truth = (4, 11) = 7 seconds -> precision 7/9.
    assert _close(result.overall.precision, 7 / 9, tol=1e-9)


def test_overlapping_predicted_ranges_are_merged_not_double_counted():
    truth = [Range("clipA.mov", 0.0, 6.0)]
    predicted = [Range("clipA.mov", 0.0, 5.0), Range("clipA.mov", 3.0, 8.0)]
    result = score(predicted, truth, handle_tolerance_sec=0.0)
    per_source = next(iter(result.overall.per_source.values()))
    assert _close(per_source.predicted_sec, 8.0, tol=1e-9)  # merged (0,8), not 5+5=10
    assert _close(per_source.precision, 6 / 8, tol=1e-9)
    assert per_source.recall == 1.0


def test_per_ruleset_breakdown_when_ruleset_present():
    truth = [Range("clipA.mov", 0.0, 10.0)]
    predicted = [
        Range("clipA.mov", 0.0, 5.0, ruleset="narrative"),
        Range("clipA.mov", 5.0, 10.0, ruleset="visual"),
    ]
    result = score(predicted, truth, handle_tolerance_sec=0.0)

    # Combined ("overall") pools both rulesets and covers all of truth.
    assert result.overall.precision == 1.0
    assert result.overall.recall == 1.0

    assert set(result.rulesets) == {"narrative", "visual"}
    assert _close(result.rulesets["narrative"].recall, 0.5, tol=1e-9)
    assert _close(result.rulesets["visual"].recall, 0.5, tol=1e-9)
    assert result.rulesets["narrative"].precision == 1.0
    assert result.rulesets["visual"].precision == 1.0


def test_basename_fallback_across_different_mount_points():
    truth = [Range("/Volumes/DriveA/clip1.mov", 0.0, 10.0)]
    predicted = [Range("/Volumes/DriveB/clip1.mov", 0.0, 10.0)]
    result = score(predicted, truth, handle_tolerance_sec=0.0)
    assert result.overall.precision == 1.0
    assert result.overall.recall == 1.0
    # Grouped as ONE source, not two unmatched ones.
    assert len(result.overall.per_source) == 1


def test_largest_misses_and_false_positives_are_reported():
    truth = [
        Range("clipA.mov", 0.0, 10.0),   # fully missed
        Range("clipA.mov", 20.0, 22.0),  # fully covered
    ]
    predicted = [
        Range("clipA.mov", 20.0, 22.0),   # matches second truth range
        Range("clipA.mov", 50.0, 60.0),   # pure false positive
    ]
    result = score(predicted, truth, handle_tolerance_sec=0.0)

    assert any(m["in_sec"] == 0.0 and m["out_sec"] == 10.0 for m in result.overall.largest_misses)
    assert any(f["in_sec"] == 50.0 and f["out_sec"] == 60.0 for f in result.overall.largest_false_positives)


def test_dilation_does_not_merge_across_a_short_recompose_gap():
    """Code review finding 1 (the most important fix): two truth ranges
    5.0-12.0 and 13.5-20.0 have a 1.5s recompose gap between them (a cut
    between two takes). One unsplit predicted range spanning straight
    across that gap (never cutting on the disturbance) must NOT score a
    perfect, invisible precision — the old ``_dilate`` widened each truth
    range by the full 1.0s tolerance and then merged them into one
    interval covering the whole gap, hiding the false positive entirely."""
    truth = [Range("clipA.mov", 5.0, 12.0), Range("clipA.mov", 13.5, 20.0)]
    predicted = [Range("clipA.mov", 5.0, 20.0)]
    result = score(predicted, truth, handle_tolerance_sec=1.0)

    assert result.overall.precision < 1.0
    assert result.overall.recall == 1.0  # every truth second is still covered
    assert result.overall.largest_false_positives, (
        "the recompose gap must show up as a false positive, not vanish"
    )
    fp = result.overall.largest_false_positives[0]
    assert _close(fp["false_positive_sec"], 0.5, tol=1e-9)


def test_basename_ambiguous_across_two_cards_is_refused_not_miscredited():
    """Code review finding 2: two truth sources share a camera-native
    basename (C0001.MP4 on CardA and CardB). A predicted source with that
    same basename, mounted at yet another path, must NOT be silently
    credited to whichever truth happened to be indexed first — the
    fallback must be refused and the predicted source reported as
    unmatched, contributing to neither truth source's score."""
    truth = [
        Range("/Volumes/X/CardA/C0001.MP4", 0.0, 10.0),
        Range("/Volumes/X/CardB/C0001.MP4", 0.0, 10.0),
    ]
    predicted = [Range("/Volumes/Y/CardB/C0001.MP4", 0.0, 10.0)]
    result = score(predicted, truth, handle_tolerance_sec=0.0)

    # Neither truth source is credited: both fully missed.
    assert result.overall.recall == 0.0
    assert len(result.overall.per_source) == 2
    for per_source in result.overall.per_source.values():
        assert per_source.recall == 0.0
        assert per_source.predicted_sec == 0.0

    assert len(result.unmatched_predicted_sources) == 1
    assert result.unmatched_predicted_sources[0]["source"] == "C0001.MP4"
    assert _close(result.unmatched_predicted_sources[0]["predicted_sec"], 10.0, tol=1e-9)
    assert result.unscored_predicted_sources == []


def test_unscored_predicted_source_excluded_from_overall_but_reported():
    """Code review finding 10 (added after the real answer key landed):
    Ryan's answer key covers only one of two raw clips. A predicted
    source with no truth marked yet must not be scored as a false
    positive — there is no judgment yet to be wrong against — it must be
    excluded from precision/recall/IoU and listed separately."""
    truth = [Range("/Volumes/X/A.MP4", 0.0, 10.0)]
    predicted = [
        Range("/Volumes/X/A.MP4", 0.0, 10.0),
        Range("/Volumes/X/B.MP4", 0.0, 5.0),  # no truth for B at all
    ]
    result = score(predicted, truth, handle_tolerance_sec=0.0)

    assert result.overall.precision == 1.0
    assert result.overall.recall == 1.0
    assert _close(result.overall.predicted_sec, 10.0, tol=1e-9)  # B's 5.0s excluded

    assert len(result.unscored_predicted_sources) == 1
    assert result.unscored_predicted_sources[0]["source"] == "B.MP4"
    assert _close(result.unscored_predicted_sources[0]["predicted_sec"], 5.0, tol=1e-9)
    assert result.unmatched_predicted_sources == []


# ---------------------------------------------------------------------------
# _decode_pathurl
# ---------------------------------------------------------------------------

def test_decode_pathurl_does_not_truncate_on_unencoded_hash():
    """Code review finding 9: urlparse() treats an unencoded '#' as a
    fragment separator, which would silently truncate a real filename
    containing one. Stripping the file://localhost prefix and unquoting
    directly (no urlparse) must not have that problem."""
    from posthouse.benchmark import _decode_pathurl

    path, basename = _decode_pathurl("file://localhost/Volumes/Drive/Clip #A.mov")
    assert path == "/Volumes/Drive/Clip #A.mov"
    assert basename == "Clip #A.mov"


def test_decode_pathurl_handles_percent_encoded_hash_too():
    from posthouse.benchmark import _decode_pathurl

    path, basename = _decode_pathurl("file://localhost/Volumes/Drive/Clip%20%23A.mov")
    assert path == "/Volumes/Drive/Clip #A.mov"
    assert basename == "Clip #A.mov"


# ---------------------------------------------------------------------------
# Nested sequences (answer key)
# ---------------------------------------------------------------------------

_NESTED_SEQUENCE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="4">
  <project>
    <name>Nested Sequence Test</name>
    <children>
      <sequence id="outer-seq">
        <name>Outer Selects</name>
        <duration>600</duration>
        <rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate>
        <media>
          <video>
            <track>
              <clipitem id="outer-clipitem">
                <name>Nested Sub-Sequence</name>
                <rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate>
                <in>0</in>
                <out>600</out>
                <sequence id="inner-seq">
                  <name>Inner Nest</name>
                  <duration>1800</duration>
                  <rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate>
                  <media>
                    <video>
                      <track>
                        <clipitem id="inner-clipitem-1">
                          <name>Clip A.mov</name>
                          <rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate>
                          <in>0</in>
                          <out>900</out>
                          <file id="inner-file-1">
                            <name>Clip A.mov</name>
                            <pathurl>file://localhost/Volumes/My%20Drive/Clip%20A.mov</pathurl>
                            <rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate>
                          </file>
                        </clipitem>
                        <clipitem id="inner-clipitem-2">
                          <name>Clip B.mov</name>
                          <rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate>
                          <in>0</in>
                          <out>900</out>
                          <file id="inner-file-2">
                            <name>Clip B.mov</name>
                            <pathurl>file://localhost/Volumes/My%20Drive/Clip%20B.mov</pathurl>
                            <rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate>
                          </file>
                        </clipitem>
                      </track>
                    </video>
                  </media>
                </sequence>
              </clipitem>
            </track>
          </video>
        </media>
      </sequence>
    </children>
  </project>
</xmeml>
"""


def test_nested_sequence_inside_clipitem_raises_loudly(tmp_path):
    """Code review finding 3: an outer clipitem in=0 out=600 (20s) that
    wraps an inline nested <sequence> whose two inner clipitems are
    0-900 frames each (30s each, 60s total) must not silently yield 60s
    of truth when only the outer 20s is actually on the selects
    sequence. Refusing loudly beats silently over-counting."""
    xml_path = tmp_path / "nested.xml"
    xml_path.write_text(_NESTED_SEQUENCE_XML, encoding="utf-8")

    with pytest.raises(AnswerKeyParseError) as excinfo:
        parse_answer_key_xml(xml_path)

    message = str(excinfo.value)
    assert "Nested Sub-Sequence" in message
    assert "Inner Nest" in message


# ---------------------------------------------------------------------------
# load_culls
# ---------------------------------------------------------------------------

def test_load_culls_reads_coldfootage_shaped_segments_with_ruleset(tmp_path):
    culls = {
        "contract_version": 1,
        "sequence_name": "Cull Test",
        "segments": [
            {"source_path": STABLE, "in_sec": 1.0, "out_sec": 2.0, "ruleset": "narrative"},
            {"source_path": STABLE, "in_sec": 3.0, "out_sec": 4.0, "ruleset": "visual"},
        ],
    }
    culls_path = tmp_path / "culls.json"
    culls_path.write_text(json.dumps(culls))

    ranges = load_culls(culls_path)
    assert len(ranges) == 2
    assert {r.ruleset for r in ranges} == {"narrative", "visual"}


def test_load_culls_rejects_bad_contract_version(tmp_path):
    culls_path = tmp_path / "culls.json"
    culls_path.write_text(json.dumps({"contract_version": 99, "segments": []}))
    with pytest.raises(CullsLoadError):
        load_culls(culls_path)


def test_load_culls_lists_every_offender(tmp_path):
    culls = {
        "contract_version": 1,
        # sequence_name is required (item 7: culls.json is contractually a
        # segments file, and this fixture must not trip that rule too, or
        # the offender count below would drift).
        "sequence_name": "Cull Test",
        "segments": [
            {"source_path": "", "in_sec": 0.0, "out_sec": 1.0},
            {"source_path": STABLE, "in_sec": 2.0, "out_sec": 1.0},
            {"source_path": STABLE, "in_sec": 0.0, "out_sec": 1.0, "ruleset": "bogus"},
        ],
    }
    culls_path = tmp_path / "culls.json"
    culls_path.write_text(json.dumps(culls))
    with pytest.raises(CullsLoadError) as excinfo:
        load_culls(culls_path)
    assert len(excinfo.value.problems) == 3


def test_load_culls_requires_sequence_name(tmp_path):
    """Code review finding 7: culls.json is contractually a segments
    file, so it must be held to the same 'sequence_name' requirement as
    posthouse.coldfootage's own segments loader, closing the drift
    between the two loaders' contracts (coldfootage always required it;
    benchmark's load_culls did not, until both shared one validator)."""
    culls = {
        "contract_version": 1,
        "segments": [{"source_path": STABLE, "in_sec": 0.0, "out_sec": 1.0}],
    }
    culls_path = tmp_path / "culls.json"
    culls_path.write_text(json.dumps(culls))
    with pytest.raises(CullsLoadError) as excinfo:
        load_culls(culls_path)
    assert any("sequence_name" in p for p in excinfo.value.problems)


def test_load_culls_rejects_negative_in_sec(tmp_path):
    culls = {
        "contract_version": 1,
        "sequence_name": "Cull Test",
        "segments": [{"source_path": STABLE, "in_sec": -1.0, "out_sec": 2.0}],
    }
    culls_path = tmp_path / "culls.json"
    culls_path.write_text(json.dumps(culls))
    with pytest.raises(CullsLoadError) as excinfo:
        load_culls(culls_path)
    assert any("in_sec" in p for p in excinfo.value.problems)


def test_load_culls_rejects_nan(tmp_path):
    culls = {
        "contract_version": 1,
        "sequence_name": "Cull Test",
        "segments": [{"source_path": STABLE, "in_sec": float("nan"), "out_sec": 2.0}],
    }
    culls_path = tmp_path / "culls.json"
    culls_path.write_text(json.dumps(culls, allow_nan=True))
    with pytest.raises(CullsLoadError) as excinfo:
        load_culls(culls_path)
    assert len(excinfo.value.problems) == 1


def test_load_culls_rejects_bool_in_sec(tmp_path):
    """bool is an int subclass in Python — float(True) == 1.0 would
    silently accept a stray boolean as a valid in_sec unless bools are
    explicitly rejected."""
    culls = {
        "contract_version": 1,
        "sequence_name": "Cull Test",
        "segments": [{"source_path": STABLE, "in_sec": True, "out_sec": 2.0}],
    }
    culls_path = tmp_path / "culls.json"
    culls_path.write_text(json.dumps(culls))
    with pytest.raises(CullsLoadError) as excinfo:
        load_culls(culls_path)
    assert len(excinfo.value.problems) == 1


def test_load_culls_rejects_non_dict_segment_without_crashing(tmp_path):
    """Code review finding 5: a non-dict entry in 'segments' must produce
    a CullsLoadError listing the offender, not an AttributeError from
    calling .get() on a string or None."""
    culls = {
        "contract_version": 1,
        "sequence_name": "Cull Test",
        "segments": ["oops", None, {"source_path": STABLE, "in_sec": 0.0, "out_sec": 1.0}],
    }
    culls_path = tmp_path / "culls.json"
    culls_path.write_text(json.dumps(culls))
    with pytest.raises(CullsLoadError) as excinfo:
        load_culls(culls_path)
    problems = excinfo.value.problems
    assert len(problems) == 2
    assert any("expected an object, got str" in p for p in problems)
    assert any("expected an object, got NoneType" in p for p in problems)


def test_load_culls_is_exhaustive_across_header_and_segment_problems(tmp_path):
    """Code review finding 6: a bad contract_version must not short-
    circuit before per-segment validation runs — both problem classes
    must be collected and raised together."""
    culls = {
        "contract_version": 99,
        "sequence_name": "Cull Test",
        "segments": [{"source_path": "", "in_sec": 0.0, "out_sec": 1.0}],
    }
    culls_path = tmp_path / "culls.json"
    culls_path.write_text(json.dumps(culls))
    with pytest.raises(CullsLoadError) as excinfo:
        load_culls(culls_path)
    problems = excinfo.value.problems
    assert any("contract_version" in p for p in problems)
    assert any("missing source_path" in p for p in problems)


# ---------------------------------------------------------------------------
# write_report
# ---------------------------------------------------------------------------

def test_write_report_json_roundtrips_and_text_has_no_em_dash(tmp_path):
    truth = [Range("clipA.mov", 0.0, 10.0)]
    predicted = [Range("clipA.mov", 0.0, 10.0, ruleset="narrative")]
    result = score(predicted, truth, handle_tolerance_sec=1.0)

    json_path, txt_path = write_report(result, tmp_path)

    on_disk = json.loads(json_path.read_text(encoding="utf-8"))
    assert on_disk == json.loads(json.dumps(result.to_dict()))

    text = txt_path.read_text(encoding="utf-8")
    assert "—" not in text  # no em dash anywhere in the human summary
    assert "OVERALL" in text
    assert "RULESET: narrative" in text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_scores_successfully_end_to_end(tmp_path):
    """Code review finding 8: the CLI needs neither PRECUT_ROOT nor
    ffprobe (parse_answer_key_xml reads XML, load_culls reads JSON —
    neither touches PreCut or the filesystem beyond the two input
    files), so this test manufactures its answer key by writing the
    already-hand-written _PREMIERE_STYLE_XML fixture straight to disk
    instead of round-tripping through build_coldfootage_xml (which does
    need both). The one place PreCut is legitimately needed —
    test_roundtrip_through_coldfootage_writer_recovers_exact_ranges — is
    marked to skip when precut_pipeline isn't importable."""
    answer_key_path = tmp_path / "answer_key.xml"
    answer_key_path.write_text(_PREMIERE_STYLE_XML, encoding="utf-8")

    culls = {
        "contract_version": 1,
        "sequence_name": "CLI Culls",
        "segments": [
            {"source_path": "/Volumes/My Drive/Clip A.mov", "in_sec": 5.0, "out_sec": 15.0},
        ],
    }
    culls_path = tmp_path / "culls.json"
    culls_path.write_text(json.dumps(culls))

    out_dir = tmp_path / "report"
    result = subprocess.run(
        [
            sys.executable, "-m", "posthouse.benchmark", "score",
            "--answer-key", str(answer_key_path),
            "--culls", str(culls_path),
            "--out", str(out_dir),
        ],
        capture_output=True, text=True, timeout=30,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert (out_dir / "benchmark_report.json").exists()
    assert (out_dir / "benchmark_report.txt").exists()


def test_cli_exits_nonzero_and_lists_every_problem(tmp_path):
    """Code review finding 6: header AND per-segment problems must both
    appear in stderr in one run — this used to pass on the version
    problem alone, since the old load_culls raised before per-segment
    validation ran at all."""
    answer_key_path = tmp_path / "missing_answer_key.xml"

    culls_path = tmp_path / "culls.json"
    culls_path.write_text(json.dumps({
        "contract_version": 99,
        "sequence_name": "CLI Test",
        "segments": [{"source_path": "", "in_sec": 0.0, "out_sec": 1.0}],
    }))

    result = subprocess.run(
        [
            sys.executable, "-m", "posthouse.benchmark", "score",
            "--answer-key", str(answer_key_path),
            "--culls", str(culls_path),
        ],
        capture_output=True, text=True, timeout=30,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode != 0
    assert "answer key" in result.stderr
    assert "culls" in result.stderr
    # Both problem classes present in one run — the version problem AND
    # the per-segment problem, not just whichever one raised first.
    assert "contract_version" in result.stderr
    assert "missing source_path" in result.stderr
