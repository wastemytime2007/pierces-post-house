"""posthouse.broll_interpret — B-roll frame-rate interpretation for export.

**Genuinely new capability, not harvested.** Confirmed PreCut has nothing
like this: its exporter declares every clip's own probed native rate and
lets Premiere's own (non-destructive, non-speed-changing) sequence conform
handle any mismatch on the timeline (`exporter.py`'s own docstring: "A-roll
and B-roll source files keep their native frame rate — Premiere handles
the rate conform when they land on a different-rate sequence"). Ryan wants
something stronger for B-roll specifically: a real, baked-in speed change
(what Premiere's "Modify > Interpret Footage > Frame Rate" does by hand),
so B-roll is already at the project's target rate before anyone drags it
onto a timeline. His own words (2026-09-03): "any framerate that doesn't
match the intended export framerate to be interpreted to that framerate
for items labeled b-roll... we would never interpret up... realistically
all exports will happen in 24(23.976) or 30(29.97)... based on the footage
captured we'd export in whichever the smallest captured framerate was."
A-roll is never touched by any of this, regardless of its own native rate.

**How this actually works — two real approaches tried, the first
overturned by real evidence, not by reasoning:**

1. A naive FCP7 XML rate-mismatch declaration does NOT work — tested
   directly in real Premiere, which re-probes the actual media and
   ignores a declared rate that doesn't match it.

2. `ffmpeg -itsscale <ratio> -c copy` DOES genuinely retime a file (real
   presentation-timestamp rescaling, no re-encode, verified via raw
   frame-level PTS inspection to be mathematically exact) — but Ryan
   caught the real cost before this shipped: it means a full-resolution
   duplicate of every clip needing interpretation, permanently, on
   already-tight footage storage ("does this mean we're going to be
   duplicating footage files on the drive and eating up more space?").
   Right concern, and this repo doesn't keep the code for that path —
   it's in git history (search for `itsscale`) if ever genuinely needed.

3. **What ships**: Ryan's own real Premiere workflow doesn't duplicate
   media either — he duplicates the Project-panel ITEM (same file, zero
   extra disk), then applies Interpret Footage to one duplicate. He sent
   a real FCP7 XML export of exactly that. Reading it closely settled
   the actual mechanism: Interpret Footage is NOT expressed anywhere in
   the static bin/master `<clip>`/`<file>` block — both duplicates in
   his export declare the identical native 60fps. The only place the
   effect shows up is in the frame math of a clipitem ALREADY placed on
   a sequence: the interpreted instance's `duration`/`out` equals the
   clip's raw frame count taken at face value against the sequence's
   rate (490 real frames "become" 490 timeline frames at 30fps = 2x real
   time), while the untouched instance gets Premiere's ordinary
   real-time-preserving conform (490 native 60fps frames -> 245 frames
   at 30fps, real duration unchanged). Ryan confirmed the interpreted
   STATE does persist on that Project-panel item across future drops in
   his live Premiere session — but that's Premiere's own internal
   project database remembering it, invisible to a statically generated
   XML with no live session behind it. A generated file cannot inject
   that persistent bin-level state.

Given that, `build_broll_reference_sequence` below builds a pre-placed
reference sequence — same pattern this codebase already uses for "All
Synced A-Roll" — where every B-roll clip is placed once with the correct
frame math baked in (interpreted ones using raw-frame-count timing,
native ones using normal real-time-preserving timing), referencing the
SAME original media, zero extra disk. The tradeoff, stated plainly: this
requires pulling B-roll from that reference sequence, not the raw
B-Roll Library bin directly — dragging straight from the bin comes in at
native speed like any other untouched clip, since that state can't be
attached to a bin item in a static export.

**Target rate**: the numeric minimum native fps among ALL real footage
declared in the project (both A-roll and B-roll) — not a rounding/family
heuristic. Ryan's "realistically 24 or 30" is a description of what his
footage looks like in practice, not an algorithmic constraint; the rule
itself is a plain minimum. A clip already at or below the target is never
touched (interpreting "up" is meaningless — you cannot invent frames that
don't exist — and is explicitly banned regardless).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, List, Optional
from xml.dom import minidom

DEFAULT_TOLERANCE = 0.02  # 2%: a clip already essentially AT the target
                          # (e.g. 29.97 vs a computed target of 29.970297)
                          # should never trigger a needless "interpretation"
                          # for a rounding difference.


def probe_native_fps(path: Path, ffprobe: str = "ffprobe") -> Optional[float]:
    """A file's real native fps via r_frame_rate (the stream's own
    timebase -- the same field multi_exporter.py's _safe_probe prefers,
    for the same reason: robust against slo-mo/VFR duration math)."""
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    raw = proc.stdout.strip()
    if "/" not in raw:
        return None
    try:
        num, den = raw.split("/")
        den = float(den)
        return float(num) / den if den else None
    except ValueError:
        return None


def compute_target_fps(native_fps_list: List[float]) -> Optional[float]:
    """The project's B-roll interpretation target: the numeric minimum
    native fps among all real captured footage. None if no valid fps
    values were given (caller should then skip interpretation entirely
    rather than guess at a target)."""
    valid = [f for f in native_fps_list if f and f > 0]
    return min(valid) if valid else None


def needs_interpretation(native_fps: float, target_fps: float,
                          tolerance: float = DEFAULT_TOLERANCE) -> bool:
    """True only when native_fps is genuinely higher than the target
    (never up -- a native rate at or below target is left alone)."""
    if native_fps is None or target_fps is None or target_fps <= 0:
        return False
    return native_fps > target_fps * (1 + tolerance)


def build_broll_reference_sequence(
    doc: minidom.Document,
    entries: List[Any],
    aroll_paths: List[Path],
    *,
    sequence_id: str = "sequence-broll-interpreted",
    sequence_name: str = "B-Roll (Interpreted)",
    ffprobe: str = "ffprobe",
) -> Optional[minidom.Element]:
    """Build the "B-Roll (Interpreted)" reference sequence: every entry in
    `entries` (a `precut_pipeline.multi_exporter.BrollLibraryEntry` list --
    typed `Any` so this module never imports that donor-owned class)
    placed end-to-end on V1, each with the frame math that reproduces
    Premiere's real Interpret Footage effect for clips above the
    project's target rate (see module docstring for how this was
    verified), and ordinary real-time-correct placement for clips already
    at or below it.

    Returns None if there's nothing to place (empty library, or no valid
    fps data to compute a target from) -- caller should skip inserting
    anything in that case rather than write an empty sequence.

    Mints its own master-clip and file ids under a `broll-ref-` prefix,
    deliberately a different namespace than `export_multi_timeline`'s own
    bare `masterclip-N`/`file-N` counters -- avoids needing to know or
    thread through whatever that function's internal counters ended at
    for the same document.
    """
    from precut_pipeline.bin_builders import (  # local import: keeps this
        _build_full_video_file, _build_rate, _append_text,           # module import-safe even for callers
    )                                                                 # that never use this function

    if not entries:
        return None

    aroll_native = [f for p in aroll_paths if (f := probe_native_fps(p, ffprobe))]
    broll_native = [e.fps for e in entries if getattr(e, "fps", None)]
    target_fps = compute_target_fps(aroll_native + broll_native)
    if target_fps is None:
        return None

    from precut_pipeline.exporter import detect_frame_rate  # donor's own rate-snapping helper
    seq_rate = detect_frame_rate(target_fps)

    # Build the track's clipitems first so the real total duration is known
    # before the <sequence> element (which wants <duration> before <rate>,
    # per FCP7's own element order) gets constructed.
    track = doc.createElement("track")

    next_master = 1
    next_file = 1
    next_mc_clipitem = [0]

    def _next_mc_id() -> str:
        next_mc_clipitem[0] += 1
        return f"broll-ref-mc-clipitem-{next_mc_clipitem[0]}"

    timeline_cursor = 0
    total_duration = 0
    for entry in entries:
        native_fps = getattr(entry, "fps", None)
        duration_sec = getattr(entry, "duration_sec", None)
        if not native_fps or not duration_sec:
            continue

        interpreted = needs_interpretation(native_fps, target_fps)
        if interpreted:
            # The actual effect (verified against Ryan's real Premiere
            # export): treat every real captured frame as one frame of
            # the target rate. frame_count is the exact real frame count
            # when known (ffprobe'd at tag time); duration_sec * native_fps
            # is the fallback, matching how the rest of this codebase
            # already prefers an exact frame_count over a derived one.
            duration_frames = int(round(
                entry.frame_count if getattr(entry, "frame_count", None)
                else duration_sec * native_fps
            ))
        else:
            # Ordinary real-time-preserving placement: however many
            # target-rate frames this clip's real duration occupies.
            duration_frames = int(round(duration_sec * target_fps))
        if duration_frames <= 0:
            continue

        master_id = f"broll-ref-masterclip-{next_master}"
        file_id = f"broll-ref-file-{next_file}"
        next_master += 1
        next_file += 1

        # The master clip itself always declares the file's TRUE native
        # rate -- only the SEQUENCE clipitem's frame math carries the
        # interpretation. This mirrors Ryan's own reference export
        # exactly (both his duplicated master clips stayed at native
        # 60fps; only the placed clipitem's numbers differed).
        native_rate = detect_frame_rate(native_fps)
        file_el = _build_full_video_file(
            doc, file_id=file_id, pathurl=_path_to_url(entry.original_path),
            name=Path(entry.original_path).name,
            duration_frames=int(round(duration_sec * native_fps)),
            timebase=native_rate.timebase, ntsc=native_rate.ntsc,
            width=int(getattr(entry, "width", 1920) or 1920),
            height=int(getattr(entry, "height", 1080) or 1080),
            has_audio=bool(getattr(entry, "has_audio", False)),
            audio_samplerate=getattr(entry, "audio_samplerate", None) or 48000,
            audio_channels=getattr(entry, "audio_channels", None) or 2,
            audio_depth=getattr(entry, "audio_depth", None) or 16,
        )

        clipitem = doc.createElement("clipitem")
        clipitem.setAttribute("id", _next_mc_id())
        _append_text(doc, clipitem, "masterclipid", master_id)
        _append_text(doc, clipitem, "name", Path(entry.original_path).name)
        _append_text(doc, clipitem, "enabled", "TRUE")
        _append_text(doc, clipitem, "duration", str(duration_frames))
        clipitem.appendChild(_build_rate(doc, seq_rate.timebase, seq_rate.ntsc))
        _append_text(doc, clipitem, "start", str(timeline_cursor))
        _append_text(doc, clipitem, "end", str(timeline_cursor + duration_frames))
        _append_text(doc, clipitem, "in", "0")
        _append_text(doc, clipitem, "out", str(duration_frames))
        clipitem.appendChild(file_el)
        track.appendChild(clipitem)

        timeline_cursor += duration_frames
        total_duration = timeline_cursor

    if timeline_cursor == 0:
        return None

    sequence = doc.createElement("sequence")
    sequence.setAttribute("id", sequence_id)
    _append_text(doc, sequence, "name", sequence_name)
    _append_text(doc, sequence, "duration", str(total_duration))
    sequence.appendChild(_build_rate(doc, seq_rate.timebase, seq_rate.ntsc))
    video = doc.createElement("video")
    video.appendChild(track)
    media = doc.createElement("media")
    media.appendChild(video)
    sequence.appendChild(media)

    return sequence


def _path_to_url(path: str) -> str:
    """Reproduced from precut_pipeline/exporter.py (relative-import
    package; kept local here to avoid a fragile cross-package import for
    one helper)."""
    from urllib.parse import quote
    abs_path = str(Path(path).resolve())
    return f"file://localhost{quote(abs_path, safe='/')}"
