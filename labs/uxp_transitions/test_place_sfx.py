"""Proves place_sfx.py on a one-cut and a three-cut sequence.   python3 labs/uxp_transitions/test_place_sfx.py

1. One cut (Ryan's own test export with his whoosh and transition taken out): the pair lands where his hand-made one did (frames 112 to 123 around the cut at 116, tracks A3 and A4, stereo pair linked),
   and plays his selection: the file placed is the baked `Whoosh Push.wav`, in 0 to 11 frames, no Gain and no Time Remap setting left for Premiere to misread.
2. The baked file is the right sound: its length matches his selection at 120% speed, it is not silent, and its loudest point sits where the selection's does.
3. Three cuts: three pairs on the same two tracks at cut-4 to cut+7, each pair linked to itself, ids unique, the file defined once.
4. The output is the input plus one inserted block; nothing else changed.
"""
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp())
subprocess.run([sys.executable, str(HERE / "make_demo_input.py"), str(tmp)], check=True, capture_output=True)
one_in, three_in = tmp / "1 - one cut, no transition, no sound.xml", tmp / "2 - three cuts, no transition, no sound.xml"
FFMPEG = "/Users/pierce/buttercut-test/src/dependencies/ffmpeg"


def run(src, dst):
    r = subprocess.run([sys.executable, str(HERE / "place_sfx.py"), str(src), str(dst)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def sfx_clips(path):
    seq = ET.parse(path).getroot().find("sequence")
    rows = []
    for i, tr in enumerate(seq.findall("media/audio/track"), 1):
        for c in tr.findall("clipitem"):
            if c.findtext("name") == "Whoosh Push.wav":
                f = c.find("file")
                rows.append({"track": i, "start": c.findtext("start"), "end": c.findtext("end"), "in": c.findtext("in"), "out": c.findtext("out"), "channel": c.findtext("sourcetrack/trackindex"),
                             "filters": [e.findtext("name") for e in c.findall("filter/effect")], "path": f.findtext("pathurl"), "id": c.get("id"),
                             "links": [(l.findtext("linkclipref"), l.findtext("trackindex"), l.findtext("clipindex")) for l in c.findall("link")], "enabled": c.findtext("enabled")})
    return rows


# 1. one cut
out1 = tmp / "one_out.xml"
print(run(one_in, out1).strip().splitlines()[0])
rows = sfx_clips(out1)
assert len(rows) == 2
for r, (track, ch) in zip(rows, ((3, "1"), (4, "2"))):
    assert (r["track"], r["start"], r["end"], r["in"], r["out"], r["channel"], r["enabled"]) == (track, "112", "123", "0", "11", ch, "TRUE"), r
    assert r["filters"] == [], "no Gain or Time Remap setting left to misread"
    assert [(l[1], l[2]) for l in r["links"]] == [("3", "1"), ("4", "1")], "the pair is linked on tracks 3 and 4, clip 1"
assert rows[0]["path"] and rows[0]["path"].endswith("Whoosh%20Push.wav") and rows[1]["path"] is None, "file defined once, by the first clip"
print("one cut: whoosh pair at frames 112 to 123 on A3 and A4, plain file, in 0 to 11, no settings to misread")

# 2. the baked file is the right sound
import json
info = json.loads(Path("/Users/pierce/Documents/Transition Lab/sfx/Whoosh Push.json").read_text())
want = (info["out_s"] - info["in_s"]) / info["speed"]
assert abs(info["seconds"] - want) < 0.03, (info["seconds"], want)
src = np.frombuffer(subprocess.run([FFMPEG, "-v", "error", "-i", info["source"], "-ac", "1", "-ar", "8000", "-f", "f32le", "-"], capture_output=True).stdout, dtype=np.float32)
baked = np.frombuffer(subprocess.run([FFMPEG, "-v", "error", "-i", info["out"], "-ac", "1", "-ar", "8000", "-f", "f32le", "-"], capture_output=True).stdout, dtype=np.float32)
sel = src[int(info["in_s"] * 8000):int(info["out_s"] * 8000)]
assert np.sqrt(np.mean(baked ** 2)) > 0.02, "not silent"
# the baked sound is the selection sped up and 5 dB quieter: compare loudness shapes in 4 equal slices
def shape(x):
    n = len(x) // 4
    s = np.array([np.sqrt(np.mean(x[i * n:(i + 1) * n] ** 2)) for i in range(4)])
    return s / s.max()
assert np.allclose(shape(baked), shape(sel), atol=0.25), (shape(baked), shape(sel))
assert abs(np.sqrt(np.mean(baked ** 2)) / np.sqrt(np.mean(sel ** 2)) - 10 ** (-5 / 20)) < 0.15, "about 5 dB quieter than the selection"
print(f"baked file: {info['seconds']} s (selection {info['out_s'] - info['in_s']:.3f} s at {info['speed']}x = {want:.3f} s), same loudness shape as his selection, about 5 dB quieter")

# 3. three cuts
out3 = tmp / "three_out.xml"
print(run(three_in, out3).strip().splitlines()[0])
rows = sfx_clips(out3)
assert len(rows) == 6, len(rows)
cuts = [100, 185, 290]
for cut in cuts:
    pair = [r for r in rows if r["start"] == str(cut - 4)]
    assert len(pair) == 2 and {p["end"] for p in pair} == {str(cut + 7)} and {p["track"] for p in pair} == {3, 4}, (cut, pair)
    ids = {p["id"] for p in pair}
    for p in pair:
        assert {l[0] for l in p["links"]} == ids, "each pair is linked to itself only"
ids = [r["id"] for r in rows]
assert len(ids) == len(set(ids)), "unique ids"
assert sum(1 for r in rows if r["path"]) == 1, "the whoosh file is defined exactly once"
assert [r["links"][0][2] for r in rows if r["track"] == 3] == ["1", "2", "3"], "clip positions on the track run 1, 2, 3"
doc_ids = [e.get("id") for e in ET.parse(out3).getroot().iter() if e.tag == "clipitem"]
assert len(doc_ids) == len(set(doc_ids)), "no clash with the sequence's own clip ids"
print("three cuts: 3 pairs at", [(c - 4, c + 7) for c in cuts], "linked per pair, ids unique, file defined once")

# 4. nothing else changed
before = re.sub(r"\s+", "", three_in.read_text(encoding="utf-8-sig"))
after = out3.read_text(encoding="utf-8-sig")
for b in re.findall(r'<track [^>]*>(?:(?!</track>).)*clipitem-whoosh.*?</track>', after, re.S):
    after = after.replace(b, "")
assert re.sub(r"\s+", "", after) == before, "removing the inserted tracks gives back the input exactly"
print("untouched: removing the two inserted tracks gives back the input exactly")
print("ALL CHECKS PASSED")
