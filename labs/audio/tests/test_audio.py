"""Hermetic tests for generated audio: levels, ducking, caching, refusals and XML placement.
No network (ElevenLabs is faked) and synthetic media only."""
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "review_loop"))
sys.path.insert(0, str(HERE.parent / "overlay"))
sys.path.insert(0, str(HERE.parents[1] / "safety_net"))

import make_audio as ma  # noqa: E402
import place_audio as pa  # noqa: E402
import timeline  # noqa: E402
import verify_audio as va  # noqa: E402

_spec = importlib.util.spec_from_file_location("rl_tests", HERE.parent / "review_loop" / "tests" / "test_revise.py")
rl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rl)

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
SR = 48000


def _write_wav(path: Path, x: np.ndarray, stereo: bool = False) -> None:
    import wave
    x = np.stack([x, x], axis=1) if stereo else x[:, None]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(x.shape[1]); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


@pytest.fixture(scope="module")
def stems(tmp_path_factory):
    """Speech that talks 0-4s, 8-12s, 16-20s and is quiet between; a steady tone standing in for music."""
    d = tmp_path_factory.mktemp("audio")
    t = np.arange(20 * SR) / SR
    rng = np.random.default_rng(1)
    talk = (((t % 8) < 4)).astype(float)
    speech = 0.05 * rng.standard_normal(len(t)) * talk + 0.0005 * rng.standard_normal(len(t))
    _write_wav(d / "speech.wav", speech)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=23", "-c:a", "libmp3lame", str(d / "music.mp3")], check=True)
    return d


def test_speech_activity_finds_the_talking_frames_and_their_level(stems):
    sp = va.pcm(stems / "speech.wav")
    act, lvl = ma.speech_activity(sp, int(0.01 * SR))
    frac = act.mean()
    assert 0.55 < frac < 0.65                                            # talks 12 of the 20 seconds
    assert lvl == pytest.approx(20 * np.log10(0.05), abs=1.0)


def test_music_sits_where_asked_and_ducks_by_the_amount_asked(stems):
    out = stems / "stem.wav"
    lv = ma.build_music_stem(stems / "music.mp3", stems / "speech.wav", out, 20.0, -8.0, 10.0)
    m, s = va.pcm(out), va.pcm(stems / "speech.wav")
    assert len(m) == pytest.approx(20 * SR, abs=2)
    mdb = va.rms_db(m)
    pause = mdb[int(4.6 * 10):int(7.6 * 10)].mean()                      # settled in the first 4s pause
    talk = mdb[int(1.0 * 10):int(3.6 * 10)].mean()
    assert lv["music_gap_level_db"] == pytest.approx(lv["speech_level_db"] - 8.0, abs=0.01)
    assert pause == pytest.approx(lv["speech_level_db"] - 8.0, abs=1.5)
    assert pause - talk == pytest.approx(10.0, abs=1.5)
    assert mdb[:1].mean() < mdb[int(1.0 * 10):int(3.6 * 10)].mean() + 5 and mdb[0] < pause - 10       # fade in
    assert mdb[-1] < pause - 10                                                                          # fade out
    assert float(np.abs(m).max()) < 0.95


def test_music_shorter_than_the_window_is_refused(stems):
    with pytest.raises(ma.AudioError, match="shorter than the"):
        ma.build_music_stem(stems / "music.mp3", stems / "speech.wav", stems / "x.wav", 60.0, -8.0, 10.0)


def test_effect_peak_is_set_below_the_speech_peak(stems):
    out = stems / "sfx.wav"
    ma.build_sfx_clip(stems / "music.mp3", stems / "speech.wav", out, 6.0)
    sp, sf = va.pcm(stems / "speech.wav"), va.pcm(out)
    assert 20 * np.log10(np.abs(sp).max()) - 20 * np.log10(np.abs(sf).max()) == pytest.approx(6.0, abs=0.5)


class _Resp:
    def __init__(self, b): self._b = b
    def read(self): return self._b


def test_generation_is_cached_by_prompt_and_never_leaks_the_key(tmp_path, monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append((req.full_url, json.loads(req.data), req.headers))
        return _Resp(b"ID3" + b"x" * 2000)
    monkeypatch.setattr(ma.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(ma, "load_key", lambda: "SECRET-KEY-123")
    p1, i1 = ma.generate("sfx", "a pop", 1.2, tmp_path)
    p2, i2 = ma.generate("sfx", "a pop", 1.2, tmp_path)
    p3, _ = ma.generate("sfx", "a different pop", 1.2, tmp_path)
    assert len(calls) == 2 and i1["cached"] is False and i2["cached"] is True and p1 == p2 != p3
    assert calls[0][0].endswith("/sound-generation?output_format=mp3_44100_128") and calls[0][1] == {"text": "a pop", "duration_seconds": 1.2}
    assert "SECRET-KEY-123" not in json.dumps(i1) and "SECRET-KEY-123" not in p1.read_bytes().decode("latin1")
    ma.generate("music", "warm", 23.0, tmp_path)
    assert calls[2][0].endswith("/music?output_format=mp3_44100_128") and calls[2][1] == {"prompt": "warm", "music_length_ms": 23000}


def test_a_refused_request_says_why_without_the_key(tmp_path, monkeypatch):
    def boom(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 401, "x", {}, io.BytesIO(b'{"detail":{"status":"missing_permissions"}}'))
    monkeypatch.setattr(ma.urllib.request, "urlopen", boom)
    monkeypatch.setattr(ma, "load_key", lambda: "SECRET-KEY-123")
    with pytest.raises(ma.AudioError, match="missing_permissions") as e:
        ma.generate("music", "p", 5.0, tmp_path)
    assert "SECRET-KEY-123" not in str(e.value)


def test_a_missing_or_empty_key_file_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(ma, "KEYFILE", tmp_path / "nope.env")
    with pytest.raises(ma.AudioError, match="no ElevenLabs key"):
        ma.load_key()
    (tmp_path / "e.env").write_text("ELEVENLABS_API_KEY=\n")
    monkeypatch.setattr(ma, "KEYFILE", tmp_path / "e.env")
    with pytest.raises(ma.AudioError, match="no ELEVENLABS_API_KEY"):
        ma.load_key()


@pytest.fixture(scope="module")
def cut_media(tmp_path_factory):
    d = tmp_path_factory.mktemp("place_audio")
    vid, lav = d / "a.mp4", d / "lav.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=d=60:c=white:r=48000:a=0.5", str(lav)], check=True)
    cam = d / "cam.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "5.005", "-i", str(lav), str(cam)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30", "-i", str(cam), "-t", "55",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(vid)], check=True)
    return d, vid, lav


def _folder(tmp_path, sr=48000, ch=2, music_at=0.0, sfx_at=3.0):
    f = tmp_path / "au"
    f.mkdir()
    for name, secs in (("music_stem.wav", 5.0), ("sfx_clip.wav", 1.0)):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"anoisesrc=d={secs}:r={sr}:a=0.1", "-ac", str(ch), "-c:a", "pcm_s16le", str(f / name)], check=True)
    (f / "placement.json").write_text(json.dumps({"kind": "audio", "clips": [
        {"kind": "music", "name": "music_stem.wav", "path": str(f / "music_stem.wav"), "start_sec": music_at, "duration_sec": 5.0},
        {"kind": "sfx", "name": "sfx_clip.wav", "path": str(f / "sfx_clip.wav"), "start_sec": sfx_at, "duration_sec": 1.0}]}))
    return f


def _xml(tmp_path, cut_media):
    _d, vid, lav = cut_media
    p = rl.make_xml(tmp_path / "cut.xml", vid, lav)
    p.write_text(p.read_text().replace("<audio><track>", "<audio><format><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics></format><track>", 1))
    return p


def test_music_and_effect_go_on_new_stereo_track_pairs_at_their_times(tmp_path, cut_media):
    xml = _xml(tmp_path, cut_media)
    out = tmp_path / "placed.xml"
    info = pa.place(xml, out, _folder(tmp_path))
    rows = pa.verify_placed(xml, out, info)
    assert not [(n, d) for n, ok, d in rows if ok is False], rows
    seq = timeline._seq_for_cut(ET.parse(out).getroot())
    tracks = seq.findall("media/audio/track")
    old = len(timeline._seq_for_cut(ET.parse(xml).getroot()).findall("media/audio/track"))
    new = tracks[old:]
    assert len(new) == 4
    starts = [int(t.find("clipitem").findtext("start")) for t in new]
    fps = info["fps"]
    assert starts[0] == starts[1] == 0 and starts[2] == starts[3] == round(3.0 * fps)
    assert [t.find("clipitem/sourcetrack").findtext("trackindex") for t in new] == ["1", "2", "1", "2"]
    assert new[1].find("clipitem/file").get("id") == new[0].find("clipitem/file").get("id") and len(list(new[1].find("clipitem/file"))) == 0   # right channel refers to the same file
    assert out.read_text().startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>')


def test_the_speech_tracks_are_left_exactly_as_they_were(tmp_path, cut_media):
    xml = _xml(tmp_path, cut_media)
    out = tmp_path / "placed.xml"
    pa.place(xml, out, _folder(tmp_path))
    a = timeline._seq_for_cut(ET.parse(xml).getroot()).findall("media/audio/track")
    b = timeline._seq_for_cut(ET.parse(out).getroot()).findall("media/audio/track")[:len(a)]
    assert pa.po._ser(a) == pa.po._ser(b)


def test_refuses_a_file_at_the_wrong_sample_rate_or_channel_count(tmp_path, cut_media):
    xml = _xml(tmp_path, cut_media)
    with pytest.raises(pa.PlaceError, match="44100 Hz"):
        pa.place(xml, tmp_path / "o.xml", _folder(tmp_path, sr=44100))


def test_refuses_a_clip_that_would_run_past_the_end_of_the_cut(tmp_path, cut_media):
    xml = _xml(tmp_path, cut_media)
    with pytest.raises(pa.PlaceError, match="outside the cut"):
        pa.place(xml, tmp_path / "o.xml", _folder(tmp_path, sfx_at=999.0))


def test_refuses_when_the_sequence_declares_no_audio_rate(tmp_path, cut_media):
    _d, vid, lav = cut_media
    xml = rl.make_xml(tmp_path / "cut.xml", vid, lav)                    # the bare synthetic cut has no audio format
    with pytest.raises(pa.PlaceError, match="no audio sample rate"):
        pa.place(xml, tmp_path / "o.xml", _folder(tmp_path))
