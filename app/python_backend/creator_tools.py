"""The app's door to the creator-workflow tools (labs/, bundled beside this file by safety_net/sync_labs.sh).

The tools are file-in, file-out and each adds its own sibling folders to sys.path, so this module only has to put the bundled `labs/*` folders on the
path once and call them. In the bundle, python_backend/ plays the part the repo root plays in the repo: labs/, posthouse/ and safety_net/ sit side by side.

Nothing here reimplements a tool. Each function is the tool's own entry point plus the plain dict the UI needs.
"""
from __future__ import annotations

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
    return {"xml": str(src), "folder": str(folder), "page": str(page), "preview": str(folder / "preview.mp4"),
            "sequence": cut.sequence_name, "clips": len(cut.video), "duration": round(cut.zone_end, 2)}


def _tail(text: str, n: int = 14) -> str:
    return "\n".join([ln for ln in text.splitlines() if ln.strip()][-n:])


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
    folder = Path(out).expanduser() if out else src.parent / f"{src.stem} - revised"
    use_labs()
    stage = on_stage or (lambda _s: None)

    stage("Reading the notes and revising the cut (the notes are interpreted by the local claude CLI, this can take a few minutes)")
    cmd = [sys.executable, str(LABS / "review_loop" / "revise.py"), str(src), str(nfile), "--out", str(folder), "--height", str(height)]
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
    return {**base, "xml": str(v2), "page": str(folder / "review.html"), "qa": qa_out, "message": ""}


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
