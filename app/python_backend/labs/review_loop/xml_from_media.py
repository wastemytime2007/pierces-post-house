#!/usr/bin/env python3
"""Turn a finished video file into a one-clip edit (FCP7 XML) so the whole review pipeline can open it.

    python3 labs/review_loop/xml_from_media.py "<video.mp4>" --out "<folder>/<name>.xml" [--name "sequence name"]

The pipeline (review page, bleeps, editable bleeps, learning) works on an edit: a sequence with a video clip and the audio that goes with it. An exported video
is just that, in one piece: one video clip and one audio clip, both the whole file, at the file's own size and frame rate. Nothing is changed in the video file.
The XML is for this pipeline; it is not an export for Premiere.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote


class MediaError(Exception):
    pass


def probe(path: Path) -> dict:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate,sample_rate,channels:format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True)
    try:
        j = json.loads(p.stdout)
        v = next(s for s in j["streams"] if s["codec_type"] == "video")
        a = next(s for s in j["streams"] if s["codec_type"] == "audio")
        num, den = (int(x) for x in v["r_frame_rate"].split("/"))
        return {"w": int(v["width"]), "h": int(v["height"]), "fps": num / den, "num": num, "den": den, "dur": float(j["format"]["duration"]), "sr": int(a["sample_rate"])}
    except (StopIteration, KeyError, ValueError, ZeroDivisionError):
        raise MediaError(f"{path.name}: needs a video stream and an audio stream to read")


def make(media: Path, out: Path, name: str | None = None) -> Path:
    if not media.exists():
        raise MediaError(f"{media} is not there")
    m = probe(media)
    ntsc = abs(m["num"] / m["den"] - round(m["num"] / m["den"])) > 0.01 and m["den"] != 1
    tb = round(m["num"] / m["den"] * (1.001 if ntsc else 1.0)) if ntsc else round(m["fps"])
    frames = int(round(m["dur"] * (m["num"] / m["den"])))
    rate = f"<rate><timebase>{tb}</timebase><ntsc>{'TRUE' if ntsc else 'FALSE'}</ntsc></rate>"
    url = "file://localhost" + quote(str(media.resolve()), safe="/")
    file_def = (f'<file id="f1"><name>{media.name}</name><pathurl>{url}</pathurl>{rate}<duration>{frames}</duration>'
                f"<media><video><samplecharacteristics><width>{m['w']}</width><height>{m['h']}</height></samplecharacteristics></video>"
                f"<audio><samplecharacteristics><depth>16</depth><samplerate>{m['sr']}</samplerate></samplecharacteristics></audio></media></file>")
    clip = lambda cid, body, audio: (f'<clipitem id="{cid}"><name>{media.name}</name><enabled>TRUE</enabled><duration>{frames}</duration>{rate}'            # noqa: E731
                                     f"<start>0</start><end>{frames}</end><in>0</in><out>{frames}</out><masterclipid>m-f1</masterclipid>{body}"
                                     + ("<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>" if audio else "") + "</clipitem>")
    audio_clip = clip("a0", '<file id="f1"/>', True)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4"><sequence id="s"><name>' + (name or media.stem) + f"</name>{rate}"
           f"<media><video><format><samplecharacteristics><width>{m['w']}</width><height>{m['h']}</height></samplecharacteristics></format><track>{clip('v0', file_def, False)}</track></video>"
           f"<audio><format><samplecharacteristics><depth>16</depth><samplerate>{m['sr']}</samplerate></samplecharacteristics></format>"
           f"<track>{audio_clip}<enabled>TRUE</enabled></track></audio></media>"
           "<marker><name>posthouse: whole video file</name><in>0</in><out>-1</out></marker></sequence></xmeml>")
    out.write_text(xml)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("media", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--name")
    a = ap.parse_args()
    try:
        p = make(a.media, a.out, a.name)
    except MediaError as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1
    print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
