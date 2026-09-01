"""Tests for posthouse.projectmanager — the Project Manager role, headless.

Phase 2's final slice: ties `posthouse.manifest` and `posthouse.brandbrief`
together into `organize_project`, the PM's entry point. Hermetic, same
discipline as `test_manifest.py`/`test_brandbrief.py`: no golden master
here (there's no new serialization format — the output is a manifest
dict already covered by `test_manifest.py`'s golden), but a realistic
fixture project tree is built in `tmp_path` for every test, reusing the
committed fixture media (`safety_net/fixtures/media/`) for footage
instead of generating new video, per this slice's brief.

`posthouse` is imported directly as a sibling top-level package, per the
"run pytest from the repo root" convention `safety_net/run_safety_net.sh`
already uses.
"""
from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from posthouse import manifest as M
from posthouse import projectmanager as PM
from posthouse.harvest import auto_include as _auto_include
from posthouse.precut_bridge import PIN_FILE

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_MEDIA = Path(__file__).parent.parent / "fixtures" / "media"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _real_pin() -> str:
    return PIN_FILE.read_text(encoding="utf-8").strip()


# ---------------------------------------------------------------------------
# Fixture project tree
# ---------------------------------------------------------------------------

def _make_logo(path: Path) -> None:
    from PIL import Image

    im = Image.new("RGBA", (60, 60), (10, 60, 130, 255))
    for x in range(30):
        for y in range(60):
            im.putpixel((x, y), (240, 105, 11, 255))
    im.save(path)


def _make_project_tree(root: Path, brand_source: Path):
    """Build:
    <root>/A-Roll/            AROLL_01.MOV, stable.mp4  (aroll, dual_use)
    <root>/B-Roll/            shaky.mp4, blurred.mp4, weird.lrf  (broll)
    <root>/Audio - Lav/       lav.wav  (source_audio)
    <brand_source>/           logo.png, Brand_Guidelines.pdf  (external — never a project.root_dir descendant)

    Returns a dict of the folder paths.
    """
    aroll_dir = root / "A-Roll"
    broll_dir = root / "B-Roll"
    audio_dir = root / "Audio - Lav"
    for d in (aroll_dir, broll_dir, audio_dir, brand_source):
        d.mkdir(parents=True)

    shutil.copy(FIXTURES_MEDIA / "AROLL_01.MOV", aroll_dir / "AROLL_01.MOV")
    shutil.copy(FIXTURES_MEDIA / "stable.mp4", aroll_dir / "stable.mp4")

    shutil.copy(FIXTURES_MEDIA / "shaky.mp4", broll_dir / "shaky.mp4")
    shutil.copy(FIXTURES_MEDIA / "blurred.mp4", broll_dir / "blurred.mp4")
    (broll_dir / "weird.lrf").write_bytes(b"not a real lrf file, just bytes")

    shutil.copy(FIXTURES_MEDIA / "lav.wav", audio_dir / "lav.wav")

    _make_logo(brand_source / "logo.png")
    (brand_source / "Brand_Guidelines.pdf").write_bytes(b"%PDF-1.4 fake but harmless\n")

    return {"aroll": aroll_dir, "broll": broll_dir, "audio": audio_dir}


def _base_sources(dirs: dict) -> list[dict]:
    return [
        {"path": str(dirs["aroll"]), "kind": "aroll", "dual_use": True, "notes": "interview + coverage"},
        {"path": str(dirs["broll"]), "kind": "broll"},
        {"path": str(dirs["audio"]), "kind": "source_audio"},
    ]


def _organize(root: Path, dirs: dict, brand_source: Path, **overrides) -> PM.OrganizeResult:
    kwargs = dict(
        root_dir=root,
        client_name="Mendez Realty",
        project_name="Mendez Listing",
        project_type="interview",
        sources=_base_sources(dirs),
        brand_assets_source_dir=brand_source,
    )
    kwargs.update(overrides)
    return PM.organize_project(**kwargs)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_happy_path_emits_valid_handoff_ready_manifest(tmp_path):
    root = tmp_path / "Project"
    brand_source = tmp_path / "BrandKit"
    dirs = _make_project_tree(root, brand_source)

    result = _organize(root, dirs, brand_source)

    assert result.manifest_path == root / "manifest.json"
    assert result.manifest_path.exists()
    assert result.is_new_project is True

    reval = M.validate_manifest(result.manifest, mode="handoff")
    assert reval.ok, reval.errors

    assert result.manifest["brand"]["assets_dir"] == str(root / PM.DEFAULT_ASSETS_SUBDIR)
    assert result.manifest["brand"]["logos"][0]["file"] == "logo.png"
    assert result.manifest["brand"]["documents"][0]["file"] == "Brand_Guidelines.pdf"

    assets_dir = root / PM.DEFAULT_ASSETS_SUBDIR
    assert (assets_dir / "BRAND_README.txt").exists()
    assert (assets_dir / "brand-card.png").exists()
    assert (assets_dir / "logo.png").exists()
    assert (assets_dir / "Brand_Guidelines.pdf").exists()


def test_manifest_loads_and_revalidates_from_disk(tmp_path):
    root = tmp_path / "Project"
    brand_source = tmp_path / "BrandKit"
    dirs = _make_project_tree(root, brand_source)
    _organize(root, dirs, brand_source)

    loaded = M.load_manifest(root / "manifest.json")
    result = M.validate_manifest(loaded, mode="handoff")
    assert result.ok, result.errors


# ---------------------------------------------------------------------------
# Footage is never copied
# ---------------------------------------------------------------------------

def test_footage_is_never_copied(tmp_path):
    root = tmp_path / "Project"
    brand_source = tmp_path / "BrandKit"
    dirs = _make_project_tree(root, brand_source)

    original_listing = {}
    for name, d in dirs.items():
        original_listing[name] = sorted(p.name for p in d.iterdir() if p.is_file())

    result = _organize(root, dirs, brand_source)

    # Original source folders are untouched: same files, same names.
    for name, d in dirs.items():
        after_listing = sorted(p.name for p in d.iterdir() if p.is_file())
        assert after_listing == original_listing[name], f"{name} folder changed"

    # No video/audio file anywhere under root_dir exists outside its
    # original source folder — walk the whole project root and count
    # every video/audio file found; it must equal exactly what the three
    # source folders contain, never double-counted by a stray copy.
    original_media_count = 0
    for d in dirs.values():
        for f in d.iterdir():
            if f.is_file() and _auto_include.kind_for_path(f) in ("video", "audio"):
                original_media_count += 1

    all_media_under_root = [
        p for p in root.rglob("*")
        if p.is_file() and _auto_include.kind_for_path(p) in ("video", "audio")
    ]
    assert len(all_media_under_root) == original_media_count

    # And the staged assets folder contains zero video/audio files.
    assets_dir = root / PM.DEFAULT_ASSETS_SUBDIR
    staged_media = [
        p for p in assets_dir.rglob("*")
        if p.is_file() and _auto_include.kind_for_path(p) in ("video", "audio")
    ]
    assert staged_media == []


def test_brand_assets_source_overlapping_a_footage_source_is_refused(tmp_path):
    root = tmp_path / "Project"
    brand_source = tmp_path / "BrandKit"
    dirs = _make_project_tree(root, brand_source)

    with pytest.raises(PM.OrganizeError):
        _organize(root, dirs, dirs["broll"])  # brand source == a footage source


# ---------------------------------------------------------------------------
# Assets ARE copied, co-location holds
# ---------------------------------------------------------------------------

def test_assets_are_copied_and_colocation_holds(tmp_path):
    root = tmp_path / "Project"
    brand_source = tmp_path / "BrandKit"
    dirs = _make_project_tree(root, brand_source)
    result = _organize(root, dirs, brand_source)

    assets_dir = root / PM.DEFAULT_ASSETS_SUBDIR
    from posthouse import brandbrief as B
    problems = B.validate_brief_colocation(result.manifest["brand"], assets_dir)
    assert problems == []

    card_path = assets_dir / result.manifest["brand"]["brief"]["card_png_path"]
    readme_path = assets_dir / result.manifest["brand"]["brief"]["readme_path"]
    assert card_path.is_file()
    assert readme_path.is_file()
    assert card_path.parent == assets_dir
    assert readme_path.parent == assets_dir


# ---------------------------------------------------------------------------
# Census correctness
# ---------------------------------------------------------------------------

def test_census_counts_and_total_bytes(tmp_path):
    root = tmp_path / "Project"
    brand_source = tmp_path / "BrandKit"
    dirs = _make_project_tree(root, brand_source)
    result = _organize(root, dirs, brand_source)

    by_id = {s["display_name"]: s for s in result.manifest["sources"]}

    aroll = by_id["A-Roll"]
    expected_aroll_bytes = (
        (dirs["aroll"] / "AROLL_01.MOV").stat().st_size
        + (dirs["aroll"] / "stable.mp4").stat().st_size
    )
    assert aroll["media"] == {
        "video_count": 2, "audio_count": 0, "image_count": 0,
        "other_count": 0, "total_bytes": expected_aroll_bytes,
    }
    assert aroll.get("unsupported", []) == []

    broll = by_id["B-Roll"]
    expected_broll_bytes = (
        (dirs["broll"] / "shaky.mp4").stat().st_size
        + (dirs["broll"] / "blurred.mp4").stat().st_size
        + (dirs["broll"] / "weird.lrf").stat().st_size
    )
    assert broll["media"] == {
        "video_count": 2, "audio_count": 0, "image_count": 0,
        "other_count": 1, "total_bytes": expected_broll_bytes,
    }
    assert broll["unsupported"] == [M.categorize_unsupported(".lrf", 1)]

    audio = by_id["Audio - Lav"]
    expected_audio_bytes = (dirs["audio"] / "lav.wav").stat().st_size
    assert audio["media"] == {
        "video_count": 0, "audio_count": 1, "image_count": 0,
        "other_count": 0, "total_bytes": expected_audio_bytes,
    }


def test_census_source_helper_directly(tmp_path):
    d = tmp_path / "src"
    d.mkdir()
    shutil.copy(FIXTURES_MEDIA / "stable.mp4", d / "stable.mp4")
    (d / "notes.txt").write_text("hello")

    media, unsupported, video_files = PM.census_source(d)
    assert media["video_count"] == 1
    assert media["other_count"] == 1
    assert video_files == [d / "stable.mp4"]
    assert unsupported == [M.categorize_unsupported(".txt", 1)]


# ---------------------------------------------------------------------------
# dual_use survives on the right source
# ---------------------------------------------------------------------------

def test_dual_use_flag_survives_on_the_declared_source(tmp_path):
    root = tmp_path / "Project"
    brand_source = tmp_path / "BrandKit"
    dirs = _make_project_tree(root, brand_source)
    result = _organize(root, dirs, brand_source)

    by_display = {s["display_name"]: s for s in result.manifest["sources"]}
    assert by_display["A-Roll"]["dual_use"] is True
    assert "dual_use" not in by_display["B-Roll"]
    assert "dual_use" not in by_display["Audio - Lav"]


# ---------------------------------------------------------------------------
# Inference: recorded, real pin, declared kind wins
# ---------------------------------------------------------------------------

def test_inference_recorded_with_real_pin_and_agreement_field(tmp_path):
    root = tmp_path / "Project"
    brand_source = tmp_path / "BrandKit"
    dirs = _make_project_tree(root, brand_source)
    result = _organize(root, dirs, brand_source)

    pin = _real_pin()
    for s in result.manifest["sources"]:
        inf = s["inference"]
        assert inf["method"] == f"camera_inference@{pin}"
        assert "agrees_with_declaration" in inf
        assert isinstance(inf["camera_tags"], list)


def test_declared_kind_wins_even_when_inference_disagrees(tmp_path):
    root = tmp_path / "Project"
    brand_source = tmp_path / "BrandKit"
    dirs = _make_project_tree(root, brand_source)

    # A folder organized by camera model (infer_camera_tags matches
    # ancestor folder names, not just filenames — see its docstring),
    # declared as aroll: a drone/aerial source should never plausibly be
    # a locked-off interview, so this should disagree, but the declared
    # kind must still win.
    weird_aroll = root / "Weird A-Roll (Mavic 2)"
    weird_aroll.mkdir(parents=True)
    shutil.copy(FIXTURES_MEDIA / "stable.mp4", weird_aroll / "clip.mp4")

    sources = _base_sources(dirs) + [{"path": str(weird_aroll), "kind": "aroll"}]
    result = PM.organize_project(
        root_dir=root, client_name="Mendez Realty", project_name="Mendez Listing",
        project_type="interview", sources=sources, brand_assets_source_dir=brand_source,
    )

    weird_source = next(
        s for s in result.manifest["sources"] if s["display_name"] == "Weird A-Roll (Mavic 2)"
    )
    assert weird_source["kind"] == "aroll"
    assert weird_source["inference"]["agrees_with_declaration"] is False
    assert "drone" in weird_source["inference"]["camera_tags"]


# ---------------------------------------------------------------------------
# Shoot dates
# ---------------------------------------------------------------------------

def test_census_skips_precut_proxies_and_appledouble_sidecars(tmp_path):
    """Reproduces the first real-footage run (Runnells Day 1, 2026-09-01):
    a 2-clip Osmo folder censused as 6 videos with a phantom shoot date,
    because PreCut leaves `proxies/` next to the originals and macOS adds
    `._*` AppleDouble sidecars that also end in .mp4. Only the originals
    are footage; the returned video_files list (which shoot dates derive
    from) must exclude everything else too."""
    src = tmp_path / "Osmo"
    (src / "proxies").mkdir(parents=True)
    (src / "PreCut_Output").mkdir()
    (src / "DJI_0005.MP4").write_bytes(b"\x00" * 1000)
    (src / "DJI_0006.MP4").write_bytes(b"\x00" * 500)
    # PreCut's proxies + macOS sidecars, exactly as found on the drive
    (src / "proxies" / "DJI_0005.mp4").write_bytes(b"\x00" * 100)
    (src / "proxies" / "DJI_0006.mp4").write_bytes(b"\x00" * 100)
    (src / "proxies" / "._DJI_0005.mp4").write_bytes(b"\x00" * 10)
    (src / "proxies" / "._DJI_0006.mp4").write_bytes(b"\x00" * 10)
    (src / "._proxies").write_bytes(b"\x00" * 10)
    (src / ".DS_Store").write_bytes(b"\x00" * 10)
    (src / "PreCut_Output" / "export.xml").write_text("<xmeml/>")

    media, unsupported, video_files = PM.census_source(src)

    assert media["video_count"] == 2, media
    assert media["other_count"] == 0, media
    assert media["total_bytes"] == 1500, media
    assert unsupported == [], unsupported
    assert sorted(p.name for p in video_files) == ["DJI_0005.MP4", "DJI_0006.MP4"]


def test_shoot_dates_derived_and_deterministic_iso_format(tmp_path):
    root = tmp_path / "Project"
    brand_source = tmp_path / "BrandKit"
    dirs = _make_project_tree(root, brand_source)
    result = _organize(root, dirs, brand_source)

    dates = result.manifest["project"]["shoot_dates"]
    assert dates == sorted(dates)
    assert dates == sorted(set(dates))
    for d in dates:
        assert _DATE_RE.match(d), d

    video_files = [
        dirs["aroll"] / "AROLL_01.MOV", dirs["aroll"] / "stable.mp4",
        dirs["broll"] / "shaky.mp4", dirs["broll"] / "blurred.mp4",
    ]
    assert dates == PM.derive_shoot_dates(video_files)


def test_derive_shoot_dates_uses_birthtime_when_available(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x")

    class _StatWithBirthtime:
        st_birthtime = datetime(2026, 1, 5).timestamp()
        st_mtime = datetime(2026, 6, 30).timestamp()  # deliberately different

    real_stat = Path.stat

    def fake_stat(self, *a, **kw):
        if self == f:
            return _StatWithBirthtime()
        return real_stat(self, *a, **kw)

    orig = Path.stat
    Path.stat = fake_stat
    try:
        dates = PM.derive_shoot_dates([f])
    finally:
        Path.stat = orig

    assert dates == ["2026-01-05"]


def test_derive_shoot_dates_falls_back_to_mtime_without_birthtime(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x")

    class _StatNoBirthtime:
        st_mtime = datetime(2026, 3, 14).timestamp()

    real_stat = Path.stat

    def fake_stat(self, *a, **kw):
        if self == f:
            return _StatNoBirthtime()
        return real_stat(self, *a, **kw)

    orig = Path.stat
    Path.stat = fake_stat
    try:
        dates = PM.derive_shoot_dates([f])
    finally:
        Path.stat = orig

    assert dates == ["2026-03-14"]


# ---------------------------------------------------------------------------
# Handoff record
# ---------------------------------------------------------------------------

def test_handoff_entry_appended_on_emit(tmp_path):
    root = tmp_path / "Project"
    brand_source = tmp_path / "BrandKit"
    dirs = _make_project_tree(root, brand_source)
    result = _organize(root, dirs, brand_source)

    handoffs = result.manifest["handoffs"]
    assert len(handoffs) == 1
    h = handoffs[0]
    assert h["role"] == "project_manager"
    assert h["action"] == "emitted"
    assert h["revision"] == 1
    assert h["agent"] == "posthouse.pm/0.1.0"
    assert "at" in h


# ---------------------------------------------------------------------------
# Idempotent re-run
# ---------------------------------------------------------------------------

def test_idempotent_rerun_does_not_duplicate_sources(tmp_path):
    root = tmp_path / "Project"
    brand_source = tmp_path / "BrandKit"
    dirs = _make_project_tree(root, brand_source)

    first = _organize(root, dirs, brand_source)
    first_ids = sorted(s["id"] for s in first.manifest["sources"])

    second = _organize(root, dirs, brand_source)
    second_ids = sorted(s["id"] for s in second.manifest["sources"])

    assert second_ids == first_ids
    assert len(second.manifest["sources"]) == len(first.manifest["sources"])
    assert second.added_source_ids == []
    assert second.manifest["revision"] == first.manifest["revision"] + 1
    assert len(second.manifest["handoffs"]) == 2


def test_rerun_refreshes_census_of_existing_sources_but_keeps_identity(tmp_path):
    """Found on Runnells Day 1: revision 2 carried a wrong video_count from
    revision 1 while shoot_dates (also disk-derived) had already corrected
    itself. A re-run must refresh the snapshot fields (media, unsupported,
    inference) of sources it already knows, while their id and added_at
    stay frozen."""
    root = tmp_path / "Project"
    brand_source = tmp_path / "BrandKit"
    dirs = _make_project_tree(root, brand_source)

    first = _organize(root, dirs, brand_source)
    target = first.manifest["sources"][0]
    before_count = target["media"]["video_count"]
    before_id, before_added = target["id"], target["added_at"]

    # Footage changes on disk between runs: one more clip lands in the folder.
    (Path(target["path"]) / "LATE_CLIP.MP4").write_bytes(b"\x00" * 2048)

    second = _organize(root, dirs, brand_source)
    after = next(s for s in second.manifest["sources"] if s["id"] == before_id)

    assert after["media"]["video_count"] == before_count + 1, after["media"]
    assert after["id"] == before_id
    assert after["added_at"] == before_added
    assert second.manifest["revision"] == first.manifest["revision"] + 1


# ---------------------------------------------------------------------------
# Late footage: new revision, existing ids undisturbed
# ---------------------------------------------------------------------------

def test_late_footage_appends_new_source_without_disturbing_existing_ids(tmp_path):
    root = tmp_path / "Project"
    brand_source = tmp_path / "BrandKit"
    dirs = _make_project_tree(root, brand_source)

    first = _organize(root, dirs, brand_source)
    first_ids = {s["id"]: s for s in first.manifest["sources"]}

    late_dir = root / "B-Roll Drone (late)"
    late_dir.mkdir()
    shutil.copy(FIXTURES_MEDIA / "overexposed.mp4", late_dir / "overexposed.mp4")

    sources = _base_sources(dirs) + [{"path": str(late_dir), "kind": "broll"}]
    second = PM.organize_project(
        root_dir=root, client_name="Mendez Realty", project_name="Mendez Listing",
        project_type="interview", sources=sources, brand_assets_source_dir=brand_source,
    )

    second_by_id = {s["id"]: s for s in second.manifest["sources"]}
    for sid, original in first_ids.items():
        assert second_by_id[sid] == original, f"existing source {sid} was disturbed"

    assert len(second.manifest["sources"]) == len(first.manifest["sources"]) + 1
    assert len(second.added_source_ids) == 1
    new_id = second.added_source_ids[0]
    assert new_id not in first_ids
    assert second_by_id[new_id]["path"] == str(late_dir)
    assert second.manifest["revision"] == first.manifest["revision"] + 1


# ---------------------------------------------------------------------------
# Failing case: a declared source that does not exist
# ---------------------------------------------------------------------------

def test_missing_source_path_surfaces_as_handoff_error_not_written(tmp_path):
    root = tmp_path / "Project"
    brand_source = tmp_path / "BrandKit"
    dirs = _make_project_tree(root, brand_source)

    missing = root / "Does Not Exist"
    sources = _base_sources(dirs) + [{"path": str(missing), "kind": "broll"}]

    with pytest.raises(PM.OrganizeError) as exc_info:
        PM.organize_project(
            root_dir=root, client_name="Mendez Realty", project_name="Mendez Listing",
            project_type="interview", sources=sources, brand_assets_source_dir=brand_source,
        )

    assert any("does not exist" in e for e in exc_info.value.errors)
    assert not (root / "manifest.json").exists()


def test_missing_source_path_on_rerun_does_not_corrupt_existing_manifest(tmp_path):
    root = tmp_path / "Project"
    brand_source = tmp_path / "BrandKit"
    dirs = _make_project_tree(root, brand_source)

    first = _organize(root, dirs, brand_source)
    before_text = (root / "manifest.json").read_text(encoding="utf-8")

    missing = root / "Does Not Exist"
    sources = _base_sources(dirs) + [{"path": str(missing), "kind": "broll"}]
    with pytest.raises(PM.OrganizeError):
        PM.organize_project(
            root_dir=root, client_name="Mendez Realty", project_name="Mendez Listing",
            project_type="interview", sources=sources, brand_assets_source_dir=brand_source,
        )

    after_text = (root / "manifest.json").read_text(encoding="utf-8")
    assert after_text == before_text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_organize_happy_path_exits_zero(tmp_path, capsys):
    root = tmp_path / "Project"
    brand_source = tmp_path / "BrandKit"
    dirs = _make_project_tree(root, brand_source)

    rc = PM._main([
        "organize",
        "--root", str(root),
        "--client", "Mendez Realty",
        "--project", "Mendez Listing",
        "--type", "interview",
        "--source", f"{dirs['aroll']}:aroll:dual_use",
        "--source", f"{dirs['broll']}:broll",
        "--source", f"{dirs['audio']}:source_audio",
        "--assets", str(brand_source),
    ])
    assert rc == 0
    assert (root / "manifest.json").exists()
    out = capsys.readouterr().out
    assert "OK" in out


def test_cli_organize_missing_source_exits_nonzero_and_lists_errors(tmp_path, capsys):
    root = tmp_path / "Project"
    brand_source = tmp_path / "BrandKit"
    dirs = _make_project_tree(root, brand_source)

    rc = PM._main([
        "organize",
        "--root", str(root),
        "--client", "Mendez Realty",
        "--project", "Mendez Listing",
        "--type", "interview",
        "--source", f"{root / 'Nope'}:broll",
    ])
    assert rc != 0
    err = capsys.readouterr().err
    assert "error:" in err
    assert not (root / "manifest.json").exists()


def test_cli_source_arg_parsing():
    assert PM._parse_source_arg("/a/b:broll") == {"path": "/a/b", "kind": "broll"}
    assert PM._parse_source_arg("/a/b:aroll:dual_use") == {
        "path": "/a/b", "kind": "aroll", "dual_use": True,
    }
    with pytest.raises(Exception):
        PM._parse_source_arg("/a/b:aroll:bogus")
    with pytest.raises(Exception):
        PM._parse_source_arg("no-colon-here")
