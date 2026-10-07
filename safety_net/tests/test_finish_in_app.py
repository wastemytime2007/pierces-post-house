"""Finishing a cut and keeping its layers through a revision, as the app drives them. finish_cut.run is faked (labs/review_loop/tests/test_finish_cut.py tests the chain); what is tested here is what the app
hands it and what it does with the answer: which options it passes, that a failure never costs the revised picture, and that a finished version comes back shaped like a revision."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "app" / "python_backend"))
import creator_tools as ct  # noqa: E402

ct.use_labs()
import finish_cut as fc  # noqa: E402


@pytest.fixture()
def layered(tmp_path, monkeypatch):
    """A version folder that was finished once: finish.json, the captions folder with one correction, the generated music, and an XML the fake says carries every layer."""
    src_dir = tmp_path / "proj_v2 - revised"
    (src_dir / "captions").mkdir(parents=True)
    music = src_dir / "audio_in" / "music_matched.wav"
    music.parent.mkdir()
    music.write_bytes(b"x")
    (src_dir / "captions" / "captions.json").write_text(json.dumps({"fixes": [{"at": 3.0, "text": "Fixed", "was": "Fxed", "note": 1}], "groups": [], "window": {"start": 0.0, "end": 10.0}}))
    (src_dir / "finish.json").write_text(json.dumps({"folder": str(src_dir), "music_raw": str(music), "steps": []}))
    src = src_dir / "proj_v2.xml"
    src.write_text("<xmeml/>")
    monkeypatch.setattr(fc, "layers_present", lambda x: {"on_screen": True, "music": True, "bleep": True})
    return src, music


def fake_run(calls, fail=None):
    def run(xml, out, **kw):
        calls.append({"xml": Path(xml), "out": Path(out), **kw})
        if fail:
            raise fc.FinishError(fail)
        final = Path(out) / kw["final_name"]
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_text("<xmeml>layered</xmeml>")
        return {"xml": str(final), "folder": str(out), "steps": [{"name": "captions", "done": True, "summary": "rebuilt"}], "checks": [("PICTURE-UNCHANGED", True, "ok")], "music_raw": kw.get("music_file")}
    return run


def test_a_revision_of_a_layered_cut_rebuilds_the_layers_with_the_same_music_and_the_kept_fixes(layered, tmp_path, monkeypatch):
    src, music = layered
    folder = tmp_path / "proj_v3 - revised"
    folder.mkdir()
    v3 = folder / "proj_v3.xml"
    v3.write_text("<xmeml>picture revised</xmeml>")
    (folder / "ops.json").write_text("[]")
    calls = []
    monkeypatch.setattr(fc, "run", fake_run(calls))
    import build_review
    monkeypatch.setattr(build_review, "build", lambda xml, folder, height=540, **kw: None)
    r = ct._put_layers_back(src, v3, folder, [], lambda s: None, 540)
    c = calls[0]
    assert c["rebuild"] is True and c["music_file"] == str(music) and c["caption_fixes"] == [{"at": 3.0, "text": "Fixed", "was": "Fxed", "note": 1}]
    assert c["captions"] and c["music"] and c["bleep"]
    assert v3.read_text() == "<xmeml>layered</xmeml>"                                   # the layered cut is now this version
    assert (folder / "proj_v3 (before layers).xml").read_text() == "<xmeml>picture revised</xmeml>"      # and the picture-only one is kept beside it
    assert r["steps"][0]["name"] == "captions"


def test_a_cut_with_no_layers_is_left_exactly_as_the_revision_made_it(layered, tmp_path, monkeypatch):
    src, _ = layered
    monkeypatch.setattr(fc, "layers_present", lambda x: {"on_screen": False, "music": False, "bleep": False})
    v3 = tmp_path / "v3.xml"
    v3.write_text("<xmeml>picture revised</xmeml>")
    calls = []
    monkeypatch.setattr(fc, "run", fake_run(calls))
    assert ct._put_layers_back(src, v3, tmp_path, [], lambda s: None, 540) is None
    assert not calls and v3.read_text() == "<xmeml>picture revised</xmeml>"


def test_when_the_layers_cannot_be_rebuilt_the_revised_picture_is_kept_and_it_says_so(layered, tmp_path, monkeypatch):
    src, _ = layered
    folder = tmp_path / "v3f"
    folder.mkdir()
    v3 = folder / "proj_v3.xml"
    v3.write_text("<xmeml>picture revised</xmeml>")
    monkeypatch.setattr(fc, "run", fake_run([], fail="The captions failed, so nothing was kept."))
    with pytest.raises(ct.ToolError) as e:
        ct._put_layers_back(src, v3, folder, [], lambda s: None, 540)
    assert "captions failed" in str(e.value)
    assert v3.read_text() == "<xmeml>picture revised</xmeml>"                          # the revision itself is not lost
    assert not (folder / "proj_v3 (before layers).xml").exists()


def test_bleep_notes_are_handed_to_the_bleep(layered, tmp_path, monkeypatch):
    src, _ = layered
    folder = tmp_path / "f"
    folder.mkdir()
    v3 = folder / "proj_v3.xml"
    v3.write_text("<xmeml/>")
    (folder / "ops.json").write_text(json.dumps([{"note": 1, "op": "bleep_word"}]))
    notes = [{"timeline_sec": 5.0, "text": "bleep 4.0-4.3"}]
    calls = []
    monkeypatch.setattr(fc, "run", fake_run(calls))
    import build_review
    monkeypatch.setattr(build_review, "build", lambda *a, **k: None)
    ct._put_layers_back(src, v3, folder, notes, lambda s: None, 540)
    assert calls[0]["bleep_requests"] == [{"kind": "exact", "start": 4.0, "end": 4.3}]


def test_finish_cut_comes_back_shaped_like_a_revision_so_the_tab_opens_it_as_the_next_version(tmp_path, monkeypatch):
    src = tmp_path / "proj_v1.xml"
    src.write_text("<xmeml/>")
    out = tmp_path / "proj_v2 - revised"

    def run(xml, out_, **kw):
        Path(out_).mkdir(parents=True, exist_ok=True)
        (Path(out_) / "proj_v2.xml").write_text("<xmeml/>")
        return {"xml": str(Path(out_) / "proj_v2.xml"), "folder": str(out_), "steps": [{"name": "captions", "done": True, "summary": "x"}, {"name": "music", "done": False, "summary": "already"}],
                "checks": [("PICTURE-UNCHANGED", True, "same")]}
    monkeypatch.setattr(fc, "run", lambda xml, out_, captions, music, bleep, sfx_at, progress=None, **kw: run(xml, out_))
    import build_review
    monkeypatch.setattr(build_review, "build", lambda xml, folder, height=540, **kw: (Path(folder) / "review.html").write_text("<html/>"))
    r = ct.finish_cut(str(src), str(out))
    assert r["xml"].endswith("proj_v2.xml") and r["applied"] == 1 and r["notes"] == 2 and r["qa"] is None
    assert [i["op"] for i in r["items"]] == ["captions", "music"] and r["url"].startswith("http://127.0.0.1")


def test_a_failed_check_on_the_finished_cut_keeps_nothing(tmp_path, monkeypatch):
    src = tmp_path / "proj_v1.xml"
    src.write_text("<xmeml/>")

    def run(xml, out_, captions, music, bleep, sfx_at, progress=None, **kw):
        return {"xml": str(tmp_path / "x.xml"), "folder": str(out_), "steps": [], "checks": [("PICTURE-UNCHANGED", False, "the picture changed")]}
    monkeypatch.setattr(fc, "run", run)
    with pytest.raises(ct.ToolError) as e:
        ct.finish_cut(str(src), str(tmp_path / "out"))
    assert "picture changed" in str(e.value)
