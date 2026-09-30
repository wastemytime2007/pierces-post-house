"""The layers placed on a cut (callouts, captions, music, effects), read back from the export XML and
composited into a second preview, so the review page shows what Premiere will show.

Only layers our own tools placed are picked up (file ids `overlay-file-*` on picture tracks and
`audio-file-*` on audio tracks). The clean preview stays as it is and stays verified; the full one is
checked against it: same length, identical outside the layers, and the layers actually visible and audible.
"""
from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from timeline import (LAYER_AUDIO_PREFIX, LAYER_VIDEO_PREFIX, TimelineError, _collect_file_defs, _decode_pathurl,
                      _effective_fps, _rate_of, _seq_for_cut)

FFMPEG = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
SR = 48000


@dataclass
class Layer:
    kind: str            # "video" or "audio"
    name: str
    path: str
    start: float         # timeline seconds
    end: float
    track: int
    src_in: float = 0.0  # where in the file this piece starts (a split layer has several pieces of one file)
    src_out: float = 0.0


def find_layers(xml: Path) -> list[Layer]:
    root = ET.parse(xml).getroot()
    seq = _seq_for_cut(root)
    fps = _effective_fps(*_rate_of(seq.find("rate")))
    defs = _collect_file_defs(root)
    out: list[Layer] = []
    seen: set[tuple] = set()
    for kind, prefix in (("video", LAYER_VIDEO_PREFIX), ("audio", LAYER_AUDIO_PREFIX)):
        for ti, track in enumerate(seq.findall(f"media/{kind}/track")):
            for ci in track.findall("clipitem"):
                fid = ci.find("file").get("id") or ""
                if not fid.startswith(prefix) or (ci.findtext("enabled") or "TRUE").strip().upper() != "TRUE":
                    continue
                key = (kind, fid, *(int(ci.findtext(x)) for x in ("start", "end", "in", "out")))
                if key in seen:
                    continue
                seen.add(key)                                     # a stereo clip sits on two tracks; count it once
                pu = defs.get(fid, {}).get("pathurl")
                if not pu:
                    raise TimelineError(f"layer {ci.findtext('name')}: file {fid} has no pathurl")
                path = _decode_pathurl(pu)[0]
                if not Path(path).exists():
                    raise TimelineError(f"layer file not found: {path}")
                out.append(Layer(kind, ci.findtext("name") or Path(path).name, path,
                                 int(ci.findtext("start")) / fps, int(ci.findtext("end")) / fps, ti,
                                 int(ci.findtext("in")) / fps, int(ci.findtext("out")) / fps))
    return sorted(out, key=lambda l: (l.kind != "video", l.track, l.start))


LANE_OF = (("overlay", "Callout"), ("callout", "Callout"), ("caption", "Captions"), ("music", "Music"), ("sfx", "SFX"))
LANE_ORDER = ["Cuts", "Edits", "Callout", "Captions", "Music", "SFX"]


def lane_name(l: Layer) -> str:
    n = l.name.lower()
    return next((lane for key, lane in LANE_OF if key in n), "Graphics" if l.kind == "video" else "Audio")


def beatmap(cut, layers: list[Layer], items: list[dict] | None = None) -> list[dict]:
    """The edit decisions along the timeline, one lane per kind: cuts, the revision's edits, and each layer
    (captions expand to one block per line when the layer's captions.json sits beside its file)."""
    import json
    lanes: dict[str, list[dict]] = {"Cuts": [{"start": round(c.tl_start, 3), "end": round(c.tl_start, 3), "label": f"cut to clip {c.idx} ({c.name})"} for c in cut.video[1:]]}
    for i in items or []:
        if i.get("applied") and i.get("v2_time") is not None:
            lanes.setdefault("Edits", []).append({"start": round(i["v2_time"], 3), "end": round(i["v2_time"], 3), "label": f"note {i['note']}: {i['summary'][:90]}"})
    for l in layers:
        lane = lane_name(l)
        blocks = lanes.setdefault(lane, [])
        lines = None
        cj = Path(l.path).parent / "captions.json"
        if lane == "Captions" and cj.exists():
            try:
                lines = json.loads(cj.read_text()).get("groups")
            except (OSError, ValueError):
                lines = None
        if lines:
            for g in lines:
                a, b = g.get("show_start"), g.get("show_end")
                if a is None or b is None or b <= l.src_in or a >= l.src_out:
                    continue
                s, e = max(a, l.src_in), min(b, l.src_out)
                blocks.append({"start": round(l.start + (s - l.src_in), 3), "end": round(l.start + (e - l.src_in), 3), "label": g["text"]})
        else:
            blocks.append({"start": round(l.start, 3), "end": round(l.end, 3), "label": l.name.rsplit(".", 1)[0]})
    order = LANE_ORDER + sorted(k for k in lanes if k not in LANE_ORDER)
    return [{"name": k, "kind": "ticks" if k in ("Cuts", "Edits") else "blocks", "items": sorted(lanes[k], key=lambda x: x["start"])} for k in order if k in lanes]


def layer_warnings(before: list[Layer], after: list[Layer]) -> list[str]:
    """What a revision did to the layers: any layer that lost time was cut through by it. apply_ops ripples every
    track alike, which keeps captions with the speech but chops a callout or an effect anchored to a moment."""
    out = []
    for name in dict.fromkeys(l.name for l in before):
        b = sum(l.end - l.start for l in before if l.name == name)
        a = sum(l.end - l.start for l in after if l.name == name)
        if a < b - 0.05:
            out.append(f"{name} lost {b - a:.2f}s: this revision cut through it. Captions stay with the speech; "
                       "a callout, effect or music bed is disturbed and should be re-placed on the new cut.")
    return out


def _run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        raise TimelineError(f"ffmpeg failed: {p.stderr.strip()[:400]}")


def _size(path: Path) -> tuple[int, int]:
    s = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
                       capture_output=True, text=True).stdout.strip()
    w, h = s.split("x")
    return int(w), int(h)


def composite(base: Path, layers: list[Layer], out: Path) -> None:
    """The clean preview with every picture layer over it (lowest track first) and every audio layer mixed in."""
    vids = [l for l in layers if l.kind == "video"]
    auds = [l for l in layers if l.kind == "audio"]
    w, h = _size(base)
    cmd = FFMPEG + ["-i", str(base)]
    for l in vids:
        cmd += ["-ss", f"{l.src_in:.4f}", "-t", f"{l.end - l.start:.4f}", "-itsoffset", f"{l.start:.4f}", "-i", l.path]
    for l in auds:
        cmd += ["-i", l.path]
    graph, prev = [], "0:v"
    for i in range(1, len(vids) + 1):
        graph.append(f"[{i}:v]scale={w}:{h},fps=30[l{i}];[{prev}][l{i}]overlay=eof_action=pass:format=auto[o{i}]")
        prev = f"o{i}"
    apart = []
    for j, l in enumerate(auds):
        k = len(vids) + 1 + j
        s = int(round(l.start * SR))                              # sample-exact: a whole millisecond is 48 samples of error
        graph.append(f"[{k}:a]atrim=start={l.src_in:.5f}:end={l.src_out:.5f},asetpts=PTS-STARTPTS,aformat=sample_rates={SR}:channel_layouts=stereo,adelay={s}S|{s}S[x{j}]")
        apart.append(f"[x{j}]")
    if auds:
        graph.append(f"[0:a]aformat=sample_rates={SR}:channel_layouts=stereo[b];[b]{''.join(apart)}amix=inputs={len(auds) + 1}:normalize=0:duration=first[a]")
    maps = ["-map", f"[{prev}]" if vids else "0:v", "-map", "[a]" if auds else "0:a"]
    cmd += ["-filter_complex", ";".join(graph)] if graph else []
    _run(cmd + maps + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k",
                       "-movflags", "+faststart", str(out)])


def _frame(path: Path, t: float, w: int, h: int) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 3).astype(int) if len(raw) == w * h * 3 else np.zeros((h, w, 3), dtype=int)


def _pcm(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"], capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).astype(np.float64)


def _dur(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout)


def verify(base: Path, full: Path, layers: list[Layer], zone_end: float) -> list[tuple[str, bool | None, str]]:
    rows = _verify(base, full, layers, zone_end)
    return [(n, None if ok is None else bool(ok), d) for n, ok, d in rows]      # numpy bools are not `is False`


def _verify(base: Path, full: Path, layers: list[Layer], zone_end: float) -> list[tuple[str, bool | None, str]]:
    rows: list[tuple[str, bool | None, str]] = []
    vids, auds = [l for l in layers if l.kind == "video"], [l for l in layers if l.kind == "audio"]
    rows.append(("LAYERS-FOUND", bool(layers), f"{len(vids)} picture layer(s) and {len(auds)} audio layer(s) read from the XML"))
    db, df = _dur(base), _dur(full)
    rows.append(("SAME-LENGTH", abs(db - df) < 0.15, f"clean {db:.2f}s, with layers {df:.2f}s"))
    w, h = _size(base)
    if (w, h) != _size(full):
        rows.append(("SAME-SIZE", False, f"{_size(full)} vs {(w, h)}"))
        return rows

    end = max([l.end for l in layers] or [0.0])
    if end + 3 < min(db, zone_end):
        t = (end + min(db, zone_end)) / 2
        d = np.abs(_frame(base, t, w, h) - _frame(full, t, w, h))
        rows.append(("CLEAN-OUTSIDE-THE-LAYERS", float(d.mean()) < 3.0, f"at {t:.1f}s, after every layer has ended, the picture matches the clean preview (mean difference {d.mean():.2f} of 255)"))
    else:
        rows.append(("CLEAN-OUTSIDE-THE-LAYERS", None, "the layers run to the end of the cut, nothing to compare"))

    def label(l: Layer, group: list[Layer]) -> str:
        return l.name if sum(x.name == l.name for x in group) == 1 else f"{l.name} {l.start:.1f}-{l.end:.1f}s"

    for l in vids:
        best, at = 0, l.start
        for k in range(24):
            t = l.start + (l.end - l.start) * (k + 0.5) / 24
            if t >= db - 0.1:
                continue
            n = int((np.abs(_frame(base, t, w, h) - _frame(full, t, w, h)).max(axis=2) > 40).sum())
            if n > best:
                best, at = n, t
        rows.append((f"VISIBLE ({label(l, vids)})", best >= 300, f"{best} pixels differ from the clean picture at {at:.1f}s, inside its {l.start:.1f}-{l.end:.1f}s"))
    if auds:
        b, f = _pcm(base), _pcm(full)
        n = min(len(b), len(f))
        diff = f[:n] - b[:n]
        rms_db = lambda x: 20 * np.log10(max(float(np.sqrt(np.mean(x ** 2))), 1e-9))
        expect = np.zeros(n)
        spans = []
        for l in auds:
            x = _pcm(Path(l.path))[int(round(l.src_in * SR)):int(round(l.src_out * SR))]
            s = int(round(l.start * SR))
            m = max(0, min(len(x), n - s))
            expect[s:s + m] += x[:m]
            spans.append((l, s, s + m))
        for l, s, e in spans:                                    # mix minus (clean + layers) must be far below the layers themselves
            rel = rms_db((diff - expect)[s:e]) - rms_db(expect[s:e])
            rows.append((f"AUDIO-LAYER-IN-THE-MIX ({label(l, auds)})", rel <= -10.0,
                         f"in its {l.start:.1f}-{l.end:.1f}s the sound added to the mix matches the file: mismatch is {rel:.1f} dB below the layer (need 10 or more)"))
        t0 = int(min(end + 2, db - 3) * SR)
        if t0 + SR < n and end + 3 < db:
            r = float(np.sqrt(np.mean((f[t0:t0 + SR] - b[t0:t0 + SR]) ** 2)))
            rows.append(("CLEAN-AUDIO-OUTSIDE", r < 0.003, f"after the layers end the sound is unchanged (difference {20 * np.log10(max(r, 1e-9)):.0f} dBFS)"))
    return rows
