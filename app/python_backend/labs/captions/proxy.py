"""The 1080p render of a cut that the captions are transcribed from (captions/cut_1080.mp4) and the music is mixed against.

Ryan, 2026-10-13: "None of the captions match up and when i asked the editor to fix them it couldnt." The captions of V5 (43.7 s) were the words of an older 41.2 s cut: make_captions rendered the proxy
only `if not base.exists()`, so a folder that already held a render of ANOTHER cut (a retry after a failed check writes into the same folder; so did an older finish) was transcribed as if it were this
one, every caption fix re-used the same file, and the independent-transcript check agreed with it because it read the same file. A proxy is now stamped with a signature of the cut it was rendered
from and is used only for that cut.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def cut_signature(cut) -> str:
    """What the render holds: every clip's source, range and place on the timeline, picture and the speech audio (a recorder switched on or off changes the sound)."""
    rows = {"v": [[c.src_path, round(c.src_in, 3), round(c.src_out, 3), round(c.tl_start, 3), round(c.tl_end, 3)] for c in cut.video],
            "a": [[a.src_path, round(a.src_in, 3), round(a.src_out, 3), round(a.tl_start, 3), round(a.tl_end, 3)] for a in cut.audio]}
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def stamp_of(base: Path) -> Path:
    return base.with_name(base.name + ".cut")


def is_current(base: Path, cut) -> bool:
    s = stamp_of(base)
    return base.is_file() and s.is_file() and s.read_text().strip() == cut_signature(cut)


def ensure_proxy(cut, base: Path, height: int = 1080) -> bool:
    """Make `base` the render of `cut`: kept when its stamp says it already is, else rendered again. Returns True when it was rendered."""
    if is_current(base, cut):
        return False
    from render_preview import render_preview
    base.unlink(missing_ok=True)
    stamp_of(base).unlink(missing_ok=True)
    render_preview(cut, base, height=height)
    stamp_of(base).write_text(cut_signature(cut))
    return True


def captions_match(captions_json: Path, transcript, floor: float = 0.8) -> tuple[bool | None, str]:
    """The captions on a finished cut are the words the cut itself says, AT THE TIME they are shown (Ryan, 2026-10-13: "None of the captions match up"). `transcript` is the cut's own audio read
    from its XML (ai_review.transcript_of), never a file the captions step wrote: V5's captions were the transcript of an older, shorter render left in the folder, and every check passed because each
    one read that same render. Each caption line is held against what is said within a second of it, so the same words in another order (a reshuffled cut) do not pass."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "review_loop"))
    import words as words_mod
    j = json.loads(Path(captions_json).read_text())
    groups, off = j.get("groups", []), j.get("window", {}).get("start", 0.0)
    if not groups or not transcript:
        return None, "nothing to compare"
    ok, bad = 0, []
    for g in groups:
        t0, t1 = g["words"][0]["start"] + off, g["words"][-1]["end"] + off
        said = {w for row in transcript if row[1] >= t0 - 1.0 and row[0] <= t1 + 1.0 for w in words_mod.tokens(row[2])}
        toks = words_mod.tokens(g["text"])
        if toks and sum(w in said for w in toks) / len(toks) >= 0.6:
            ok += 1
        else:
            bad.append(f'{t0:.1f}s "{g["text"]}"')
    return ok / len(groups) >= floor, (f"{ok} of {len(groups)} caption lines are said in the cut's own audio at their time (need {floor:.0%})" + ("; first that are not: " + "; ".join(bad[:3]) if bad else ""))
