"""posthouse.benchmark — the Phase 3 scoring harness.

Scores the Assistant Editor's cull (``culls.json``, Phase 4) against a
human-made answer key, so the cull is measured rather than tuned by feel
(ground rule 4, "every skill ships with its measurement"). Built now, on
fixtures, so it runs the moment Ryan's real answer key exists.

The answer key
--------------
Per Ryan's chosen method (see the Decision Log, "Benchmark v1 nominated
and staged: Runnells Day 1" — closed this way, not with a "usable but
unused" marking pass over a finished edit): for every usable range in the
raw footage, Ryan sets in/out and inserts it onto one "selects" sequence
in Premiere, then exports that sequence as an FCP7 XML (File > Export >
Final Cut Pro XML). Marking usable ranges directly *is* the answer key —
there is no survivorship gap to correct for, because nothing about "would
this footage have been usable" is being inferred from what a finished cut
happened to keep.

:func:`parse_answer_key_xml` reads that export. It must tolerate real
Premiere output, not just PreCut's own writer (which this module also
supports, since that is what the tests here manufacture via
``posthouse.coldfootage.build_coldfootage_xml``) — see that function's
docstring for the specific quirks handled.

The predicted side
-------------------
:func:`load_culls` reads a segments file shaped exactly like
``posthouse.coldfootage``'s segments contract (``contract_version: 1`` —
see that module's docstring and its shared :func:`~posthouse.coldfootage.
validate_segments_shape`), with one addition: a per-segment
``ruleset: "narrative" | "visual"`` field for dual-use sources, per
``docs/contracts/PROJECT_MANIFEST.md`` §5 ("a dual-use source appears
twice under the same source_id, segments tagged
ruleset: 'narrative' | 'visual'"). The real Phase 4 ``culls.json`` adds
``manifest_id``/``manifest_revision``/``source_id``/``rel_path`` on top of
this (contract §5); its producer is expected to resolve those against the
manifest into the ``source_path`` this loader reads, exactly as
``posthouse.coldfootage`` already consumes plain ``source_path`` values.

Scoring
-------
:func:`score` computes **time-based** precision/recall/F1 per source and
overall, treating a segment's assigned "source" identity as an interval
set on that source's timeline. See the function's own docstring for the
exact handle-tolerance semantics — this is a deliberate, documented
scoring choice, not an incidental implementation detail.

Basename fallback
------------------
:func:`_group_by_source` matches a predicted range to a truth source
primarily by resolved absolute path. When the resolved paths differ, it
falls back to matching by filename (basename, case-insensitive), because
camera-native filenames like ``C0001.MP4`` repeat across cards. That
fallback is used only when a basename maps to EXACTLY ONE truth source
path; when it maps to more than one (e.g. ``/Volumes/CardA/C0001.MP4``
and ``/Volumes/CardB/C0001.MP4`` are both truth), the fallback is refused
for any predicted source sharing that basename — crediting the wrong
card silently is worse than reporting nothing — and that predicted
source is listed in ``Score.unmatched_predicted_sources`` instead of
being scored against either card.

A predicted source that matches no truth at all (not by path, not by an
unambiguous basename fallback) is excluded from precision/recall/IoU
entirely rather than scored as a pure false positive, because there is
no truth yet to judge it against — for example, an answer key that only
covers one of several raw clips. It is listed in
``Score.unscored_predicted_sources`` instead.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from .coldfootage import CONTRACT_VERSION, validate_segments_shape

DEFAULT_HANDLE_TOLERANCE_SEC = 1.0
_BOUNDS_EPSILON_SEC = 0.05  # rounding slack when comparing an out point to a file's own duration
_ALLOWED_RULESETS = {"narrative", "visual"}
_MISS_TOP_N = 20
_FP_TOP_N = 20
_GRANULARITY_EVENT_TOP_N = 20


class BenchmarkError(Exception):
    """Base class for benchmark harness failures."""


class AnswerKeyParseError(BenchmarkError):
    """The answer-key XML could not be parsed into ranges."""


class CullsLoadError(BenchmarkError):
    """``culls.json`` (or the coldfootage-shaped segments file it reuses)
    failed validation. ``.problems`` lists every offender, not just the
    first — same exhaustive-validation convention as
    ``posthouse.coldfootage.ColdFootageValidationError``."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        message = "culls file validation failed:\n" + "\n".join(
            f"  - {p}" for p in problems
        )
        super().__init__(message)


# ---------------------------------------------------------------------------
# Range: the common currency between answer key and predicted culls
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Range:
    """One usable range on one source clip, seconds-based.

    ``source_path`` is the best-effort resolved/decoded absolute path as
    found in the XML or JSON; ``source_basename`` is always populated
    (derived from ``source_path`` if not given) because two drives can
    mount the same footage at different roots, and matching by filename is
    the documented fallback — see the module docstring's "Basename
    fallback" section and :func:`_group_by_source`.
    """
    source_path: str
    in_sec: float
    out_sec: float
    source_basename: str = ""
    ruleset: Optional[str] = None
    label: str = ""

    def __post_init__(self):
        if not self.source_basename:
            basename = self.source_path.rsplit("/", 1)[-1] if self.source_path else ""
            object.__setattr__(self, "source_basename", basename)


# ---------------------------------------------------------------------------
# Answer-key XML parsing (FCP7 xmeml — Premiere's export AND PreCut's own)
# ---------------------------------------------------------------------------

_PATHURL_PREFIXES = ("file://localhost", "file://", "file:")


def _decode_pathurl(pathurl: str) -> tuple[str, str]:
    """Decode an FCP7 ``<pathurl>`` into (path, basename).

    ``path_to_url()`` (precut's ``exporter.py``) emits
    ``file://localhost`` + ``urllib.parse.quote(abs_path, safe="/")``.
    Premiere's own exports use the same ``file://localhost/...``
    convention with percent-encoded spaces/unicode.

    This strips the ``file://localhost`` (or bare ``file://``/``file:``)
    prefix directly and decodes the remainder with ``unquote``, rather
    than routing through ``urllib.parse.urlparse`` — ``urlparse`` treats
    an unencoded ``#`` as a fragment separator and an unencoded ``?`` as
    a query separator, either of which would silently truncate a real
    filename that happens to contain one. The prefix list is checked
    longest-first so ``file://localhost/...`` (host ``localhost``) is
    never mistaken for a bare ``file:///...`` (no host) and left with a
    stray ``localhost`` glued onto the path.
    """
    raw = pathurl
    for prefix in _PATHURL_PREFIXES:
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    raw_path = unquote(raw)
    basename = raw_path.rsplit("/", 1)[-1] if raw_path else pathurl
    return raw_path, basename


def _effective_fps(timebase: int, ntsc: bool) -> float:
    """FCP7's frame rate convention: an NTSC-flagged integer timebase
    (30, 24, 60...) means the *real* frame rate is timebase * 1000/1001
    (29.97, 23.976, 59.94...); a non-NTSC timebase is exact."""
    if ntsc:
        return timebase * (1000.0 / 1001.0)
    return float(timebase)


def _collect_file_defs(root: ET.Element) -> dict[str, dict]:
    """Build file-id -> {pathurl, timebase, ntsc} across the WHOLE document.

    Premiere (and PreCut) write a ``<file>``'s full body (pathurl, rate,
    name) once and reference it afterward as a bare, self-closing
    ``<file id="...">`` with no children. A single forward pass merges
    whichever fields each occurrence of a given id happens to carry, so
    resolution never depends on definition-before-use ordering.
    """
    defs: dict[str, dict] = {}
    for file_el in root.iter("file"):
        fid = file_el.get("id")
        if not fid:
            continue
        entry = defs.setdefault(fid, {})
        pathurl_el = file_el.find("pathurl")
        if pathurl_el is not None and pathurl_el.text:
            entry["pathurl"] = pathurl_el.text.strip()
        rate_el = file_el.find("rate")
        if rate_el is not None:
            tb_text = rate_el.findtext("timebase")
            ntsc_text = rate_el.findtext("ntsc")
            if tb_text:
                try:
                    entry["timebase"] = int(round(float(tb_text)))
                except ValueError:
                    pass
            if ntsc_text is not None:
                entry["ntsc"] = ntsc_text.strip().upper() == "TRUE"
        duration_el = file_el.find("duration")
        if duration_el is not None and duration_el.text:
            try:
                entry["duration_frames"] = int(round(float(duration_el.text)))
            except ValueError:
                pass
    return defs


def _resolve_clipitem_rate(clipitem: ET.Element, file_entry: dict) -> tuple[int, bool]:
    """A clipitem's rate is its own ``<rate>`` if present, else its file's,
    else a conservative 30/non-drop default."""
    rate_el = clipitem.find("rate")
    if rate_el is not None:
        tb_text = rate_el.findtext("timebase")
        if tb_text:
            try:
                timebase = int(round(float(tb_text)))
            except ValueError:
                timebase = None
            if timebase:
                ntsc_text = rate_el.findtext("ntsc")
                ntsc = (ntsc_text or "FALSE").strip().upper() == "TRUE"
                return timebase, ntsc
    if "timebase" in file_entry:
        return file_entry["timebase"], file_entry.get("ntsc", False)
    return 30, False


def _self_consistent_range(
    in_frames: int, out_frames: int, rate: tuple[int, bool], duration_frames: Optional[int]
) -> tuple[float, float, bool]:
    """Convert ``in_frames``/``out_frames`` to seconds using ``rate``, and
    report whether the result is self-consistent with ``duration_frames``
    (also at ``rate``) — i.e. the out point fits inside that duration
    within :data:`_BOUNDS_EPSILON_SEC`.

    When ``duration_frames`` is falsy (no duration known for this
    candidate — e.g. an old-style clipitem with no own ``<duration>``
    tag), there is nothing to check the range against, so this reports
    the candidate as fitting *vacuously* — the caller has no basis to
    reject it, and this preserves pre-existing behavior for XML that
    never carried the extra duration data this check needs.
    """
    timebase, ntsc = rate
    fps = _effective_fps(timebase, ntsc)
    if fps <= 0:
        return float("nan"), float("nan"), False
    in_sec = in_frames / fps
    out_sec = out_frames / fps
    if not duration_frames:
        return in_sec, out_sec, True
    duration_sec = duration_frames / fps
    return in_sec, out_sec, out_sec <= duration_sec + _BOUNDS_EPSILON_SEC


def parse_answer_key_xml(xml_path: Path) -> list[Range]:
    """Parse an FCP7 xmeml answer key into usable :class:`Range` objects.

    Tolerates real Premiere output:

    * ``<clipitem>`` -> ``<file id=...>`` reference reuse (see
      :func:`_collect_file_defs`).
    * Percent-encoded ``<pathurl>`` (``file://localhost/...``), decoded.
    * ``<in>``/``<out>`` in frames, converted to seconds by trying two
      candidate (rate, duration) interpretations in a fixed preference
      order and using the first that is *self-consistent* — i.e. the
      resolved out point fits inside that candidate's own declared
      duration (see :func:`_self_consistent_range`):

      1. The referenced ``<file>``'s own ``<rate>``/``<duration>``. This
         is the common case, and also the fix for the FCP7
         conform-to-sequence quirk: when a clip's native frame rate
         differs from the sequence it is cut into, Premiere writes the
         clipitem's own ``<rate>`` as the SEQUENCE's rate (not the
         source file's), while the ``<in>``/``<out>`` frame counts stay
         in the SOURCE FILE's native rate — so the file's rate is
         required to convert them correctly.
      2. The clipitem's OWN ``<rate>``/``<duration>``, tried only if (1)
         is not self-consistent. This covers a retimed/reinterpreted
         source (e.g. a clip conformed to play at half rate for slow
         motion): Premiere then writes that clipitem instance's own
         ``<duration>`` reflecting the retimed total, which disagrees
         with the file's own duration even though the declared rate
         (timebase) may be identical. In this case the frame numbers
         are proportionally rescaled against the file's REAL duration
         (``(frame / clipitem_own_duration_frames) * (file_duration_frames
         / file_rate)``) rather than divided by the clipitem's own
         (retimed) rate — dividing by the retimed rate would give a
         position in the retimed timeline (up to ~2x past the file's
         actual end), not a real seconds-into-the-source-file position,
         which is the contract every ``Range`` must honor.

      If NEITHER candidate is self-consistent, :class:`AnswerKeyParseError`
      is raised naming the clipitem, the file, and both candidate
      interpretations (their seconds values and why each failed) rather
      than silently emitting an impossible range. A clipitem with no own
      ``<duration>`` tag (the common case) never engages candidate 2 —
      behavior is unchanged from before this two-candidate resolution
      existed.
    * Clipitems with no ``<file>`` child (gaps, titles, adjustment
      layers) are skipped — they carry no source footage.
    * A ``<sequence>`` sitting alongside other sequences under
      ``<project>``/``<children>``/``<bin>`` — every ``<sequence>`` in the
      document is walked independently (this also naturally excludes
      bin-level "whole clip" master-clip entries, which are NOT wrapped
      in a ``<sequence><media><video|audio><track>`` structure and
      therefore never visited).

    Does NOT tolerate a ``<sequence>`` **nested inside a ``<clipitem>``**
    (Premiere's "Nest..." command) — the outer clipitem's in/out trims
    only the nested sequence's *position*, not its content, so counting
    it as one range would silently over-count however much of the nested
    sequence the outer in/out doesn't actually cover. This raises
    :class:`AnswerKeyParseError` naming both clipitem and nested sequence
    instead (Ryan's selects workflow should never produce one; see
    ``benchmark/README.md``).

    Every resolved range is deduplicated (same source + in/out to
    millisecond precision) because a linked video/audio clipitem pair
    describes the SAME edited range twice, and a nested sequence
    referenced from an outer one would otherwise be walked twice.

    Raises :class:`AnswerKeyParseError` on a malformed XML document, a
    nested sequence inside a clipitem, or a document that yields zero
    resolvable ranges.
    """
    xml_path = Path(xml_path)
    if not xml_path.exists():
        raise AnswerKeyParseError(f"answer key not found: {xml_path}")

    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as e:
        raise AnswerKeyParseError(f"{xml_path} is not valid XML: {e}") from e

    root = tree.getroot()
    file_defs = _collect_file_defs(root)

    seen: set[tuple[str, int, int]] = set()
    ranges: list[Range] = []

    for seq in root.iter("sequence"):
        for ci in seq.iter("clipitem"):
            nested_seq_el = ci.find("sequence")
            if nested_seq_el is not None:
                ci_name = ci.findtext("name") or "(unnamed clipitem)"
                nested_name = nested_seq_el.findtext("name") or "(unnamed sequence)"
                raise AnswerKeyParseError(
                    f"{xml_path}: clipitem '{ci_name}' contains a nested "
                    f"<sequence> ('{nested_name}') — nested sequences are not "
                    f"supported (an in/out on the outer clipitem does not "
                    f"trim the sequence nested inside it, so the range would "
                    f"be silently over-counted). Flatten '{nested_name}' onto "
                    f"the Selects sequence in Premiere (copy its contents in "
                    f"directly, rather than nesting it) and re-export."
                )

            file_el = ci.find("file")
            if file_el is None:
                continue  # gap, title, adjustment layer — no source

            fid = file_el.get("id")
            entry = file_defs.get(fid, {}) if fid else {}
            pathurl = entry.get("pathurl")
            if not pathurl:
                inline = file_el.find("pathurl")
                pathurl = inline.text.strip() if inline is not None and inline.text else None
            if not pathurl:
                continue  # unresolvable — never guess a source

            in_text = ci.findtext("in")
            out_text = ci.findtext("out")
            if in_text is None or out_text is None:
                continue
            try:
                in_frames = int(float(in_text))
                out_frames = int(float(out_text))
            except ValueError:
                continue
            if in_frames < 0 or out_frames <= in_frames:
                continue  # -1 (point marker) or degenerate range

            clipitem_rate = _resolve_clipitem_rate(ci, entry)

            ci_duration_frames = None
            ci_duration_el = ci.find("duration")
            if ci_duration_el is not None and ci_duration_el.text:
                try:
                    ci_duration_frames = int(round(float(ci_duration_el.text)))
                except ValueError:
                    pass

            source_path, source_basename = _decode_pathurl(pathurl)

            file_timebase = entry.get("timebase")
            file_duration_frames = entry.get("duration_frames")

            # Candidate 1: the referenced <file>'s own rate/duration — the
            # common case, and the fix for the sequence-conform quirk (see
            # module docstring). Tried first.
            file_rate = None
            file_ok = False
            file_in_sec = file_out_sec = None
            if file_timebase is not None:
                file_rate = (file_timebase, entry.get("ntsc", False))
                file_in_sec, file_out_sec, file_ok = _self_consistent_range(
                    in_frames, out_frames, file_rate, file_duration_frames
                )

            if file_rate is not None and file_ok:
                in_sec, out_sec = file_in_sec, file_out_sec
            else:
                # Candidate 2: the clipitem's OWN rate/duration — the
                # retimed-source case (a clip reinterpreted at the source,
                # e.g. slow motion), tried only because candidate 1 either
                # doesn't exist (no file rate) or wasn't self-consistent.
                # `clip_ok` (whether in/out fit inside the clipitem's own
                # declared duration) is rate-invariant — it only compares
                # frame counts — so it correctly decides WHICH candidate
                # wins regardless of what follows.
                clip_in_sec, clip_out_sec, clip_ok = _self_consistent_range(
                    in_frames, out_frames, clipitem_rate, ci_duration_frames
                )
                if ci_duration_frames and clip_ok and file_rate is not None and file_duration_frames:
                    # The clipitem's own <duration> is a RETIMED total (see
                    # module docstring) — frame_number / clipitem_own_rate
                    # gives a position in the retimed timeline, not a real
                    # position in the actual source file. Rescale
                    # proportionally against the file's real duration
                    # instead: this is rate-agnostic (works for any retime
                    # ratio, not just an assumed 2x) because it only uses
                    # the ratio of declared total span to real total span.
                    file_timebase_disp, file_ntsc_disp = file_rate
                    file_real_duration_sec = file_duration_frames / _effective_fps(
                        file_timebase_disp, file_ntsc_disp
                    )
                    in_sec = (in_frames / ci_duration_frames) * file_real_duration_sec
                    out_sec = (out_frames / ci_duration_frames) * file_real_duration_sec
                elif ci_duration_frames and clip_ok:
                    # No file duration to rescale against (file_rate is
                    # None or file_duration_frames is falsy) — nothing to
                    # rescale to, so the clipitem's own rate is the best
                    # available real-seconds interpretation.
                    in_sec, out_sec = clip_in_sec, clip_out_sec
                elif file_rate is None:
                    # No file rate at all — fall back to the clipitem's own
                    # rate with no duration to check against (pre-existing
                    # default behavior; nothing to raise about).
                    in_sec, out_sec = clip_in_sec, clip_out_sec
                else:
                    # Neither candidate is self-consistent. Candidate 1 only
                    # reaches here when file_duration_frames was actually
                    # present (otherwise file_ok was vacuously True above),
                    # so this is a genuine, reportable disagreement.
                    ci_name = ci.findtext("name") or "(unnamed clipitem)"
                    file_timebase_disp, file_ntsc_disp = file_rate
                    if file_duration_frames:
                        file_dur_sec = file_duration_frames / _effective_fps(
                            file_timebase_disp, file_ntsc_disp
                        )
                        cand1_desc = (
                            f"in={file_in_sec:.2f}s out={file_out_sec:.2f}s vs "
                            f"file's own duration {file_dur_sec:.2f}s "
                            f"({file_duration_frames} frames at "
                            f"{file_timebase_disp}fps) — out of bounds"
                        )
                    else:
                        cand1_desc = (
                            f"in={file_in_sec:.2f}s out={file_out_sec:.2f}s "
                            f"(file has no declared duration to check against)"
                        )
                    clip_timebase_disp, clip_ntsc_disp = clipitem_rate
                    if ci_duration_frames:
                        clip_dur_sec = ci_duration_frames / _effective_fps(
                            clip_timebase_disp, clip_ntsc_disp
                        )
                        cand2_desc = (
                            f"in={clip_in_sec:.2f}s out={clip_out_sec:.2f}s vs "
                            f"clipitem's own duration {clip_dur_sec:.2f}s "
                            f"({ci_duration_frames} frames at "
                            f"{clip_timebase_disp}fps) — out of bounds"
                        )
                    else:
                        cand2_desc = (
                            f"in={clip_in_sec:.2f}s out={clip_out_sec:.2f}s "
                            f"(clipitem has no own <duration> to check against)"
                        )
                    raise AnswerKeyParseError(
                        f"{xml_path}: clipitem '{ci_name}' (file "
                        f"'{source_basename}') has no self-consistent "
                        f"rate/duration interpretation. Candidate 1 — file's "
                        f"own rate ({file_timebase_disp}fps, "
                        f"ntsc={file_ntsc_disp}): {cand1_desc}. Candidate 2 — "
                        f"clipitem's own rate ({clip_timebase_disp}fps, "
                        f"ntsc={clip_ntsc_disp}): {cand2_desc}. Neither fits; "
                        f"the answer key or this parser's rate resolution "
                        f"needs a look."
                    )

            if in_sec != in_sec or out_sec != out_sec:  # NaN — no valid rate resolved
                continue

            dedup_key = (source_path, round(in_sec * 1000), round(out_sec * 1000))
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            ranges.append(Range(
                source_path=source_path,
                source_basename=source_basename,
                in_sec=in_sec,
                out_sec=out_sec,
            ))

    if not ranges:
        raise AnswerKeyParseError(
            f"{xml_path} parsed but yielded zero usable ranges "
            f"(no clipitem in any <sequence> resolved to a source file with a "
            f"valid in/out) — is this the right export?"
        )

    return ranges


# ---------------------------------------------------------------------------
# culls.json / coldfootage-shaped segments loading
# ---------------------------------------------------------------------------

def load_culls(culls_json_path: Path) -> list[Range]:
    """Load a culls/segments file into :class:`Range` objects.

    Reads the ``contract_version: 1`` segments shape
    (``posthouse.coldfootage``'s module docstring and its shared
    :func:`~posthouse.coldfootage.validate_segments_shape` — a
    culls.json is contractually a segments file, so it is held to the
    same shape rules, ``sequence_name`` included), plus the optional
    per-segment ``ruleset`` field for dual-use sources (Project Manifest
    contract §5), validated here on top of the shared shape check.
    Validation is exhaustive — every offending header field and segment
    is collected into :class:`CullsLoadError` rather than stopping at the
    first.
    """
    culls_json_path = Path(culls_json_path)
    if not culls_json_path.exists():
        raise CullsLoadError([f"culls file not found: {culls_json_path}"])

    try:
        raw = culls_json_path.read_text(encoding="utf-8")
    except OSError as e:
        raise CullsLoadError([f"could not read {culls_json_path}: {e}"])

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise CullsLoadError([f"{culls_json_path} is not valid JSON: {e}"])

    problems = validate_segments_shape(data)

    segments = data.get("segments")
    segments = segments if isinstance(segments, list) else []

    # The shared shape check already flags a non-dict/malformed segment;
    # only well-shaped segments reach the ruleset check, added here on
    # top since ruleset is a benchmark-only extension to the contract.
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        ruleset = seg.get("ruleset")
        if ruleset is not None and ruleset not in _ALLOWED_RULESETS:
            problems.append(
                f"segment[{i}] ({seg.get('source_path', '')!r}): ruleset "
                f"{ruleset!r} not one of {sorted(_ALLOWED_RULESETS)}"
            )

    if problems:
        raise CullsLoadError(problems)

    ranges: list[Range] = []
    for seg in segments:
        ranges.append(Range(
            source_path=seg["source_path"],
            in_sec=float(seg["in_sec"]),
            out_sec=float(seg["out_sec"]),
            ruleset=seg.get("ruleset"),
            label=seg.get("label") or "",
        ))

    return ranges


# ---------------------------------------------------------------------------
# Interval math
# ---------------------------------------------------------------------------

Interval = tuple[float, float]


def _merge_intervals(intervals: list[Interval]) -> list[Interval]:
    """Merge overlapping/touching intervals so no scoring math can ever
    double-count the same second twice, whether that second came from two
    overlapping predicted segments or a linked video+audio pair in the
    answer key."""
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [list(ordered[0])]
    for start, end in ordered[1:]:
        last = merged[-1]
        if start <= last[1]:
            last[1] = max(last[1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def _total_sec(intervals: list[Interval]) -> float:
    return sum(e - s for s, e in intervals)


def _dilate(intervals: list[Interval], tol: float) -> list[Interval]:
    """Dilate each (already-merged, disjoint) interval by up to ``tol`` on
    each side, independently — never letting a short gap between two
    truth ranges (a recompose point, a bad take between two good ones)
    disappear into one merged blob.

    A naive "dilate everything by tol, then merge" — which is what this
    used to do — swallows any gap narrower than ``2 * tol`` whole: two
    truth ranges 1.5s apart with a 1.0s handle tolerance dilate to
    touching/overlapping and merge into a single span, so a predicted
    range that spans straight across the gap (never cutting on the
    disturbance between them) scores a perfect, false, 1.0 precision
    with no false positive recorded anywhere. That is exactly the "cull
    that never cuts on short disturbances must not be invisible" failure
    mode this fixes.

    Each side's dilation toward a neighbor is capped so the two
    neighbors' combined claim on the gap can never exceed the gap
    itself: ``dilate_side = min(tol, max(0, gap - tol))``. When the gap
    is wide open (``gap >= 2*tol``) this is just ``tol`` — unrestricted,
    matching the old behavior exactly. As the gap narrows below
    ``2*tol``, both sides give back ground symmetrically, so a strictly
    positive residue of the gap — up to the full gap width — always
    stays outside dilated truth. The residue is what the boundary of the
    two dilated ranges can never fully close: at ``gap == 2*tol`` it is
    exactly zero (touching, no residue — the largest gap this ever
    fully absorbs); below that it grows again, reaching the full,
    un-dilated gap width once ``gap <= tol`` (dilation toward that
    neighbor drops to zero — too tight to safely claim any of it).
    Sides with no neighbor (the outer ends of the source's truth) still
    dilate by the full, uncapped ``tol``.
    """
    if tol <= 0 or not intervals:
        return list(intervals)
    ordered = sorted(intervals)
    n = len(ordered)
    dilated: list[Interval] = []
    for i, (s, e) in enumerate(ordered):
        left = tol
        if i > 0:
            gap = s - ordered[i - 1][1]
            if gap < 2 * tol:
                left = max(0.0, min(tol, gap - tol))
        right = tol
        if i < n - 1:
            gap = ordered[i + 1][0] - e
            if gap < 2 * tol:
                right = max(0.0, min(tol, gap - tol))
        dilated.append((max(0.0, s - left), e + right))
    return dilated


def _overlap_sec(a: list[Interval], b: list[Interval]) -> float:
    """Sum of intersection seconds between two already-merged interval
    lists (sweep over both, sorted)."""
    if not a or not b:
        return 0.0
    total = 0.0
    i = j = 0
    a = sorted(a)
    b = sorted(b)
    while i < len(a) and j < len(b):
        lo = max(a[i][0], b[j][0])
        hi = min(a[i][1], b[j][1])
        if hi > lo:
            total += hi - lo
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return total


def _coverage_of(one: Interval, covering: list[Interval]) -> float:
    """Fraction of a single interval covered by a merged interval set."""
    duration = one[1] - one[0]
    if duration <= 0:
        return 1.0
    return _overlap_sec([one], covering) / duration


# ---------------------------------------------------------------------------
# Granularity: segment counts, duration distributions, and the
# under-/over-segmentation events precision/recall/IoU cannot see.
#
# precision/recall/IoU are purely time-overlap measures: they never look
# at how many pieces the covered time is split into. That let a real
# failure hide in plain sight (Historic Valley Junction, 2026-09-02): a
# detector producing 4 giant blobs (one spanning 154s) that happened to
# blanket most of Ryan's 28 real, granular selects scored a healthy
# P/R/IoU despite doing almost none of the actual culling work. The
# fields and events below are diagnostic, not a gate — no pass/fail
# threshold is invented here; the report reader judges the number.
# ---------------------------------------------------------------------------

@dataclass
class DurationStats:
    """Duration distribution of a set of (already-merged) segments."""
    count: int
    median_sec: float
    mean_sec: float
    p10_sec: float
    p90_sec: float


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Linear-interpolation percentile (matches numpy's default 'linear'
    method) over an already-sorted list. ``pct`` is 0..1."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    k = (n - 1) * pct
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[int(f)] * (c - k) + sorted_vals[int(c)] * (k - f)


def _duration_stats(intervals: list[Interval]) -> Optional[DurationStats]:
    """Duration distribution of ``intervals`` (median, mean, p10, p90), or
    ``None`` when there are no segments to describe (an empty predicted or
    truth set for a source)."""
    if not intervals:
        return None
    durations = sorted(e - s for s, e in intervals)
    n = len(durations)
    return DurationStats(
        count=n,
        median_sec=_percentile(durations, 0.5),
        mean_sec=sum(durations) / n,
        p10_sec=_percentile(durations, 0.10),
        p90_sec=_percentile(durations, 0.90),
    )


def _granularity_ratio(predicted_count: int, truth_count: int) -> Optional[float]:
    """predicted_segment_count / truth_segment_count. ``None`` only in the
    degenerate 0-truth-segments case (no truth at all to divide by) —
    never raised as ZeroDivisionError, and never emitted as a JSON-illegal
    infinity."""
    if truth_count == 0:
        return None
    return predicted_count / truth_count


def _find_under_segmentation_events(
    pred_intervals: list[Interval],
    truth_intervals: list[Interval],
    handle_tolerance_sec: float,
    display: str,
) -> list[dict]:
    """For each PREDICTED segment (raw span), find how many DISTINCT
    merged truth segments it overlaps. 2+ overlapped truth segments with a
    real gap between them (wider than ``handle_tolerance_sec``, so this is
    never just edge-tolerance noise) means the predicted segment fused
    together footage Ryan actually treated as separate selects — the
    Historic Valley Junction failure mode. Sorted most-severe first, same
    fields-and-sorting spirit as ``largest_misses``/``largest_false_positives``."""
    events: list[dict] = []
    truth_sorted = sorted(truth_intervals)
    for ps, pe in sorted(pred_intervals):
        overlapped = [t for t in truth_sorted if min(pe, t[1]) > max(ps, t[0])]
        if len(overlapped) < 2:
            continue
        swallowed_gap_sec = sum(
            s2 - e1
            for (_, e1), (s2, _) in zip(overlapped, overlapped[1:])
            if s2 - e1 > handle_tolerance_sec
        )
        if swallowed_gap_sec <= 0:
            continue
        events.append({
            "source": display,
            "predicted_in_sec": ps,
            "predicted_out_sec": pe,
            "predicted_duration_sec": pe - ps,
            "truth_segment_count": len(overlapped),
            "truth_segments": [{"in_sec": s, "out_sec": e} for s, e in overlapped],
            "swallowed_gap_sec": swallowed_gap_sec,
        })
    events.sort(key=lambda ev: ev["swallowed_gap_sec"], reverse=True)
    return events


def _find_over_segmentation_events(
    pred_intervals: list[Interval],
    truth_intervals: list[Interval],
    handle_tolerance_sec: float,
    display: str,
) -> list[dict]:
    """The symmetric case: a single merged TRUTH segment covered by 2+
    separate PREDICTED segments with a real internal gap between them
    (wider than ``handle_tolerance_sec``) not present in truth — the
    detector cut where Ryan did not. Sorted most-severe first."""
    events: list[dict] = []
    pred_sorted = sorted(pred_intervals)
    for ts, te in sorted(truth_intervals):
        overlapped = [p for p in pred_sorted if min(te, p[1]) > max(ts, p[0])]
        if len(overlapped) < 2:
            continue
        split_gap_sec = sum(
            s2 - e1
            for (_, e1), (s2, _) in zip(overlapped, overlapped[1:])
            if s2 - e1 > handle_tolerance_sec
        )
        if split_gap_sec <= 0:
            continue
        events.append({
            "source": display,
            "truth_in_sec": ts,
            "truth_out_sec": te,
            "truth_duration_sec": te - ts,
            "predicted_segment_count": len(overlapped),
            "predicted_segments": [{"in_sec": s, "out_sec": e} for s, e in overlapped],
            "split_gap_sec": split_gap_sec,
        })
    events.sort(key=lambda ev: ev["split_gap_sec"], reverse=True)
    return events


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@dataclass
class PerSourceScore:
    source_key: str
    display_name: str
    precision: float
    recall: float
    f1: float
    iou: float
    predicted_sec: float
    truth_sec: float
    overlap_sec: float
    predicted_segment_count: int = 0
    truth_segment_count: int = 0
    granularity_ratio: Optional[float] = None
    predicted_duration_stats: Optional[DurationStats] = None
    truth_duration_stats: Optional[DurationStats] = None


@dataclass
class ScoreBlock:
    label: str
    precision: float
    recall: float
    f1: float
    iou: float
    predicted_sec: float
    truth_sec: float
    overlap_sec: float
    per_source: dict[str, PerSourceScore] = field(default_factory=dict)
    largest_misses: list[dict] = field(default_factory=list)
    largest_false_positives: list[dict] = field(default_factory=list)
    unmatched_predicted_sources: list[dict] = field(default_factory=list)
    unscored_predicted_sources: list[dict] = field(default_factory=list)
    predicted_segment_count: int = 0
    truth_segment_count: int = 0
    granularity_ratio: Optional[float] = None
    predicted_duration_stats: Optional[DurationStats] = None
    truth_duration_stats: Optional[DurationStats] = None
    under_segmentation_events: list[dict] = field(default_factory=list)
    over_segmentation_events: list[dict] = field(default_factory=list)


@dataclass
class Score:
    overall: ScoreBlock
    rulesets: dict[str, ScoreBlock] = field(default_factory=dict)
    handle_tolerance_sec: float = DEFAULT_HANDLE_TOLERANCE_SEC
    unmatched_predicted_sources: list[dict] = field(default_factory=list)
    unscored_predicted_sources: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _group_by_source(
    predicted: list[Range], truth: list[Range]
) -> tuple[dict[str, tuple[list[Range], list[Range], str]], set[str]]:
    """Group predicted and truth ranges into shared source identities.

    Matches primarily by resolved absolute path; falls back to basename
    when the resolved paths differ (a drive remounted at a different
    mount point) — see the module docstring's "Basename fallback"
    section. The fallback is refused (not applied at all) when a
    basename maps to MORE THAN ONE truth path — camera-native filenames
    repeat across cards, and crediting the wrong one silently is worse
    than reporting nothing; such a predicted source's own resolved path
    is used as its group key instead (so it lands in a truth-less group,
    same as a source with no truth anywhere), and that key is returned
    in the second element of the tuple so the caller can tell "ambiguous,
    refused" truth-less groups apart from "no truth anywhere" ones.

    A source appearing on only one side still gets its own group (all-FP
    or all-miss), because a silently dropped source is worse than a
    correctly-reported one — the caller decides, per item 10 of the
    dilation/basename review, whether a truth-less group (no truth
    anywhere for that source) counts toward totals or is reported
    separately as unscored.
    """
    def _resolved(path: str) -> str:
        try:
            return str(Path(path).resolve())
        except (OSError, RuntimeError):
            return path

    truth_by_resolved: dict[str, list[Range]] = {}
    for r in truth:
        truth_by_resolved.setdefault(_resolved(r.source_path), []).append(r)

    # basename (lowercased) -> set of truth keys sharing it. More than one
    # entry means the fallback is ambiguous and must be refused.
    basename_to_truth_keys: dict[str, set[str]] = {}
    for key, ranges in truth_by_resolved.items():
        bn = ranges[0].source_basename.lower()
        basename_to_truth_keys.setdefault(bn, set()).add(key)

    predicted_by_key: dict[str, list[Range]] = {}
    unmatched_predicted_sources: set[str] = set()
    for r in predicted:
        resolved = _resolved(r.source_path)
        if resolved in truth_by_resolved:
            key = resolved
        else:
            candidates = basename_to_truth_keys.get(r.source_basename.lower(), set())
            if len(candidates) == 1:
                key = next(iter(candidates))
            else:
                # Zero candidates: no truth anywhere under this basename
                # either — a genuinely truth-less source, handled by the
                # caller as "unscored". More than one candidate: the
                # basename is ambiguous across truth sources (e.g. the
                # same camera-native filename on two different cards) —
                # refuse the fallback rather than credit the wrong one.
                key = resolved
                if len(candidates) > 1:
                    unmatched_predicted_sources.add(key)
        predicted_by_key.setdefault(key, []).append(r)

    all_keys = set(truth_by_resolved) | set(predicted_by_key)
    groups: dict[str, tuple[list[Range], list[Range], str]] = {}
    for key in all_keys:
        preds = predicted_by_key.get(key, [])
        truths = truth_by_resolved.get(key, [])
        display = (truths[0].source_basename if truths
                   else preds[0].source_basename if preds else key)
        groups[key] = (preds, truths, display)
    return groups, unmatched_predicted_sources


def _score_block(
    predicted: list[Range], truth: list[Range], handle_tolerance_sec: float, label: str
) -> ScoreBlock:
    groups, unmatched_keys = _group_by_source(predicted, truth)

    per_source: dict[str, PerSourceScore] = {}
    total_predicted = 0.0
    total_truth = 0.0
    total_overlap_raw = 0.0        # for recall / IoU
    total_overlap_precision = 0.0  # for precision (tolerance-forgiving)

    misses: list[dict] = []
    false_positives: list[dict] = []
    unmatched_predicted_sources: list[dict] = []
    unscored_predicted_sources: list[dict] = []
    under_segmentation_events: list[dict] = []
    over_segmentation_events: list[dict] = []
    all_pred_intervals: list[Interval] = []
    all_truth_intervals: list[Interval] = []

    for key, (preds, truths, display) in groups.items():
        if not truths:
            # No truth for this source at all — scoring it would either
            # falsely credit a basename that actually belongs to a
            # different truth source (the refused-fallback case) or
            # record false positives against footage nobody has judged
            # yet (the no-truth-anywhere case). Neither belongs in
            # precision/recall/IoU; both are reported separately instead.
            predicted_sec = _total_sec(_merge_intervals([(r.in_sec, r.out_sec) for r in preds]))
            entry = {"source": display, "predicted_sec": predicted_sec}
            if key in unmatched_keys:
                unmatched_predicted_sources.append(entry)
            else:
                unscored_predicted_sources.append(entry)
            continue

        pred_intervals = _merge_intervals([(r.in_sec, r.out_sec) for r in preds])
        truth_intervals = _merge_intervals([(r.in_sec, r.out_sec) for r in truths])
        truth_dilated = _dilate(truth_intervals, handle_tolerance_sec)

        predicted_sec = _total_sec(pred_intervals)
        truth_sec = _total_sec(truth_intervals)
        overlap_raw = _overlap_sec(pred_intervals, truth_intervals)
        overlap_precision = _overlap_sec(pred_intervals, truth_dilated)

        precision = 1.0 if predicted_sec == 0 else min(1.0, overlap_precision / predicted_sec)
        recall = 1.0 if truth_sec == 0 else overlap_raw / truth_sec
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        union = predicted_sec + truth_sec - overlap_raw
        iou = 1.0 if union <= 0 else overlap_raw / union

        predicted_segment_count = len(pred_intervals)
        truth_segment_count = len(truth_intervals)

        per_source[key] = PerSourceScore(
            source_key=key, display_name=display,
            precision=precision, recall=recall, f1=f1, iou=iou,
            predicted_sec=predicted_sec, truth_sec=truth_sec, overlap_sec=overlap_raw,
            predicted_segment_count=predicted_segment_count,
            truth_segment_count=truth_segment_count,
            granularity_ratio=_granularity_ratio(predicted_segment_count, truth_segment_count),
            predicted_duration_stats=_duration_stats(pred_intervals),
            truth_duration_stats=_duration_stats(truth_intervals),
        )

        total_predicted += predicted_sec
        total_truth += truth_sec
        total_overlap_raw += overlap_raw
        total_overlap_precision += overlap_precision
        all_pred_intervals.extend(pred_intervals)
        all_truth_intervals.extend(truth_intervals)

        under_segmentation_events.extend(
            _find_under_segmentation_events(pred_intervals, truth_intervals, handle_tolerance_sec, display)
        )
        over_segmentation_events.extend(
            _find_over_segmentation_events(pred_intervals, truth_intervals, handle_tolerance_sec, display)
        )

        # Largest misses: each individual truth range (not the merged
        # source total) that is under 50% covered by predicted footage.
        for r in truths:
            cov = _coverage_of((r.in_sec, r.out_sec), pred_intervals)
            if cov < 0.5:
                misses.append({
                    "source": display,
                    "in_sec": r.in_sec,
                    "out_sec": r.out_sec,
                    "duration_sec": r.out_sec - r.in_sec,
                    "coverage": cov,
                    "uncovered_sec": (r.out_sec - r.in_sec) * (1.0 - cov),
                })

        # Largest false positives: merged predicted intervals (to avoid
        # double-counting overlapping predicted segments) with real time
        # outside the tolerance-widened truth for this source.
        for s, e in pred_intervals:
            fp_sec = (e - s) - _coverage_of((s, e), truth_dilated) * (e - s)
            if fp_sec > 1e-6:
                false_positives.append({
                    "source": display,
                    "in_sec": s,
                    "out_sec": e,
                    "duration_sec": e - s,
                    "false_positive_sec": fp_sec,
                })

    precision_overall = 1.0 if total_predicted == 0 else min(1.0, total_overlap_precision / total_predicted)
    recall_overall = 1.0 if total_truth == 0 else total_overlap_raw / total_truth
    f1_overall = (
        2 * precision_overall * recall_overall / (precision_overall + recall_overall)
        if (precision_overall + recall_overall) > 0 else 0.0
    )
    union_overall = total_predicted + total_truth - total_overlap_raw
    iou_overall = 1.0 if union_overall <= 0 else total_overlap_raw / union_overall

    misses.sort(key=lambda m: m["uncovered_sec"], reverse=True)
    false_positives.sort(key=lambda f: f["false_positive_sec"], reverse=True)
    unmatched_predicted_sources.sort(key=lambda u: u["predicted_sec"], reverse=True)
    unscored_predicted_sources.sort(key=lambda u: u["predicted_sec"], reverse=True)
    under_segmentation_events.sort(key=lambda ev: ev["swallowed_gap_sec"], reverse=True)
    over_segmentation_events.sort(key=lambda ev: ev["split_gap_sec"], reverse=True)

    predicted_segment_count_overall = len(all_pred_intervals)
    truth_segment_count_overall = len(all_truth_intervals)

    return ScoreBlock(
        label=label,
        precision=precision_overall, recall=recall_overall, f1=f1_overall, iou=iou_overall,
        predicted_sec=total_predicted, truth_sec=total_truth, overlap_sec=total_overlap_raw,
        per_source=per_source,
        largest_misses=misses[:_MISS_TOP_N],
        largest_false_positives=false_positives[:_FP_TOP_N],
        unmatched_predicted_sources=unmatched_predicted_sources,
        unscored_predicted_sources=unscored_predicted_sources,
        predicted_segment_count=predicted_segment_count_overall,
        truth_segment_count=truth_segment_count_overall,
        granularity_ratio=_granularity_ratio(predicted_segment_count_overall, truth_segment_count_overall),
        predicted_duration_stats=_duration_stats(all_pred_intervals),
        truth_duration_stats=_duration_stats(all_truth_intervals),
        under_segmentation_events=under_segmentation_events[:_GRANULARITY_EVENT_TOP_N],
        over_segmentation_events=over_segmentation_events[:_GRANULARITY_EVENT_TOP_N],
    )


def score(
    predicted: list[Range], truth: list[Range], *,
    handle_tolerance_sec: float = DEFAULT_HANDLE_TOLERANCE_SEC,
) -> Score:
    """Score predicted culls against the answer key.

    **Overlap and tolerance semantics (deliberate scoring choices):**

    * Both predicted and truth ranges are merged (overlap-or-touch) per
      source before any measurement, so overlapping predicted segments or
      a linked video+audio pair in the answer key can never double-count.
    * **Precision** = (predicted seconds landing inside truth ranges
      *widened by* ``handle_tolerance_sec`` on each side) / total
      predicted seconds. Widening truth, not shrinking predicted, is what
      makes "a predicted range that over-covers truth by exactly the
      handle tolerance" score precision 1.0: the cull is expected to add
      trim handles (ROADMAP §4), so the first ``handle_tolerance_sec`` of
      predicted overhang on each side is neutral, never a false positive.
      Overhang *beyond* the tolerance is a real false positive and lowers
      precision by exactly that excess.
    * **Recall** = (predicted seconds landing inside the RAW, un-widened
      truth ranges) / total truth seconds — handles are a forgiveness on
      the predicted side only; they never let the cull claim credit for
      less truth than it actually covered.
    * **IoU** is the standard intersection/union of the raw (un-widened)
      merged interval sets, reported per source and overall.
    * A source with zero predicted seconds gets precision = 1.0 (nothing
      to be a false positive); a source with zero truth seconds gets
      recall = 1.0 (nothing to miss). This is a deliberate zero-division
      convention (equivalent to scikit-learn's zero_division=1), not an
      arbitrary default — flagged here because ROADMAP §5 does not
      specify one.
    * **Dual-use** (``ruleset`` on predicted segments): each distinct
      ruleset value present is scored separately against the SAME truth
      set (the answer key does not distinguish rulesets — the two
      questions are "does the narrative cull find the talking-head
      usable ranges" and "does the visual cull find the coverage usable
      ranges" against one shared ground truth), and ``overall`` scores
      all predicted segments pooled regardless of ruleset.
    * **Truth scope**: a predicted source with no truth ranges at all —
      including one whose basename fallback was refused as ambiguous —
      is excluded from precision/recall/IoU entirely (not scored as a
      pure false positive), because an answer key legitimately does not
      have to cover every source yet (see the module docstring's
      "Basename fallback" section). It is reported in
      ``Score.unscored_predicted_sources`` (or
      ``Score.unmatched_predicted_sources`` for the ambiguous-basename
      case) instead.
    """
    overall = _score_block(predicted, truth, handle_tolerance_sec, "overall")

    ruleset_values = sorted({r.ruleset for r in predicted if r.ruleset})
    rulesets = {
        v: _score_block([r for r in predicted if r.ruleset == v], truth, handle_tolerance_sec, v)
        for v in ruleset_values
    }

    return Score(
        overall=overall, rulesets=rulesets, handle_tolerance_sec=handle_tolerance_sec,
        unmatched_predicted_sources=overall.unmatched_predicted_sources,
        unscored_predicted_sources=overall.unscored_predicted_sources,
    )


# ---------------------------------------------------------------------------
# Report writing
# ---------------------------------------------------------------------------

def _format_block_text(block: ScoreBlock, heading: str) -> str:
    lines = [
        heading,
        "-" * len(heading),
        f"Precision: {block.precision:.3f}",
        f"Recall:    {block.recall:.3f}",
        f"F1:        {block.f1:.3f}",
        f"IoU:       {block.iou:.3f}",
        f"Predicted: {block.predicted_sec:.1f}s across {len(block.per_source)} sources",
        f"Truth:     {block.truth_sec:.1f}s",
        "",
        f"Granularity: {block.predicted_segment_count} predicted segments vs "
        f"{block.truth_segment_count} truth segments"
        + (
            f" (ratio {block.granularity_ratio:.2f}; near 1.0 is healthy, "
            f"well below flags under-segmentation, well above flags over-segmentation)"
            if block.granularity_ratio is not None else ""
        ),
    ]
    if block.predicted_duration_stats:
        d = block.predicted_duration_stats
        lines.append(
            f"  Predicted segment duration: median {d.median_sec:.1f}s, mean {d.mean_sec:.1f}s, "
            f"p10 {d.p10_sec:.1f}s, p90 {d.p90_sec:.1f}s"
        )
    if block.truth_duration_stats:
        d = block.truth_duration_stats
        lines.append(
            f"  Truth segment duration:     median {d.median_sec:.1f}s, mean {d.mean_sec:.1f}s, "
            f"p10 {d.p10_sec:.1f}s, p90 {d.p90_sec:.1f}s"
        )
    lines.append("")
    if block.under_segmentation_events:
        lines.append("Under-segmentation (one predicted segment swallows multiple distinct truth segments):")
        for ev in block.under_segmentation_events[:10]:
            lines.append(
                f"  - {ev['source']} [{ev['predicted_in_sec']:.1f}s to {ev['predicted_out_sec']:.1f}s] "
                f"({ev['predicted_duration_sec']:.1f}s) swallows {ev['truth_segment_count']} truth "
                f"segments, {ev['swallowed_gap_sec']:.1f}s of real gap lost"
            )
        lines.append("")
    if block.over_segmentation_events:
        lines.append("Over-segmentation (one truth segment split across multiple predicted segments):")
        for ev in block.over_segmentation_events[:10]:
            lines.append(
                f"  - {ev['source']} [{ev['truth_in_sec']:.1f}s to {ev['truth_out_sec']:.1f}s] "
                f"({ev['truth_duration_sec']:.1f}s) split into {ev['predicted_segment_count']} predicted "
                f"segments, {ev['split_gap_sec']:.1f}s of gap not present in truth"
            )
        lines.append("")
    if block.largest_misses:
        lines.append("Largest misses (truth ranges under 50% covered):")
        for m in block.largest_misses[:10]:
            lines.append(
                f"  - {m['source']} [{m['in_sec']:.1f}s to {m['out_sec']:.1f}s] "
                f"({m['duration_sec']:.1f}s), coverage {m['coverage']*100:.0f}%"
            )
        lines.append("")
    if block.largest_false_positives:
        lines.append("Largest false positives:")
        for f in block.largest_false_positives[:10]:
            lines.append(
                f"  - {f['source']} [{f['in_sec']:.1f}s to {f['out_sec']:.1f}s] "
                f"({f['false_positive_sec']:.1f}s not in truth)"
            )
        lines.append("")
    if block.unscored_predicted_sources:
        lines.append("Unscored predicted sources (no truth marked yet, excluded from the totals above):")
        for u in block.unscored_predicted_sources[:10]:
            lines.append(f"  - {u['source']} ({u['predicted_sec']:.1f}s predicted)")
        lines.append("")
    if block.unmatched_predicted_sources:
        lines.append("Unmatched predicted sources (basename matches more than one truth source; excluded from the totals above):")
        for u in block.unmatched_predicted_sources[:10]:
            lines.append(f"  - {u['source']} ({u['predicted_sec']:.1f}s predicted)")
        lines.append("")
    return "\n".join(lines)


def _format_report_text(result: Score) -> str:
    lines = [
        "Pierce's Post House. Benchmark report.",
        f"Handle tolerance: {result.handle_tolerance_sec:.2f}s",
        "",
        _format_block_text(result.overall, "OVERALL"),
    ]
    for name, block in sorted(result.rulesets.items()):
        lines.append(_format_block_text(block, f"RULESET: {name}"))
    return "\n".join(lines)


def write_report(score_result: Score, out_dir: Path, basename: str = "benchmark_report") -> tuple[Path, Path]:
    """Write the score as JSON (machine) and plain text (human, project-
    facing — no em dashes). Returns (json_path, txt_path)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / f"{basename}.json"
    txt_path = out_dir / f"{basename}.txt"

    json_path.write_text(
        json.dumps(score_result.to_dict(), indent=2, allow_nan=False), encoding="utf-8"
    )
    txt_path.write_text(_format_report_text(score_result), encoding="utf-8")

    return json_path, txt_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m posthouse.benchmark",
        description="Score a culls/segments file against a Premiere answer-key XML.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    score_p = sub.add_parser("score", help="Score culls.json against an answer key.")
    score_p.add_argument("--answer-key", required=True, type=Path)
    score_p.add_argument("--culls", required=True, type=Path)
    score_p.add_argument("--out", type=Path, default=Path("."))
    score_p.add_argument("--handle-tolerance", type=float, default=DEFAULT_HANDLE_TOLERANCE_SEC)

    args = parser.parse_args(argv)

    problems: list[str] = []
    truth: Optional[list[Range]] = None
    predicted: Optional[list[Range]] = None

    try:
        truth = parse_answer_key_xml(args.answer_key)
    except BenchmarkError as e:
        problems.append(f"answer key: {e}")

    try:
        predicted = load_culls(args.culls)
    except CullsLoadError as e:
        problems.extend(f"culls: {p}" for p in e.problems)
    except BenchmarkError as e:
        problems.append(f"culls: {e}")

    if problems:
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        return 1

    result = score(predicted, truth, handle_tolerance_sec=args.handle_tolerance)
    json_path, txt_path = write_report(result, args.out)
    print(f"wrote {json_path}")
    print(f"wrote {txt_path}")
    print(f"overall: precision={result.overall.precision:.3f} recall={result.overall.recall:.3f} "
          f"f1={result.overall.f1:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
