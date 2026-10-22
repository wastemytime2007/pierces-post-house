"""The app's door to the creator-workflow tools (labs/, bundled beside this file by safety_net/sync_labs.sh).

The tools are file-in, file-out and each adds its own sibling folders to sys.path, so this module only has to put the bundled `labs/*` folders on the
path once and call them. In the bundle, python_backend/ plays the part the repo root plays in the repo: labs/, posthouse/ and safety_net/ sit side by side.

Nothing here reimplements a tool. Each function is the tool's own entry point plus the plain dict the UI needs.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABS = HERE / "labs"


class ToolError(Exception):
    """A tool refused or could not run; the message is for Ryan, not a traceback."""


def capture(fn, *args, **kwargs):
    """Run a tool and hand back (result, the lines it printed). The tools print their check rows ([PASS] ...); in the app, stdout is the event channel, so those lines are
    caught here and sent on as log events instead of corrupting it. (backend.emit writes to the original stdout, so events from other jobs are not swallowed.)"""
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*args, **kwargs)
    return result, [ln for ln in buf.getvalue().splitlines() if ln.strip()]


def use_labs() -> None:
    for d in (HERE, LABS, *sorted(p for p in LABS.iterdir() if p.is_dir() and p.name != "__pycache__")):
        s = str(d)
        if s not in sys.path:
            sys.path.insert(0, s)


# ---------------------------------------------------------------------------------------------------------------------------------------------
# The review page inside the app: a small local server frames each review folder for the Review tab.
#
# The page is plain HTML plus preview.mp4 and the tab shows it in a frame. A WebView cannot seek a video it is served without byte ranges, so this is a server that
# does ranges (python's own http.server does not). It binds 127.0.0.1 only and serves nothing outside a folder the app registered.
# The port is fixed when it is free so the page's own browser storage (its unsent notes) stays under one origin from one run to the next.
# ---------------------------------------------------------------------------------------------------------------------------------------------

import hashlib
import http.server
import mimetypes
import socketserver
import threading
import urllib.parse

REVIEW_PORT = 47821
_ROOTS: dict[str, Path] = {}
_SERVER: dict = {}
_SERVER_LOCK = threading.Lock()


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "PostHouseReview/1"

    def log_message(self, *a):
        pass

    def _target(self) -> Path | None:
        parts = urllib.parse.unquote(urllib.parse.urlparse(self.path).path).split("/")        # ['', 'r', token, ...file]
        if len(parts) < 4 or parts[1] != "r" or parts[2] not in _ROOTS:
            return None
        root = _ROOTS[parts[2]].resolve()
        t = (root / "/".join(parts[3:])).resolve()
        return t if (root in t.parents) and t.is_file() else None

    def _send(self, head_only: bool):
        t = self._target()
        if t is None:
            self.send_error(404)
            return
        size = t.stat().st_size
        start, end, code = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", "").strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:                                                  # "bytes=-N": the last N bytes
                start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            code = 206
        ctype = "text/html; charset=utf-8" if t.suffix == ".html" else (mimetypes.guess_type(str(t))[0] or "application/octet-stream")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-store")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head_only:
            return
        try:
            with open(t, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1 << 16, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass                                                   # the player closed the request (a seek); not an error

    def do_GET(self):
        self._send(False)

    def do_HEAD(self):
        self._send(True)


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve_review(folder: str | Path, page: str = "review.html") -> str:
    """The URL the Review tab frames for a review folder (built by build_review or apply_notes)."""
    f = Path(folder).expanduser().resolve()
    if not (f / page).is_file():
        raise ToolError(f"there is no {page} in {f}")
    token = hashlib.sha1(str(f).encode()).hexdigest()[:10]
    with _SERVER_LOCK:
        _ROOTS[token] = f
        if "srv" not in _SERVER:
            try:
                srv = _Server(("127.0.0.1", REVIEW_PORT), _Handler)
            except OSError:
                srv = _Server(("127.0.0.1", 0), _Handler)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            _SERVER["srv"] = srv
        port = _SERVER["srv"].server_address[1]
    return f"http://127.0.0.1:{port}/r/{token}/{page}"


def save_notes(folder: str | Path, payload: dict) -> Path:
    """The notes the page handed over, written where the revise step and QA read them (the same file 'Download JSON' makes)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("notes"), list):
        raise ToolError("the review page did not hand over its notes")
    p = Path(folder).expanduser() / "review_notes.json"
    import json
    p.write_text(json.dumps(payload, indent=2))
    return p


def next_version_folder(xml: str | Path) -> Path:
    """Where the next version of this XML is written: beside it, named by the version, so applying notes to V1, then V2, never overwrites an earlier version."""
    import re as _re
    src = Path(xml).expanduser()
    m = _re.match(r"^(.*)_v(\d+)$", src.stem)
    return src.parent / f"{m.group(1) if m else src.stem}_v{(int(m.group(2)) if m else 1) + 1} - revised"


def free_version_folder(xml: str | Path) -> tuple[Path, str]:
    """The next version folder of this XML that does not already hold a version, and the XML name to write in it. Finishing an earlier version (the editor finishes its best one, which need not be
    its last) must not write over the version that already came after it: on 2026-10-08 the finished V3 went into V4's folder, under V4's name and address, and the tab kept showing the old V4."""
    import re as _re
    src = Path(xml).expanduser()
    m = _re.match(r"^(.*)_v(\d+)$", src.stem)
    base, n = (m.group(1) if m else src.stem), (int(m.group(2)) if m else 1) + 1
    while (src.parent / f"{base}_v{n} - revised").exists() and any((src.parent / f"{base}_v{n} - revised").glob("*.xml")):
        n += 1
    return src.parent / f"{base}_v{n} - revised", f"{base}_v{n}.xml"


def remember_export(project_dir: str | Path, xml_path: str) -> None:
    """Record an XML the app's export just wrote, so the Review tab can start from it (the export dialog lets the XML go anywhere, so the project folder alone does not know)."""
    import json
    import time
    f = Path(project_dir) / "review_exports.json"
    try:
        rows = json.loads(f.read_text())
    except (OSError, ValueError):
        rows = []
    rows = [r for r in rows if r.get("path") != str(xml_path)]
    rows.insert(0, {"path": str(xml_path), "time": time.time()})
    f.write_text(json.dumps(rows[:50], indent=2))


# ---------------------------------------------------------------------------------------------------------------------------------------------
# A review session is everything the Review tab shows for one cut: its versions (V1, V2, ...), the AI review of each, the QA ledger, what the AI editor concluded. It is written beside the cut's
# XML as the events happen (the backend calls record_event for each), so closing the app, leaving the project or opening another cut loses nothing, and reopening shows the session as it was
# instead of building and reviewing the cut again. Starting over is a separate, explicit act (a fresh build_review, which resets the session).
# ---------------------------------------------------------------------------------------------------------------------------------------------

SESSION_EVENTS = {"review_built", "notes_applied", "ai_review_done", "auto_edit_done"}
# Which behaviour of the AI editor made a session. A saved session is shown as it was left, so one made before a fix looks exactly like one made after it: the tab compares this and says so.
# Change it whenever the editor's behaviour changes in a way that makes an earlier result out of date.
EDITOR_REVISION = "2026-10-15.6"


def session_path(root_xml: str | Path) -> Path:
    r = Path(root_xml).expanduser()
    return r.parent / f"{r.stem} - session.json"


def _read_session(root_xml: str | Path) -> dict | None:
    import json
    try:
        s = json.loads(session_path(root_xml).read_text())
        return s if isinstance(s, dict) and isinstance(s.get("versions"), list) else None
    except (OSError, ValueError):
        return _session_from_disk(root_xml)                       # a cut reviewed before sessions were saved: its versions are still on disk


def _ai_subset(path: Path) -> dict | None:
    import json
    try:
        r = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return {k: r.get(k) for k in ("notes", "checks", "summary", "unverified_quotes_dropped", "not_covered")}


def _session_from_disk(root_xml: str | Path) -> dict | None:
    """Rebuild the session of a cut from the files its review left: its review folder, then each version's folder nested in the one before (`<name>_v2 - revised/`, then `<name>_v3 - revised/` inside
    it, ...) with the change ledger, the QA result and the AI review each wrote. What was not written down (the editor's own conclusion) is left out. The result is saved, so this runs once."""
    import json
    import time
    root = Path(root_xml).expanduser()
    review = root.parent / f"{root.stem} - review"
    if not root.is_file() or not (review / "review.html").is_file():
        return None
    versions = [{"label": "V1", "xml": str(root), "folder": str(review), "applied": None, "notes": None, "qa": None}]
    ai: dict = {}
    first = _ai_subset(review / "ai_review.json")
    if first:
        ai["V1"] = first
    folder, n = root.parent, 2
    while True:
        d = folder / f"{root.stem}_v{n} - revised"
        x = d / f"{root.stem}_v{n}.xml"
        if not (x.is_file() and (d / "review.html").is_file() and (d / "qa" / "qa.json").is_file()):
            break                                                 # a revision only gets its QA result after it passed its own checks; one without it was refused and is not shown as a version
        applied = notes = None
        try:
            items = json.loads((d / "changes.json").read_text()).get("items", [])
            applied, notes = sum(1 for i in items if i.get("applied")), len(items)
        except (OSError, ValueError):
            pass
        qa = None
        try:
            q = json.loads((d / "qa" / "qa.json").read_text())
            qa = {"report": str(d / "qa" / "qa_report.html"), "notes": q.get("notes", []), "whole_cut": q.get("checks", []), "unrequested": q.get("unrequested", [])}
        except (OSError, ValueError):
            pass
        versions.append({"label": f"V{n}", "xml": str(x), "folder": str(d), "applied": applied, "notes": notes, "qa": qa})
        sub = _ai_subset(d / "ai_review.json")
        if sub:
            ai[f"V{n}"] = sub
        folder, n = d, n + 1
    s = {"root": str(root), "versions": versions, "ai": ai, "auto": None, "updated": max(Path(v["folder"]).stat().st_mtime for v in versions) or time.time()}
    try:
        session_path(root).write_text(json.dumps(s))
    except OSError:
        pass
    return s


def record_event(ev: dict) -> None:
    """Fold one backend event into its cut's session file. `root` names the cut (the V1 XML); review_built is its own root. Events without one are not part of a session."""
    import json
    import time
    t = ev.get("type")
    if t not in SESSION_EVENTS:
        return
    root = ev.get("xml") if t == "review_built" else ev.get("root")
    if not root:
        return
    s = _read_session(root) or {"root": str(root), "versions": [], "ai": {}, "auto": None}
    if t == "review_built":
        s = {"root": str(root), "versions": [{"label": "V1", "xml": ev["xml"], "folder": ev["folder"], "applied": None, "notes": None, "qa": None}], "ai": {}, "auto": None}   # a rebuild starts the session over
    elif t == "notes_applied":
        if not ev.get("xml"):
            return
        label = ev.get("label") or f"V{len(s['versions']) + 1}"
        s["versions"] = [v for v in s["versions"] if v["xml"] != ev["xml"] and v["label"] != label]
        s["versions"].append({"label": label, "xml": ev["xml"], "folder": ev["folder"], "applied": ev.get("applied"), "notes": ev.get("notes"), "qa": ev.get("qa")})
        s["versions"].sort(key=lambda v: int(str(v["label"])[1:] or 0))
    elif t == "ai_review_done":
        s["ai"][ev.get("tag") or "V1"] = {k: ev.get(k) for k in ("notes", "checks", "summary", "unverified_quotes_dropped", "not_covered")}
    elif t == "auto_edit_done":
        s["auto"] = {**{k: ev.get(k) for k in ("status", "summary", "best", "left", "versions", "rounds", "message")}, "editor": EDITOR_REVISION}
    s["updated"] = time.time()
    p = session_path(root)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(s))
    tmp.replace(p)


def load_session(root_xml: str | Path) -> dict | None:
    """The saved session for a cut, ready to show: versions whose files are still there (each with its review page served again), the AI reviews, the editor's conclusion. None when there is no
    usable session (nothing saved, or the cut's own XML is gone), and the caller then builds the cut as a new review."""
    s = _read_session(root_xml)
    if not s:
        return None
    versions = []
    for v in s["versions"]:
        if not Path(v["xml"]).is_file() or not (Path(v["folder"]) / "review.html").is_file():
            if v["label"] == "V1":
                return None
            continue
        versions.append({**v, "url": serve_review(v["folder"])})
    if not versions or versions[0]["label"] != "V1":
        return None
    ai = {k: v for k, v in (s.get("ai") or {}).items() if k in {x["label"] for x in versions}}
    auto = s.get("auto")
    return {"root": s["root"], "versions": versions, "ai": ai, "auto": auto, "updated": s.get("updated"),
            "stale": bool(auto and auto.get("status") not in ("stopped",) and auto.get("editor") != EDITOR_REVISION)}      # made by an older editor than this one (no revision recorded counts as older)


def session_summary(root_xml: str | Path) -> dict | None:
    """What the exports list shows for a cut that has a session: how many versions, which one the editor named, how much was left, when."""
    s = _read_session(root_xml)
    if not s or not s["versions"]:
        return None
    auto = s.get("auto") or {}
    return {"versions": len(s["versions"]), "latest": s["versions"][-1]["label"], "best": auto.get("best") or s["versions"][-1]["label"], "left": len(auto.get("left") or []),
            "status": auto.get("status"), "updated": s.get("updated")}


def list_exports(project_dir: str | Path) -> list[dict]:
    """The exports this project made that still exist, newest first (recorded ones, then any XML in the project's own exports folder)."""
    import json
    d = Path(project_dir)
    seen: dict[str, float] = {}
    try:
        for r in json.loads((d / "review_exports.json").read_text()):
            if Path(r["path"]).is_file():
                seen[r["path"]] = max(float(r.get("time", 0)), Path(r["path"]).stat().st_mtime)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if (d / "exports").is_dir():
        for p in (d / "exports").rglob("*.xml"):
            if p.name.startswith("."):                    # macOS "._" sidecar on an external drive, not an export
                continue
            seen.setdefault(str(p), p.stat().st_mtime)
    return [{"path": p, "name": Path(p).name, "mtime": t, "session": session_summary(p)} for p, t in sorted(seen.items(), key=lambda kv: kv[1], reverse=True)[:30]]


def premiere_apps(roots: tuple[str, ...] = ("/Applications", "~/Applications")) -> list[dict]:
    """Every Adobe Premiere Pro installed, stable versions newest first, then Betas: [{"path", "name", "beta"}]. Adobe puts each install in its own folder under /Applications
    ("Adobe Premiere Pro 2026/Adobe Premiere Pro 2026.app", "Adobe Premiere Pro (Beta)/Adobe Premiere Pro (Beta).app"); a bare .app there is found too."""
    found: dict[str, dict] = {}
    for root in roots:
        base = Path(root).expanduser()
        if not base.is_dir():
            continue
        for pattern in ("Adobe Premiere Pro*/Adobe Premiere Pro*.app", "Adobe Premiere Pro*.app"):
            for p in base.glob(pattern):
                if p.is_dir():
                    found[str(p)] = {"path": str(p), "name": p.stem, "beta": "beta" in p.stem.lower()}
    return sorted(found.values(), key=lambda a: (a["beta"], _neg_name(a["name"])))


def _neg_name(name: str) -> tuple:
    """Sort key putting the higher version first among stable installs (2026 before 2025)."""
    import re
    m = re.search(r"(\d{4})", name)
    return (-int(m.group(1)) if m else 0, name)


def premiere_app(prefer: str | None = None, apps: list[dict] | None = None) -> str | None:
    """The Premiere to open: the one asked for if it is a detected install (a path that is not one is ignored, so nothing else can be launched through this), else the newest stable, else a Beta."""
    apps = apps if apps is not None else premiere_apps()
    paths = [a["path"] for a in apps]
    if prefer and prefer in paths:
        return prefer
    return paths[0] if paths else None


def root_xml_of(xml: Path) -> Path | None:
    """The first version of the cut this one was made from: versions are written inside the folder of the one before ("<cut>_v2 - revised/..."), so the first sits beside that folder as "<cut>.xml".
    None when this XML is itself the first."""
    import re as _re
    for p in xml.parents:
        m = _re.match(r"^(.*)_v2 - revised$", p.name)
        if m and (p.parent / f"{m.group(1)}.xml").is_file():
            return p.parent / f"{m.group(1)}.xml"
    return None


def add_transitions(src: Path, transitions: list[dict]) -> tuple[Path, dict]:
    """The click-a-cut choices written into a copy of a version's XML: "<name> - with transitions.xml" next to it (never over it). `transitions` is the review page's list of
    {"timeline_sec", "direction"}; each is matched to the cut it was clicked on (V1, within 0.3 s). Ryan's whoosh goes under each, plus a marker (Left is applied by the installed
    Premiere plugin by itself, the other directions get an orange marker for his own preset)."""
    use_labs()
    import place_sfx
    import cut_menu
    raw = src.read_bytes().decode("utf-8")
    info = cut_menu.read_sequence(str(src), None)
    fps = info["fps"]
    usable = [c["frame"] for c in info["cuts"] if c["usable"]]
    plan, skipped = [], []
    for item in transitions:
        sec = float(item["timeline_sec"])
        near = min(usable, key=lambda f: abs(f / fps - sec), default=None)
        if near is None or abs(near / fps - sec) > 0.3:
            skipped.append(round(sec, 2))                  # a cut too close to the start or end for the whoosh, or one the cut no longer has
            continue
        plan.append({"frame": near, "direction": item["direction"]})
    if not plan:
        raise ToolError("none of the chosen cuts can take a transition sound in this version" + (f" (looked at {skipped})" if skipped else ""))
    text, rep = place_sfx.apply_plan(raw, plan)
    dst = src.with_name(f"{src.stem} - with transitions.xml")
    n = 2
    while dst.exists() and dst.read_bytes().decode("utf-8") != text:
        dst = src.with_name(f"{src.stem} - with transitions {n}.xml")
        n += 1
    dst.write_bytes(text.encode("utf-8"))
    return dst, {"cuts": len(plan), "left": sum(1 for x in plan if x["direction"] == "left"), "skipped": skipped, "out": str(dst)}


def export_xml(xml: str, open_in_premiere: bool = True, app: str | None = None, root: str | None = None, transitions: list | None = None) -> dict:
    """The XML a version of the cut is, checked by the export check and (only if it passes) opened in Premiere. Never hands over an export it has not verified.

    A check that already failed on the FIRST version of the cut is a defect the export came in with, not one the editing made (2026-10-13: the original export's leftover bin had two clips over the same
    footage, so every version of the cut was refused and "Open in Premiere" failed on all of them). It is reported, and does not block; a check that fails only on this version still does."""
    import os
    src = Path(xml).expanduser()
    if not src.is_file():
        raise ToolError(f"that XML is not there: {src}")
    trans_info = None
    if transitions:
        src, trans_info = add_transitions(src, transitions)
    use_labs()
    import export_gate
    import verify_export
    rep = verify_export.Report()
    verify_export.check_xml(src, rep)
    skip = {"CUT-GRANULARITY"} if export_gate.is_whole_file(src) else set()
    rows = [{"name": n, "ok": ok, "detail": d} for n, ok, d in rep.rows]
    failed = [r["name"] for r in rows if r["ok"] is False and r["name"] not in skip]
    inherited: list[str] = []
    base = Path(root).expanduser() if root else root_xml_of(src)
    if failed and base and base.is_file() and base.resolve() != src.resolve():
        rep0 = verify_export.Report()
        verify_export.check_xml(base, rep0)
        skip0 = {"CUT-GRANULARITY"} if export_gate.is_whole_file(base) else set()          # a check skipped for that version is not a defect it had
        before = {n for n, ok, _d in rep0.rows if ok is False and n not in skip0}
        inherited = [n for n in failed if n in before]
        failed = [n for n in failed if n not in before]
    side = src.with_name(src.name + ".inherited.json")                      # a piece cut out of a many-sequence XML: what it already failed when it was cut out came in with the export
    if failed and (not base or base.resolve() == src.resolve()) and side.is_file():
        came_in = set(json.loads(side.read_text()).get("failed", []))
        inherited = inherited + [n for n in failed if n in came_in]
        failed = [n for n in failed if n not in came_in]
    out = {"xml": str(src), "verified": not failed, "failed": failed, "inherited": inherited, "rows": rows, "opened": False, "app": None}
    if trans_info:
        out["transitions"] = trans_info
    if failed or not open_in_premiere:
        return out
    chosen = premiere_app(app)
    out["app"] = Path(chosen).stem if chosen else None
    out["app_path"] = chosen
    if os.environ.get("POSTHOUSE_NO_OPEN"):
        return out                                                  # tests: report what would open, open nothing
    subprocess.run(["open", "-a", chosen, str(src)] if chosen else ["open", str(src)], check=False)
    out["opened"] = True
    return out


def list_sequences(xml: str) -> dict:
    """The sequences an XML holds, for the Review tab's picker: [{"id", "name", "bin", "clips", "seconds"}]. One entry means the XML is a single cut and opens as it always did."""
    src = Path(xml).expanduser()
    if not src.is_file():
        raise ToolError(f"that XML is not there: {src}")
    use_labs()
    import split_sequences as ss
    try:
        rows = ss.list_sequences(ss._load(src))
    except (ss.SplitError, ET.ParseError) as exc:
        raise ToolError(f"could not read the sequences in {src.name}: {exc}") from exc
    return {"xml": str(src), "sequences": rows}


def extract_sequences(xml: str, ids: list[str]) -> dict:
    """The chosen sequences of a many-sequence XML, each written as its own self-contained XML in `<name> - seqs/` beside it (the file definitions each one needs are copied in). Never touches the source."""
    src = Path(xml).expanduser()
    if not src.is_file():
        raise ToolError(f"that XML is not there: {src}")
    use_labs()
    import split_sequences as ss
    try:
        done = ss.split_all(src, src.parent / f"{src.stem} - seqs", only=list(ids))
    except (ss.SplitError, ET.ParseError) as exc:
        raise ToolError(str(exc)) from exc
    if not done:
        raise ToolError("none of the chosen sequences were found in the XML")
    import export_gate
    import verify_export
    for d in done:                                      # what the export check already finds wrong in the piece, as it came: a defect the export brought in, which the check reports but does not block (see export_xml)
        rep = verify_export.Report()
        px = Path(d["xml"])
        verify_export.check_xml(px, rep)
        skip = {"CUT-GRANULARITY"} if export_gate.is_whole_file(px) else set()
        px.with_name(px.name + ".inherited.json").write_text(json.dumps({"source": str(src), "failed": [n for n, ok, _d in rep.rows if ok is False and n not in skip]}))
    return {"xml": str(src), "folder": str(src.parent / f"{src.stem} - seqs"), "sequences": done}


def build_review(xml: str, out: str | None = None, height: int = 540) -> dict:
    """An export XML in; a review folder (preview.mp4, review.html, timeline.json) out. Returns where it is and what it holds."""
    src = Path(xml).expanduser()
    if not src.is_file():
        raise ToolError(f"that XML is not there: {src}")
    use_labs()
    from build_review import build
    from timeline import TimelineError, load_cut
    folder = Path(out).expanduser() if out else src.parent / f"{src.stem} - review"
    try:
        page = build(src, folder, height=height)
        cut = load_cut(src)
    except TimelineError as exc:
        raise ToolError(str(exc)) from exc
    return {"xml": str(src), "folder": str(folder), "page": str(page), "preview": str(folder / "preview.mp4"), "url": serve_review(folder),
            "sequence": cut.sequence_name, "clips": len(cut.video), "duration": round(cut.zone_end, 2)}


def _tail(text: str, n: int = 14) -> str:
    """What a tool said when it stopped, for a person: its own refusal or failed-check lines if it printed any, otherwise the last lines without progress bars and library warnings."""
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    noise = ("frames/s", "UserWarning", "warnings.warn", "it/s]", "FutureWarning", "DeprecationWarning")
    says = [ln for ln in lines if ("REFUSING" in ln or "[FAIL]" in ln or "FAILED" in ln) and not any(x in ln for x in noise)]
    return "\n".join(says[:8] if says else [ln for ln in lines if not any(x in ln for x in noise)][-n:])


def _plan_with_suggestions(src: Path, note_list: list[dict], folder: Path, stage) -> str | None:
    """When some notes carry a `suggested_op`, build the plan here: their fixes as they are, and the interpreter (the local claude CLI) only for the notes that have none, and not at all
    when every note has one. Returns the plan file's path, or None when no note carries a fix (then revise interprets everything, as before)."""
    import json
    import ops as ops_mod
    import timeline
    if not any(isinstance(n.get("suggested_op"), dict) for n in note_list):
        return None
    cut = timeline.load_cut(src)
    sug = ops_mod.from_suggestions(note_list, cut)
    have = {o["note"] for o in sug}
    rest = [i for i in range(1, len(note_list) + 1) if i not in have]
    interpreted: list[dict] = []
    if rest:
        stage("Reading the notes that have no ready-made fix (the local claude CLI)")
        masked = [dict(n, text="(handled separately)") if i in have else n for i, n in enumerate(note_list, start=1)]
        interpreted = [o for o in ops_mod.interpret(cut, masked) if o["note"] not in have]
    plan = ops_mod.validate(sug + interpreted, note_list, cut)
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / "ops_in.json"
    f.write_text(json.dumps(plan, indent=2))
    return str(f)


def _with_filler_notes(src: Path, note_list: list[dict], nfile: Path, folder: Path) -> tuple[list[dict], Path]:
    """The notes plus one note for every um or uh the cut still has (labs/review_loop/fillers.py), each carrying its measured cut as its own fix. A filler a note of the person's already
    asks to remove (its moment inside the filler, the note saying um or uh) is left to that note. Returns the notes and the file they are in (the original file when no
    filler was added)."""
    import timeline
    use_labs()
    import fillers
    import ops as ops_mod
    cut = timeline.load_cut(src)
    found = fillers.find(cut)
    asked = [float(n.get("timeline_sec", 0.0)) for n in note_list if ops_mod.SAYS_FILLER.search(str(n.get("text", "")))]
    added = list(note_list)
    for f in found:
        if any(f["start"] - 0.3 <= t <= f["end"] + 0.3 for t in asked):
            continue
        mid = (f["start"] + f["end"]) / 2
        clip = next((c.idx for c in cut.video if c.tl_start <= mid < c.tl_end), 1)
        words = " ".join(f["words"])
        added.append({"timeline_sec": round(mid, 2), "clip": clip, "source": Path(cut.video[clip - 1].src_path).name, "source_sec": round(cut.video[clip - 1].src_in + (mid - cut.video[clip - 1].tl_start), 2),
                      "where": f"filler at {f['start']:.2f}s", "text": f'AI: removing the filler "{words}" ({f["start"]:.2f} to {f["end"]:.2f} seconds): an um or uh is cut from the sound and the picture.',
                      "shapes": [], "ai": True, "kind": "filler", "key": f"filler-{f['start']:.2f}",
                      "suggested_op": {"op": "remove_range", "start": f["start"], "end": f["end"], "why": f"the filler '{words}', measured by listening again with fillers in the prompt"}})
    if len(added) == len(note_list):
        return note_list, nfile
    return added, _notes_path(folder, added)


def _caption_lines_of(src: Path) -> list[tuple[float, float, str]]:
    """The caption lines of the cut `src` (the layered cut a note was left on): [(start, end, words)] on the timeline, in order. Empty when the cut has no captions."""
    import json
    rec = _finish_record(src) or {}
    cj = Path(rec.get("folder", "")) / "captions" / "captions.json"
    if not cj.is_file():
        return []
    try:
        j = json.loads(cj.read_text())
        ws = float(j["window"]["start"])
        return [(g["show_start"] + ws, g["show_end"] + ws, str(g["text"])) for g in j["groups"]]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _with_caption_context(src: Path, note_list: list[dict], nfile: Path, folder: Path) -> tuple[list[dict], Path]:
    """Each note gets the caption lines around its moment (`caption_lines`: previous / this / next), so the notes reader can see what the captions say (it was blind to them: "move the 23 to the next
    caption" replaced a whole line with "$23,000") and the check can accept words that stay or move between neighbouring lines. Returns the notes and the file they are in."""
    lines = _caption_lines_of(src)
    if not lines:
        return note_list, nfile
    out = []
    for n in note_list:
        t = float(n.get("timeline_sec", 0.0))
        k = next((i for i, (a, b, _x) in enumerate(lines) if a - 0.05 <= t <= b + 0.05), None)
        if k is None:
            k = min(range(len(lines)), key=lambda i: min(abs(t - lines[i][0]), abs(t - lines[i][1])))
            if min(abs(t - lines[k][0]), abs(t - lines[k][1])) > 1.5:
                out.append(n)
                continue
        out.append({**n, "caption_lines": {"previous": lines[k - 1][2] if k > 0 else "", "this": lines[k][2], "next": lines[k + 1][2] if k + 1 < len(lines) else ""}})
    return out, _notes_path(folder, out, "notes_with_captions.json")


def _notes_path(folder: Path, notes: list[dict], name: str = "notes_with_fillers.json") -> Path:
    import json
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / name
    f.write_text(json.dumps({"schema": "review_notes.v0-draft", "sequence": "", "notes": notes}, indent=2))
    return f


def apply_notes(xml: str, notes: str, out: str | None = None, height: int = 540, on_stage=None, ops: str | None = None, auto_fillers: bool = True) -> dict:
    """An export XML and a review_notes.json in; a revised cut (next version XML + review page) and the QA pass on it out.

    The revise step is the tool's own script (labs/review_loop/revise.py), run as it is run by hand, and the QA pass is labs/qa/qa_pass.qa on its files. Nothing about
    what a note means is decided here. A refusal or a failed check comes back as a ToolError carrying the tool's own last lines."""
    import json
    import re
    src, nfile = Path(xml).expanduser(), Path(notes).expanduser()
    if not src.is_file():
        raise ToolError(f"that XML is not there: {src}")
    if not nfile.is_file():
        raise ToolError(f"that notes file is not there: {nfile}")
    try:
        note_list = json.loads(nfile.read_text())["notes"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ToolError(f"that is not a review_notes.json (no 'notes' list): {exc}") from exc
    if not note_list:
        raise ToolError("that notes file has no notes")
    folder = Path(out).expanduser() if out else next_version_folder(src)
    use_labs()
    stage = on_stage or (lambda _s: None)
    if not ops and auto_fillers:
        # every um and uh in the cut is cut out of the sound and the picture, on every revision (Ryan, 2026-10-11): each one is a note of its own, made and checked like any other
        stage("Listening for every um and uh")
        try:
            note_list, nfile = _with_filler_notes(src, note_list, nfile, folder)
        except Exception as exc:                                       # a failed listen costs the filler cuts only, never the revision
            stage(f"The um and uh pass did not run ({type(exc).__name__}: {str(exc)[:120]}); the notes were still made")

    if not ops:
        try:
            note_list, nfile = _with_caption_context(src, note_list, nfile, folder)       # the notes reader sees the caption lines around each note
        except Exception as exc:
            stage(f"The captions around the notes could not be read ({type(exc).__name__}: {str(exc)[:100]}); the notes were still read")

    if all(isinstance(n.get("suggested_op"), dict) for n in note_list):
        stage("Making the changes the notes ask for")
    else:
        stage("Reading the notes and revising the cut (the notes are interpreted by the local claude CLI, this can take a few minutes)")
    cmd = [sys.executable, str(LABS / "review_loop" / "revise.py"), str(src), str(nfile), "--out", str(folder), "--height", str(height)]
    if not ops:
        ops = _plan_with_suggestions(src, note_list, folder, stage)       # notes that carry their own measured fix (the AI review's) are not sent to the interpreter
    if ops:
        cmd += ["--ops", str(Path(ops).expanduser())]          # a reviewed plan: the interpretation step is skipped
    p = subprocess.run(cmd, capture_output=True, text=True)
    set_aside: list[int] = []
    for _attempt in range(3):
        if p.returncode == 0:
            break
        # A check that names a note ("[FAIL] note 9 SEAM-TEXT ...") says that ONE change did not hold up once made. That note is set aside with the check's own words and the rest are made,
        # so one bad change never throws away everything that was asked. A failure that names no note (the cut's length, its seams, the export check) is the whole revision's and stays one.
        text = p.stdout + "\n" + p.stderr
        bad = {int(m.group(1)): m.group(0) for m in re.finditer(r"\[FAIL\] note (\d+) [^\n]*", text)}
        for m in re.finditer(r"REFUSING: ([^\n]*)\(notes? ([\d, ]+)\)", text):                 # a refusal that names its notes is those notes' alone: they are set aside with its words
            for n in re.findall(r"\d+", m.group(2)):
                bad.setdefault(int(n), f"[FAIL] note {n} REFUSED  {m.group(1).strip()}")
        plan_file = folder / "ops.json"
        if not bad or not plan_file.is_file():
            break
        plan = json.loads(plan_file.read_text())
        kept = [o for o in plan if o["note"] not in bad]
        for n, line in sorted(bad.items()):
            reason = "made, then its own check failed, so it was not kept: " + re.sub(r"^\[FAIL\] note \d+ ", "", line).strip()[:220]
            kept.append({"note": n, "op": "unsupported", "reason": reason, "why": ""})
            set_aside.append(n)
        retry = folder / "ops_retry.json"
        retry.write_text(json.dumps(sorted(kept, key=lambda o: o["note"]), indent=2))
        stage(f"{len(bad)} change{'s' if len(bad) != 1 else ''} did not hold up and {'were' if len(bad) != 1 else 'was'} set aside; making the rest")
        p = subprocess.run([*cmd[:cmd.index("--out") + 2], "--height", str(height), "--ops", str(retry)], capture_output=True, text=True)
    if p.returncode != 0 and (folder / "ops.json").is_file():
        # A check that belongs to the WHOLE revision (the lav sync, the pool, the length) names no note, and the loop used to give up with every change lost (Ryan, 2026-10-15: "the editor needs to be
        # smarter ... use some sort of logic to get around revisions it doesn't understand"). The changes are tried in halves: those that hold up are kept, those that break a check are set aside, each
        # with the check's own words, and the rest are made.
        fail_text = p.stdout + "\n" + p.stderr
        plan = json.loads((folder / ("ops_retry.json" if (folder / "ops_retry.json").is_file() else "ops.json")).read_text())
        kept, dropped = _find_passing_changes(cmd, plan, folder, height, stage)
        if dropped and len(kept) < len(plan):
            first = next((l.strip() for l in fail_text.splitlines() if "[FAIL]" in l), "a check on the whole cut failed")
            keep = [o for o in kept]
            for o in dropped:
                keep.append({"note": o["note"], "op": "unsupported", "reason": f"made, then it broke a check that guards the whole cut ({first[:150]}), so this change was left out and the rest were made", "why": ""})
                set_aside.append(o["note"])
            retry = folder / "ops_retry.json"
            retry.write_text(json.dumps(sorted(keep, key=lambda o: o["note"]), indent=2))
            stage(f"{len(dropped)} change{'s' if len(dropped) != 1 else ''} broke a check on the whole cut and {'were' if len(dropped) != 1 else 'was'} left out; making the other {len(kept)}")
            p = subprocess.run([*cmd[:cmd.index("--out") + 2], "--height", str(height), "--ops", str(retry)], capture_output=True, text=True)
    if p.returncode != 0:
        raise ToolError("The revision did not pass its own checks, so no revised cut was kept.\n" + _tail(p.stdout + "\n" + p.stderr))
    items_file = folder / "changes.json"
    items = json.loads(items_file.read_text())["items"] if items_file.exists() else []
    m = re.match(r"^(.*)_v(\d+)$", src.stem)
    v2 = folder / f"{m.group(1) if m else src.stem}_v{(int(m.group(2)) if m else 1) + 1}.xml"
    base = {"folder": str(folder), "items": items, "applied": sum(1 for i in items if i.get("applied")), "notes": len(note_list)}
    if not v2.is_file():
        # a note that only changes a caption's words or asks for a bleep touches no clip, so revise wrote no version; on a cut that carries layers that is still a change to make
        try:
            ops_now = json.loads((folder / "ops.json").read_text()) if (folder / "ops.json").is_file() else []
            import finish_cut as _fc
            layered = any(_fc.layers_present(src).values())
        except Exception:
            ops_now, layered = [], False
        if not (layered and any(o.get("op") in REBUILD_OPS for o in ops_now)):
            return {**base, "xml": None, "page": None, "qa": None, "message": "Nothing in the notes could be applied to the timeline, so no new version was written."}
        v2.write_text(src.read_text())                                            # the picture is unchanged; the layers are what this version changes
        for i in items:
            if i.get("op") in REBUILD_OPS:
                i["applied"], i["summary"] = True, "made when the layers were rebuilt below"
        base["applied"] = sum(1 for i in items if i.get("applied"))

    stage("Checking every note against the new version")
    import qa_pass
    import timeline
    old, new = timeline.load_cut(src), timeline.load_cut(v2)
    prev = src.parent / f"{src.stem} - review"
    results, glob, unreq, report = qa_pass.qa(note_list, json.loads((folder / "ops.json").read_text()), items, old, new, v2, folder / "qa",
                                              before_page=prev if (prev / "preview.mp4").exists() else None, after_page=folder, title=f"QA pass: {v2.stem}", before_xml=src)
    qa_out = {"report": str(report),
              "notes": [{"note": r.note, "time": round(r.time, 2), "status": r.status, "text": r.text, "rows": [{"op": x.op, "status": x.status, "detail": x.detail} for x in r.rows]} for r in results],
              "whole_cut": [{"name": n, "ok": ok, "detail": d} for n, ok, d in glob], "unrequested": list(unreq)}
    message = ""
    layers_made = None
    try:
        layers_made = _put_layers_back(src, v2, folder, note_list, stage, height)
    except ToolError as exc:
        message = f"The picture was revised, but the captions, music and bleep could not be put back on it, so this version has none of them: {exc}"
        for it in items:
            if it.get("op") in REBUILD_OPS:                                  # a change the rebuild was to make is NOT made: it says so, in the rebuild's own words
                it["applied"], it["summary"] = False, "not made: the layers could not be rebuilt (" + str(exc).splitlines()[0][:160] + ")"
        base["applied"] = sum(1 for i in items if i.get("applied"))
        if not (folder / "review.html").is_file():
            try:
                from build_review import build
                build(v2, folder, height=height)                              # the revised picture still gets its review page, so the failure is seen, not a crash
            except Exception as build_exc:
                message += f" (and its review page could not be built: {build_exc})"
    if layers_made:
        message = "The captions, music and bleep were put back on the revised cut: " + "; ".join(f"{s['name']}: {s['summary']}" for s in layers_made["steps"] if s["done"])
        for it in items:                                                      # a note the rebuild made says so (it was reported "not on the timeline" before the rebuild ran)
            res = layers_made.get("edit_results", {}).get(it.get("note"))
            if res:
                it["applied"], it["summary"] = res
            elif it.get("op") in REBUILD_OPS:
                it["applied"], it["summary"] = True, "made when the layers were rebuilt"
        base["applied"] = sum(1 for i in items if i.get("applied"))
        done = layers_made.get("edit_results", {})
        for entry in qa_out["notes"]:                                         # a row about a graphic, the music or an effect was measured BEFORE the rebuild made it: it now says what the rebuild did
            for row in entry["rows"]:
                if row["op"] in REBUILD_OPS and row["op"] not in ("edit_caption", "bleep_word") and entry["note"] in done:
                    row["status"] = qa_pass.UNMEASURED
                    row["detail"] = ("made: " if done[entry["note"]][0] else "not made: ") + done[entry["note"]][1] + "; read back by the revision battery (safety_net/battery_revisions.py), not by this pass"
    return {**base, "xml": str(v2), "page": str(folder / "review.html"), "url": serve_review(folder), "qa": qa_out, "message": message, "layers": layers_made}


MAX_BISECT_TRIES = 40


def _find_passing_changes(cmd: list, plan: list, folder: Path, height: int, stage) -> tuple[list, list]:
    """The changes of `plan` that can be made together without breaking a check on the revised XML, and those that cannot: (kept, dropped).

    Only changes that touch the timeline can break such a check; the rest are always kept. Each trial is revise.py --checks-only (no render), so a trial costs seconds. The changes are tried whole, then in
    halves (the first half, then the second half added to what held up), so a few bad changes among many cost a few trials; a change that fails alone is dropped."""
    import json
    import shutil
    use_labs()
    import ops as opsmod
    cut_ops = set(opsmod.CUT_OPS)
    fixed = [o for o in plan if o.get("op") not in cut_ops]
    cands = [o for o in plan if o.get("op") in cut_ops]
    trial = folder / "ops_try.json"
    base = cmd[:cmd.index("--out") + 1]
    tries = {"n": 0}

    def holds(subset: list) -> bool:
        if tries["n"] >= MAX_BISECT_TRIES:                                   # a bound on the time spent: past it nothing more is tried and what is left is set aside
            return False
        tries["n"] += 1
        trial.write_text(json.dumps(sorted(fixed + subset, key=lambda o: o["note"]), indent=2))
        out = folder / f"_try{tries['n']}"
        r = subprocess.run([*base, str(out), "--height", str(height), "--ops", str(trial), "--checks-only"], capture_output=True, text=True)
        shutil.rmtree(out, ignore_errors=True)
        return r.returncode == 0

    def grow(held: list, rest: list) -> tuple[list, list]:
        if not rest:
            return held, []
        if holds(held + rest):
            return held + rest, []
        if len(rest) == 1:
            return held, rest
        mid = len(rest) // 2
        h1, d1 = grow(held, rest[:mid])
        h2, d2 = grow(h1, rest[mid:])
        return h2, d1 + d2

    if not cands:
        return plan, []
    stage(f"A check on the whole cut failed: trying the changes in groups to find which one breaks it")
    held, dropped = grow([], cands)
    trial.unlink(missing_ok=True)
    return fixed + held, dropped


def _finish_record(src: Path) -> dict | None:
    """finish.json of the version `src` came from (written by finish_cut.run), wherever that version's folder keeps it."""
    import json
    for p in (src.parent / "finish.json", src.parent / "layers" / "finish.json"):
        if p.is_file():
            try:
                return json.loads(p.read_text())
            except ValueError:
                return None
    return None


MUSIC_STEP_DB = 4.0
REBUILD_OPS = ("edit_caption", "redo_captions", "cta_reason", "bleep_word", "edit_callout", "remove_graphic", "extend_graphic", "end_graphic", "music_volume", "remove_music", "remove_sfx", "replace_sfx")     # what the layer rebuild makes (labs/review_loop/capabilities.py)


def _sfx_named_in(rec: dict) -> str:
    """The effect the last finish put on its graphics, read from the music step's own summary ("... a sound effect on each graphic (3) (Air Slicers - Short Dirty Woosh)")."""
    import re
    for s in rec.get("steps", []):
        m = re.search(r"sound effect on each graphic \(\d+\) \(([^()]+)\)", str(s.get("summary", "")))
        if m:
            return m.group(1).strip()
    return ""


def _finish_edits(ops: list[dict], note_list: list[dict], rec: dict, cta: str = "") -> tuple[dict, dict[int, tuple[bool, str]]]:
    """What the notes change in the finish itself (Ryan, 2026-10-12: anything the editor made, it must be able to change from a note): the title card, the name tags and the call to action
    (their words, taking one out, keeping it up longer or ending it), the music level or the music, the sound effects. Returns the cumulative changes (earlier ones are kept, so a later
    rebuild does not undo them) and, per note, whether it was made and what was done, in words."""
    import json
    import re
    use_labs()
    import finish_cut as fc
    opts = dict(rec.get("options") or {})
    gedits = list(opts.get("graphics_edits") or [])
    music_db = opts.get("music_db")
    sfx_exclude = list(opts.get("sfx_exclude") or [])
    no_music = no_sfx = False
    results: dict[int, tuple[bool, str]] = {}
    cta_short = fc.short_cta(cta or opts.get("cta") or "")
    targets: list[tuple[str, str, float, float]] = []                    # (edit target, how to say it, on, off) for every graphic the last finish put on the cut
    try:
        folder = Path(rec.get("folder", ""))
        spec = json.loads((folder / "graphics_spec.json").read_text())
        plan = json.loads((folder / "graphics" / "placement.json").read_text())["plan"]
        if plan.get("title"):
            targets.append(("title", "the title card", plan["title"]["on"][0], plan["title"]["off"]))
        for ls, lp in zip(spec.get("lower_thirds", []), plan.get("lower_thirds", [])):
            key = ls.get("key") or ls["name"]
            if key == "cta" or (cta_short and ls["name"] == cta_short):          # a finish made before the call to action was marked in its plan: found by its words
                key = "cta"
            targets.append(("cta" if key == "cta" else "name:" + key, f'the "{lp["name"]}" graphic', lp["on"], lp["off"]))
    except (OSError, ValueError, KeyError):
        targets = []

    def pick(text: str, at: float):
        low = text.lower()
        if re.search(r"call to action|\bcta\b", low):
            hit = next((x for x in targets if x[0] == "cta"), None)
            if hit:
                return hit
        if re.search(r"\btitle\b", low):
            hit = next((x for x in targets if x[0] == "title"), None)
            if hit:
                return hit
        for x in targets:
            if x[0].startswith("name:") and x[0][5:].lower() in low:
                return x
        inside = [x for x in targets if x[2] - 0.4 <= at <= x[3] + 0.4]
        if inside:
            return min(inside, key=lambda x: x[3] - x[2])
        near = min(targets, key=lambda x: min(abs(at - x[2]), abs(at - x[3])), default=None)
        return near if near and min(abs(at - near[2]), abs(at - near[3])) <= 2.0 else None

    for o in ops:
        kind, n = o.get("op"), o.get("note")
        note = note_list[n - 1] if isinstance(n, int) and 1 <= n <= len(note_list) else {}
        at, text = float(note.get("timeline_sec", 0.0)), str(note.get("text", ""))
        if kind in ("edit_callout", "remove_graphic", "extend_graphic", "end_graphic"):
            hit = pick(text, at)
            if hit is None:
                results[n] = (False, "no title card, name tag or call to action is on screen at that moment or named in the note")
                continue
            key, label, on, off = hit
            said = []
            if kind == "edit_callout":
                if o.get("title"):
                    gedits.append({"target": key, "do": "text", "text": o["title"]})
                    said.append(f'{label} now reads "{o["title"]}"')
                if o.get("subtitle"):
                    gedits.append({"target": key, "do": "sub", "text": o["subtitle"]})
                    said.append(f'its second line now reads "{o["subtitle"]}"')
                elif o.get("remove_subtitle"):
                    gedits.append({"target": key, "do": "sub", "text": ""})
                    said.append("its second line is taken out")
            elif kind == "remove_graphic":
                gedits.append({"target": key, "do": "remove"})
                said.append(f"{label} is taken out")
            elif kind == "extend_graphic":
                secs = o.get("seconds")
                gedits.append({"target": key, "do": "longer", "seconds": secs})
                said.append(f"{label} stays up {secs if secs else fc.GFX_LONGER_DEFAULT:g} s longer")
            elif kind == "end_graphic":
                cut_by = max(off - at, 0.3)
                gedits.append({"target": key, "do": "shorter", "seconds": cut_by})
                said.append(f"{label} ends {cut_by:.1f} s sooner")
            results[n] = (True, "; ".join(said) + ", made when the layers were rebuilt") if said else (False, "the note asked for no change to the words")
        elif kind == "cta_reason":
            hit = next((x for x in targets if x[0] == "cta"), None)
            full = cta or opts.get("cta") or ""
            reason = fc.cta_reason(full)
            if hit is None:
                results[n] = (False, "no call to action is on screen on this cut, so there is none to give a reason")
            elif not reason:
                results[n] = (False, "the idea's own call to action gives no reason that fits a line (" + (f'"{full[:120]}"' if full else "the idea has none") + "); tell me the words you want under it and I will put them there")
            else:
                gedits.append({"target": "cta", "do": "sub", "text": reason})
                results[n] = (True, f'the call to action now carries the idea\'s own reason on a second line: "{reason}", made when the layers were rebuilt')
        elif kind == "music_volume":
            base = fc.MUSIC_DB if music_db is None else music_db
            music_db = max(min(base + (MUSIC_STEP_DB if o.get("direction") == "up" else -MUSIC_STEP_DB), -1.0), -24.0)
            results[n] = (True, f"the music is {'louder' if o.get('direction') == 'up' else 'quieter'}: {music_db:g} dB under the voice (was {base:g}), made when the layers were rebuilt")
        elif kind == "remove_music":
            no_music = no_sfx = True
            results[n] = (True, "the music and the sound effects riding on it are taken out, made when the layers were rebuilt")
        elif kind == "replace_sfx":
            current = opts.get("sfx_name") or _sfx_named_in(rec)                  # a finish made before the effect's name was recorded still says which it used, in its own summary
            if current and current not in sfx_exclude:
                sfx_exclude.append(current)
            results[n] = (True, f"the sound effect is swapped for another whoosh from your library (not \"{current or 'the current one'}\"); it is picked by how it plays" + (f", not by the description \"{o.get('sound')}\"" if o.get("sound") else "") + ", made when the layers were rebuilt")
        elif kind == "remove_sfx":
            no_sfx = True
            results[n] = (True, "the sound effects are taken out, made when the layers were rebuilt")
    return {"graphics_edits": gedits, "music_db": music_db, "no_music": no_music, "no_sfx": no_sfx, "sfx_exclude": sfx_exclude}, results


def _put_layers_back(src: Path, v2: Path, folder: Path, note_list: list[dict], stage, height: int) -> dict | None:
    """A revision of a cut that carries finishing layers: the picture was revised under them, so they are taken off, made again for the revised cut (same music track, caption corrections kept,
    bleep notes honoured) and the layered cut becomes this version. Returns None when the cut carried no layers."""
    import json
    use_labs()
    import finish_cut as fc
    have = fc.layers_present(src)
    if not any(have.values()):
        return None
    stage("Putting the captions, music and bleep back on the revised cut (several minutes)")
    rec = _finish_record(src) or {}
    fixes: list[dict] = []
    cj = Path(rec.get("folder", "")) / "captions" / "captions.json"
    if cj.is_file():
        try:
            fixes = json.loads(cj.read_text()).get("fixes") or []
        except ValueError:
            fixes = []
    ops_file = folder / "ops.json"
    ops = json.loads(ops_file.read_text()) if ops_file.is_file() else []
    if any(o.get("op") == "edit_caption" for o in ops) and cj.is_file():              # a caption's new words: the same record fix_caption.py keeps, so the rebuild applies it
        import fix_caption
        old = json.loads(cj.read_text())
        fixes, _ledger = fix_caption.collect(ops, note_list, old["groups"], old["window"]["start"], fixes)
    requests = []
    try:
        import bleep as bp
        requests = bp.requests_from_notes(ops, note_list)
    except Exception:
        requests = []
    music_raw = rec.get("music_raw")
    upd, edit_results = _finish_edits(ops, note_list, rec, str((rec.get("options") or {}).get("cta") or idea_brief(src).get("call_to_action") or ""))
    unlayered = folder / f"{v2.stem} (before layers).xml"
    unlayered.write_text(v2.read_text())
    try:
        opts = rec.get("options") or {}
        r = fc.run(unlayered, folder / "layers", captions=opts.get("captions", have["on_screen"]) and have["on_screen"], music=have["music"] and not upd["no_music"], bleep=True, rebuild=True,
                   graphics=bool(opts.get("graphics")) and have["on_screen"], sfx=bool(opts.get("sfx")) and have["music"] and not upd["no_sfx"] and not upd["no_music"],
                   music_file=music_raw if music_raw and Path(music_raw).is_file() else None, caption_fixes=fixes or None, bleep_requests=requests or None,
                   progress=stage, final_name=v2.name, cta=str(opts.get("cta") or idea_brief(src).get("call_to_action") or ""),   # a finish made before the call to action was recorded still gets it: the idea is the source
                   graphics_edits=upd["graphics_edits"], music_db=upd["music_db"], sfx_exclude=upd["sfx_exclude"],
                   sfx_keep=str(opts.get("sfx_name") or _sfx_named_in(rec)))
    except (fc.FinishError, Exception) as exc:
        unlayered.unlink(missing_ok=True)
        raise ToolError(str(exc)) from exc
    failed = [f"{n}: {d}" for n, ok, d in r["checks"] if ok is False]
    if failed:
        unlayered.unlink(missing_ok=True)
        raise ToolError("the rebuilt layers did not pass their checks:\n" + "\n".join(failed[:5]))
    v2.write_text(Path(r["xml"]).read_text())
    stage("Building the review page with the layers")
    from build_review import build
    build(v2, folder, height=height)
    return {"steps": r["steps"], "folder": r["folder"], "edit_results": edit_results}


def finish_cut(xml: str, out: str | None = None, captions: bool = True, music: bool = True, bleep: bool = True, sfx_at: float | None = None,
               height: int = 540, on_stage=None, graphics: bool = False, sfx: bool = False, music_file: str | None = None, cta: str | None = None, keep_going: bool = False, music_fallback: bool = True) -> dict:
    """A cut in; the next version out with the finishing layers on it (captions, a music bed, the bleep), each made by the tool that already does it (labs/review_loop/finish_cut.py). The result has the
    shape of apply_notes' so the Review tab opens it as the next version. Nothing is kept if a step or a check fails."""
    src = Path(xml).expanduser()
    if not src.is_file():
        raise ToolError(f"that XML is not there: {src}")
    folder, name = (Path(out).expanduser(), None) if out else free_version_folder(src)
    use_labs()
    stage = on_stage or (lambda _s: None)
    import finish_cut as fc
    from timeline import TimelineError
    try:
        r = fc.run(src, folder, captions, music, bleep, sfx_at, progress=stage, graphics=graphics, sfx=sfx, final_name=name, cta=cta if cta is not None else str(idea_brief(src).get("call_to_action") or ""),
                   music_file=music_file, keep_going=keep_going, music_fallback=music_fallback)                                     # a recorded track (the replay harness): nothing is generated
    except (fc.FinishError, TimelineError) as exc:
        raise ToolError(str(exc)) from exc
    failed = [f"{n}: {d}" for n, ok, d in r["checks"] if ok is False]
    if failed:
        raise ToolError("The finished cut did not pass its own checks, so it was not kept:\n" + "\n".join(failed[:6]))
    stage("Building the review page")
    from build_review import build
    build(Path(r["xml"]), folder, height=height)
    items = [{"note": i, "note_time": 0, "note_text": "Finish the cut: " + s["name"], "op": s["name"], "applied": s["done"], "failed": bool(s.get("failed")), "summary": s["summary"], "why": ""} for i, s in enumerate(r["steps"], start=1)]
    return {"folder": str(folder), "items": items, "applied": sum(1 for i in items if i["applied"]), "notes": len(items), "xml": r["xml"], "page": str(folder / "review.html"),
            "url": serve_review(folder), "qa": None, "message": "",
            "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in r["checks"]]}


CREATIVE_FILE = "get_creative.json"
DEFAULT_CREATIVE = {"fillers": True, "captions": True, "graphics": True, "music": True, "bleep": True, "sfx": True, "redo": False}


def creative_file(xml: str | Path) -> Path:
    """Where a Get Creative batch keeps its progress: beside the first sequence, so the Review tab finds it again after it was closed, switched away from, or the app restarted."""
    return Path(xml).expanduser().parent / CREATIVE_FILE


def read_creative(xml: str | Path) -> dict | None:
    import json
    f = creative_file(xml)
    try:
        return json.loads(f.read_text()) if f.is_file() else None
    except (OSError, ValueError):
        return None


def _write_creative(xml: str | Path, state: dict) -> None:
    import json
    import time
    f = creative_file(xml)
    state["updated"] = time.time()
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(f)


def get_creative(xmls: list[str], opts: dict | None = None, emit=None, stop=None) -> dict:
    """Get Creative on each cut in turn: ums and uhs cut out, then captions, a title card and name tag, a music bed and the bleep (the finish), each result a new version in that cut's own review session.

    One cut after another, in the background, so Ryan can leave and come back to all of them done. Progress is kept in `get_creative.json` beside the first cut (state of each: waiting, running, done,
    failed, skipped) and every version is recorded in its cut's session AS IT IS MADE, so a stop, a failure or a closed app loses only the step in progress. One cut failing does not stop the others.
    A cut that already carries captions, graphics or music (the finish is not idempotent) is skipped and says so. `emit(event)` receives creative_progress / creative_item / creative_batch_done
    and the review_built / notes_applied events the sessions are built from; `stop()` ends the batch after the cut being worked on. Nothing is decided about the picture here."""
    import time
    use_labs()
    emit = emit or (lambda _e: None)
    stop = stop or (lambda: False)
    o = {**DEFAULT_CREATIVE, **(opts or {})}
    xmls = [str(Path(x).expanduser()) for x in xmls]
    if not xmls:
        raise ToolError("no sequences to work on")
    for x in xmls:
        if not Path(x).is_file():
            raise ToolError(f"that XML is not there: {x}")
    state = {"state": "running", "started": time.time(), "opts": o,
             "items": [{"xml": x, "name": Path(x).stem, "status": "waiting", "step": "", "message": "", "final_xml": ""} for x in xmls]}
    _write_creative(xmls[0], state)
    emit({"type": "creative_started", "xml": xmls[0], "total": len(xmls), "items": state["items"]})

    def save():
        _write_creative(xmls[0], state)

    for i, it in enumerate(state["items"]):
        if stop():
            state["state"] = "stopped"
            break
        it["status"] = "running"

        def say(step, it=it, i=i):
            it["step"] = str(step)[:200]
            save()
            emit({"type": "creative_progress", "xml": it["xml"], "index": i + 1, "total": len(xmls), "name": it["name"], "step": it["step"]})
        say("starting")
        try:
            msg = _creative_one(it["xml"], o, emit, say, it)
            it["status"] = "skipped" if msg.startswith("skipped") else "done"
            it["message"] = msg
        except ToolError as exc:
            it["status"], it["message"] = "failed", str(exc)[:900]
        except Exception as exc:                                   # one sequence failing must never end the batch
            it["status"], it["message"] = "failed", f"{type(exc).__name__}: {exc}"[:900]
        it["step"] = ""
        save()
        emit({"type": "creative_item", "xml": it["xml"], "index": i + 1, "total": len(xmls), "name": it["name"], "status": it["status"], "message": it["message"], "final_xml": it["final_xml"]})
    else:
        state["state"] = "done"
    if state["state"] == "stopped":
        for it in state["items"]:
            if it["status"] == "running":
                it["status"] = "waiting"
    save()
    counts = {k: sum(1 for it in state["items"] if it["status"] == k) for k in ("done", "skipped", "failed", "waiting")}
    emit({"type": "creative_batch_done", "xml": xmls[0], "state": state["state"], "counts": counts, "items": state["items"]})
    return {"state": state["state"], "counts": counts, "items": state["items"]}


def _creative_one(xml: str, o: dict, emit, say, it: dict) -> str:
    """One cut: its review (if it has none), then the steps. Returns what happened in a sentence (it starts with "skipped" when the cut was left alone)."""
    import shutil
    import timeline
    use_labs()
    import finish_cut as fc
    if o.get("redo"):                                           # start over from the original: the earlier versions are moved aside (never deleted) so the new ones take V2, V3 again
        src = Path(xml)
        old = [d for d in src.parent.glob(f"{src.stem}_v*[0-9] - revised") if d.is_dir()]
        if old:
            keep = src.parent / "older versions"
            keep.mkdir(exist_ok=True)
            for d in old:
                dest = keep / d.name
                n = 2
                while dest.exists():
                    dest = keep / f"{d.name} ({n})"
                    n += 1
                shutil.move(str(d), str(dest))
            say(f"the {len(old)} earlier version(s) were moved to 'older versions'")
        sess = None
    else:
        sess = load_session(xml)
    if sess is None:
        say("building the review")
        res, _p = capture(build_review, xml)
        emit({"type": "review_built", "batch": True, **res})          # "batch": the Review tab must not treat these as the review on screen; the session on disk is what it reads
        sess = load_session(xml)
    versions = (sess or {}).get("versions") or [{"label": "V1", "xml": xml}]
    cur = Path(versions[-1]["xml"])
    have = fc.layers_present(cur)
    if have["on_screen"] or have["music"]:
        return "skipped: it already has " + " and ".join(k for k, v in (("on-screen graphics or captions", have["on_screen"]), ("music", have["music"])) if v) + " (open it to look, or start it over to do it again)"
    n = len(versions) + 1
    did = []
    if o.get("fillers"):
        say("listening for ums and uhs")
        import fillers
        found = fillers.find(timeline.load_cut(cur))
        if found:
            nfile = _notes_path(cur.parent, [], "notes_empty.json")
            res, _p = capture(apply_notes, str(cur), str(nfile), None, 540, lambda s: say(f"ums and uhs: {s}"), None, True)
            emit({"type": "notes_applied", "batch": True, "root": xml, "label": f"V{n}", "auto": True, **res})
            cur, n = Path(res["xml"]), n + 1
            did.append(f"{len(found)} um/uh cut out")
        else:
            did.append("no ums or uhs heard")
    steps = [k for k in ("captions", "graphics", "music", "bleep") if o.get(k)]
    if steps:
        say("adding " + ", ".join(steps))
        res, _p = capture(finish_cut, str(cur), None, bool(o.get("captions")), bool(o.get("music")), bool(o.get("bleep")), None, 540, lambda s: say(s), bool(o.get("graphics")), bool(o.get("sfx")), keep_going=True)
        emit({"type": "notes_applied", "batch": True, "root": xml, "label": f"V{n}", "auto": True, "finish": True, **res})
        cur = Path(res["xml"])
        failed = [i for i in res.get("items", []) if i.get("failed")]
        added = [s for s in steps if not any(i["op"] == s for i in failed)]
        if added:
            did.append("added " + ", ".join(added))
        for i in failed:                                       # a layer that could not be made is named with its reason; the ones that worked are kept
            did.append(f"{i['op']} {i['summary']}")
        for i in res.get("items", []):                         # music that was not generated but taken from the cache or the Artlist library: say which, so it is never mistaken for a new bed
            if i.get("op") == "music" and i.get("applied") and ", because the music could not be generated" in i.get("summary", ""):
                did.append("music is " + i["summary"].split(", because the music could not be generated")[0] + " (it could not be generated)")
    it["final_xml"] = str(cur)
    return "; ".join(did) if did else "nothing was chosen to do"


def edit_graphics(xml: str, edits: list[dict], out: str | None = None, height: int = 540, on_stage=None) -> dict:
    """Move and scale on-screen graphics: the next version of a cut with each graphic in `edits` ({"id", "dx", "dy", "scale"}: the graphic's clip id, its move as fractions of the frame, its size in percent)
    written as Basic Motion on its own clip (labs/review_loop/graphics_edit.py). Nothing is re-rendered and nothing else changes; the result is read back and the preview is rebuilt with the graphics where they now are.
    Same shape as finish_cut's result so the Review tab opens it as the next version."""
    src = Path(xml).expanduser()
    if not src.is_file():
        raise ToolError(f"that XML is not there: {src}")
    if not edits:
        raise ToolError("no graphic was moved or scaled")
    use_labs()
    import graphics_edit as ge
    stage = on_stage or (lambda _s: None)
    stage("Reading the graphics on this cut")
    found = {g["id"]: g for g in ge.describe(src)}
    unknown = [e.get("id") for e in edits if e.get("id") not in found]
    if unknown:
        raise ToolError(f"these graphics are not on this version of the cut: {unknown}")
    for e in edits:
        g = found[e["id"]]
        if e.get("text") and not g.get("fields"):
            raise ToolError(f"\"{g['label']}\" has no words of its own to change")
        if e.get("lines") and not g.get("lines"):
            raise ToolError(f"\"{g['label']}\" has no caption lines to change")
    W, H = found[edits[0]["id"]]["_size"]
    centers = {i: ((g["box"][0] + g["box"][2]) / 2 * W, (g["box"][1] + g["box"][3]) / 2 * H) for i, g in found.items()}
    folder, name = (Path(out).expanduser(), src.name) if out else free_version_folder(src)
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / name
    try:
        edits = ge.render_text_edits(found, edits, folder, src, stage)                 # words changed: only those graphics are redrawn, into this version's folder
        ge.apply(src, dst, edits, centers, W, H)
    except ge.GraphicsError as exc:
        raise ToolError(str(exc)) from exc
    bad = [f"{n}: {d}" for n, ok, d in ge.check(src, dst, edits, centers, W, H) if not ok]
    if bad:
        raise ToolError("The edited cut did not pass its own checks, so it was not kept:\n" + "\n".join(bad))
    stage("Building the review page")
    from build_review import build
    build(dst, folder, height=height)
    items = []
    for i, e in enumerate(edits, start=1):
        g = found[e["id"]]
        moved = []
        if abs(float(e.get("dx", 0))) > 1e-6 or abs(float(e.get("dy", 0))) > 1e-6:
            moved.append(f"moved {float(e.get('dx', 0)) * 100:+.1f}% across and {float(e.get('dy', 0)) * 100:+.1f}% down")
        if abs(float(e.get("scale", 100)) - 100) > 1e-6:
            moved.append(f"scaled to {float(e.get('scale', 100)):g}%")
        if e.get("text"):
            moved.append("words changed to " + " / ".join(f'"{v}"' for v in e["text"].values() if v))
        if e.get("lines"):
            moved.append(f"{len(e['lines'])} caption line{'s' if len(e['lines']) != 1 else ''} changed")
        items.append({"note": i, "note_time": 0, "note_text": "Graphic: " + g["label"], "op": "graphic", "applied": True, "failed": False,
                      "summary": f"{g['label']}: " + (" and ".join(moved) if moved else "put back as it was made"), "why": ""})
    return {"folder": str(folder), "items": items, "applied": len(items), "notes": len(items), "xml": str(dst), "page": str(folder / "review.html"),
            "url": serve_review(folder), "qa": None, "message": ""}


def idea_direction(xml: str | Path) -> str:
    """What the chosen idea says the cut is for, as the direction the AI reviewer judges the story by. A cut made from a generated idea carries the idea's id in its name (`<project> <last 8 of the id> <time>`);
    the idea (plans/idea_*<id>.json: title, thesis, hook, tone, call to action, and the story order with a label on each range) is read from the project folder. The person does not retype it. Empty when the cut
    has no idea (an XML brought in from outside)."""
    b = idea_brief(xml)
    if not b:
        return ""
    lines = [f'The idea this cut was made from: "{b.get("title", "")}".']
    thesis = str(b.get("why_it_works", "")).strip().split("\n\n")[0].removeprefix("Thesis:").strip()     # the first paragraph says what the cut says; the rest is research notes and links
    for text, label in ((thesis, "What it says"), (b.get("hook"), "It opens on"), (b.get("tone"), "Tone"), (b.get("call_to_action"), "The call to action at the end")):
        if str(text or "").strip():
            lines.append(f"{label}: {str(text).strip()[:700]}")
    lines.append("The parts of this idea are the cut's story: the opening, the build and the ending it names are meant to be there. Do not propose dropping what carries them.")
    use_labs()
    import capabilities                                                    # one list of what the finish makes, shared with the notes reader (capabilities.py)
    lines.append(f"When the editing is done, the finish adds: {capabilities.finish_adds()}. Do not raise a problem only those solve "
                 "(a missing call to action, a word viewers might mishear, a jump cut).")
    return "\n".join(lines)


IDEA_BRIEF_OVERRIDE: dict | None = None      # set by safety_net/replay_cut.py: a replayed copy of a cut lives outside the project, so its idea is given, not searched for


def idea_brief(xml: str | Path) -> dict:
    """The brief of the idea the cut was made from ({} when the cut has none): its id is in the cut's name, the idea in the project's plans folder."""
    import json
    import re as _re
    if IDEA_BRIEF_OVERRIDE is not None:
        return IDEA_BRIEF_OVERRIDE
    src = Path(xml).expanduser()
    m = _re.search(r" ([0-9a-f]{8}) \d{8}-\d{6}", src.name)
    plans = next((d / "plans" for d in src.parents if (d / "plans").is_dir()), None) if m else None
    found = sorted(plans.glob(f"idea_*{m.group(1)}.json")) if plans else []
    if not found:
        return {}
    try:
        return json.loads(found[0].read_text()).get("data", {}).get("brief") or {}
    except (OSError, ValueError, AttributeError):
        return {}


def _direction_for(xml: str | Path, extra: str = "") -> str:
    """The chosen idea's direction, then anything the person added on top of it."""
    return "\n\n".join(d for d in (idea_direction(xml), (extra or "").strip()) if d)


def ai_review(xml: str, folder: str | None = None, on_stage=None, story: bool = True, direction: str = "") -> dict:
    """The AI review of a cut (labs/review_loop/ai_review.py): cut edges against the voice, where the voice comes from, the story read from the words. Saved as ai_review.json beside the page."""
    import json
    src = Path(xml).expanduser()
    if not src.is_file():
        raise ToolError(f"that XML is not there: {src}")
    use_labs()
    import ai_review as air
    from timeline import TimelineError
    try:
        res = air.review(src, story=story, progress=on_stage or (lambda _s: None), direction=_direction_for(src, direction))
    except TimelineError as exc:
        raise ToolError(str(exc)) from exc
    if folder:
        out = Path(folder).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        (out / "ai_review.json").write_text(json.dumps(res, indent=2))
    return res


AUTO_MAX_ROUNDS = 6            # 4 left two fixable notes "not tried" on Ryan's V5 (2026-10-15) while each round was still making changes; the loop still stops on a worse measured score, a cut too short, or nothing left to try
AUTO_KEEP_FRACTION = 0.3           # the editor may not cut the video below this share of the length it started the loop with. 0.5 blocked every fix its reviewer asked for on the Septic cut (2026-10-08: V3 was 33.7 s against a 33 s floor); Ryan: "It should be able to do all of the requested changes"
AUTO_MIN_CLIPS_PER_MIN = 6.3       # the export check (safety_net/verify_export CUT-GRANULARITY) wants at least 6 clips a minute; a little over, since extensions lengthen the cut


def review_score(res: dict) -> int:
    """How much is left to fix in a version, from its AI review (lower is better): one point per finding, two for a hole in the voice or a weak hook or abrupt ending, three for a recorder out of line."""
    n = 0
    for note in res.get("notes", []):
        n += 2 if str(note.get("kind", "")).startswith("audio") else 1
    for c in res.get("checks", []):
        if c["name"] in ("HOOK", "ENDING") and c["ok"] is False:
            n += 2
        elif c["name"] == "SYNC" and c["ok"] is False:
            n += 3
    return n


MEASURED_SKIP = ("story",)


def measured_score(res: dict) -> int:
    """review_score without the story reader's verdicts: only what is measured (cut edges, a voice off the microphones, framing, where the voice comes from, sync). The loop decides "worse" on this."""
    n = 0
    for note in res.get("notes", []):
        k = str(note.get("kind", ""))
        if k in MEASURED_SKIP:
            continue
        n += 2 if k.startswith("audio") else 1
    if any(c["name"] == "SYNC" and c["ok"] is False for c in res.get("checks", [])):
        n += 3
    return n


def _left_reason(n: dict, parked: dict, last: dict, status: str, best_label: str) -> str:
    """Why a note is still there, truthfully: what the editor tried and what happened, or why it did not try."""
    if n.get("key") in parked:
        return parked[n["key"]]
    if isinstance(n.get("suggested_op"), dict):
        if status == "worse" and n.get("key") in last["keys"]:
            return f"made in {last['label']}, which was set aside because its measured checks got worse than {best_label}'s"
        if status == "limit":
            return "not tried: the editor used all its rounds before getting to it"
        if status in ("worse", "short"):
            return "not tried: the editor stopped before getting to it"
        return "not tried"
    return "needs different words (a cleaner take or a change to the captions), which no cut of this footage can give"


def _op_sig(n: dict):
    """What a story fix asks for, independent of the wording of the note and of clip numbers (both change from version to version): start_at_words is named by its words. Other fixes have none."""
    op = n.get("suggested_op") or {}
    return (op.get("op"), op.get("words")) if op.get("op") in ("start_at_words", "end_at_words") and op.get("words") else None


PIN_TOL = 0.25


def _pinned(c, pins: set, kind: str) -> bool:
    """A clip edge a story fix placed by its words (kind "start" or "end"): (kind, file, source second) within PIN_TOL."""
    return any(k == kind and path == c.src_path and abs(sec - (c.src_in if kind == "start" else c.src_out)) <= PIN_TOL for k, path, sec in pins)


def _compatible(fixable: list[dict], cut, floor_sec: float, parked: dict, pins: set | None = None) -> list[dict]:
    """The fixes that can be made together. A clip that is being dropped needs no edge fix; a clip whose start is being moved later by a story fix must not also start earlier; and story fixes
    that drop clips may not take the video below the length floor or leave fewer than two clips (those are parked with the reason). Order is kept."""
    drops, later, earlier = set(), set(), set()
    remaining, count = cut.zone_end, len(cut.video)
    refused = set()
    for n in fixable:
        op = n["suggested_op"]
        if op["op"] == "drop_clip" and 1 <= op["clip"] <= len(cut.video):
            c = cut.video[op["clip"] - 1]
            dur = c.tl_end - c.tl_start
            after_clips, after_dur = count - len(drops) - 1, remaining - dur
            if after_dur < floor_sec or after_clips < 2:
                parked[n["key"]] = "dropping this clip would shorten the video too much"
                refused.add(id(n))
                continue
            if after_dur > 0 and after_clips / (after_dur / 60.0) < AUTO_MIN_CLIPS_PER_MIN:
                parked[n["key"]] = "dropping this clip would leave too few clips for the length (the export check wants at least 6 a minute)"
                refused.add(id(n))
                continue
            drops.add(op["clip"])
            remaining -= dur
        elif op["op"] == "start_at_words":
            later.add(op["clip"])
        elif op["op"] == "end_at_words":
            earlier.add(op["clip"])
    keep = []
    for n in fixable:
        op = n["suggested_op"]
        if id(n) in refused:
            continue
        if op["op"] in ("extend_start", "extend_end", "start_at_words", "end_at_words") and op["clip"] in drops:
            continue                                                  # the clip is going: nothing to extend
        if op["op"] == "move_clip" and (op["clip"] in drops or op["before"] in drops):
            continue                                                  # the clip is going: nothing to extend
        if op["op"] == "extend_start" and op["clip"] in later:
            continue                                                  # a story fix starts this clip later; it cannot also start earlier
        if op["op"] == "extend_end" and op["clip"] in earlier:
            continue                                                  # the same, at the end
        if pins and 1 <= op.get("clip", 0) <= len(cut.video):
            c = cut.video[op["clip"] - 1]
            if (op["op"] == "extend_end" and _pinned(c, pins, "end")) or (op["op"] == "extend_start" and _pinned(c, pins, "start")):
                parked[n["key"]] = "this edge was placed on purpose by an earlier story fix (it chose the words), so it is not moved back"       # 2026-10-15: end_at_words, extend_end, end_at_words used rounds 5 to 7 undoing each other
                continue
        keep.append(n)
    return keep


def auto_edit(xml: str, folder: str, label: str = "V1", max_rounds: int = AUTO_MAX_ROUNDS, emit=None, cancelled=None, story: bool = True, root: str | None = None,
              finish: dict | None = None, direction: str = "") -> dict:
    """The AI editor's own loop: review the version, submit the findings it can fix as notes, review the new version, repeat, until the cut has nothing left to fix or the editor cannot do more.

    Everything is visible: each review and each revision is announced with the same events a manual review and a manual Submit produce (so the Review tab shows the AI's notes on the page, every
    version, and the QA ledger), plus `auto_edit_*` events for the rounds. Every version is kept as a file; nothing is overwritten. Guards: a note the editor could not carry out is parked and not
    submitted again; a round that leaves MORE to fix than before ends the loop and the earlier version is named as the one to review; the cut is not allowed below 60% of the length the loop
    started with; at most `max_rounds` revisions. Returns how it ended, the version to review, and what is left that it could not do."""
    raw_emit = emit or (lambda ev: None)
    emit = lambda ev: raw_emit({**ev, "root": root or xml})                # every event names its cut, so the session file can be kept even when no window is listening   # noqa: E731
    cancelled = cancelled or (lambda: False)
    use_labs()
    import timeline

    def review(x: str, f: str, lab: str) -> dict:
        emit({"type": "ai_review_started", "xml": x, "tag": lab, "auto": True})
        res = ai_review(x, f, lambda st: emit({"type": "ai_review_stage", "xml": x, "tag": lab, "stage": st, "auto": True}), story, direction)
        emit({"type": "ai_review_done", "xml": x, "tag": lab, "auto": True, **res})
        return res

    out: dict = {"status": "", "best": label, "versions": [], "left": [], "rounds": [], "message": ""}
    parked: dict[str, str] = {}
    pins: set = set()                                                 # clip edges a story fix placed by words: ("start"|"end", file, source second); the edge fixes do not move them back
    parked_sigs: set = set()                                       # fixes that failed, named by what they ask for: the same fix re-worded by the next review is not tried again
    cur_xml, cur_folder, cur_label = xml, folder, label
    try:
        floor = timeline.load_cut(Path(xml)).zone_end * AUTO_KEEP_FRACTION
        emit({"type": "auto_edit_started", "xml": xml, "tag": label, "max_rounds": max_rounds})
        res = review(cur_xml, cur_folder, cur_label)
        best = {"label": cur_label, "xml": cur_xml, "score": review_score(res), "measured": measured_score(res), "res": res}
        out["versions"].append({"label": cur_label, "xml": cur_xml, "score": best["score"]})
        rounds = 0
        last = {"keys": set(), "label": ""}                                # the notes the last round submitted, and the version it made
        while True:
            if cancelled():
                out["status"] = "stopped"
                break
            fixable = [n for n in res["notes"] if isinstance(n.get("suggested_op"), dict) and n.get("key") not in parked and _op_sig(n) not in parked_sigs]
            if not fixable:
                out["status"] = "clean" if not res["notes"] else "left"
                break
            if rounds >= max_rounds:
                out["status"] = "limit"
                break
            cut_now = timeline.load_cut(Path(cur_xml))
            fixable = _compatible(fixable, cut_now, floor, parked, pins)
            if not fixable:
                out["status"] = "left"
                break
            rounds += 1
            emit({"type": "auto_edit_round", "round": rounds, "of": max_rounds, "label": cur_label, "fixing": len(fixable)})

            def submit(batch: list[dict]) -> dict:
                payload = {"schema": "review_notes.v0-draft", "sequence": res.get("sequence", ""), "notes": [{**n, "shapes": n.get("shapes", [])} for n in batch]}
                nf = save_notes(cur_folder, payload)
                emit({"type": "notes_started", "xml": cur_xml, "notes": f"the AI editor's {len(batch)} notes", "auto": True})
                return apply_notes(cur_xml, str(nf), None, 540, lambda st: emit({"type": "notes_stage", "stage": st, "auto": True}))

            try:
                try:
                    r = submit(fixable)
                except ToolError as first:
                    edge_only = [n for n in fixable if n.get("kind", "").startswith("edge")]
                    if not edge_only or len(edge_only) == len(fixable):
                        raise
                    emit({"type": "notes_failed", "message": f"{first} Trying again with only the edge fixes.", "auto": True})        # the story fixes clashed with each other or with an edge fix
                    why = next((ln.strip() for ln in str(first).splitlines() if "REFUSING" in ln or "[FAIL]" in ln), "the checks refused the combination")
                    for n in fixable:
                        if n not in edge_only:
                            parked[n["key"]] = f"tried together with the other fixes and refused: {why[:200]}"
                            if _op_sig(n):
                                parked_sigs.add(_op_sig(n))
                    fixable = edge_only
                    r = submit(fixable)
            except ToolError as exc:
                emit({"type": "notes_failed", "message": str(exc), "auto": True})
                out["status"], out["message"] = "failed", str(exc)
                break
            items = {i.get("note"): i for i in r.get("items", [])}
            done = 0
            for i, n in enumerate(fixable, start=1):
                it = items.get(i, {})
                if it.get("applied"):
                    done += 1
                    if n.get("kind") == "framing":
                        parked[n["key"]] = "already made once; the picture is not re-framed every round"
                else:
                    parked[n["key"]] = str(it.get("summary") or "the editor could not make this change")
                    if _op_sig(n):
                        parked_sigs.add(_op_sig(n))
            out["rounds"].append({"round": rounds, "from": cur_label, "submitted": len(fixable), "applied": done})
            placed = [(n["suggested_op"]["op"], cut_now.video[n["suggested_op"]["clip"] - 1]) for i, n in enumerate(fixable, start=1)
                      if items.get(i, {}).get("applied") and n["suggested_op"]["op"] in ("start_at_words", "end_at_words") and 1 <= n["suggested_op"].get("clip", 0) <= len(cut_now.video)]
            if not r.get("xml"):
                emit({"type": "notes_applied", "auto": True, **r})
                out["status"] = "left"
                out["message"] = r.get("message", "")
                break
            new_label = f"V{int(cur_label[1:]) + 1}"
            emit({"type": "notes_applied", "auto": True, "label": new_label, **r})
            last = {"keys": {n.get("key") for i, n in enumerate(fixable, start=1) if items.get(i, {}).get("applied")}, "label": new_label}
            res = review(r["xml"], r["folder"], new_label)
            sc, ms = review_score(res), measured_score(res)
            out["versions"].append({"label": new_label, "xml": r["xml"], "score": sc})
            cur_xml, cur_folder, cur_label = r["xml"], r["folder"], new_label
            if placed:
                try:
                    cut_new = timeline.load_cut(Path(cur_xml))
                    for kind_, old_c in placed:                       # the clip now holding that footage: its edge is where the story fix put it
                        nc = next((c for c in cut_new.video if c.src_path == old_c.src_path and c.src_in - 0.6 <= old_c.src_in <= c.src_out), None)
                        if nc is not None:
                            pins.add(("end", nc.src_path, nc.src_out) if kind_ == "end_at_words" else ("start", nc.src_path, nc.src_in))
                except Exception:
                    pass
            if timeline.load_cut(Path(cur_xml)).zone_end < floor:
                out["status"] = "short"
                break
            if ms > best["measured"]:
                # worse on what is MEASURED (cut edges, a voice off the microphones, framing, sync). The story verdicts are not used here: they change from one reading to the next (2026-10-08:
                # V5 made both edge fixes it was asked for, then scored worse only because the reviewer now called the ending abrupt, and the fixes were thrown away)
                out["status"] = "worse"
                break
            best = {"label": cur_label, "xml": cur_xml, "score": sc, "measured": ms, "res": res}
        out["best"] = best["label"]
        final = best["res"] if out["status"] in ("worse", "short") else res
        out["left"] = [{"text": n["text"], "kind": n.get("kind", ""), "reason": _left_reason(n, parked, last, out["status"], best["label"])} for n in final["notes"]]
        out["score"] = best["score"]
        if finish and out["status"] not in ("stopped", "failed") and not cancelled():
            # the editor ends on the best version it made, and finishes that one: captions, title card and name tags, music, an effect on each graphic, the bleep (the same step as the tab's Finish button)
            bx = next(v for v in out["versions"] if v["label"] == best["label"])["xml"]
            try:
                fin = finish_cut(bx, None, bool(finish.get("captions", True)), bool(finish.get("music", True)), bool(finish.get("bleep", True)), None, 540,
                                 lambda st: emit({"type": "notes_stage", "stage": st, "auto": True}), bool(finish.get("graphics", True)), bool(finish.get("sfx", True)))
                top = max(int(v["label"][1:]) for v in out["versions"])
                nxt = "V" + str(top + 1)
                emit({"type": "notes_applied", "auto": True, "finish": True, "label": nxt, **fin})
                out["versions"].append({"label": nxt, "xml": fin["xml"], "score": best["score"]})
                out["picked"], out["best"] = best["label"], nxt                  # the version to review is the FINISHED one (the tab opens the version named here); the plain edit it was made from stays as the one before it
                out["finished"] = {"label": nxt, "steps": [{"name": i["op"], "done": i["applied"], "summary": i["summary"]} for i in fin["items"]]}
            except ToolError as exc:
                out["finish_failed"] = str(exc)
    except ToolError as exc:
        out["status"], out["message"] = "failed", str(exc)
    except Exception as exc:
        out["status"], out["message"] = "failed", f"{type(exc).__name__}: {exc}"
    out["summary"] = _auto_summary(out) + ("" if _direction_for(xml, direction) or not story else " This cut has no chosen idea and no direction, so the reviewer judged the story by its own idea of it.")
    emit({"type": "auto_edit_done", **out})
    return out


AGENT_WORK_ROOT = Path.home() / "Library" / "Application Support" / "Post House" / "agent_work"      # a plain path: a project folder on an external drive can have a name the Write tool refuses (2026-10-16: "back wall./" read as a suspicious Windows path)
AGENT_TURNS = 80
AGENT_MINUTES = 45


def agent_tools_allowed(work: Path) -> list[str]:
    """What the agent may run, and nothing else (the CLI refuses the rest, since nobody is there to approve it): its own toolbox script (inspect, search, apply), ffmpeg and ffprobe to listen, Read,
    and Write into its own working folder. Not arbitrary python, not the shell: the agent decides what to do, the toolbox is the only thing that changes a cut."""
    tools = Path(__file__).resolve().parent / "agent_tools.py"
    return [f"Bash(python3 {tools} *)", "Bash(ffmpeg *)", "Bash(ffprobe *)", "Read", f"Write(/{work}/**)", f"Edit(/{work}/**)"]      # a rule's path starting with ONE slash is relative to the project; two slashes make it absolute (the first run, 2026-10-16, was refused its own folder)

AGENT_BRIEF = """You are the problem solver inside Ryan Pierce's video editing app (Post House). Its rule-based AI editor reviewed a cut, made the changes it could, and left the notes below because its fixed rules
refused them. Ryan's complaint: it keeps leaving notes unmade that a human editor would simply reason around. You are that human editor. Make these changes, or find the closest honest thing that makes
the video better, and say plainly when something truly cannot be done.

THE CUT: {xml}
Its folder: {folder}     Your working folder: {work}     The notes, as a notes file: {notes}   (note numbers below are 1..N in that file)

NOTES THE EDITOR COULD NOT MAKE (with the reason it gave):
{left}

{direction}YOUR TOOLS (run them with Bash; the app's own code runs underneath, so every change goes through the same checks Ryan's Submit button does):
  python3 {tools} ops-help                         the operations you can ask for, with their fields
  python3 {tools} inspect "<cut.xml>"              every clip: where it plays, which recording it is from, and the words heard in it with their times (word@seconds in that recording)
  python3 {tools} search "<cut.xml>" "some words"  lines of the recordings that hold those words, including lines the cut does not use
  python3 {tools} listen "<raw file>" <start> <seconds>   the words in any window of any RAW file (a recorder's wav, a camera's mp4: use the paths inspect prints), with its loudness. The cut's selection is not all there is: look
                                                   at what the recording says before and after it, and compare the camera's own sound with each recorder.
  python3 {tools} frames "<raw video>" <start> <seconds>  five stills from a raw video file; Read the jpg files it prints to LOOK at the picture (who is on screen, what the shot shows)
  python3 {tools} apply "<cut.xml>" "{notes}" "<ops.json>"   makes your operations as a NEW version (the checks run; a refusal or failed check makes nothing and is explained)
You may also listen with ffmpeg/ffprobe (levels, silence) and read files. Write your ops.json files into {work}. Do not edit, move or delete anything else; do not touch other projects.

HOW TO WORK
1. Run inspect on the cut. Read each note against the actual words and times. Decide what the note is really asking for.
2. For each note choose the operation that does it. Notes that quote words: use start_at_words / end_at_words with the words as the recording has them, not the note's spelling. A refusal tells you why: use
   that. Examples: a start that 'runs straight on from the word before it' means start at the beginning of the SENTENCE (start_at_words with the first words of that sentence); a check that reads a near
   word (seam / same) means the words you gave are right and the transcript is hearing them differently: give 3-5 words that are heard clearly; a line 'already in the cut' means the note is already met,
   say so; a missing fact the cut never states may exist elsewhere in the recordings: search for it, and restore_footage it if a line really says it (read it, do not trust a match score).
3. Apply. If it is refused or a check fails, read the words, change your plan, apply again. Combine notes in one apply when they do not touch the same clip edge; if one change breaks a check, apply the rest.
   Each success is a new version Ryan can open, and the next apply starts from the newest one (use the path `apply` printed after NEW VERSION). Use at most 6 applies.
   The shell here refuses variables, heredocs, `&&` chains and `python -c`: give every command in full, with literal paths, one command per call, and write each ops.json with the Write tool into {work}.
   AFTER an apply that adds or moves a line (restore_footage, start_at_words, end_at_words), run inspect on the NEW version and read the words under the clip you changed: they must be the words you meant.
   The check that runs inside apply only asks that SOME speech is heard. A recorder that was not near the speaker reads other words (2026-10-16: the restored "That piece of trim is added" played a
   recorder that heard "attractive circuit of the west finished"). The camera's own sound under the same picture often carries the line: search shows the words in the picture file, and use_camera_audio {{"clip": n}}
   plays the camera's sound under that clip. If the words under a clip you added are not the words you restored, fix it that way and say so; never report a line as made when it plays the wrong words.
4. Do not re-record anything (what is not in the footage cannot be made). Do not change the length of the video beyond what the notes need. Never invent a quote: every word you put on the timeline
   comes from a recording you read with the tools. Do not claim a note is made unless `apply` printed MADE for it.
5. Stop when everything doable is done. Your LAST message must be one JSON object and nothing after it:
   {{"made": [{{"note": 1, "what": "<one plain sentence>"}}], "not_made": [{{"note": 2, "why": "<the real reason, in plain words for Ryan>"}}], "final_xml": "<the newest version's xml path, or null>", "needs": "<a capability the app lacks that would have let you finish, or empty>"}}
"""


def _agent_left_text(left: list[dict]) -> str:
    return "\n".join(f'  {i}. {n.get("text", "")}\n     (the editor left it because: {n.get("reason", "")})' for i, n in enumerate(left, start=1))


def _last_json(text: str) -> dict:
    """The last JSON object in the agent's final message ({} when there is none)."""
    import json
    end = text.rfind("}")
    while end != -1:
        depth = 0
        for i in range(end, -1, -1):
            depth += (text[i] == "}") - (text[i] == "{")
            if depth == 0:
                try:
                    obj = json.loads(text[i:end + 1])
                    return obj if isinstance(obj, dict) else {}
                except ValueError:
                    break
        end = text.rfind("}", 0, end)
    return {}


def agent_edit(xml: str, folder: str, label: str, left: list[dict], emit=None, cancelled=None, root: str | None = None, direction: str = "", runner=None) -> dict:
    """A Claude agent (the local `claude` CLI, the plan and not the API key) takes the notes the rule-based editor left and works on them with the editor's own tools until they are made or it can say
    why they cannot be: it reads the cut's words, chooses the operations, applies them through every check, reads the refusals and tries again. Each version it makes is reported as an editor round is.
    `runner(prompt, workdir, env, on_line, cancelled)` is injectable (tests); it returns the agent's final text. Returns {"versions", "made", "not_made", "left", "best", "summary", "needs", "status"}."""
    import json
    import os
    import shutil
    import subprocess
    import time
    raw_emit = emit or (lambda ev: None)
    emit = lambda ev: raw_emit({**ev, "root": root or xml})                # noqa: E731
    cancelled = cancelled or (lambda: False)
    out: dict = {"versions": [], "made": [], "not_made": [], "left": [], "best": label, "needs": "", "status": "left"}
    if not left:
        out.update(status="clean", summary="No notes were left for the agent.")
        return out
    import hashlib
    work = AGENT_WORK_ROOT / f"{label}-{hashlib.sha1(str(xml).encode()).hexdigest()[:8]}-{time.strftime('%Y%m%d-%H%M%S')}"
    work.mkdir(parents=True, exist_ok=True)
    state = work / "agent_state.json"
    state.unlink(missing_ok=True)
    review_file = Path(folder) / "ai_review.json"
    times = {}
    try:
        for n in json.loads(review_file.read_text()).get("notes", []):
            times[str(n.get("text", "")).strip()] = n
    except (OSError, ValueError):
        pass
    notes = [{"timeline_sec": (times.get(str(n.get("text", "")).strip()) or {}).get("timeline_sec", 0.0), "text": n.get("text", ""), "kind": n.get("kind", ""), "shapes": []} for n in left]
    nfile = work / "review_notes.json"
    nfile.write_text(json.dumps({"schema": "review_notes.v0-draft", "sequence": "", "notes": notes}, indent=1))
    dir_text = f"WHAT THE VIDEO IS FOR (the owner's direction):\n{direction}\n\n" if direction.strip() else ""
    prompt = AGENT_BRIEF.format(xml=xml, folder=folder, work=work, notes=nfile, left=_agent_left_text(left), direction=dir_text, tools=Path(__file__).resolve().parent / "agent_tools.py")
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_WORKSPACE_ID")}      # the Claude Code plan, never the API key
    env["AGENT_STATE_FILE"] = str(state)
    emit({"type": "agent_edit_started", "tag": label, "notes": len(left)})
    seen = {"n": 0}

    def on_line(text: str) -> None:
        emit({"type": "notes_stage", "stage": text, "auto": True})

    def default_runner(pr: str, wd: Path, en: dict, on: object, cancel) -> str:
        exe = shutil.which("claude")
        if not exe:
            raise ToolError("the `claude` CLI is not on PATH, so the agent cannot start")
        cmd = [exe, "-p", "--output-format", "stream-json", "--verbose", "--max-turns", str(AGENT_TURNS), "--allowedTools", ",".join(agent_tools_allowed(wd)), "--add-dir", str(Path(xml).parent)]
        if en.get("AGENT_EDIT_MODEL"):
            cmd += ["--model", en["AGENT_EDIT_MODEL"]]
        proc = subprocess.Popen(cmd, cwd=wd, env=en, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        proc.stdin.write(pr)
        proc.stdin.close()
        final, deadline = "", time.time() + AGENT_MINUTES * 60
        log = (wd / "agent_log.txt").open("a")                                      # everything the agent printed, so a run that made nothing can be read afterwards
        log.write(f"$ {' '.join(cmd)}\n")
        for line in proc.stdout:
            log.write(line)
            log.flush()
            if cancel() or time.time() > deadline:
                proc.terminate()
                break
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "assistant":
                for part in (ev.get("message") or {}).get("content", []):
                    if part.get("type") == "tool_use" and part.get("name") == "Bash":
                        c = str((part.get("input") or {}).get("command", ""))
                        m = re.search(r"agent_tools\.py (ops-help|inspect|search|apply)", c)
                        on(f"The agent is {'reading the cut' if m and m.group(1) in ('inspect', 'ops-help') else 'searching the recordings' if m and m.group(1) == 'search' else 'making changes' if m else 'checking the audio'}")
            elif ev.get("type") == "result":
                final = str(ev.get("result") or "")
        proc.wait(timeout=30)
        log.write(f"\n[exit code {proc.returncode}]\n")
        log.close()
        if not final and proc.returncode not in (0, None):
            raise ToolError(f"the claude CLI exited with code {proc.returncode}; see {wd / 'agent_log.txt'}")
        return final

    try:
        final = (runner or default_runner)(prompt, work, env, on_line, cancelled)
    except ToolError as exc:
        out.update(status="failed", message=str(exc), summary=f"The agent could not run: {exc}")
        emit({"type": "auto_edit_done", **out})
        return out
    except Exception as exc:
        out.update(status="failed", message=f"{type(exc).__name__}: {exc}", summary=f"The agent stopped: {type(exc).__name__}: {exc}")
        emit({"type": "auto_edit_done", **out})
        return out
    rows = json.loads(state.read_text()) if state.is_file() else []
    cur = int(label[1:])
    for r in rows:
        cur += 1
        v = {"label": f"V{cur}", "xml": r["xml"], "score": 0}
        out["versions"].append(v)
        emit({"type": "notes_applied", "auto": True, "label": v["label"], **{k: x for k, x in r.items() if k != "from"}})
    verdict = _last_json(final or "")
    made, not_made = verdict.get("made") or [], verdict.get("not_made") or []
    out["made"], out["needs"] = made, str(verdict.get("needs") or "")
    if rows:
        out["best"] = out["versions"][-1]["label"]
    said = {int(x.get("note", 0)): str(x.get("why", "")) for x in not_made if isinstance(x, dict)}
    made_ids = {int(x.get("note", 0)) for x in made if isinstance(x, dict)}
    out["left"] = [{"text": n.get("text", ""), "kind": n.get("kind", ""), "reason": said.get(i) or n.get("reason", "") or "the agent did not make it"} for i, n in enumerate(left, start=1) if i not in made_ids]
    out["not_made"] = out["left"]
    if not final:
        out["summary"] = "The agent was stopped before it finished." + (f" {len(rows)} version{'s' if len(rows) != 1 else ''} made so far." if rows else "")
        out["status"] = "stopped"
    elif not rows:
        out["summary"] = "The agent made no new version. " + (f"It says: {out['left'][0]['reason']}" if out["left"] else "")
        out["status"] = "left"
    else:
        out["summary"] = f"The agent made {len(made)} of {len(left)} change{'s' if len(left) != 1 else ''} in {len(rows)} version{'s' if len(rows) != 1 else ''}. {out['best']} is the one to review." + (f" {len(out['left'])} left (each says why)." if out["left"] else "")
        out["status"] = "left" if out["left"] else "clean"
    if rows:                                                                # the newest version gets its own review, as every editor round does, so its page and notes are there
        try:
            x, f = out["versions"][-1]["xml"], rows[-1].get("folder")
            emit({"type": "ai_review_started", "xml": x, "tag": out["best"], "auto": True})
            res = ai_review(x, f, lambda st: emit({"type": "ai_review_stage", "xml": x, "tag": out["best"], "stage": st, "auto": True}), True, direction)
            emit({"type": "ai_review_done", "xml": x, "tag": out["best"], "auto": True, **res})
        except Exception as exc:
            emit({"type": "ai_review_failed", "xml": xml, "tag": out["best"], "message": f"{type(exc).__name__}: {exc}"})
    emit({"type": "auto_edit_done", **out})
    return out


def _auto_summary(o: dict) -> str:
    """How the run ended, in words. A finished run names the finished version and what was added; a failed finish is said, not hidden."""
    if o.get("finished"):
        n = max(len(o["versions"]) - 2, 0)
        done = ", ".join(s["name"] for s in o["finished"]["steps"] if s["done"])
        left = len(o["left"])
        rest = f" {left} thing{'s' if left != 1 else ''} left that the editor could not fix by itself (each says why)." if left else " The editor found nothing left to fix."
        return f"Ready for your review: {o['best']}, the finished cut ({done} added to {o.get('picked', '')}, after {n} revision{'s' if n != 1 else ''}).{rest}"
    return _auto_summary_base(o) + (f" It could not finish the cut: {o['finish_failed'][:240]}" if o.get("finish_failed") else "")


def _auto_summary_base(o: dict) -> str:
    n = max(len(o["versions"]) - 1, 0)
    best, left = o["best"], len(o["left"])
    ran = f"{n} revision{'s' if n != 1 else ''}"
    return {
        "clean": f"Ready for your review: {best} ({ran}). The editor found nothing left to fix.",
        "left": f"Ready for your review: {best} ({ran}). {left} thing{'s' if left != 1 else ''} left that the editor could not fix by itself (each says why).",
        "limit": f"Ready for your review: {best} ({ran}, the round limit). {left} thing{'s' if left != 1 else ''} left.",
        "worse": f"Stopped: the last revision left more to fix than before. {best} is the one to review ({ran}).",
        "short": f"Stopped: another revision would have cut the video below {AUTO_KEEP_FRACTION:.0%} of its length. {best} is the one to review ({ran}).",
        "stopped": f"Stopped by you after {ran}. {best} is the latest good version.",
        "failed": f"The editor could not continue: {o.get('message', '')[:300]}. {best} is the latest good version.",
    }.get(o["status"], f"Finished: {best}.")


def notify(title: str, text: str) -> bool:
    """A macOS notification (the batch ran while the person was elsewhere). Quiet failure: it is a courtesy, never a reason for a step to fail. True when it was sent."""
    if sys.platform != "darwin":
        return False
    try:
        script = f"display notification {json.dumps(str(text))} with title {json.dumps(str(title))}"
        return subprocess.run(["osascript", "-e", script], check=False, timeout=10, capture_output=True).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def finished_message(state: dict | None) -> tuple[str, str]:
    """The title and line for the notification after a Get Creative batch, from the batch's own record."""
    items = (state or {}).get("items") or []
    done = sum(1 for i in items if i.get("status") in ("done", "skipped"))
    failed = sum(1 for i in items if i.get("status") == "failed")
    left = sum(1 for i in items if i.get("status") in ("waiting", "running"))
    if left:
        return "Get Creative stopped", f"{done} of {len(items)} finished. The rest are waiting for you."
    if failed:
        return "Get Creative finished", f"{done} of {len(items)} finished, {failed} need a look."
    return "Get Creative finished", f"All {len(items)} sequence{'' if len(items) == 1 else 's'} finished. Ready for review."


def reveal(path: str) -> None:
    """Open a file in its default app (the review page opens in the browser) or a folder in Finder."""
    p = Path(path).expanduser()
    if not p.exists():
        raise ToolError(f"nothing at {p}")
    if sys.platform == "darwin":
        subprocess.run(["open", str(p)], check=False)
    elif sys.platform.startswith("win"):
        subprocess.run(["cmd", "/c", "start", "", str(p)], check=False)
    else:
        subprocess.run(["xdg-open", str(p)], check=False)
