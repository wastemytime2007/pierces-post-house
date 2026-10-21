#!/usr/bin/env python3
"""The click-a-cut page: open a trimmed XML exported from Premiere, click a cut between two clips, choose "Add transition", and save an XML that carries only those choices.

    python3 labs/uxp_transitions/cut_menu.py [--port 8765] [--no-open] [--roots DIR ...]

It starts a small server on this Mac only (127.0.0.1) and opens the page in the browser. The page lists your recent exported XMLs (Desktop and Documents by default), shows the first video track as a strip of clips with each cut
between them, and a menu at the mouse: Add transition > Film Impact Push > Left / Up / Down / Right. Saving writes "<name> - with transitions.xml" next to the original (never overwriting it), through place_sfx.apply_plan:
your whoosh under each chosen cut, and a marker on each: Left is applied by the installed Transition Lab plugin by itself; Up, Down and Right get a "PH Push ..." marker for you to drop your own preset on
(a plugin cannot set a transition's direction or apply a preset).
"""
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import webbrowser
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import place_sfx  # noqa: E402

FFMPEG = "/Users/pierce/buttercut-test/src/dependencies/ffmpeg"
ROOTS = [(Path.home() / "Desktop", 2), (Path.home() / "Documents", 1)]      # Documents is large: one level only (a first search two levels down took 39 s)
THUMBS = Path(tempfile.gettempdir()) / "ph_cut_menu_thumbs"


def list_xmls(roots: list, budget: float = 4.0) -> list[dict]:
    """Recent exported XMLs. A root is a folder or (folder, how many levels down to look); the search stops after `budget` seconds so a huge folder can never make the page hang."""
    import time
    deadline = time.time() + budget
    found = []
    for entry in roots:
        root, levels = entry if isinstance(entry, tuple) else (entry, 1)
        if not root.exists():
            continue
        for depth in range(levels + 1):
            if time.time() > deadline:
                break
            pattern = "/".join(["*"] * depth + ["*.xml"])
            for f in root.glob(pattern):
                if f.name.startswith(".") or ".cuts." in f.name or "Translation Results" in f.name:
                    continue
                try:
                    with f.open("rb") as fh:
                        head = fh.read(400)
                    if b"<xmeml" not in head and b"<!DOCTYPE xmeml" not in head:
                        continue
                    st = f.stat()
                    found.append({"path": str(f), "name": f.name, "folder": f.parent.name, "mtime": st.st_mtime, "size": st.st_size})
                except OSError:
                    continue
    found.sort(key=lambda x: -x["mtime"])
    return found[:40]


def sequence_names(raw: str) -> list[str]:
    import re
    return [re.search(r"<name>([^<]*)</name>", raw[m.start():m.end()]).group(1) for m in re.finditer(r"<sequence\b[^>]*>.*?</sequence>", raw, re.S)]


def read_sequence(path: str, sequence: str | None, track: int = 1) -> dict:
    raw = Path(path).read_bytes().decode("utf-8")
    s0, s1, name = place_sfx.sequence_span(raw, sequence)
    seq_xml = raw[s0:s1]
    cuts, duration = place_sfx.cuts_on_track(seq_xml, track)
    usable = {c for c in cuts if c - place_sfx.BEFORE >= 0 and c + place_sfx.AFTER <= duration}
    root = ET.fromstring(seq_xml)
    rate = root.find("rate")
    tb = int(rate.findtext("timebase"))
    fps = tb * 1000 / 1001 if rate.findtext("ntsc") == "TRUE" else float(tb)
    files = {}
    for f in ET.fromstring(raw.replace("<!DOCTYPE xmeml>", "").split("?>", 1)[-1]).iter("file"):
        url = f.findtext("pathurl")
        if url:
            files[f.get("id")] = unquote(url.replace("file://localhost", ""))
    clips = []
    for c in sorted(root.findall("media/video/track")[track - 1].findall("clipitem"), key=lambda x: int(x.findtext("start"))):
        fe = c.find("file")
        clips.append({"name": c.findtext("name"), "start": int(c.findtext("start")), "end": int(c.findtext("end")), "in": int(c.findtext("in") or 0),
                      "media": files.get(fe.get("id")) if fe is not None else None})
    by_start = {c["start"]: c for c in clips}
    cut_rows = []
    for a, b in zip(clips, clips[1:]):
        if abs(a["end"] - b["start"]) <= 1 and b["start"] in cuts:
            cut_rows.append({"frame": b["start"], "usable": b["start"] in usable, "seconds": round(b["start"] / fps, 2), "before": a["name"], "after": b["name"]})
    return {"path": path, "sequences": sequence_names(raw), "sequence": name, "fps": round(fps, 3), "duration": duration, "clips": clips, "cuts": cut_rows,
            "note": "" if cut_rows else "no cuts between touching clips were found on V1 (a sequence that already has transitions writes -1 at its clips, which this reader skips)"}


def thumbnail(path: str, sequence: str | None, index: int) -> bytes | None:
    info = read_sequence(path, sequence)
    if index >= len(info["clips"]):
        return None
    c = info["clips"][index]
    media = c["media"]
    if not media or not Path(media).exists() or media.lower().endswith((".r3d", ".braw")):
        return None
    THUMBS.mkdir(exist_ok=True)
    key = hashlib.md5(f"{media}:{c['in']}:{info['fps']}".encode()).hexdigest()
    out = THUMBS / f"{key}.jpg"
    if not out.exists():
        secs = c["in"] / info["fps"]
        r = subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", f"{secs:.3f}", "-i", media, "-frames:v", "1", "-vf", "scale=240:-2", str(out)], capture_output=True, timeout=60)
        if r.returncode != 0 or not out.exists():
            return None
    return out.read_bytes()


def save(path: str, sequence: str | None, plan: list[dict]) -> dict:
    src = Path(path)
    raw = src.read_bytes().decode("utf-8")
    out_text, rep = place_sfx.apply_plan(raw, plan, sequence)
    dst = src.with_name(f"{src.stem} - with transitions.xml")
    n = 2
    while dst.exists():
        dst = src.with_name(f"{src.stem} - with transitions {n}.xml")
        n += 1
    dst.write_bytes(out_text.encode("utf-8"))
    dst.with_suffix(".cuts.json").write_text(json.dumps(rep, indent=1))
    left = sum(1 for x in rep["plan"] if x["direction"] == "left")
    return {"out": str(dst), "cuts": len(rep["plan"]), "left": left, "marked": len(rep["plan"]) - left, "plan": rep["plan"]}


class Handler(BaseHTTPRequestHandler):
    roots = ROOTS
    reveal = True

    def log_message(self, *a):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj).encode())

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/":
                self._send(200, (HERE / "cut_menu.html").read_bytes(), "text/html; charset=utf-8")
            elif u.path == "/api/xmls":
                self._json(list_xmls(self.roots))
            elif u.path == "/api/open":
                self._json(read_sequence(q["path"], q.get("sequence") or None))
            elif u.path == "/thumb":
                data = thumbnail(q["path"], q.get("sequence") or None, int(q["i"]))
                if data is None:
                    self._send(204, b"", "image/jpeg")
                else:
                    self._send(200, data, "image/jpeg")
            else:
                self._send(404, b"not found", "text/plain")
        except Exception as e:  # shown in the page, never a silent failure
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if u.path == "/api/save":
                res = save(body["path"], body.get("sequence") or None, body["plan"])
                if self.reveal:
                    subprocess.run(["open", "-R", res["out"]])
                self._json(res)
            else:
                self._send(404, b"not found", "text/plain")
        except (ValueError, KeyError) as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)


def serve(port: int, roots: list[Path], reveal: bool = True) -> ThreadingHTTPServer:
    Handler.roots = roots
    Handler.reveal = reveal
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--roots", nargs="*")
    a = ap.parse_args()
    roots = [(Path(r).expanduser(), 2) for r in a.roots] if a.roots else ROOTS
    srv = serve(a.port, roots)
    url = f"http://127.0.0.1:{a.port}/"
    print("the click-a-cut page:", url, "(Ctrl+C to stop)")
    if not a.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
