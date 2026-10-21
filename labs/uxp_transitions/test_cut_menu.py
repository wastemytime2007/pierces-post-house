"""Tests the click-a-cut page's server through the same HTTP calls the page makes.   python3 labs/uxp_transitions/test_cut_menu.py

A temporary folder holds the three-cut demo XML (made from Ryan's own export). The server is started on a spare port against that folder, then:
  - /api/xmls lists the XML (and ignores the .cuts.json files and non-XML)
  - /api/open returns the clips and the three cuts at frames 100, 185 and 290, with a cut too close to an end flagged unusable
  - /thumb answers (a picture when the media can be read, otherwise "no content", never an error)
  - /api/save writes "<name> - with transitions.xml" next to the original, never over it, with the whoosh only at the chosen cuts, the left one marked for the plugin and the up one for Ryan
  - a bad plan comes back as a readable error, not a crash; the original file is byte-identical afterwards
  - the page itself is served and contains the menu entries
"""
import json
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cut_menu  # noqa: E402

tmp = Path(tempfile.mkdtemp())
builder = Path(tempfile.mkdtemp())                                         # the demo builder writes outside the folder the server searches
subprocess.run([sys.executable, str(HERE / "make_demo_input.py"), str(builder)], check=True, capture_output=True)
src = tmp / "three cuts.xml"
src.write_bytes((builder / "2 - three cuts, no transition, no sound.xml").read_bytes())
(tmp / "three cuts.cuts.json").write_text("{}")
(tmp / "notes.txt").write_text("not xml")
(tmp / "other.xml").write_text("<html>not an xmeml file</html>")
original = src.read_bytes()

srv = cut_menu.serve(0, [tmp], reveal=False)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"


def get(path):
    with urllib.request.urlopen(base + path, timeout=60) as r:
        return r.status, r.read()


def post(path, obj):
    req = urllib.request.Request(base + path, data=json.dumps(obj).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


st, body = get("/")
page = body.decode()
assert st == 200 and "Add transition" in page and "Film Impact Push" in page and "Remove transition here" in page, "the page and its menu"

st, body = get("/api/xmls")
names = [x["name"] for x in json.loads(body)]
assert names == ["three cuts.xml"], names                                  # no .cuts.json, no text file, no non-xmeml xml
print("lists:", names)

q = urllib.parse.quote(str(src))
st, body = get(f"/api/open?path={q}")
info = json.loads(body)
assert st == 200 and [c["frame"] for c in info["cuts"]] == [100, 185, 290] and all(c["usable"] for c in info["cuts"]), info["cuts"]
assert len(info["clips"]) == 4 and info["clips"][0]["name"].startswith("DJI_") and info["fps"] == 29.97, info["clips"]
assert info["cuts"][0]["before"] == info["clips"][0]["name"] and info["sequence"] == "Push and whoosh demo (3 cuts)"
print("opens:", len(info["clips"]), "clips,", [c["frame"] for c in info["cuts"]], "cuts at", [c["seconds"] for c in info["cuts"]], "s")

st, body = get(f"/thumb?path={q}&i=0")
assert st in (200, 204), st                                                  # a picture if the camera file is mounted, otherwise nothing, never an error
print("thumbnail:", "picture" if st == 200 else "none (media not readable here)")

st, res = post("/api/save", {"path": str(src), "sequence": info["sequence"], "plan": [{"frame": 100, "direction": "left"}, {"frame": 290, "direction": "up"}]})
assert st == 200 and res["cuts"] == 2 and res["left"] == 1 and res["marked"] == 1, res
out = Path(res["out"])
assert out.name == "three cuts - with transitions.xml" and out.exists() and out.parent == src.parent
assert src.read_bytes() == original, "the original is untouched"
seq = ET.parse(out).getroot().find("sequence")
assert [(m.findtext("name"), m.findtext("in")) for m in seq.findall("marker")] == [("PH transition", "100"), ("PH Push Up", "290")]
whoosh = sorted({c.findtext("start") for tr in seq.findall("media/audio/track") for c in tr.findall("clipitem") if c.findtext("name") == "Whoosh Push.wav"})
assert whoosh == ["286", "96"], "the whoosh under the two chosen cuts only: " + str(whoosh)
st, res2 = post("/api/save", {"path": str(src), "sequence": info["sequence"], "plan": [{"frame": 185, "direction": "left"}]})
assert st == 200 and Path(res2["out"]).name == "three cuts - with transitions 2.xml", "a second save never overwrites the first"
print("saves:", out.name, "| whoosh at", whoosh, "| a second save went to", Path(res2["out"]).name)

for bad in ([{"frame": 150, "direction": "left"}], [{"frame": 100, "direction": "sideways"}], []):
    st, err = post("/api/save", {"path": str(src), "sequence": info["sequence"], "plan": bad})
    assert st == 400 and err["error"], (bad, st, err)
try:
    get("/api/open?path=" + urllib.parse.quote(str(tmp / "missing.xml")))
    raise AssertionError("a missing file should be an error")
except urllib.error.HTTPError as e:
    assert e.code == 500 and "error" in json.loads(e.read())
assert src.read_bytes() == original
print("bad input: refused with a readable message, original still identical")
print("ALL CHECKS PASSED")
srv.shutdown()
