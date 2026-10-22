"""graphics_edit: move and scale the on-screen graphics as Basic Motion on their own clips (Ryan, 2026-10-21). Synthetic media only."""
import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "reframe"))

import graphics_edit as ge  # noqa: E402
import layers as ly  # noqa: E402
import timeline  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
W, H = 270, 480                                   # a small sequence: the maths is in fractions of the frame


def _mov(path: Path, box: tuple[int, int, int, int], seconds: float = 2.0) -> Path:
    x, y, w, h = box
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=white@1.0:s={w}x{h}:r=30:d={seconds},format=yuva444p10le,pad={W}:{H}:{x}:{y}:color=black@0.0",
                    "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", str(path)], check=True)
    return path


def _clip(cid: str, name: str, fid: str, path: Path, start: int, end: int, track_extra: str = "") -> str:
    return (f'<clipitem id="{cid}"><name>{name}</name><enabled>TRUE</enabled><duration>{end - start}</duration><rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate>'
            f'<start>{start}</start><end>{end}</end><in>0</in><out>{end - start}</out><alphatype>straight</alphatype>'
            f'<file id="{fid}"><name>{name}</name><pathurl>file://localhost{quote(str(path), safe="/")}</pathurl><rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate><duration>{end - start}</duration></file>{track_extra}</clipitem>')


@pytest.fixture()
def cut(tmp_path):
    a = _mov(tmp_path / "Graphic - Title.mov", (27, 96, 216, 96))                # upper: x 0.1-0.9, y 0.2-0.4
    b = _mov(tmp_path / "Graphic - Tag Bob.mov", (14, 340, 100, 60))              # lower left
    base = tmp_path / "v1.mov"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x203040:s={W}x{H}:r=30:d=3", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(base)], check=True)
    seq = (f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4"><sequence id="seq"><name>Short</name><duration>90</duration><rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate>'
           f'<media><video><format><samplecharacteristics><width>{W}</width><height>{H}</height></samplecharacteristics></format>'
           f'<track>{_clip("v1", "cam.mov", "file-1", base, 0, 90)}</track>'
           f'<track>{_clip("overlay-clipitem-1", "Graphic - Title.mov", "overlay-file-1", a, 0, 60)}</track>'
           f'<track>{_clip("overlay-clipitem-2", "Graphic - Tag Bob.mov", "overlay-file-2", b, 30, 90)}</track>'
           f'</video><audio><track><enabled>TRUE</enabled><locked>FALSE</locked></track></audio></media></sequence></xmeml>')
    x = tmp_path / "cut.xml"
    x.write_text(seq)
    return x, base


def test_the_move_and_the_motion_are_inverses_and_no_move_is_no_motion():
    c = (135.0, 120.0)
    assert ge.to_motion(c, W, H, 0, 0, 100) == (100, 0.0, 0.0) and ge.from_motion(c, W, H, None) == (0.0, 0.0, 100.0)
    for dx, dy, sc in ((0.1, -0.05, 150), (-0.3, 0.2, 60), (0.0, 0.4, 100), (0.05, 0.0, 250)):
        s, h, v = ge.to_motion(c, W, H, dx, dy, sc)
        got = ge.from_motion(c, W, H, (s, h, v))
        assert got == pytest.approx((dx, dy, sc), abs=1e-9)


def test_a_graphic_scaled_about_its_own_middle_grows_where_it_stands_and_a_move_lands_its_middle_exactly():
    """Premiere scales about the clip's centre and `center` is where that centre lands: the graphic's own middle c must end at c + d whatever the scale."""
    c = (60.0, 400.0)
    for dx, dy, sc in ((0.0, 0.0, 200), (0.1, -0.1, 50), (-0.2, 0.05, 150)):
        s, horiz, vert = ge.to_motion(c, W, H, dx, dy, sc)
        P = (W / 2 + horiz * W, H / 2 + vert * H)                              # where the clip's centre lands
        landed = (P[0] + (s / 100) * (c[0] - W / 2), P[1] + (s / 100) * (c[1] - H / 2))      # where the graphic's own middle ends up
        assert landed == pytest.approx((c[0] + dx * W, c[1] + dy * H), abs=1e-6)


def test_each_graphic_is_found_measured_from_its_own_pixels_and_named_by_what_it_is(cut, tmp_path):
    x, _ = cut
    gs = {g["name"]: g for g in ge.describe(x, tmp_path / "pics")}
    assert set(gs) == {"Graphic - Title.mov", "Graphic - Tag Bob.mov"}
    t, b = gs["Graphic - Title.mov"], gs["Graphic - Tag Bob.mov"]
    assert t["box"] == pytest.approx([0.1, 0.2, 0.9, 0.4], abs=0.02) and b["box"] == pytest.approx([14 / W, 340 / H, 114 / W, 400 / H], abs=0.02)
    assert (t["dx"], t["dy"], t["scale"]) == (0.0, 0.0, 100.0) and t["id"] == "overlay-clipitem-1"
    assert (t["start"], t["end"]) == (0.0, 2.0) and (b["start"], b["end"]) == (1.0, 3.0), "each is a short clip of its own span"
    assert (tmp_path / "pics" / "overlay-clipitem-1.png").is_file() and t["picture"] == "overlay-clipitem-1.png"


def test_the_edit_is_written_as_basic_motion_on_that_clip_only_and_reads_back_as_asked(cut, tmp_path):
    x, _ = cut
    gs = {g["id"]: g for g in ge.describe(x)}
    centers = {i: ((g["box"][0] + g["box"][2]) / 2 * W, (g["box"][1] + g["box"][3]) / 2 * H) for i, g in gs.items()}
    out = tmp_path / "moved.xml"
    edits = [{"id": "overlay-clipitem-1", "dx": 0.05, "dy": 0.3, "scale": 80}]
    ge.apply(x, out, edits, centers, W, H)
    rows = ge.check(x, out, edits, centers, W, H)
    assert all(ok for _n, ok, _d in rows), rows
    moved = {l.cid: l for l in ly.find_layers(out)}
    assert moved["overlay-clipitem-1"].motion is not None and moved["overlay-clipitem-2"].motion is None, "the tag was not touched"
    dx, dy, sc = ge.from_motion(centers["overlay-clipitem-1"], W, H, moved["overlay-clipitem-1"].motion)
    assert (dx, dy, sc) == pytest.approx((0.05, 0.3, 80), abs=1e-4)
    assert {g["id"]: (g["dx"], g["dy"], g["scale"]) for g in ge.describe(out)}["overlay-clipitem-1"] == pytest.approx((0.05, 0.3, 80), abs=1e-3), "describe reads the move back, so the page starts from where it is"
    assert "Basic Motion" in out.read_text() and (tmp_path / "cut.xml").read_text() == x.read_text(), "the input is never changed"


def test_putting_a_graphic_back_removes_its_motion_and_a_silly_scale_is_refused(cut, tmp_path):
    x, _ = cut
    centers = {"overlay-clipitem-1": (135.0, 144.0)}
    moved = tmp_path / "m.xml"
    ge.apply(x, moved, [{"id": "overlay-clipitem-1", "dx": 0.1, "dy": 0, "scale": 100}], centers, W, H)
    back = tmp_path / "b.xml"
    ge.apply(moved, back, [{"id": "overlay-clipitem-1", "dx": 0, "dy": 0, "scale": 100}], centers, W, H)
    assert "Basic Motion" not in back.read_text()
    with pytest.raises(ge.GraphicsError):
        ge.apply(x, tmp_path / "x.xml", [{"id": "overlay-clipitem-1", "dx": 0, "dy": 0, "scale": 900}], centers, W, H)
    with pytest.raises(ge.GraphicsError):
        ge.apply(x, tmp_path / "y.xml", [{"id": "nope", "dx": 0, "dy": 0, "scale": 100}], centers, W, H)


def _box_in_frame(video: Path, t: float) -> list[float] | None:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(video), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    im = np.frombuffer(raw, dtype=np.uint8).reshape(H, W, 3).astype(int)
    white = (im[..., 0] > 200) & (im[..., 1] > 200) & (im[..., 2] > 200)
    if not white.any():
        return None
    ys, xs = np.where(white)
    return [xs.min() / W, ys.min() / H, (xs.max() + 1) / W, (ys.max() + 1) / H]


def test_the_preview_shows_a_moved_and_scaled_graphic_where_the_edit_says(cut, tmp_path):
    """End to end: the compositor draws the graphic's box at c + d, scaled about its own middle, the same place the maths puts it."""
    x, base = cut
    g = next(g for g in ge.describe(x) if g["id"] == "overlay-clipitem-1")
    c = ((g["box"][0] + g["box"][2]) / 2 * W, (g["box"][1] + g["box"][3]) / 2 * H)
    plain = tmp_path / "plain.mp4"
    ly.composite(base, ly.find_layers(x), plain)
    assert _box_in_frame(plain, 0.5) == pytest.approx([0.1, 0.2, 0.9, 0.4], abs=0.03), "unmoved: where it was made"
    out = tmp_path / "moved.xml"
    edits = [{"id": "overlay-clipitem-1", "dx": 0.0, "dy": 0.3, "scale": 50}]
    ge.apply(x, out, edits, {"overlay-clipitem-1": c}, W, H)
    moved = tmp_path / "moved.mp4"
    ly.composite(base, ly.find_layers(out), moved)
    box = _box_in_frame(moved, 0.5)
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    assert (cx, cy) == pytest.approx((0.5, 0.3 + 0.3), abs=0.03), "its middle moved down by 0.3 of the frame"
    assert (box[2] - box[0], box[3] - box[1]) == pytest.approx((0.4, 0.1), abs=0.03), "half the size, about its own middle"


def test_the_apps_command_writes_the_next_version_with_the_edits_and_says_what_it_did(cut, tmp_path, monkeypatch):
    """creator_tools.edit_graphics, what the Review tab's Apply button calls: the preview build is stubbed, everything else is real."""
    sys.path.insert(0, str(HERE.parents[1] / "app" / "python_backend"))
    import creator_tools as ct
    ct.use_labs()
    import build_review
    built = []
    monkeypatch.setattr(build_review, "build", lambda xml, folder, height=540: built.append((Path(xml), Path(folder))) or (Path(folder) / "review.html").write_text("<html>"))
    x, _ = cut
    original = x.read_text()
    res = ct.edit_graphics(str(x), [{"id": "overlay-clipitem-1", "dx": 0.0, "dy": 0.12, "scale": 100}, {"id": "overlay-clipitem-2", "dx": 0.18, "dy": -0.04, "scale": 70}])
    assert Path(res["xml"]).name == "cut_v2.xml" and Path(res["folder"]).name == "cut_v2 - revised" and Path(res["xml"]).is_file()
    assert x.read_text() == original, "the version it was made from is never touched"
    assert res["applied"] == 2 and built and built[0][0] == Path(res["xml"]), "the review page is built from the new XML"
    s = [i["summary"] for i in res["items"]]
    assert "moved +0.0% across and +12.0% down" in s[0] and "moved +18.0% across and -4.0% down and scaled to 70%" in s[1], s
    assert {l.cid: l.motion is not None for l in ly.find_layers(Path(res["xml"]))} == {"overlay-clipitem-1": True, "overlay-clipitem-2": True}


def test_the_command_refuses_in_words_a_graphic_that_is_not_on_the_cut_or_nothing_to_do(cut):
    sys.path.insert(0, str(HERE.parents[1] / "app" / "python_backend"))
    import creator_tools as ct
    x, _ = cut
    with pytest.raises(ct.ToolError, match="not on this version of the cut"):
        ct.edit_graphics(str(x), [{"id": "overlay-clipitem-9", "dx": 0.1, "dy": 0, "scale": 100}])
    with pytest.raises(ct.ToolError, match="no graphic was moved"):
        ct.edit_graphics(str(x), [])


def _words_for(cut_xml: Path, tmp_path: Path):
    """Put placement.json beside the title and tag, and captions.json beside a captions graphic, the way the finish leaves them."""
    title_dir = tmp_path
    (title_dir / "placement.json").write_text(json.dumps({"kind": "title_layer", "plan": {"title": {"small": "If it is someone's home", "big": "", "joke": ""}, "lower_thirds": []}}))


def test_each_graphic_says_what_it_says_and_the_captions_list_their_lines(cut, tmp_path):
    x, _ = cut
    _words_for(x, tmp_path)
    gs = {g["name"]: g for g in ge.describe(x)}
    assert gs["Graphic - Title.mov"]["fields"] == {"small": "If it is someone's home"} and gs["Graphic - Title.mov"]["lines"] is None
    capdir = tmp_path / "cap"
    capdir.mkdir()
    mov = _mov(capdir / "captions.mov", (20, 380, 230, 40))
    (capdir / "captions.json").write_text(json.dumps({"window": {"start": 1.0, "end": 3.0}, "style": "pill", "fixes": [], "groups": [
        {"text": "one line", "pos": "bottom", "show_start": 0.0, "show_end": 0.8, "words": []}, {"text": "next line", "pos": "top", "show_start": 0.9, "show_end": 1.9, "words": []}]}))
    lay = ly.Layer("video", "captions.mov", str(mov), 1.0, 3.0, 4, 0, 0, "overlay-clipitem-9", None)
    lines = ge.caption_lines(lay)
    assert [(l["at"], l["text"], l["pos"]) for l in lines] == [(1.4, "one line", "bottom"), (2.4, "next line", "top")], "`at` is on the timeline: the window's start plus the middle of the line"
    assert ge.fields_of(lay) is None, "the captions have lines, not fields"


def test_the_captions_are_redrawn_with_the_changed_lines_as_fixes_tied_to_the_words_they_replaced(tmp_path, monkeypatch):
    pdir = tmp_path / "caps"
    pdir.mkdir()
    (pdir / "cut_1080.mp4").write_bytes(b"proxy")
    (pdir / "captions.json").write_text(json.dumps({"window": {"start": 0.0, "end": 8.0}, "style": "pill",
                                                    "fixes": [{"at": 1.0, "text": "This is where SOMEONE lives,", "was": "There's a lot of ways"}],
                                                    "groups": [{"text": "This is where SOMEONE lives,", "pos": "bottom", "show_start": 0.5, "show_end": 1.5, "words": []},
                                                               {"text": "to make money.", "pos": "bottom", "show_start": 1.6, "show_end": 2.4, "words": []}]}))
    (pdir / "placement.json").write_text(json.dumps({"avoid": [str(tmp_path / "graphics_title")]}))
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = [str(c) for c in cmd]
        out = Path(seen["cmd"][seen["cmd"].index("--out") + 1])
        (out / "captions.mov").write_bytes(b"mov")
        seen["fixes"] = json.loads(Path(seen["cmd"][seen["cmd"].index("--fixes") + 1]).read_text())
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(ge.subprocess, "run", fake_run)
    mov = ge._recaption(pdir, [{"at": 1.0, "text": "This is where HE lives,", "pos": "top"}, {"at": 2.0, "text": "to make money.", "pos": "top"}], tmp_path / "new", tmp_path / "cut.xml")
    assert mov == str((tmp_path / "new" / "captions.mov").resolve())
    fx = {f["at"]: f for f in seen["fixes"]}
    assert fx[1.0]["was"] == "There's a lot of ways", "a second edit of a line is tied to its ORIGINAL words, so it replaces the first fix rather than stacking on it"
    assert len([f for f in seen["fixes"] if 0.4 <= f["at"] <= 1.6]) == 1 and fx[1.0]["text"] == "This is where HE lives," and fx[1.0]["pos"] == "top"
    assert fx[2.0] == {"at": 2.0, "text": "to make money.", "was": "to make money.", "pos": "top"}, "a move with no change of words keeps the words"
    assert (tmp_path / "new" / "cut_1080.mp4").read_bytes() == b"proxy", "the transcript's proxy is reused, not rendered again"
    assert seen["cmd"].count("--avoid") == 1 and str(tmp_path / "graphics_title") in seen["cmd"] and "--end" in seen["cmd"]
    with pytest.raises(ge.GraphicsError, match="no caption line is showing"):
        ge._recaption(pdir, [{"at": 7.0, "text": "x"}], tmp_path / "n2", tmp_path / "cut.xml")
    monkeypatch.setattr(ge.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "HyperFrames check FAILED"))
    with pytest.raises(ge.GraphicsError, match="captions could not be redrawn"):
        ge._recaption(pdir, [{"at": 1.0, "text": "x"}], tmp_path / "n3", tmp_path / "cut.xml")


def test_a_redrawn_graphic_points_at_its_new_file_and_the_check_tolerates_exactly_that(cut, tmp_path):
    x, _ = cut
    new = _mov(tmp_path / "newdir" / "Graphic - Title.mov", (27, 96, 216, 96)) if (tmp_path / "newdir").mkdir() is None else None
    centers = {"overlay-clipitem-1": (135.0, 144.0)}
    edits = [{"id": "overlay-clipitem-1", "dx": 0.0, "dy": 0.0, "scale": 100, "new_mov": str(new)}]
    out = tmp_path / "o.xml"
    ge.apply(x, out, edits, centers, W, H)
    rows = ge.check(x, out, edits, centers, W, H)
    assert all(ok for _n, ok, _d in rows) and any(n == "GRAPHICS-NEW-FILE" for n, _o, _d in rows), rows
    assert str(new.resolve()).replace(" ", "%20") in out.read_text() or "newdir" in out.read_text()
    assert "Basic Motion" not in out.read_text(), "only the file changed: no motion was asked for"
    # a re-render of a different length would not fit where it was
    short = _mov(tmp_path / "newdir" / "short.mov", (27, 96, 216, 96), seconds=0.5)
    with pytest.raises(ge.GraphicsError, match="would not fit"):
        ge.apply(x, tmp_path / "bad.xml", [{"id": "overlay-clipitem-1", "dx": 0, "dy": 0, "scale": 100, "new_mov": str(short)}], centers, W, H)
