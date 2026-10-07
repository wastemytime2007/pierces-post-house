"""Read the cut zone of an exported FCP7 XML as a playable timeline.

Reuses posthouse.benchmark's XML helpers (see the footage-analysis skill,
reference/premiere-xml.md) and adds what a preview needs and the benchmark
parser does not: where each clip sits on the timeline, and the enabled audio.

Two frame conventions matter and they differ:
  * Video in/out are in the SOURCE file's native frame count (the exporter
    keeps native rates), so the file's rate is tried first.
  * Synced-audio in/out are written by the exporter in SEQUENCE frames
    (multi_exporter._append_synced_clipitem), while the WAV's <file> rate is
    a different number. Trusting the file rate there shifts every lav clip.
Every candidate is bounds-checked against ffprobe's real duration, and a clip
that fits no candidate raises instead of being emitted.
"""
from __future__ import annotations

import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from posthouse.benchmark import (  # noqa: E402
    _collect_file_defs,
    _decode_pathurl,
    _effective_fps,
)

MIN_ZONE_GAP_SEC = 20.0
LAYER_VIDEO_PREFIX = "overlay-file-"       # ids labs/overlay and labs/captions give the layers they place (V2, V3...)
LAYER_AUDIO_PREFIX = "audio-file-"         # ids labs/audio gives the music and effects it places
BOUNDS_EPS_SEC = 0.5


class TimelineError(Exception):
    pass


@dataclass
class VideoClip:
    idx: int
    tl_start: float
    tl_end: float
    src_path: str
    src_in: float
    src_out: float
    motion: tuple[float, float, float] | None = None   # Premiere's Basic Motion on the clip: (scale percent, center.horiz, center.vert), None when the clip has none

    @property
    def name(self) -> str:
        return Path(self.src_path).name


@dataclass
class AudioClip:
    tl_start: float
    tl_end: float
    src_path: str
    src_in: float
    src_out: float
    track: int

    @property
    def name(self) -> str:
        return Path(self.src_path).name


@dataclass
class Cut:
    sequence_name: str
    fps: float
    width: int
    height: int
    zone_end: float
    video: list[VideoClip] = field(default_factory=list)
    audio: list[AudioClip] = field(default_factory=list)


_duration_cache: dict[str, float] = {}


def probe_duration(path: str) -> float:
    if path not in _duration_cache:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True,
        )
        try:
            _duration_cache[path] = float(out.stdout.strip())
        except ValueError:
            raise TimelineError(f"ffprobe could not read {path}: {out.stderr.strip()[:200]}")
    return _duration_cache[path]


def _rate_of(el: ET.Element | None) -> tuple[int, bool] | None:
    if el is None:
        return None
    tb = el.findtext("timebase")
    if not tb:
        return None
    return int(round(float(tb))), (el.findtext("ntsc") or "FALSE").strip().upper() == "TRUE"


def video_rate_order(file_rate: tuple[int, bool] | None, clip_rate: tuple[int, bool] | None) -> list[tuple[str, tuple[int, bool]]]:
    """Which frame rate a video clip's in/out frame counts are read at, in order of preference.

    The file's own rate first (Premiere's own exports keep in/out in the source's native frames while writing the sequence's rate on the clip, footage-analysis skill), EXCEPT when the file and the
    clip declare the same timebase and differ only in the NTSC flag (file 30 NTSC, clip 30 plain): then the clip's own rate is read first. PreCut writes the cut at the preset's 30 and the
    video in-point as seconds x 30 on a 29.97 file; Ryan opened such an export in Premiere on 2026-10-07 and the picture and the separate voice recorder were in sync, which is the clip's-own-rate
    reading (read at the file's 29.97 the picture would be 0.1% of its position, 0.72 s at 12 minutes in, away from the voice). Different timebases (a 60 fps file in a 24 fps sequence) keep the
    file's rate."""
    out = [c for c in (("file", file_rate), ("clipitem", clip_rate)) if c[1]]
    if file_rate and clip_rate and file_rate[0] == clip_rate[0] and file_rate[1] != clip_rate[1]:
        out.sort(key=lambda c: c[0] != "clipitem")
    return out


def _resolve(in_f: int, out_f: int, candidates: list[tuple[str, tuple[int, bool]]],
             real_dur: float, label: str) -> tuple[float, float]:
    tried = []
    for cname, rate in candidates:
        fps = _effective_fps(*rate)
        a, b = in_f / fps, out_f / fps
        tried.append(f"{cname}@{fps:.3f}fps -> {a:.2f}-{b:.2f}s")
        if b <= real_dur + BOUNDS_EPS_SEC:
            return a, b
    raise TimelineError(
        f"{label}: no frame-rate interpretation fits the file's real duration "
        f"({real_dur:.2f}s). Tried: {'; '.join(tried)}"
    )


def motion_of(ci: ET.Element) -> tuple[float, float, float] | None:
    """(scale percent, center.horiz, center.vert) from a clipitem's Basic Motion filter, as Premiere writes it (safety_net/fixtures/premiere_motion/): horiz is the move of the clip's centre from the sequence centre
    as a fraction of the SOURCE width, positive to the right; scale is a percent of the clip's native size. vert is read the same way but its unit is not confirmed (no vertical move has been exported)."""
    for f in ci.findall("filter"):
        if (f.findtext("effect/name") or "") != "Basic Motion":
            continue
        vals = {p.findtext("parameterid"): p for p in f.findall("effect/parameter")}
        try:
            scale = float(vals["scale"].findtext("value"))
            horiz = float(vals["center"].findtext("value/horiz") or 0)
            vert = float(vals["center"].findtext("value/vert") or 0)
        except (KeyError, TypeError, ValueError):
            return None
        return (scale, horiz, vert)
    return None


def _seq_for_cut(root: ET.Element) -> ET.Element:
    for seq in root.iter("sequence"):
        if not (seq.findtext("name") or "").strip().lower().startswith("all synced"):
            return seq
    raise TimelineError("no cut sequence found in the XML")


def load_cut(xml_path: Path) -> Cut:
    root = ET.parse(xml_path).getroot()
    seq = _seq_for_cut(root)
    seq_rate = _rate_of(seq.find("rate"))
    if seq_rate is None:
        raise TimelineError("sequence has no <rate>")
    seq_fps = _effective_fps(*seq_rate)
    defs = _collect_file_defs(root)

    def file_path(ci: ET.Element) -> str:
        fid = ci.find("file").get("id")
        pu = defs.get(fid, {}).get("pathurl")
        if not pu:
            raise TimelineError(f"clipitem {ci.get('id')}: file {fid} has no pathurl")
        return _decode_pathurl(pu)[0]

    def file_rate(ci: ET.Element) -> tuple[int, bool] | None:
        d = defs.get(ci.find("file").get("id"), {})
        return (d["timebase"], d.get("ntsc", False)) if "timebase" in d else None

    vt = seq.find("media/video/track")
    items = sorted(vt.findall("clipitem"), key=lambda c: int(c.findtext("start")))
    if not items:
        raise TimelineError("cut sequence has no video clips")
    spans = [(int(c.findtext("start")), int(c.findtext("end"))) for c in items]
    stop = len(items)
    for i in range(1, len(items)):
        if (spans[i][0] - spans[i - 1][1]) / seq_fps >= MIN_ZONE_GAP_SEC:
            stop = i
            break
    zone_end = spans[stop - 1][1] / seq_fps

    cut = Cut(
        sequence_name=seq.findtext("name") or "",
        fps=seq_fps,
        width=int(seq.findtext("media/video/format/samplecharacteristics/width") or 0),
        height=int(seq.findtext("media/video/format/samplecharacteristics/height") or 0),
        zone_end=zone_end,
    )

    for n, ci in enumerate(items[:stop], start=1):
        path = file_path(ci)
        if not Path(path).exists():
            raise TimelineError(f"source not found (drive mounted?): {path}")
        cands = video_rate_order(file_rate(ci), _rate_of(ci.find("rate")))
        a, b = _resolve(int(ci.findtext("in")), int(ci.findtext("out")), cands,
                        probe_duration(path), f"video clip {n} {Path(path).name}")
        cut.video.append(VideoClip(n, spans[n - 1][0] / seq_fps, spans[n - 1][1] / seq_fps, path, a, b, motion_of(ci)))

    for ti, track in enumerate(seq.findall("media/audio/track")):
        for ci in track.findall("clipitem"):
            if (ci.findtext("enabled") or "TRUE").strip().upper() != "TRUE":
                continue
            if (ci.find("file").get("id") or "").startswith(LAYER_AUDIO_PREFIX):
                continue                                         # music or an effect placed by labs/audio: a layer, not the cut's speech
            start, end = int(ci.findtext("start")), int(ci.findtext("end"))
            if start / seq_fps >= zone_end:
                continue
            path = file_path(ci)
            if not Path(path).exists():
                raise TimelineError(f"audio source not found (drive mounted?): {path}")
            in_f, out_f = int(ci.findtext("in")), int(ci.findtext("out"))
            if out_f - in_f != end - start:
                raise TimelineError(
                    f"audio clip {Path(path).name}: (out-in)={out_f - in_f} != (end-start)={end - start}"
                )
            cands = [c for c in (("clipitem", _rate_of(ci.find("rate"))), ("file", file_rate(ci))) if c[1]]
            a, b = _resolve(in_f, out_f, cands, probe_duration(path),
                            f"audio clip {Path(path).name} at {start / seq_fps:.1f}s")
            cut.audio.append(AudioClip(start / seq_fps, min(end / seq_fps, zone_end), path, a, b, ti))
    return cut
