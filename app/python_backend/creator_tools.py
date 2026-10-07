"""The app's door to the creator-workflow tools (labs/, bundled beside this file by safety_net/sync_labs.sh).

The tools are file-in, file-out and each adds its own sibling folders to sys.path, so this module only has to put the bundled `labs/*` folders on the
path once and call them. In the bundle, python_backend/ plays the part the repo root plays in the repo: labs/, posthouse/ and safety_net/ sit side by side.

Nothing here reimplements a tool. Each function is the tool's own entry point plus the plain dict the UI needs.
"""
from __future__ import annotations

import re
import subprocess
import sys
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
            seen.setdefault(str(p), p.stat().st_mtime)
    return [{"path": p, "name": Path(p).name, "mtime": t} for p, t in sorted(seen.items(), key=lambda kv: kv[1], reverse=True)[:30]]


def premiere_app() -> str | None:
    """The newest installed Adobe Premiere Pro (a Beta only if nothing else is installed)."""
    import glob
    found = sorted(glob.glob("/Applications/Adobe Premiere Pro*/Adobe Premiere Pro*.app"))
    stable = [a for a in found if "Beta" not in a]
    return (stable or found or [None])[-1]


def export_xml(xml: str, open_in_premiere: bool = True) -> dict:
    """The XML a version of the cut is, checked by the export check and (only if it passes) opened in Premiere. Never hands over an export it has not verified."""
    import os
    src = Path(xml).expanduser()
    if not src.is_file():
        raise ToolError(f"that XML is not there: {src}")
    use_labs()
    import export_gate
    import verify_export
    rep = verify_export.Report()
    verify_export.check_xml(src, rep)
    skip = {"CUT-GRANULARITY"} if export_gate.is_whole_file(src) else set()
    rows = [{"name": n, "ok": ok, "detail": d} for n, ok, d in rep.rows]
    failed = [r["name"] for r in rows if r["ok"] is False and r["name"] not in skip]
    out = {"xml": str(src), "verified": not failed, "failed": failed, "rows": rows, "opened": False, "app": None}
    if failed or not open_in_premiere:
        return out
    app = premiere_app()
    out["app"] = Path(app).stem if app else None
    if os.environ.get("POSTHOUSE_NO_OPEN"):
        return out                                                  # tests: report what would open, open nothing
    subprocess.run(["open", "-a", app, str(src)] if app else ["open", str(src)], check=False)
    out["opened"] = True
    return out


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


def apply_notes(xml: str, notes: str, out: str | None = None, height: int = 540, on_stage=None, ops: str | None = None) -> dict:
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
    if p.returncode != 0:
        raise ToolError("The revision did not pass its own checks, so no revised cut was kept.\n" + _tail(p.stdout + "\n" + p.stderr))
    items_file = folder / "changes.json"
    items = json.loads(items_file.read_text())["items"] if items_file.exists() else []
    m = re.match(r"^(.*)_v(\d+)$", src.stem)
    v2 = folder / f"{m.group(1) if m else src.stem}_v{(int(m.group(2)) if m else 1) + 1}.xml"
    base = {"folder": str(folder), "items": items, "applied": sum(1 for i in items if i.get("applied")), "notes": len(note_list)}
    if not v2.is_file():
        return {**base, "xml": None, "page": None, "qa": None, "message": "Nothing in the notes could be applied to the timeline, so no new version was written."}

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
    return {**base, "xml": str(v2), "page": str(folder / "review.html"), "url": serve_review(folder), "qa": qa_out, "message": ""}


def ai_review(xml: str, folder: str | None = None, on_stage=None, story: bool = True) -> dict:
    """The AI review of a cut (labs/review_loop/ai_review.py): cut edges against the voice, where the voice comes from, the story read from the words. Saved as ai_review.json beside the page."""
    import json
    src = Path(xml).expanduser()
    if not src.is_file():
        raise ToolError(f"that XML is not there: {src}")
    use_labs()
    import ai_review as air
    from timeline import TimelineError
    try:
        res = air.review(src, story=story, progress=on_stage or (lambda _s: None))
    except TimelineError as exc:
        raise ToolError(str(exc)) from exc
    if folder:
        out = Path(folder).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        (out / "ai_review.json").write_text(json.dumps(res, indent=2))
    return res


AUTO_MAX_ROUNDS = 4
AUTO_KEEP_FRACTION = 0.6           # the editor may not cut the video below this share of the length it started the loop with
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


def _compatible(fixable: list[dict], cut, floor_sec: float, parked: dict) -> list[dict]:
    """The fixes that can be made together. A clip that is being dropped needs no edge fix; a clip whose start is being moved later by a story fix must not also start earlier; and story fixes
    that drop clips may not take the video below the length floor or leave fewer than two clips (those are parked with the reason). Order is kept."""
    drops, later = set(), set()
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
    keep = []
    for n in fixable:
        op = n["suggested_op"]
        if id(n) in refused:
            continue
        if op["op"] in ("extend_start", "extend_end", "start_at_words") and op["clip"] in drops:
            continue                                                  # the clip is going: nothing to extend
        if op["op"] == "extend_start" and op["clip"] in later:
            continue                                                  # a story fix starts this clip later; it cannot also start earlier
        keep.append(n)
    return keep


def auto_edit(xml: str, folder: str, label: str = "V1", max_rounds: int = AUTO_MAX_ROUNDS, emit=None, cancelled=None, story: bool = True) -> dict:
    """The AI editor's own loop: review the version, submit the findings it can fix as notes, review the new version, repeat, until the cut has nothing left to fix or the editor cannot do more.

    Everything is visible: each review and each revision is announced with the same events a manual review and a manual Submit produce (so the Review tab shows the AI's notes on the page, every
    version, and the QA ledger), plus `auto_edit_*` events for the rounds. Every version is kept as a file; nothing is overwritten. Guards: a note the editor could not carry out is parked and not
    submitted again; a round that leaves MORE to fix than before ends the loop and the earlier version is named as the one to review; the cut is not allowed below 60% of the length the loop
    started with; at most `max_rounds` revisions. Returns how it ended, the version to review, and what is left that it could not do."""
    emit = emit or (lambda ev: None)
    cancelled = cancelled or (lambda: False)
    use_labs()
    import timeline

    def review(x: str, f: str, lab: str) -> dict:
        emit({"type": "ai_review_started", "xml": x, "tag": lab, "auto": True})
        res = ai_review(x, f, lambda st: emit({"type": "ai_review_stage", "xml": x, "tag": lab, "stage": st, "auto": True}), story)
        emit({"type": "ai_review_done", "xml": x, "tag": lab, "auto": True, **res})
        return res

    out: dict = {"status": "", "best": label, "versions": [], "left": [], "rounds": [], "message": ""}
    parked: dict[str, str] = {}
    cur_xml, cur_folder, cur_label = xml, folder, label
    try:
        floor = timeline.load_cut(Path(xml)).zone_end * AUTO_KEEP_FRACTION
        emit({"type": "auto_edit_started", "xml": xml, "tag": label, "max_rounds": max_rounds})
        res = review(cur_xml, cur_folder, cur_label)
        best = {"label": cur_label, "xml": cur_xml, "score": review_score(res), "res": res}
        out["versions"].append({"label": cur_label, "xml": cur_xml, "score": best["score"]})
        rounds = 0
        while True:
            if cancelled():
                out["status"] = "stopped"
                break
            fixable = [n for n in res["notes"] if isinstance(n.get("suggested_op"), dict) and n.get("key") not in parked]
            if not fixable:
                out["status"] = "clean" if not res["notes"] else "left"
                break
            if rounds >= max_rounds:
                out["status"] = "limit"
                break
            cut_now = timeline.load_cut(Path(cur_xml))
            fixable = _compatible(fixable, cut_now, floor, parked)
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
                else:
                    parked[n["key"]] = str(it.get("summary") or "the editor could not make this change")
            out["rounds"].append({"round": rounds, "from": cur_label, "submitted": len(fixable), "applied": done})
            if not r.get("xml"):
                emit({"type": "notes_applied", "auto": True, **r})
                out["status"] = "left"
                out["message"] = r.get("message", "")
                break
            new_label = f"V{int(cur_label[1:]) + 1}"
            emit({"type": "notes_applied", "auto": True, **r})
            res = review(r["xml"], r["folder"], new_label)
            sc = review_score(res)
            out["versions"].append({"label": new_label, "xml": r["xml"], "score": sc})
            cur_xml, cur_folder, cur_label = r["xml"], r["folder"], new_label
            if timeline.load_cut(Path(cur_xml)).zone_end < floor:
                out["status"] = "short"
                break
            if sc > best["score"]:
                out["status"] = "worse"
                break
            best = {"label": cur_label, "xml": cur_xml, "score": sc, "res": res}
        out["best"] = best["label"]
        final = best["res"] if out["status"] in ("worse", "short") else res
        left = [{"text": n["text"], "kind": n.get("kind", ""), "reason": parked.get(n.get("key"), "no ready-made fix: needs a decision or new words, not an edit")} for n in final["notes"]]
        out["left"] = left
        out["score"] = best["score"]
    except ToolError as exc:
        out["status"], out["message"] = "failed", str(exc)
    except Exception as exc:
        out["status"], out["message"] = "failed", f"{type(exc).__name__}: {exc}"
    out["summary"] = _auto_summary(out)
    emit({"type": "auto_edit_done", **out})
    return out


def _auto_summary(o: dict) -> str:
    n = max(len(o["versions"]) - 1, 0)
    best, left = o["best"], len(o["left"])
    ran = f"{n} revision{'s' if n != 1 else ''}"
    return {
        "clean": f"Ready for your review: {best} ({ran}). The editor found nothing left to fix.",
        "left": f"Ready for your review: {best} ({ran}). {left} thing{'s' if left != 1 else ''} left that the editor could not fix by itself (each says why).",
        "limit": f"Ready for your review: {best} ({ran}, the round limit). {left} thing{'s' if left != 1 else ''} left.",
        "worse": f"Stopped: the last revision left more to fix than before. {best} is the one to review ({ran}).",
        "short": f"Stopped: another revision would have cut the video below 60% of its length. {best} is the one to review ({ran}).",
        "stopped": f"Stopped by you after {ran}. {best} is the latest good version.",
        "failed": f"The editor could not continue: {o.get('message', '')[:300]}. {best} is the latest good version.",
    }.get(o["status"], f"Finished: {best}.")


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
