"""Hermetic tests for the image card: layout, timing, and the verifier, including cards that are deliberately wrong.
No HyperFrames: a synthetic card is drawn with numpy and encoded to ProRes 4444."""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "review_loop"))
sys.path.insert(0, str(HERE.parents[1] / "safety_net"))

import make_image_card as mc  # noqa: E402

_spec = importlib.util.spec_from_file_location("rl_tests", HERE.parent / "review_loop" / "tests" / "test_revise.py")
rl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rl)

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
RW, RH, FPS = 960, 540, 30            # render size for the synthetic cards: half of the 1920x1080 layout
S = RW / 1920
HL = {"x0": 0.2, "y0": 0.3, "x1": 0.6, "y1": 0.7}


def test_the_whole_image_is_fitted_inside_the_limits_and_never_cropped():
    wide = mc.layout(3000, 1000, None, None)
    assert wide["image"]["w"] <= mc.MAX_W and wide["image"]["h"] <= mc.MAX_H and wide["image"]["w"] / wide["image"]["h"] == pytest.approx(3.0, abs=0.01)
    tall = mc.layout(600, 2400, None, None)
    assert tall["image"]["h"] <= mc.MAX_H and tall["image"]["w"] / tall["image"]["h"] == pytest.approx(0.25, abs=0.01)
    small = mc.layout(200, 120, None, None)
    assert small["scale"] == 1.5                                                  # a small image is not blown up past 1.5x


def test_the_border_surrounds_the_image_and_the_card_stays_inside_the_frame_in_every_position():
    for pos in ("left", "center", "right"):
        c = mc.layout(1600, 900, HL, "Caption", pos)
        f, im = c["frame"], c["image"]
        assert (im["x"] - f["x"], im["y"] - f["y"]) == (mc.BORDER, mc.BORDER) and f["w"] == im["w"] + 2 * mc.BORDER
        bottom = c["caption"]["y"] + c["caption"]["h"]
        assert f["x"] >= 60 and f["x"] + f["w"] <= 1920 - 60 and f["y"] >= 40 and bottom <= 1080 - 40


def test_a_highlight_is_mapped_from_image_fractions_to_the_frame_and_must_sit_inside_the_image():
    c = mc.layout(1000, 500, HL, None)
    im, hl = c["image"], c["highlight"]
    assert hl["x"] == pytest.approx(im["x"] + 0.2 * im["w"], abs=1) and hl["w"] == pytest.approx(0.4 * im["w"], abs=1)
    assert hl["y"] == pytest.approx(im["y"] + 0.3 * im["h"], abs=1) and hl["h"] == pytest.approx(0.4 * im["h"], abs=1)
    assert mc.parse_box("0.1,0.2,0.5,0.6") == {"x0": 0.1, "y0": 0.2, "x1": 0.5, "y1": 0.6}
    for bad in ("0.5,0.2,0.4,0.6", "0.1,0.2,1.5,0.6", "a,b,c,d", "0.1,0.2,0.5"):
        with pytest.raises(mc.CardError):
            mc.parse_box(bad)


def test_a_tiny_image_and_an_unknown_position_are_refused():
    with pytest.raises(mc.CardError, match="too small"):
        mc.layout(30, 30, None, None)
    with pytest.raises(mc.CardError, match="position must be"):
        mc.layout(800, 600, None, None, "middle")


TIMELINE = {"clips": [{"idx": 1, "start": 0.0, "end": 10.0, "source": "A.MP4", "src_in": 100.0, "src_out": 110.0, "source_path": "/x/A.MP4"}]}


def test_the_card_enters_before_its_frame_and_the_hold_shrinks_at_the_end_of_a_shot():
    t = mc.timing("A.MP4", 104.0, TIMELINE, lead=0.5, hold=3.5)
    assert t["start"] == pytest.approx(3.5) and t["hold"] == 3.5 and t["total"] == pytest.approx(0.5 + 3.5 + 0.7)
    late = mc.timing("A.MP4", 107.0, TIMELINE, lead=0.5, hold=3.5)             # 3s of shot left after the frame
    assert late["start"] + late["total"] <= 10.0 + 1e-6 and late["hold"] < 3.5
    early = mc.timing("A.MP4", 100.2, TIMELINE, lead=0.5, hold=3.5)
    assert early["start"] == 0.0 and early["lead"] == pytest.approx(0.2)


def test_timing_refuses_a_frame_not_in_the_cut_and_a_shot_with_no_room():
    with pytest.raises(Exception, match="not in this cut"):
        mc.timing("A.MP4", 50.0, TIMELINE, 0.5, 3.5)
    with pytest.raises(mc.CardError, match="not enough of this shot"):
        mc.timing("A.MP4", 109.5, TIMELINE, 0.5, 3.5)


# ---------- a synthetic card, drawn the way the real one should look ----------
def _gradient(w, h):
    u, v = np.meshgrid(np.linspace(0, 1, w), np.linspace(0, 1, h))
    return np.stack([u * 255, v * 255, (1 - u) * 160 + 40], axis=2).astype(np.uint8)


def _write_png(path: Path, arr: np.ndarray) -> None:
    h, w, _ = arr.shape
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-i", "-", "-frames:v", "1", str(path)], input=arr.tobytes(), check=True)


def _card_frame(cfg, *, border=True, shift=0, filled=False, caption=True) -> np.ndarray:
    a = np.zeros((RH, RW, 4), dtype=np.uint8)
    S_ = lambda v: int(round(v * S))                                    # noqa: E731
    f, im, hl, cap = cfg["frame"], cfg["image"], cfg["highlight"], cfg["caption"]
    if border:
        a[S_(f["y"]):S_(f["y"] + f["h"]), S_(f["x"]):S_(f["x"] + f["w"])] = (3, 145, 216, 255)
    iw, ih = S_(im["w"]), S_(im["h"])
    img = _gradient(iw, ih)
    if shift:
        img = np.roll(img, shift, axis=1)                               # the picture slides: its left edge no longer shows the source's left edge
    a[S_(im["y"]):S_(im["y"]) + ih, S_(im["x"]):S_(im["x"]) + iw, :3] = img
    a[S_(im["y"]):S_(im["y"]) + ih, S_(im["x"]):S_(im["x"]) + iw, 3] = 255
    if hl:
        x, y, w, h, r = S_(hl["x"]), S_(hl["y"]), S_(hl["w"]), S_(hl["h"]), S_(6)
        if filled:
            a[y:y + h, x:x + w] = (244, 105, 11, 255)
        else:
            for sl in (a[y:y + r, x:x + w], a[y + h - r:y + h, x:x + w], a[y:y + h, x:x + r], a[y:y + h, x + w - r:x + w]):
                sl[:] = (244, 105, 11, 255)
    if cap:
        x, y, w, h = S_(cap["x"]), S_(cap["y"]), S_(cap["w"]), S_(cap["h"])
        a[y:y + h, x:x + w] = (3, 52, 89, 247)
        a[y + S_(20):y + S_(60), x + S_(60):x + S_(260)] = (255, 255, 255, 255)           # stand-in for text
    return a


def _make_folder(tmp_path, *, border=True, shift=0, filled=False, caption=True, tamper=False):
    d = tmp_path / "card"
    d.mkdir()
    src = d / "source.png"
    _write_png(src, _gradient(600, 360))
    cfg = mc.layout(600, 360, HL, "The spacer" if caption else None, "center")
    t_in, hold, fade, tail = 0.4, 1.5, 0.35, 0.35
    total = round(t_in + hold + fade + tail, 3)
    cfg.update({"t_in": t_in, "t_out": round(t_in + hold, 3), "fade_out": fade, "total": total})
    full = _card_frame(cfg, border=border, shift=shift, filled=filled, caption=caption)
    empty = np.zeros_like(full)
    n = int(round(total * FPS))
    raw = b"".join((full if 0.5 * FPS <= i < (t_in + hold) * FPS else empty).tobytes() for i in range(n))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{RW}x{RH}", "-r", str(FPS), "-i", "-", "-c:v", "prores_ks", "-profile:v", "4444",
                    "-pix_fmt", "yuva444p10le", str(d / "card.mov")], input=raw, check=True)
    import hashlib
    sha = hashlib.sha1(src.read_bytes()).hexdigest()
    if tamper:
        src.write_bytes(src.read_bytes() + b"x")
    (d / "placement.json").write_text(json.dumps({"image": str(src), "image_sha1": sha, "duration_sec": total, "render": {"width": RW, "height": RH, "fps": float(FPS)},
                                                  "geometry": dict(cfg, border=mc.BORDER)}))
    return d


def _verify(d):
    p = subprocess.run([sys.executable, str(HERE / "verify_image_card.py"), str(d)], capture_output=True, text=True)
    rows = {}
    for line in p.stdout.splitlines():
        if line.startswith("  ["):
            rows[line[8:].split()[0]] = line[3:7]
    return p.returncode, rows, p.stdout


def test_a_correct_card_passes_every_check(tmp_path):
    code, rows, out = _verify(_make_folder(tmp_path))
    assert code == 0 and set(rows.values()) == {"PASS"} and {"BORDER", "IMAGE-PIXELS", "NOT-CROPPED", "HIGHLIGHT", "CAPTION"} <= set(rows), out


def test_a_card_with_no_border_is_rejected(tmp_path):
    code, rows, _o = _verify(_make_folder(tmp_path, border=False))
    assert code == 1 and rows["BORDER"] == "FAIL" and rows["CARD-EDGES"] == "FAIL"


def test_a_card_whose_picture_is_shifted_or_cropped_is_rejected(tmp_path):
    code, rows, _o = _verify(_make_folder(tmp_path, shift=40))
    assert code == 1 and rows["NOT-CROPPED"] == "FAIL" and rows["IMAGE-PIXELS"] == "FAIL"


def test_a_filled_highlight_that_hides_the_picture_is_rejected(tmp_path):
    code, rows, _o = _verify(_make_folder(tmp_path, filled=True))
    assert code == 1 and rows["HIGHLIGHT"] == "FAIL"


def test_a_missing_caption_and_an_altered_source_are_rejected(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    code, rows, _o = _verify(_make_folder(tmp_path / "a", caption=True))
    assert code == 0
    # the placement promises a caption, but the render has none
    d = _make_folder(tmp_path / "b", caption=False)
    pl = json.loads((d / "placement.json").read_text())
    pl["geometry"]["caption"] = mc.layout(600, 360, HL, "The spacer", "center")["caption"]
    (d / "placement.json").write_text(json.dumps(pl))
    code, rows, _o = _verify(d)
    assert code == 1 and rows["CAPTION"] == "FAIL"
    (tmp_path / "c").mkdir()
    code, rows, _o = _verify(_make_folder(tmp_path / "c", tamper=True))
    assert code == 1 and rows["SOURCE-UNCHANGED"] == "FAIL"


def test_build_card_writes_a_placement_the_captions_and_reconform_can_read(tmp_path):
    vid, lav = tmp_path / "a.mp4", tmp_path / "lav.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=d=60:c=white:r=48000:a=0.3", "-af", "lowpass=f=3000", str(lav)], check=True)
    cam = tmp_path / "cam.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "5.005", "-i", str(lav), str(cam)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "smptebars=size=320x180:rate=30", "-i", str(cam), "-t", "55", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(vid)], check=True)
    xml = rl.make_xml(tmp_path / "cut.xml", vid, lav)
    img = tmp_path / "shot.png"
    _write_png(img, _gradient(600, 360))
    spec = {"width": RW, "height": RH, "fps": float(FPS), "fps_arg": str(FPS), "resolution": None}

    def fake_render(proj, mov, spec_, cfg):
        assert (proj / "index.html").exists() and "data:image/png;base64," in (proj / "index.html").read_text()
        full = _card_frame(cfg)
        n = int(round(cfg["total"] * FPS))
        raw = b"".join((full if 0.5 * FPS <= i < cfg["t_out"] * FPS else np.zeros_like(full)).tobytes() for i in range(n))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{RW}x{RH}", "-r", str(FPS), "-i", "-", "-c:v", "prores_ks", "-profile:v", "4444",
                        "-pix_fmt", "yuva444p10le", str(mov)], input=raw, check=True)
    code, p = mc.build_card(img, tmp_path / "out", xml, "a.mp4", 22.0, highlight=HL, caption="The spacer", render=fake_render, spec_of=lambda _x: spec)
    assert code == 0 and p["kind"] == "image_card" and p["anchor"]["source"] == "a.mp4" and p["anchor"]["source_sec"] == 22.0
    g = p["geometry"]
    assert {"box", "label", "arrow", "t_in", "t_out", "fade_out"} <= set(g) and g["box"]["w"] == g["frame"]["w"]          # what captions and reconform read
    assert p["image_sha1"] and p["overlay"] == "card.mov" and (tmp_path / "out" / "placement.json").exists()
