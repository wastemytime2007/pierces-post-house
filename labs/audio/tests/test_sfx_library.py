import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import make_audio as ma
import sfx_library as sl


def _tone(path: Path, seconds: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", str(path)], check=True)
    return path


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "lib"
    for n in ("Essential Doors - Doorknob, Opening_.wav", "Essential Doors - Doorknob, Opening_ (1).wav", "Camera, shutter button, shoot.wav", "Explainer Video - Censorship Tone Beep.wav"):
        _tone(root / n, 0.3)
    (root / "notes.txt").write_text("not audio")
    (root / ".hidden.wav").write_bytes(b"x")
    return root


def test_the_index_lists_each_sound_once_by_its_description_and_skips_non_audio_hidden_and_missing_folders(lib, tmp_path):
    items = sl.index([lib, tmp_path / "does_not_exist"])
    assert sorted(i["description"] for i in items) == ["Camera, shutter button, shoot", "Essential Doors - Doorknob, Opening", "Explainer Video - Censorship Tone Beep"]
    assert sl.describe(Path("Pests - Rodent Vocalization, Squeaking_ (1).wav")) == "Pests - Rodent Vocalization, Squeaking"


def test_the_checks_answer_is_a_library_file_or_none_and_anything_else_is_refused(lib):
    items = sl.index([lib])
    got = sl.parse('Sure: {"match": "Camera, shutter button, shoot.wav", "why": "a camera shutter"}', items)
    assert got["item"]["name"] == "Camera, shutter button, shoot.wav"
    assert sl.parse('{"match": null, "why": "nothing like it"}', items)["item"] is None
    for bad in ('{"match": "Invented - Whoosh.wav"}', "I could not decide", "{not json}"):
        with pytest.raises(sl.LibraryError):
            sl.parse(bad, items)


def test_find_asks_once_then_answers_from_the_cache_until_the_library_changes_and_an_empty_library_is_never_asked(lib, tmp_path):
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return '{"match": "Camera, shutter button, shoot.wav", "why": "shutter"}'
    cache = tmp_path / "c.json"
    a = sl.find("a camera shutter click", [lib], ask, cache)
    b = sl.find("a camera shutter click", [lib], ask, cache)
    assert a["item"]["name"] == b["item"]["name"] and len(calls) == 1 and b["cached"] is True
    assert "THE SOUND NEEDED" in calls[0] and "Doorknob" in calls[0] and "SAME KIND" in calls[0]
    _tone(lib / "New - Whoosh.wav", 0.3)
    sl.find("a camera shutter click", [lib], ask, cache)
    assert len(calls) == 2                                                     # a new file in the library is a new question
    empty = sl.find("anything", [tmp_path / "nowhere"], lambda p: (_ for _ in ()).throw(AssertionError("must not ask")), None)
    assert empty["item"] is None and empty["checked"] == 0


def test_a_library_hit_is_used_and_nothing_is_generated(lib, tmp_path, monkeypatch):
    monkeypatch.setattr(ma, "generate", lambda *a, **k: (_ for _ in ()).throw(AssertionError("generated although the library had it")))
    ask = lambda p: '{"match": "Essential Doors - Doorknob, Opening_.wav", "why": "a doorknob opening"}'
    path, info = ma.get_sfx("a doorknob turning and a door opening", 1.2, tmp_path / "cache", [lib], ask)
    assert path.exists() and info["source"] == "library" and info["library_file"] == "Essential Doors - Doorknob, Opening_.wav"
    assert info["prompt"] == "a doorknob turning and a door opening" and info["cached"] is True and info["library_checked"] == 3


def test_with_no_fitting_sound_one_is_generated_and_kept_so_the_next_request_finds_it(lib, tmp_path, monkeypatch):
    made = []

    def fake_generate(kind, prompt, seconds, cache, salt=""):
        made.append((kind, prompt))
        out = _tone(cache / "sfx_fake.mp3", 0.5)
        return out, {"cached": False, "prompt": prompt, "ms": 5, "salt": salt, "file": out.name}
    monkeypatch.setattr(ma, "generate", fake_generate)
    root = tmp_path / "lib2"
    root.mkdir()
    for f in lib.glob("*.wav"):
        (root / f.name).write_bytes(f.read_bytes())
    ask = lambda p: '{"match": null, "why": "no whoosh in the library"}'
    path, info = ma.get_sfx("a fast whoosh", 1.2, tmp_path / "cache", [root, sl.generated_store()], ask)
    assert made == [("sfx", "a fast whoosh")] and info["source"] == "elevenlabs" and info["library_checked"] == 3
    kept = sl.generated_store() / info["kept_in_library_as"]
    assert kept.exists() and kept.name.startswith("Generated - a fast whoosh")
    # the same sound asked for again is now a library hit, so no second generation
    again = lambda p: json.dumps({"match": kept.name, "why": "generated earlier"})
    made.clear()
    path2, info2 = ma.get_sfx("a fast whoosh", 1.2, tmp_path / "cache2", [root, sl.generated_store()], again)
    assert made == [] and info2["source"] == "library" and info2["library_file"] == kept.name


def test_if_the_library_cannot_be_checked_nothing_is_generated_and_the_run_refuses(lib, tmp_path, monkeypatch):
    monkeypatch.setattr(ma, "generate", lambda *a, **k: (_ for _ in ()).throw(AssertionError("generated although the check failed")))

    def broken(prompt):
        raise sl.LibraryError("claude CLI exited 1: not logged in")
    with pytest.raises(ma.AudioError, match="could not be checked"):
        ma.get_sfx("a whoosh", 1.2, tmp_path / "cache", [lib], broken)
    with pytest.raises(ma.AudioError):
        ma.get_sfx("a whoosh", 1.2, tmp_path / "cache", [lib], lambda p: '{"match": "Invented.wav"}')     # a made-up file name is a refusal too, not a "no"


def test_a_long_library_sound_is_cut_to_four_seconds_with_a_fade_and_a_short_one_is_left_alone(tmp_path):
    long_ = _tone(tmp_path / "long.wav", 7.0)
    short = _tone(tmp_path / "short.wav", 1.0)
    assert sl.shorten(short, tmp_path / "o1.wav") == short
    out = sl.shorten(long_, tmp_path / "o2.wav")
    d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip())
    assert out != long_ and 3.9 <= d <= 4.1
