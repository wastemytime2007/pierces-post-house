"""Hermetic tests for replacing a sound effect from a note. ElevenLabs is faked; synthetic media only."""
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

import make_audio as ma  # noqa: E402
import replace_sfx as rs  # noqa: E402
import verify_audio as va  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
SR = 48000
SFX_AT = 6.0


def _mp3(path: Path, src: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-c:a", "libmp3lame", str(path)], check=True)


@pytest.fixture()
def old_folder(tmp_path):
    """A finished audio folder as make_audio.py leaves it: speech that talks in bursts, a music stem, a 0.8 s effect."""
    d = tmp_path / "audio_v1"
    (d / "generated").mkdir(parents=True)
    rng = np.random.default_rng(3)
    t = np.arange(12 * SR) / SR
    voice = np.convolve(rng.standard_normal(len(t)), np.ones(12) / 12, mode="same")          # band-limited like speech, so AAC keeps its level
    voice *= 0.05 / voice.std()
    sp = voice * (((t % 6) < 3).astype(float)) + 0.0005 * rng.standard_normal(len(t))
    import wave
    with wave.open(str(d / "speech_window.wav"), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.stack([sp, sp], 1) * 32767).astype("<i2").tobytes())
    _mp3(d / "generated" / "music.mp3", "sine=frequency=330:duration=14")
    _mp3(d / "generated" / "sfx.mp3", "sine=frequency=880:duration=0.8")
    ma.build_music_stem(d / "generated" / "music.mp3", d / "speech_window.wav", d / "music_stem.wav", 12.0, -5.0, 12.0)
    gain = ma.build_sfx_clip(d / "generated" / "sfx.mp3", d / "speech_window.wav", d / "sfx_clip.wav", 6.0)
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30", "-i", str(d / "speech_window.wav"),
                    "-t", "12", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)], check=True)
    clips = [{"kind": "music", "name": "music_stem.wav", "path": str(d / "music_stem.wav"), "start_sec": 0.0, "duration_sec": 12.0},
             {"kind": "sfx", "name": "sfx_clip.wav", "path": str(d / "sfx_clip.wav"), "start_sec": SFX_AT, "duration_sec": ma.probe_dur(d / "sfx_clip.wav")}]
    ma.mix_preview(video, d / "music_stem.wav", d / "sfx_clip.wav", d / "audio_preview.mp4", 0.0, 12.0, SFX_AT)
    (d / "audio.json").write_text(json.dumps({
        "window": {"start": 0.0, "end": 12.0}, "speech_window": "speech_window.wav", "music_db_rel_speech": -5.0, "duck_db": 12.0, "levels": {},
        "sfx_below_speech_peak_db": 6.0, "sfx_gain_db": gain, "callout_sec": SFX_AT,
        "generated": {"sfx": {"cached": False, "prompt": "a soft pop", "ms": 800}, "music": {"cached": False, "prompt": "warm", "ms": 13000}},
        "clips": clips, "speech_wav": str(d / "speech_window.wav"), "preview_video": str(video)}))
    return d


@pytest.fixture()
def fake_generation(monkeypatch):
    """A different, noisy sound for any new prompt. Records what it was asked for."""
    asked = []

    def fake(kind, prompt, seconds, cache):
        asked.append((kind, prompt, seconds))
        p = cache / f"{kind}_new.mp3"
        _mp3(p, "anoisesrc=d=0.8:c=pink:a=0.5")
        return p, {"cached": False, "prompt": prompt, "ms": int(seconds * 1000)}
    monkeypatch.setattr(ma, "generate", fake)
    return asked


NOTE = "I don't like the sound effect here, make it sound like something being highlighted on a piece of paper"


def _ops(note=1):
    return [{"note": note, "op": "replace_sfx", "sound": "something being highlighted on a piece of paper"}]


def test_the_effect_is_replaced_from_the_notes_words_and_nothing_else_changes(tmp_path, old_folder, fake_generation):
    out = tmp_path / "audio_v2"
    ledger, changed = rs.replace(old_folder, _ops(), [{"timeline_sec": SFX_AT + 0.3, "text": NOTE}], out)
    assert changed and ledger[0]["applied"]
    assert fake_generation == [("sfx", "something being highlighted on a piece of paper" + rs.PROMPT_TAIL, 0.8)]      # the note's words, the old effect's length
    rows = rs.extra_checks(old_folder, out)
    assert all(ok for _n, ok, _d in rows), rows
    meta = json.loads((out / "audio.json").read_text())
    assert meta["replaced"]["was"] == "a soft pop" and "highlighted on a piece of paper" in meta["replaced"]["now"]
    assert all(str(out) in c["path"] for c in meta["clips"])                                            # the new folder is self-contained
    assert {p.name for p in (old_folder / "generated").glob("*.mp3")} <= {p.name for p in (out / "generated").glob("*.mp3")}   # ...including its cache
    v = subprocess.run([sys.executable, str(HERE / "verify_audio.py"), str(out)], capture_output=True, text=True)
    assert v.returncode == 0, v.stdout


def test_the_old_folder_is_left_alone(tmp_path, old_folder, fake_generation):
    before = {p.name: p.read_bytes() for p in old_folder.glob("*.wav")}
    rs.replace(old_folder, _ops(), [{"timeline_sec": SFX_AT, "text": NOTE}], tmp_path / "v2")
    assert {p.name: p.read_bytes() for p in old_folder.glob("*.wav")} == before


def test_a_note_far_from_any_effect_is_reported_not_applied_and_nothing_is_written(tmp_path, old_folder, fake_generation):
    out = tmp_path / "v2"
    ledger, changed = rs.replace(old_folder, _ops(), [{"timeline_sec": 1.0, "text": NOTE}], out)
    assert not changed and not ledger[0]["applied"] and "no sound effect within" in ledger[0]["reason"]
    assert not out.exists() and fake_generation == []


def test_two_notes_about_one_effect_apply_the_first_and_say_so_about_the_second(tmp_path, old_folder, fake_generation):
    ledger, changed = rs.replace(old_folder, _ops(1) + _ops(2), [{"timeline_sec": SFX_AT, "text": NOTE}, {"timeline_sec": SFX_AT + 0.5, "text": NOTE}], tmp_path / "v2")
    assert changed and [e["applied"] for e in ledger] == [True, False] and "already changed" in ledger[1]["reason"]
    assert len(fake_generation) == 1


def test_other_operations_are_ignored_and_a_note_list_without_sound_notes_writes_nothing(tmp_path, old_folder, fake_generation):
    ledger, changed = rs.replace(old_folder, [{"note": 1, "op": "tighten_pause", "at": 3.0}], [{"timeline_sec": 3.0, "text": "tighten"}], tmp_path / "v2")
    assert ledger == [] and not changed


def test_the_nearest_effect_is_chosen_and_the_reach_is_one_second():
    clips = [{"kind": "music", "start_sec": 0, "duration_sec": 20, "name": "m"},
             {"kind": "sfx", "start_sec": 5.0, "duration_sec": 1.0, "name": "a"},
             {"kind": "sfx", "start_sec": 9.0, "duration_sec": 1.0, "name": "b"}]
    assert rs.sfx_for_note(clips, 5.4)["name"] == "a"
    assert rs.sfx_for_note(clips, 8.2)["name"] == "b"            # 1 s before b starts
    assert rs.sfx_for_note(clips, 7.0)["name"] == "a"            # exactly 1 s after a ends is still inside
    assert rs.sfx_for_note(clips, 7.2) is None
    assert rs.sfx_for_note(clips, 0.5) is None                   # music is never a target


def test_the_check_notices_when_the_new_effect_is_the_same_sound(tmp_path, old_folder, monkeypatch):
    def same(kind, prompt, seconds, cache):
        return old_folder / "generated" / "sfx.mp3", {"cached": True, "prompt": prompt, "ms": 800}
    monkeypatch.setattr(ma, "generate", same)
    out = tmp_path / "v2"
    rs.replace(old_folder, _ops(), [{"timeline_sec": SFX_AT, "text": NOTE}], out)
    rows = {n: ok for n, ok, _d in rs.extra_checks(old_folder, out)}
    assert rows["EFFECT-IS-A-DIFFERENT-SOUND"] is False


def test_a_folder_with_no_recorded_video_asks_for_one(tmp_path, old_folder, fake_generation):
    meta = json.loads((old_folder / "audio.json").read_text())
    del meta["preview_video"]
    (old_folder / "audio.json").write_text(json.dumps(meta))
    with pytest.raises(ma.AudioError, match="give --preview-video"):
        rs.replace(old_folder, _ops(), [{"timeline_sec": SFX_AT, "text": NOTE}], tmp_path / "v2")
