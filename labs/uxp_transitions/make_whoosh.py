#!/usr/bin/env python3
"""Cut Ryan's whoosh selection out of his library file into its own small sound file, with the speed change and the level baked in.

    python3 labs/uxp_transitions/make_whoosh.py [out.wav]

Why: Premiere writes a sped-up excerpt to XML as in/out frames plus a Time Remap setting, and on import it read the in/out as plain source frames (the 2026-10-21 proof placed 35 to 45 frames of `Whooshes.wav`, which is
almost silence, instead of Ryan's 41 to 54.2). A plain file with nothing to interpret places the same sound every time.

The selection comes from Ryan's own test export (templates/film_impact_push_whoosh_template.xml): source in/out in Premiere ticks, speed 120%, gain -5 dB. Output: 48 kHz stereo, 16-bit.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "templates" / "film_impact_push_whoosh_template.xml"
FFMPEG = "/Users/pierce/buttercut-test/src/dependencies/ffmpeg"
FFPROBE = "/Users/pierce/buttercut-test/src/dependencies/ffprobe"
TICKS_PER_SECOND = 254016000000
DEFAULT_OUT = Path("/Users/pierce/Documents/Transition Lab/sfx/Whoosh Push.wav")


def selection() -> dict:
    t = TEMPLATE.read_text(encoding="utf-8-sig")
    clip = re.search(r'<clipitem id="[^"]*" premiereChannelType="stereo">.*?</clipitem>', t, re.S).group(0)
    src = re.search(r"<pathurl>file://localhost([^<]*)</pathurl>", clip).group(1)
    from urllib.parse import unquote
    return {
        "source": unquote(src),
        "in_s": int(re.search(r"<pproTicksIn>(\d+)</pproTicksIn>", clip).group(1)) / TICKS_PER_SECOND,
        "out_s": int(re.search(r"<pproTicksOut>(\d+)</pproTicksOut>", clip).group(1)) / TICKS_PER_SECOND,
        "speed": float(re.search(r"<parameterid>speed</parameterid>.*?<value>([\d.]+)</value>", clip, re.S).group(1)) / 100.0,
        "gain_db": float(re.search(r"<parameterid>Gain\(dB\)</parameterid>.*?<value>(-?[\d.]+)</value>", clip, re.S).group(1)),
    }


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    s = selection()
    out.parent.mkdir(parents=True, exist_ok=True)
    af = f"atempo={s['speed']},volume={s['gain_db']}dB"
    r = subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", f"{s['in_s']:.6f}", "-to", f"{s['out_s']:.6f}", "-i", s["source"], "-af", af, "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    dur = float(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout)
    info = {**s, "out": str(out), "seconds": round(dur, 4), "frames_at_29_97": round(dur * 30000 / 1001, 2)}
    out.with_suffix(".json").write_text(json.dumps(info, indent=1))
    print(json.dumps(info, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
