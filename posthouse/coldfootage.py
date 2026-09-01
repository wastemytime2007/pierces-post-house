"""posthouse.coldfootage — the Cold Footage sequence builder.

This is the genuinely new piece of Phase 1 (see ROADMAP.md §6 Phase 1 and
§4): PreCut's ``CutList``/exporter chain has no way to express "arbitrary
in/out ranges from arbitrary source files, laid back to back" — its
A-roll track is built from a transcript's phrase/topic-range structure
(see ``precut_pipeline.story_assembler``), and its B-roll track only ever
holds matcher output or markers. A technical cull's output — "here are
the usable ranges of raw footage" — doesn't fit either shape. This module
is the client-side glue that makes it fit, by reusing the existing
``ARollPhrase`` + ``CutList`` + ``export_multi_timeline`` path exactly as
``story_assembler.py`` does, just with segments coming from a JSON file
instead of a transcript-driven planner.

Segments-file contract (``contract_version: 1``)
-------------------------------------------------
This is the culls-to-timeline contract for Phase 1; the Assistant Editor's
real ``culls.json`` (Phase 4, ROADMAP.md §4 and ARCHITECTURE.md's artifact
table) is expected to produce something shaped like this once its
field-level schema is settled — this module's input schema should be
read as a proposal for that shape, not a parallel one::

    {
      "contract_version": 1,
      "sequence_name": "Cold Footage — Kitchen Reno",
      "segments": [
        {
          "source_path": "/abs/path/to/clip.mov",
          "in_sec": 4.0,
          "out_sec": 9.5,
          "label": "wide establishing shot",   // optional, defaults to ""
          "handle_sec": 1.0                    // optional, defaults to 1.0
        },
        ...
      ]
    }

Behavior:

* Segments land on V1 in **list order**, back to back — segment *i*'s
  timeline position is the sum of the (post-handle) durations of segments
  0..i-1. Order in the file is editorial order; this module does not
  reorder by source file or timestamp (unlike ``story_assembler``, which
  sorts ranges — there is no transcript here to make that judgment call).
* **Handles.** ``in_sec``/``out_sec`` are extended outward by
  ``handle_sec`` (default 1.0s) on each side, then clamped to
  ``[0, source_duration]`` — a handle never manufactures footage that
  isn't there. The *handled* range is what actually lands on the
  timeline; the original in/out is not separately preserved in the XML
  (Premiere shows one continuous clipitem per segment, exactly like any
  other cut — the handle is just extra pre-roll/post-roll the editor can
  slip into).
* **Validation is exhaustive, not fail-fast.** Every segment is checked;
  every offender is collected and reported together
  (:class:`ColdFootageValidationError` lists all of them), because a
  culling pass over dozens of clips producing a segments file with three
  bad rows should not require three separate run-fix-rerun cycles to
  discover. A segment is rejected when: ``in_sec >= out_sec``; its
  ``source_path`` does not exist on disk; or ``[in_sec, out_sec]``
  (before handles) exceeds the source's real, ffprobe'd duration.
* **No BRollMarkers, no overlay, no library bin, audio sync off.** This
  is a technical, editor-facing "here's your usable footage in order"
  reel — not a creative assembly. One ``ARollPhrase`` per segment
  (``text`` = the segment's ``label`` or ``""``); ``broll_track`` and
  ``broll_markers`` stay empty; ``export_multi_timeline`` is called with
  ``broll_library=None``, ``include_overlay=False``,
  ``auto_include_rules=None``. The sequence still gets PreCut's standard
  Seq/Footage/Audio/Files bin structure with placeholder clips in the
  bins that end up empty (that's ``export_multi_timeline``'s own
  behavior, not something this module adds — see multi_exporter.py
  "Phase 4c").
* **Sequence dimensions** come from probing the first segment's source
  file (native width/height/fps), the same fallback ``story_assembler``
  uses for an angle with no chosen aspect — because there is no aspect
  choice here at all. Falls back to 1920x1080@30 if the probe fails.

Entry points:

* :func:`build_coldfootage_xml` — the Python API.
* ``python -m posthouse.coldfootage segments.json output.xml`` — the CLI.
  Exits non-zero with a stderr message on any failure; never hangs, never
  writes a partial file (the XML is only written once
  ``export_multi_timeline`` returns successfully).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .precut_bridge import import_precut

CONTRACT_VERSION = 1
DEFAULT_HANDLE_SEC = 1.0
DEFAULT_SEQUENCE_WIDTH = 1920
DEFAULT_SEQUENCE_HEIGHT = 1080
DEFAULT_SEQUENCE_FPS = 30.0


class ColdFootageError(Exception):
    """Base class for cold-footage build failures."""


class ColdFootageValidationError(ColdFootageError):
    """Raised with every offending segment listed, not just the first."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        message = "Cold Footage segment validation failed:\n" + "\n".join(
            f"  - {p}" for p in problems
        )
        super().__init__(message)


@dataclass
class _ResolvedSegment:
    index: int
    source_path: str
    in_sec: float
    out_sec: float
    label: str
    handled_in: float
    handled_out: float


def _safe_probe_duration(source_path: str) -> Optional[float]:
    """Probe a source file's duration in seconds. None if the probe fails.

    Reuses multi_exporter._safe_probe (the same live-probe helper
    story_assembler.py's _probe_native_dims and multi_exporter's own
    library-entry building rely on) rather than re-implementing ffprobe
    plumbing. It is a "private" helper by name, but it is already the
    established cross-module dependency for exactly this job — see
    story_assembler._probe_native_dims.
    """
    multi_exporter = import_precut("precut_pipeline.multi_exporter")
    info = multi_exporter._safe_probe(Path(source_path))
    if info is None:
        return None
    return info.get("duration")


def _probe_native_dims(source_path: str) -> tuple[int, int, float]:
    """Native (width, height, fps) of a source file, with sane fallbacks."""
    multi_exporter = import_precut("precut_pipeline.multi_exporter")
    info = multi_exporter._safe_probe(Path(source_path))
    if info is None:
        return (DEFAULT_SEQUENCE_WIDTH, DEFAULT_SEQUENCE_HEIGHT, DEFAULT_SEQUENCE_FPS)
    width = info.get("width") or DEFAULT_SEQUENCE_WIDTH
    height = info.get("height") or DEFAULT_SEQUENCE_HEIGHT
    fps = info.get("fps") or DEFAULT_SEQUENCE_FPS
    return (int(width), int(height), float(fps))


def _validate_and_resolve(segments: list[dict]) -> list[_ResolvedSegment]:
    """Validate every segment, collect every problem, then resolve handles.

    Raises ColdFootageValidationError listing every offending segment if
    any validation fails. Returns resolved segments (with handles applied
    and clamped) only when ALL segments are valid.
    """
    problems: list[str] = []
    resolved: list[_ResolvedSegment] = []

    # Cache probed durations per source path so a segments file with many
    # segments from the same clip only probes it once.
    duration_cache: dict[str, Optional[float]] = {}

    for i, seg in enumerate(segments):
        tag = f"segment[{i}]"
        source_path = seg.get("source_path", "")
        label = seg.get("label") or ""
        handle_sec = float(seg.get("handle_sec", DEFAULT_HANDLE_SEC))

        try:
            in_sec = float(seg["in_sec"])
            out_sec = float(seg["out_sec"])
        except (KeyError, TypeError, ValueError) as e:
            problems.append(f"{tag} ({source_path!r}): missing/invalid in_sec or out_sec ({e})")
            continue

        if not source_path:
            problems.append(f"{tag}: missing source_path")
            continue

        if in_sec >= out_sec:
            problems.append(
                f"{tag} ({source_path}): in_sec ({in_sec}) >= out_sec ({out_sec})"
            )
            continue

        src = Path(source_path)
        if not src.exists():
            problems.append(f"{tag} ({source_path}): source file does not exist")
            continue

        if source_path not in duration_cache:
            duration_cache[source_path] = _safe_probe_duration(source_path)
        duration = duration_cache[source_path]

        if duration is None:
            problems.append(
                f"{tag} ({source_path}): could not probe source duration "
                f"(is ffprobe on PATH? is this a valid media file?)"
            )
            continue

        if out_sec > duration + 1e-6:
            problems.append(
                f"{tag} ({source_path}): out_sec ({out_sec}) exceeds source "
                f"duration ({duration:.3f}s)"
            )
            continue

        handled_in = max(0.0, in_sec - handle_sec)
        handled_out = min(duration, out_sec + handle_sec)

        resolved.append(_ResolvedSegment(
            index=i,
            source_path=source_path,
            in_sec=in_sec,
            out_sec=out_sec,
            label=label,
            handled_in=handled_in,
            handled_out=handled_out,
        ))

    if problems:
        raise ColdFootageValidationError(problems)

    return resolved


def _load_segments_dict(segments_dict: dict) -> tuple[str, list[dict]]:
    contract_version = segments_dict.get("contract_version")
    if contract_version != CONTRACT_VERSION:
        raise ColdFootageError(
            f"unsupported contract_version {contract_version!r}; "
            f"posthouse.coldfootage supports version {CONTRACT_VERSION}"
        )

    sequence_name = segments_dict.get("sequence_name")
    if not sequence_name or not isinstance(sequence_name, str):
        raise ColdFootageError("segments file must have a non-empty 'sequence_name' string")

    segments = segments_dict.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ColdFootageError("segments file must have a non-empty 'segments' list")

    return sequence_name, segments


def build_coldfootage_xml(
    segments_dict: dict,
    output_path: Path,
    project_name: Optional[str] = None,
) -> Path:
    """Build a Cold Footage sequence XML from a parsed segments dict.

    Args:
        segments_dict: parsed JSON matching the module-docstring contract.
        output_path: where to write the FCP7 XML.
        project_name: the Premiere project name in the XML. Defaults to
            the segments file's ``sequence_name``.

    Returns:
        The output_path, on success.

    Raises:
        ColdFootageValidationError: one or more segments failed validation
            (every offender is listed in ``.problems``).
        ColdFootageError: the segments file itself is malformed (bad
            contract_version, missing sequence_name/segments).
    """
    sequence_name, raw_segments = _load_segments_dict(segments_dict)
    resolved = _validate_and_resolve(raw_segments)

    cutlist_mod = import_precut("precut_pipeline.cutlist")
    multi_exporter_mod = import_precut("precut_pipeline.multi_exporter")

    ARollPhrase = cutlist_mod.ARollPhrase
    CutList = cutlist_mod.CutList
    ExportRequest = multi_exporter_mod.ExportRequest
    export_multi_timeline = multi_exporter_mod.export_multi_timeline

    aroll_track = []
    timeline_cursor = 0.0
    for seg in resolved:
        duration = seg.handled_out - seg.handled_in
        aroll_track.append(ARollPhrase(
            phrase_id=seg.index + 1,
            source_file=seg.source_path,
            source_start=seg.handled_in,
            source_end=seg.handled_out,
            timeline_start=timeline_cursor,
            timeline_end=timeline_cursor + duration,
            text=seg.label,
        ))
        timeline_cursor += duration

    total_duration = timeline_cursor

    seq_w, seq_h, seq_fps = _probe_native_dims(resolved[0].source_path)

    cutlist = CutList(
        deliverable_concept=sequence_name,
        deliverable_preset="cold_footage",
        total_duration=total_duration,
        aroll_track=aroll_track,
        broll_track=[],
        broll_markers=[],
        creative_brief=None,
        sequence_width=seq_w,
        sequence_height=seq_h,
        sequence_fps=seq_fps,
        overlay_style="none",
    )

    request = ExportRequest(cutlist=cutlist, sequence_name=sequence_name)

    output_path = Path(output_path)
    return export_multi_timeline(
        requests=[request],
        output_path=output_path,
        broll_library=None,
        project_name=project_name or sequence_name,
        include_overlay=False,
        auto_include_rules=None,
    )


def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m posthouse.coldfootage",
        description="Build a Cold Footage FCP7 XML sequence from a segments JSON file.",
    )
    parser.add_argument("segments_json", type=Path, help="Path to the segments JSON file.")
    parser.add_argument("output_xml", type=Path, help="Path to write the output XML.")
    args = parser.parse_args(argv)

    try:
        raw = args.segments_json.read_text(encoding="utf-8")
    except OSError as e:
        print(f"error: could not read {args.segments_json}: {e}", file=sys.stderr)
        return 1

    try:
        segments_dict = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"error: {args.segments_json} is not valid JSON: {e}", file=sys.stderr)
        return 1

    try:
        build_coldfootage_xml(segments_dict, args.output_xml)
    except ColdFootageValidationError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except ColdFootageError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # pragma: no cover - defensive: never hang, never crash bare
        print(f"error: unexpected failure building Cold Footage XML: "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(f"wrote {args.output_xml}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
