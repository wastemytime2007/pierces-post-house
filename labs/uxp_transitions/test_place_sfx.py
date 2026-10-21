"""Proves place_sfx.py against Ryan's own hand-made whoosh and on a three-cut sequence.   python3 labs/uxp_transitions/test_place_sfx.py

1. Regression: his Film Impact test export has one cut and a whoosh he placed by hand. Take the whoosh and transition out (make_demo_input.py), run place_sfx.py, and the whoosh it writes must match his:
   same file, same excerpt, same gain, same speed, same frames, on tracks A3 and A4, linked as a pair.
2. Three cuts: three pairs, on the same two tracks, at cut-4 to cut+7, each pair linked to itself, ids unique, the file defined once.
3. The output is the input plus one inserted block (the tool asserts this itself); nothing else changed.
"""
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "templates" / "film_impact_push_whoosh_template.xml"
tmp = Path(tempfile.mkdtemp())
subprocess.run([sys.executable, str(HERE / "make_demo_input.py"), str(tmp)], check=True, capture_output=True)
one_in, three_in = tmp / "1 - one cut, no transition, no sound.xml", tmp / "2 - three cuts, no transition, no sound.xml"


def run(src, dst):
    r = subprocess.run([sys.executable, str(HERE / "place_sfx.py"), str(src), str(dst)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def whoosh_clips(path):
    seq = ET.parse(path).getroot().find("sequence")
    rows = []
    for i, tr in enumerate(seq.findall("media/audio/track"), 1):
        for c in tr.findall("clipitem"):
            if c.findtext("name") == "Whooshes.wav":
                f = c.find("file")
                rows.append({"track": i, "start": c.findtext("start"), "end": c.findtext("end"), "in": c.findtext("in"), "out": c.findtext("out"), "ticks": (c.findtext("pproTicksIn"), c.findtext("pproTicksOut")),
                             "channel": c.findtext("sourcetrack/trackindex"), "gain": c.findtext("filter/effect/parameter[parameterid='Gain(dB)']/value"),
                             "speed": c.findtext("filter/effect/parameter[parameterid='speed']/value"), "path": f.findtext("pathurl") if f is not None else None, "id": c.get("id"),
                             "file_id": f.get("id"), "links": [(l.findtext("linkclipref"), l.findtext("trackindex"), l.findtext("clipindex")) for l in c.findall("link")],
                             "enabled": c.findtext("enabled"), "rate": (c.findtext("rate/timebase"), c.findtext("rate/ntsc"))})
    return rows


# 1. regression against his hand-made whoosh
out1 = tmp / "one_out.xml"
print(run(one_in, out1).strip().splitlines()[0])
mine, his = whoosh_clips(out1), whoosh_clips(TEMPLATE)
assert len(mine) == len(his) == 2, (len(mine), len(his))
for m, h in zip(mine, his):
    for k in ("track", "start", "end", "in", "out", "ticks", "channel", "gain", "speed", "rate", "enabled"):
        assert m[k] == h[k], f"{k}: mine {m[k]} his {h[k]}"
    assert [(l[1], l[2]) for l in m["links"]] == [(l[1], l[2]) for l in h["links"]], "the pair is linked on tracks 3 and 4, clip 1"
assert mine[0]["path"] == his[0]["path"] and mine[1]["path"] is None, "file defined once, by the first clip"
print("regression: the whoosh written for the one-cut sequence matches the one Ryan placed by hand (frames 112 to 123, -5 dB, 120%, A3 and A4, same excerpt)")

# 2. three cuts
out3 = tmp / "three_out.xml"
print(run(three_in, out3).strip().splitlines()[0])
rows = whoosh_clips(out3)
assert len(rows) == 6, len(rows)
cuts = [100, 185, 290]
for k, cut in enumerate(cuts):
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
print("three cuts: 3 whoosh pairs at", [(c - 4, c + 7) for c in cuts], "linked per pair, ids unique, file defined once")
# the picture and sound clips of the sequence are untouched
before = re.sub(r"\s+", "", three_in.read_text(encoding="utf-8-sig"))
after = out3.read_text(encoding="utf-8-sig")
block = re.findall(r'<track [^>]*>(?:(?!</track>).)*clipitem-whoosh.*?</track>', after, re.S)
for b in block:
    after = after.replace(b, "")
assert re.sub(r"\s+", "", after) == before, "removing the inserted tracks gives back the input exactly"
print("untouched: removing the two inserted tracks gives back the input exactly")
print("ALL CHECKS PASSED")
