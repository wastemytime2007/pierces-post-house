import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reel_xml as rx


def test_the_graphics_layer_is_one_transparent_canvas_with_every_state_on_at_its_own_times_and_exactly_the_reels_length(tmp_path):
    layers = [{"png": "a.png", "t_on": 1.168, "t_off": 1.732}, {"png": "b.png", "t_on": 1.732, "t_off": 4.0}]
    cmd = [str(c) for c in rx.graphics_command(layers, 879, tmp_path / "g.mov")]
    joined = " ".join(cmd)
    assert "color=c=black@0.0:s=1080x1920" in joined and "format=rgba" in joined                      # a transparent canvas, vertical
    assert "between(t,1.1680,1.7320)" in joined and "between(t,1.7320,4.0000)" in joined               # each state on at its own times
    assert "-frames:v 879" in joined and "prores_ks" in joined and "yuva444p10le" in joined             # exactly the reel's frames, with alpha
    assert joined.count("-loop 1") == 2


def test_the_final_xml_check_catches_a_picture_clip_that_is_not_where_the_render_put_it(tmp_path):
    def clip(s, e, name="c", fid="f"):
        return f"<clipitem><name>{name}</name><start>{s}</start><end>{e}</end><in>0</in><out>{e - s}</out><file id='{fid}'><pathurl>file://localhost{tmp_path}/{fid}.mov</pathurl></file></clipitem>"
    for f in ("f1", "f2"):
        (tmp_path / f"{f}.mov").write_bytes(b"x")
    music = "".join(f"<track><clipitem><name>m.wav</name><start>0</start><end>60</end><in>0</in><out>60</out><file id='m'><pathurl>file://localhost{tmp_path}/f1.mov</pathurl></file></clipitem></track>" for _ in (1, 2))

    def xml(v1):
        return (f"<xmeml><sequence><rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate><media><video><format><samplecharacteristics><width>1080</width><height>1920</height></samplecharacteristics></format>"
                f"<track>{v1}</track><track><clipitem><name>g.mov</name><start>0</start><end>60</end></clipitem></track></video>"
                f"<audio><track>{v1}</track>{music}</audio></media></sequence></xmeml>")
    pieces = [{"start_frame": 0, "frames": 30}, {"start_frame": 30, "frames": 30}]
    good, bad = tmp_path / "good.xml", tmp_path / "bad.xml"
    good.write_text(xml(clip(0, 30, fid="f1") + clip(30, 60, fid="f2")))
    bad.write_text(xml(clip(0, 30, fid="f1") + clip(31, 61, fid="f2")))                               # the second clip is a frame late
    ok = {n: g for n, g, _ in rx.check_final(good, pieces, 60, "m.wav", "g.mov")}
    assert ok["VERTICAL-SEQUENCE"] and ok["PICTURE-PIECES"] and ok["NO-GAPS"] and ok["GRAPHICS-LAYER"] and ok["MUSIC-TRACKS"]
    nope = {n: g for n, g, _ in rx.check_final(bad, pieces, 60, "m.wav", "g.mov")}
    assert not nope["PICTURE-PIECES"] and not nope["NO-GAPS"]
