"""Hermetic tests: tiny ffmpeg-synthesized media, hand-written XML. No real footage."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import timeline  # noqa: E402
from render_preview import render_preview  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    vid, wav = d / "a.mp4", d / "lav.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "20",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(vid)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000",
                    "-t", "20", str(wav)], check=True)
    return vid, wav


def _xml(vid: Path, wav: Path, *, v_out=120, audio_enabled="TRUE", second_clip=True) -> str:
    # Sequence 60 NTSC. Source video is 30 fps native, and Premiere-style conform writes the
    # clipitem <rate> as the SEQUENCE's 60. in/out stay in the file's native 30 fps frames.
    seq_rate = "<rate><timebase>60</timebase><ntsc>TRUE</ntsc></rate>"
    second = ""
    if second_clip:
        # starts 40s after the first ends: past the 20s zone-gap rule
        second = f"""<clipitem id="c2"><name>a.mp4</name>{seq_rate}<start>2600</start><end>2780</end>
          <in>0</in><out>90</out><file id="f1"/></clipitem>"""
    return f"""<xmeml version="4"><sequence id="s"><name>Test Cut</name>{seq_rate}
<media><video><format><samplecharacteristics><width>1920</width><height>1080</height></samplecharacteristics></format>
<track>
<clipitem id="c1"><name>a.mp4</name>{seq_rate}<start>0</start><end>180</end><in>30</in><out>{v_out}</out>
 <file id="f1"><name>a.mp4</name><pathurl>file://localhost{vid}</pathurl>
 <rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate><duration>600</duration></file></clipitem>
{second}
</track></video>
<audio><track>
<clipitem id="a1"><name>lav.wav</name><enabled>TRUE</enabled>{seq_rate}<start>0</start><end>180</end><in>300</in><out>480</out>
 <file id="f2"><name>lav.wav</name><pathurl>file://localhost{wav}</pathurl>
 <rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate><duration>600</duration></file></clipitem>
</track>
<track>
<clipitem id="a2"><name>a.mp4</name><enabled>{audio_enabled}</enabled>{seq_rate}<start>0</start><end>180</end><in>30</in><out>210</out>
 <file id="f1"/></clipitem>
</track></audio></media></sequence></xmeml>"""


def _load(tmp_path, xml):
    p = tmp_path / "cut.xml"
    p.write_text(xml)
    return timeline.load_cut(p)


def test_video_in_out_use_the_source_file_rate_not_the_conformed_sequence_rate(tmp_path, media):
    cut = _load(tmp_path, _xml(*media))
    v = cut.video[0]
    assert v.src_in == pytest.approx(1.0, abs=0.01)   # 30 frames at the file's 30 fps
    assert v.src_out == pytest.approx(4.0, abs=0.01)  # not 0.5-2.0 (sequence rate)


def test_audio_in_out_use_sequence_frames_as_the_exporter_writes_them(tmp_path, media):
    cut = _load(tmp_path, _xml(*media))
    a = cut.audio[0]
    # 300 frames at 59.94 = 5.005s. Read at the WAV's declared 29.97 it would be 10.01s and
    # also fit the 20s file, so bounds alone cannot tell them apart. The exporter's
    # convention decides, and verify_preview.py's LAV-SYNC check proves it on real footage.
    assert a.src_in == pytest.approx(5.005, abs=0.01)


def test_zone_stops_at_the_gap_before_the_selects_pool(tmp_path, media):
    cut = _load(tmp_path, _xml(*media))
    assert len(cut.video) == 1
    assert cut.zone_end == pytest.approx(180 / 59.94, abs=0.01)


def test_disabled_audio_is_left_out(tmp_path, media):
    cut = _load(tmp_path, _xml(*media, audio_enabled="FALSE"))
    assert [a.name for a in cut.audio] == ["lav.wav"]


def test_impossible_range_is_refused_not_emitted(tmp_path, media):
    with pytest.raises(timeline.TimelineError, match="no frame-rate interpretation"):
        _load(tmp_path, _xml(*media, v_out=90000))


def test_missing_source_names_the_file(tmp_path, media):
    vid, wav = media
    with pytest.raises(timeline.TimelineError, match="source not found"):
        _load(tmp_path, _xml(tmp_path / "gone.mp4", wav))


def test_render_matches_cut_length_and_has_audio(tmp_path, media):
    cut = _load(tmp_path, _xml(*media, audio_enabled="FALSE"))
    out = tmp_path / "out" / "preview.mp4"
    out.parent.mkdir()
    info = render_preview(cut, out, height=180)
    assert info["duration"] == pytest.approx(cut.zone_end, abs=0.4)
    assert "synced audio: lav.wav" in info["audio_source"]
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                              str(out)], capture_output=True, text=True).stdout.split()
    assert "video" in streams and "audio" in streams


def _brightness(path: Path):
    """Mean brightness of every frame, in order."""
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", "scale=8:8,format=gray", "-vsync", "0", "-f", "rawvideo", "-"], capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, 64).astype(float).mean(axis=1)


def test_the_preview_is_frame_exact_at_the_sequence_rate_not_a_frame_late_and_not_missing_frames(tmp_path):
    """A 29.97 source where each frame is a flat grey set by its frame number (4 levels a frame), so a frame early or late is a clear step. The first renderer ran at 30 fps, duplicated each segment's first
    frame (every later frame one frame late) and lost frames in the join."""
    src = tmp_path / "s.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x180:r=30000/1001,geq=lum='mod(N*4\\,256)':cb=128:cr=128", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                    "-t", "20", "-c:v", "libx264", "-bf", "0", "-crf", "12", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)], check=True)
    rate = "<rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate>"
    xml = f"""<xmeml version="4"><sequence id="s"><name>T</name>{rate}<media><video><format><samplecharacteristics><width>320</width><height>180</height></samplecharacteristics></format><track>
<clipitem id="c1"><name>s.mp4</name>{rate}<start>0</start><end>95</end><in>20</in><out>115</out><file id="f1"><name>s.mp4</name><pathurl>file://localhost{src}</pathurl><rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate><duration>600</duration></file></clipitem>
<clipitem id="c2"><name>s.mp4</name>{rate}<start>95</start><end>171</end><in>300</in><out>376</out><file id="f1"/></clipitem>
</track></video><audio><track><clipitem id="a1"><name>s.mp4</name><enabled>TRUE</enabled>{rate}<start>0</start><end>171</end><in>20</in><out>191</out><file id="f1"/></clipitem></track></audio></media></sequence></xmeml>"""
    cut = _load(tmp_path, xml)
    assert abs(cut.fps - 30000 / 1001) < 0.001
    out = tmp_path / "o" / "preview.mp4"
    out.parent.mkdir()
    render_preview(cut, out, height=180)
    rate_out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate", "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
    assert rate_out == "30000/1001"                                          # not 30
    got, ref = _brightness(out), _brightness(src)
    assert len(got) == 171                                                   # 95 + 76 frames, exactly
    step = abs(ref[21] - ref[20])
    assert step > 3
    for k in range(95):                                                      # frame k of the first clip is source frame 20+k: not the same frame twice, not a frame late
        assert abs(got[k] - ref[20 + k]) < step / 2, (k, got[k], ref[20 + k])
    for k in range(76):                                                      # and of the second clip is source frame 300+k, with nothing lost at the join
        assert abs(got[95 + k] - ref[300 + k]) < step / 2, (k, got[95 + k], ref[300 + k])


def test_the_loader_reads_premieres_own_basic_motion_export():
    fixture = Path(__file__).resolve().parents[3] / "safety_net/fixtures/premiere_motion/carpet_sub_01_scale118_center_0.257812.xml"
    import xml.etree.ElementTree as ET
    text = fixture.read_text()
    ci = ET.fromstring(text[text.index("<xmeml"):]).find(".//clipitem")
    assert timeline.motion_of(ci) == (118.0, pytest.approx(0.257812), 0.0)
    assert timeline.motion_of(ET.fromstring("<clipitem><name>x</name></clipitem>")) is None


def test_a_cut_with_basic_motion_is_previewed_as_the_sequence_frames_it(tmp_path):
    """A 640x360 source, black on the left half and white on the right, in a 180x320 vertical sequence. A clip whose motion puts source pixel 500 mid-frame must preview white, one that puts pixel 100 there black,
    and the preview must be the sequence's shape (180x320), not the source's."""
    src = tmp_path / "split.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=640x360:r=30000/1001,geq=lum='if(gte(X,320),255,0)':cb=128:cr=128", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                    "-t", "10", "-c:v", "libx264", "-bf", "0", "-crf", "12", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)], check=True)
    rate = "<rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate>"
    scale = 88.89

    def motion(x_s):
        h = (640 / 2 - x_s) * (scale / 100) / 640
        return (f"<filter><effect><name>Basic Motion</name><parameter><parameterid>scale</parameterid><value>{scale}</value></parameter>"
                f"<parameter><parameterid>center</parameterid><value><horiz>{h:.6f}</horiz><vert>0</vert></value></parameter></effect></filter>")
    xml = f"""<xmeml version="4"><sequence id="s"><name>T</name>{rate}<media><video><format><samplecharacteristics><width>180</width><height>320</height></samplecharacteristics></format><track>
<clipitem id="c1"><name>split.mp4</name>{rate}<start>0</start><end>60</end><in>30</in><out>90</out><file id="f1"><name>split.mp4</name><pathurl>file://localhost{src}</pathurl><rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate><duration>300</duration></file>{motion(500)}</clipitem>
<clipitem id="c2"><name>split.mp4</name>{rate}<start>60</start><end>120</end><in>30</in><out>90</out><file id="f1"/>{motion(100)}</clipitem>
</track></video><audio><track><clipitem id="a1"><name>split.mp4</name><enabled>TRUE</enabled>{rate}<start>0</start><end>120</end><in>30</in><out>150</out><file id="f1"/></clipitem></track></audio></media></sequence></xmeml>"""
    cut = _load(tmp_path, xml)
    assert [c.motion is not None for c in cut.video] == [True, True]
    out = tmp_path / "o" / "preview.mp4"
    out.parent.mkdir()
    info = render_preview(cut, out, height=320)
    assert info["framed_by_motion"] is True
    size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
    assert size == "180,320"
    b = _brightness(out)
    assert len(b) == 120 and b[:60].mean() > 230 and b[60:].mean() < 25                    # first clip: the white half; second clip: the black half


def test_no_enabled_audio_falls_back_to_camera_audio_and_says_so(tmp_path, media):
    xml = _xml(*media).replace("<enabled>TRUE</enabled>", "<enabled>FALSE</enabled>")
    cut = _load(tmp_path, xml)
    assert cut.audio == []
    out = tmp_path / "out2" / "preview.mp4"
    out.parent.mkdir()
    info = render_preview(cut, out, height=180)
    assert "camera audio" in info["audio_source"]
