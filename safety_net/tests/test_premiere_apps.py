"""Which Premiere Pro to open an XML in: every install is found (stable and Beta), a choice is honoured only if it is one of them, and nothing is launched in tests."""
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "app" / "python_backend"))
import creator_tools as ct  # noqa: E402


def make(root: Path, *names: str, nested=True):
    for n in names:
        d = root / n / f"{n}.app" if nested else root / f"{n}.app"
        d.mkdir(parents=True)


def test_every_install_is_found_stable_newest_first_then_betas(tmp_path):
    make(tmp_path, "Adobe Premiere Pro 2025", "Adobe Premiere Pro 2026", "Adobe Premiere Pro (Beta)")
    (tmp_path / "Adobe Photoshop 2026" / "Adobe Photoshop 2026.app").mkdir(parents=True)               # other Adobe apps are not Premiere
    apps = ct.premiere_apps((str(tmp_path),))
    assert [a["name"] for a in apps] == ["Adobe Premiere Pro 2026", "Adobe Premiere Pro 2025", "Adobe Premiere Pro (Beta)"]
    assert [a["beta"] for a in apps] == [False, False, True]
    assert all(Path(a["path"]).is_dir() for a in apps)


def test_a_bare_app_and_a_second_applications_folder_are_found_too_and_none_is_an_empty_list(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    make(a, "Adobe Premiere Pro (Beta)")
    make(b, "Adobe Premiere Pro 2024", nested=False)
    assert [x["name"] for x in ct.premiere_apps((str(a), str(b)))] == ["Adobe Premiere Pro 2024", "Adobe Premiere Pro (Beta)"]
    assert ct.premiere_apps((str(tmp_path / "nothing"),)) == []


def test_the_chosen_version_is_used_only_if_it_is_a_real_install(tmp_path):
    make(tmp_path, "Adobe Premiere Pro 2026", "Adobe Premiere Pro (Beta)")
    apps = ct.premiere_apps((str(tmp_path),))
    beta = next(a["path"] for a in apps if a["beta"])
    assert ct.premiere_app(beta, apps) == beta                                                         # the user's choice
    assert ct.premiere_app(None, apps) == apps[0]["path"]                                              # no choice: the newest stable
    assert ct.premiere_app("/usr/bin/python3", apps) == apps[0]["path"]                                # not an install: ignored, nothing else can be launched through this
    assert ct.premiere_app(None, []) is None


def test_export_opens_the_chosen_version_after_the_check_and_reports_which(tmp_path, monkeypatch):
    import subprocess
    ct.use_labs()
    import xml_from_media
    v = tmp_path / "Clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000:duration=4", "-f", "lavfi", "-i", "testsrc2=s=180x320:r=30:d=4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(v)], check=True)
    xml = str(xml_from_media.make(v, tmp_path / "cut.xml"))
    make(tmp_path / "Apps", "Adobe Premiere Pro 2026", "Adobe Premiere Pro (Beta)")
    apps = ct.premiere_apps((str(tmp_path / "Apps"),))
    monkeypatch.setattr(ct, "premiere_apps", lambda *a, **k: apps)
    beta = next(a for a in apps if a["beta"])
    monkeypatch.setenv("POSTHOUSE_NO_OPEN", "1")
    r = ct.export_xml(xml, True, beta["path"])
    assert r["verified"] is True and r["app"] == "Adobe Premiere Pro (Beta)" and r["app_path"] == beta["path"] and r["opened"] is False
    launched = []
    monkeypatch.delenv("POSTHOUSE_NO_OPEN")
    monkeypatch.setattr(ct.subprocess, "run", lambda cmd, **k: launched.append(cmd))
    r2 = ct.export_xml(xml, True, beta["path"])
    assert r2["opened"] is True and launched == [["open", "-a", beta["path"], xml]]                    # opened in the Beta, the file as the argument
    launched.clear()
    bad = tmp_path / "bad.xml"
    bad.write_text("<xmeml version='4'><sequence id='s'><name>x</name></sequence></xmeml>")
    assert ct.export_xml(str(bad), True, beta["path"])["opened"] is False and launched == []           # a failing XML is never opened, whichever version was chosen
