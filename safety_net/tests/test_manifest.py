"""Tests for posthouse.manifest — the Project Manifest builder and validator.

Extends the Phase 0 safety net the same way test_coldfootage.py does: same
BLESS=1 golden-master mechanism, same "hermetic — fixtures/tmp_path only,
no real footage" discipline. `posthouse` is imported directly, as a
sibling top-level package found via the "run pytest from the repo root"
convention `safety_net/run_safety_net.sh` already uses.

JSON has none of the XML writer's structural nondeterminism (no random
UUIDs baked into repeated elements, no set-order-driven id allocation) —
but the manifest itself has three genuinely nondeterministic fields by
design: `manifest_id` (uuid4), `created_at`/`updated_at` (wall clock), and
`project.root_dir` (a pytest tmp_path, different every run). Those are
canonicalized to stable tokens before the golden compare, same technique
as conftest.py's XML normalizer, adapted for JSON.
"""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from posthouse import manifest as M

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = Path(__file__).parent.parent / "golden" / "expected_manifest.json"
ACTUAL_PATH = Path(__file__).parent.parent / "golden" / "actual_manifest.json"

_UUID_RE = re.compile(
    r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
)
_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def _normalize_manifest_text(raw_text: str, root_dir: Path) -> str:
    """Neutralize the three nondeterministic fields (manifest_id, the two
    timestamps, and the tmp_path root_dir) so the golden compare is stable
    across runs and machines."""
    text = raw_text
    text = text.replace(str(root_dir), "{ROOT}").replace(str(root_dir.resolve()), "{ROOT}")
    text = _UUID_RE.sub("{UUID}", text)
    text = _TS_RE.sub("{TS}", text)
    return text


# ---------------------------------------------------------------------------
# A worked example closely modeled on contract §3
# ---------------------------------------------------------------------------

def _make_example_manifest(root: Path) -> dict:
    aroll_dir = root / "A - Carla Interview"
    broll_dir = root / "B - Interior Rooms"
    aroll_dir.mkdir()
    broll_dir.mkdir()

    m = M.build_manifest(
        project_name="Mendez Listing — 128 Alder St",
        root_dir=str(root),
        client_name="Carla Mendez Realty",
        client_contact="carla@mendezrealty.com",
        project_type="interview",
        shoot_dates=["2026-08-27"],
        locations=[{"label": "128 Alder St", "address": "128 Alder St, Bend OR"}],
        people=[{"name": "Carla Mendez", "role": "agent"}],
        project_notes="Agent-led walkthrough. She wants the kitchen island featured.",
        sources=[
            {
                "path": str(aroll_dir),
                "display_name": "A - Carla Interview",
                "kind": "aroll",
                "dual_use": True,
                "subject_ids": ["carla-mendez"],
                "notes": "She keeps talking while I grab the room.",
                "media": {
                    "video_count": 14, "audio_count": 0, "image_count": 0,
                    "other_count": 0, "total_bytes": 88213004288,
                },
                "inference": {
                    "camera_tags": ["sony", "cinema"],
                    "method": "camera_inference@e035fbaf",
                    "agrees_with_declaration": True,
                },
            },
            {
                "path": str(broll_dir),
                "display_name": "B - Interior Rooms",
                "kind": "broll",
                "media": {
                    "video_count": 63, "audio_count": 0, "image_count": 0,
                    "other_count": 2, "total_bytes": 141238374400,
                },
                "unsupported": [M.categorize_unsupported(".lrf", 2)],
                "inference": {
                    "camera_tags": ["osmo", "gimbal", "pocket"],
                    "method": "camera_inference@e035fbaf",
                    "agrees_with_declaration": True,
                },
            },
        ],
        brand={
            "assets_dir": str(root / "Brand Assets"),
            "fonts": [
                {
                    "file": "Gilroy-Bold.otf", "family_name": "Gilroy",
                    "style_name": "Bold", "format": "otf",
                    "extracted_by": "name_table", "install_status": "not_installed",
                },
            ],
            "palette": [
                {"hex": "#1B3A57", "role": "primary", "source": "logo:mendez-logo.png"},
            ],
            "logos": [{"file": "mendez-logo.png", "kind": "primary", "has_alpha": True}],
            "documents": [],
            "brief": {
                "readme_path": "BRAND_README.txt", "card_png_path": "brand-card.png",
                "bin_path": "Files/Brand", "marker_written": True,
            },
        },
        default_includes=[
            {
                "id": "b7c1b0e2-0000-0000-0000-000000000000", "type": "folder",
                "source_path": "/Users/ryan/Assets/SFX/Whooshes",
                "bin_path": "Audio/SFX", "file_glob": "*.wav",
                "origin": "global_settings",
            },
        ],
    )
    return m


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

def test_round_trip_build_save_load(tmp_path):
    m = _make_example_manifest(tmp_path)
    path = tmp_path / "manifest.json"
    M.save_manifest(m, path)
    loaded = M.load_manifest(path)
    assert loaded == m
    # every top-level required key present
    for key in ("contract_version", "manifest_id", "revision", "created_at",
                "updated_at", "generator", "project", "sources", "handoffs",
                "validation"):
        assert key in loaded


def test_round_trip_is_lossless_for_optional_fields(tmp_path):
    m = _make_example_manifest(tmp_path)
    path = tmp_path / "manifest.json"
    M.save_manifest(m, path)
    loaded = M.load_manifest(path)
    assert loaded["brand"]["fonts"][0]["family_name"] == "Gilroy"
    assert loaded["sources"][1]["unsupported"][0]["category"] == "unknown_extension"
    assert loaded["project"]["locations"][0]["address"] == "128 Alder St, Bend OR"


def test_delivery_targets_absent_from_freshly_built_manifest(tmp_path):
    """Ratified ruling (contract §2.5, ROADMAP Decision Log): the PM never
    proposes delivery targets — the key is absent, not an empty list."""
    m = _make_example_manifest(tmp_path)
    assert "delivery_targets" not in m


# ---------------------------------------------------------------------------
# Golden master
# ---------------------------------------------------------------------------

def test_manifest_matches_golden_master(tmp_path):
    m = _make_example_manifest(tmp_path)
    path = tmp_path / "manifest.json"
    M.save_manifest(m, path)
    raw = path.read_text(encoding="utf-8")
    normalized = _normalize_manifest_text(raw, tmp_path)

    if os.environ.get("BLESS") == "1":
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(normalized, encoding="utf-8")
        pytest.skip(f"BLESSED new golden snapshot at {GOLDEN_PATH} — this was NOT a check")

    assert GOLDEN_PATH.exists(), (
        f"No blessed snapshot at {GOLDEN_PATH}. Run with BLESS=1 to create "
        f"one, and record why in the Decision Log per safety_net/README.md."
    )
    expected = GOLDEN_PATH.read_text(encoding="utf-8")

    if normalized == expected:
        if ACTUAL_PATH.exists():
            ACTUAL_PATH.unlink()
        return

    ACTUAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACTUAL_PATH.write_text(normalized, encoding="utf-8")

    import difflib
    diff = list(difflib.unified_diff(
        expected.splitlines(), normalized.splitlines(),
        fromfile="golden/expected_manifest.json", tofile="actual (this run)",
        lineterm="",
    ))
    excerpt = "\n".join(diff[:60])
    raise AssertionError(
        f"manifest.json no longer matches the blessed golden master.\n"
        f"Full actual output written to {ACTUAL_PATH} for inspection.\n"
        f"First ~60 diff lines:\n{excerpt}"
    )


def test_golden_fixture_key_order_matches_contract():
    """Sanity check independent of the byte-diff — catches a vacuous pass."""
    assert GOLDEN_PATH.exists(), "golden fixture must exist (see BLESS=1 above)"
    data = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert list(data.keys()) == [
        k for k in M.TOP_LEVEL_ORDER if k in data
    ]
    assert list(data["project"].keys())[0] == "name"
    assert list(data["sources"][0].keys())[0] == "id"


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("B - Interior Rooms", "b-interior-rooms"),
    ("Café Résumé", "cafe-resume"),
    ("MIXED Case_Weird!!Symbols??", "mixed-case-weird-symbols"),
    ("", "folder"),
    ("!!!???...", "folder"),
    ("---", "folder"),
])
def test_slugify_basic_cases(text, expected):
    assert M.slugify(text) == expected


def test_slugify_truncates_at_hyphen_boundary_under_40_chars():
    text = "a" * 10 + "-" + "b" * 10 + "-" + "c" * 10 + "-" + "d" * 20
    result = M.slugify(text, max_len=40)
    assert len(result) <= 40
    assert not result.endswith("-")
    # truncation must land on a hyphen boundary, not mid-word
    assert result in (
        "a" * 10 + "-" + "b" * 10 + "-" + "c" * 10,
    )


def test_slugify_hard_truncates_when_no_hyphen_boundary_available():
    text = "x" * 60
    result = M.slugify(text, max_len=40)
    assert result == "x" * 40


def test_slugify_unicode_nfkd_and_combining_marks():
    assert M.slugify("naïve") == "naive"
    assert M.slugify("Ångström") == "angstrom"


# ---------------------------------------------------------------------------
# Source ID minting
# ---------------------------------------------------------------------------

def test_mint_source_id_basic_shape():
    sid = M.mint_source_id("aroll", "B - Interior Rooms", [])
    assert sid == "aroll-b-interior-rooms-01"
    assert M.SOURCE_ID_RE.match(sid)


def test_mint_source_id_kind_mapping_source_audio_is_hyphenated():
    sid = M.mint_source_id("source_audio", "Lav Mics", [])
    assert sid.startswith("source-audio-")
    assert M.SOURCE_ID_RE.match(sid)


def test_mint_source_id_collision_increments_nn():
    existing = ["broll-interior-rooms-01"]
    sid = M.mint_source_id("broll", "Interior Rooms", existing)
    assert sid == "broll-interior-rooms-02"
    existing.append(sid)
    sid3 = M.mint_source_id("broll", "Interior Rooms", existing)
    assert sid3 == "broll-interior-rooms-03"


def test_mint_source_id_different_kind_does_not_collide():
    existing = ["broll-interior-rooms-01"]
    sid = M.mint_source_id("aroll", "Interior Rooms", existing)
    assert sid == "aroll-interior-rooms-01"


def test_mint_source_id_regex_rejects_malformed_ids():
    assert not M.SOURCE_ID_RE.match("aroll-Interior-01")   # uppercase
    assert not M.SOURCE_ID_RE.match("aroll-interior-1")    # single digit
    assert not M.SOURCE_ID_RE.match("weird-interior-01")   # bad kind
    assert not M.SOURCE_ID_RE.match("aroll--01")            # empty slug segment
    assert M.SOURCE_ID_RE.match("source-audio-lav-carla-01")


def test_mint_source_id_unicode_and_symbols_display_name():
    sid = M.mint_source_id("assets", "Café's B-Roll & Stuff!!", [])
    assert M.SOURCE_ID_RE.match(sid)
    assert "cafe" in sid


def test_mint_source_id_empty_display_name_becomes_folder():
    sid = M.mint_source_id("broll", "???", [])
    assert sid == "broll-folder-01"


def test_mint_source_id_over_40_chars_display_name():
    long_name = "This Is A Very Long Folder Name That Definitely Exceeds Forty Characters"
    sid = M.mint_source_id("broll", long_name, [])
    assert M.SOURCE_ID_RE.match(sid)
    # slug portion (between kind- and -NN) must be <= 40 chars
    slug = sid[len("broll-"):-3]
    assert len(slug) <= 40


def test_mint_delivery_target_id_shape():
    dt_id = M.mint_delivery_target_id("IG Reel — 30s", [])
    assert dt_id == "dt-ig-reel-30s-01"
    dt_id2 = M.mint_delivery_target_id("IG Reel — 30s", [dt_id])
    assert dt_id2 == "dt-ig-reel-30s-02"


# ---------------------------------------------------------------------------
# Frozen IDs: add_source on an existing manifest never changes prior ids
# ---------------------------------------------------------------------------

def test_add_source_never_changes_existing_ids(tmp_path):
    m = _make_example_manifest(tmp_path)
    original_ids = [s["id"] for s in m["sources"]]

    new_dir = tmp_path / "B - Exterior Mavic 3"
    new_dir.mkdir()
    new_id = M.add_source(
        m, path=str(new_dir), display_name="B - Exterior Mavic 3", kind="broll",
        media={"video_count": 27, "audio_count": 0, "image_count": 0,
               "other_count": 0, "total_bytes": 1000},
    )

    current_ids = [s["id"] for s in m["sources"]]
    assert current_ids[:len(original_ids)] == original_ids
    assert new_id in current_ids
    assert M.SOURCE_ID_RE.match(new_id)


def test_add_source_after_rename_keeps_old_id(tmp_path):
    """Renaming display_name/path never re-slugs a frozen id — this test
    proves it by adding a source, then adding a SECOND source with a
    colliding display_name and confirming the first id is untouched and
    the second correctly becomes -02."""
    m = _make_example_manifest(tmp_path)
    dir_a = tmp_path / "B - Interior"
    dir_a.mkdir()
    id_a = M.add_source(m, path=str(dir_a), display_name="B - Interior", kind="broll")

    dir_b = tmp_path / "B - Interior (2)"
    dir_b.mkdir()
    id_b = M.add_source(m, path=str(dir_b), display_name="B - Interior", kind="broll")

    assert id_a != id_b
    assert id_a.endswith("-01") or "interior" in id_a
    ids_after = [s["id"] for s in m["sources"]]
    assert id_a in ids_after and id_b in ids_after
    # id_a must still be exactly what it was minted as
    matching = [s for s in m["sources"] if s["id"] == id_a]
    assert len(matching) == 1
    assert matching[0]["path"] == str(dir_a)


def test_save_manifest_bumps_revision_only_after_first_write(tmp_path):
    m = _make_example_manifest(tmp_path)
    path = tmp_path / "manifest.json"
    assert m["revision"] == 1
    M.save_manifest(m, path)
    assert m["revision"] == 1  # first write: unchanged
    M.save_manifest(m, path)
    assert m["revision"] == 2
    M.save_manifest(m, path)
    assert m["revision"] == 3


# ---------------------------------------------------------------------------
# contract_version rejection
# ---------------------------------------------------------------------------

def test_load_manifest_rejects_unknown_contract_version(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"contract_version": 99}), encoding="utf-8")
    with pytest.raises(M.ManifestError):
        M.load_manifest(path)


def test_load_manifest_rejects_missing_contract_version(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"project": {}}), encoding="utf-8")
    with pytest.raises(M.ManifestError):
        M.load_manifest(path)


def test_validate_manifest_rejects_unknown_contract_version_in_handoff_mode(tmp_path):
    m = _make_example_manifest(tmp_path)
    m["contract_version"] = 2
    result = M.validate_manifest(m, mode="handoff")
    assert not result.ok
    assert any("contract_version" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Validation: REJECT rules, one per case, intake warns / handoff rejects
# ---------------------------------------------------------------------------

def _assert_intake_warns_handoff_rejects(manifest: dict, needle: str):
    intake = M.validate_manifest(manifest, mode="intake")
    assert intake.ok, f"intake mode must never produce fatal errors: {intake.errors}"
    assert any(needle in w for w in intake.warnings), (
        f"expected a warning containing {needle!r}, got: {intake.warnings}"
    )

    handoff = M.validate_manifest(manifest, mode="handoff")
    assert not handoff.ok, "handoff mode must reject this"
    assert any(needle in e for e in handoff.errors), (
        f"expected an error containing {needle!r}, got: {handoff.errors}"
    )


def test_reject_root_dir_not_a_directory(tmp_path):
    m = _make_example_manifest(tmp_path)
    m["project"]["root_dir"] = str(tmp_path / "does-not-exist")
    _assert_intake_warns_handoff_rejects(m, "root_dir")


def test_reject_empty_sources(tmp_path):
    m = _make_example_manifest(tmp_path)
    m["sources"] = []
    _assert_intake_warns_handoff_rejects(m, "sources is empty")


def test_reject_source_path_does_not_exist(tmp_path):
    m = _make_example_manifest(tmp_path)
    m["sources"][0]["path"] = str(tmp_path / "nonexistent-folder")
    _assert_intake_warns_handoff_rejects(m, "does not exist")


def test_reject_source_id_fails_regex(tmp_path):
    m = _make_example_manifest(tmp_path)
    m["sources"][0]["id"] = "AROLL-Bad-01"
    _assert_intake_warns_handoff_rejects(m, "does not match the required pattern")


def test_reject_duplicate_source_ids(tmp_path):
    m = _make_example_manifest(tmp_path)
    m["sources"][1]["id"] = m["sources"][0]["id"]
    _assert_intake_warns_handoff_rejects(m, "used by more than one source")


def test_reject_kind_conflict_same_path_different_kind(tmp_path):
    m = _make_example_manifest(tmp_path)
    dupe = copy.deepcopy(m["sources"][0])
    dupe["id"] = "broll-carla-interview-01"
    dupe["kind"] = "broll"
    m["sources"].append(dupe)
    _assert_intake_warns_handoff_rejects(m, "different kinds")


def test_warn_not_reject_kind_conflict_same_path_same_kind(tmp_path):
    m = _make_example_manifest(tmp_path)
    dupe = copy.deepcopy(m["sources"][1])
    dupe["id"] = "broll-interior-rooms-99"
    m["sources"].append(dupe)
    handoff = M.validate_manifest(m, mode="handoff")
    assert handoff.ok, f"same-kind nested/duplicate path must only warn: {handoff.errors}"
    assert any("same kind" in w for w in handoff.warnings)


def test_reject_nested_source_with_different_kind(tmp_path):
    """Contract §4.1 rule 5: 'one source nested inside another with a
    different kind' is a REJECT — not just the same-path case. Caught by
    code review; a nested broll folder inside an aroll folder used to pass
    handoff validation silently, letting double-counted, kind-confused
    footage reach the AE."""
    m = _make_example_manifest(tmp_path)
    outer = Path(m["sources"][0]["path"])
    nested = outer / "b_cam"
    nested.mkdir(parents=True, exist_ok=True)
    m["sources"].append({
        "id": "broll-b-cam-01", "path": str(nested), "display_name": "b_cam",
        "kind": "broll", "added_at": m["sources"][0]["added_at"],
    })
    _assert_intake_warns_handoff_rejects(m, "nested inside")


def test_warn_not_reject_nested_source_with_same_kind(tmp_path):
    """Contract §4.2: nested same-kind sources warn (double-counted), never
    reject."""
    m = _make_example_manifest(tmp_path)
    outer = Path(m["sources"][1]["path"])
    nested = outer / "more_broll"
    nested.mkdir(parents=True, exist_ok=True)
    m["sources"].append({
        "id": "broll-more-broll-01", "path": str(nested),
        "display_name": "more_broll", "kind": "broll",
        "added_at": m["sources"][1]["added_at"],
    })
    handoff = M.validate_manifest(m, mode="handoff")
    assert handoff.ok, f"nested same-kind must only warn: {handoff.errors}"
    assert any("nested inside" in w and "same kind" in w for w in handoff.warnings)


def test_sibling_path_with_shared_prefix_is_not_nesting(tmp_path):
    """`/proj/A-roll` must not read as nested inside `/proj/A` — nesting is
    compared as path parts, not string prefixes."""
    root = tmp_path / "proj"
    a = root / "A"
    a_roll = root / "A-roll"
    a.mkdir(parents=True, exist_ok=True)
    a_roll.mkdir(parents=True, exist_ok=True)
    assert M._nesting_pair(str(a_roll), str(a)) == (None, None)
    assert M._nesting_pair(str(a), str(a_roll)) == (None, None)
    assert M._nesting_pair(str(a / "inner"), str(a)) == (str(a / "inner"), str(a))


def test_person_id_collision_starts_at_02(tmp_path):
    """The bare slug is implicitly 01, so the first collision is -02 —
    matching _mint_person_id's documented scheme (code review caught the
    implementation returning -01 while the docstring promised -02)."""
    assert M._mint_person_id("Sam", []) == "sam"
    assert M._mint_person_id("Sam", ["sam"]) == "sam-02"
    assert M._mint_person_id("Sam", ["sam", "sam-02"]) == "sam-03"


def test_reject_kind_outside_enum(tmp_path):
    m = _make_example_manifest(tmp_path)
    m["sources"][0]["kind"] = "vroll"
    _assert_intake_warns_handoff_rejects(m, "is not a recognized enum value")


def test_reject_delivery_target_bad_aspect_key(tmp_path):
    m = _make_example_manifest(tmp_path)
    m["delivery_targets"] = [{
        "id": "dt-test-01", "label": "Test", "aspect_key": "aspect_nonsense",
        "status": "proposed",
    }]
    _assert_intake_warns_handoff_rejects(m, "aspect_key")


def test_reject_delivery_target_aspect_not_allowed_for_platform(tmp_path):
    m = _make_example_manifest(tmp_path)
    m["delivery_targets"] = [{
        "id": "dt-test-01", "label": "Test",
        "aspect_key": "aspect_horizontal_16_9",
        "platform_key": "platform_ig_reels",  # only allows vertical 9:16
        "status": "proposed",
    }]
    _assert_intake_warns_handoff_rejects(m, "not allowed for platform_key")


def test_reject_brand_brief_card_outside_assets_dir(tmp_path):
    m = _make_example_manifest(tmp_path)
    m["brand"]["brief"]["card_png_path"] = "../../etc/outside.png"
    _assert_intake_warns_handoff_rejects(m, "co-location rule")


def test_reject_interview_with_no_aroll_source(tmp_path):
    m = _make_example_manifest(tmp_path)
    m["sources"] = [s for s in m["sources"] if s["kind"] != "aroll"]
    _assert_intake_warns_handoff_rejects(m, "interview")


def test_reject_interview_with_aroll_but_zero_video_files(tmp_path):
    m = _make_example_manifest(tmp_path)
    for s in m["sources"]:
        if s["kind"] == "aroll":
            s["media"]["video_count"] = 0
    _assert_intake_warns_handoff_rejects(m, "interview")


def test_non_interview_project_type_tolerates_no_aroll(tmp_path):
    """The project_type-conditional rule (contract §4.1 rule 9) is scoped
    to project_type=='interview' — a property_tour with no A-roll at all
    must not trip it."""
    m = _make_example_manifest(tmp_path)
    m["project"]["project_type"] = "property_tour"
    m["sources"] = [s for s in m["sources"] if s["kind"] != "aroll"]
    handoff = M.validate_manifest(m, mode="handoff")
    assert not any("interview" in e for e in handoff.errors)


# ---------------------------------------------------------------------------
# Validation: WARN rules
# ---------------------------------------------------------------------------

def test_warn_dual_use_on_non_aroll_source(tmp_path):
    m = _make_example_manifest(tmp_path)
    m["sources"][1]["dual_use"] = True  # sources[1] is broll
    handoff = M.validate_manifest(m, mode="handoff")
    assert handoff.ok
    assert any("dual_use" in w for w in handoff.warnings)


def test_warn_inference_disagreement(tmp_path):
    m = _make_example_manifest(tmp_path)
    m["sources"][0]["inference"]["agrees_with_declaration"] = False
    handoff = M.validate_manifest(m, mode="handoff")
    assert handoff.ok
    assert any("disagree" in w for w in handoff.warnings)


def test_warn_font_not_installed(tmp_path):
    m = _make_example_manifest(tmp_path)
    handoff = M.validate_manifest(m, mode="handoff")
    assert any("Gilroy" in w for w in handoff.warnings)


def test_warn_brand_absent(tmp_path):
    m = _make_example_manifest(tmp_path)
    del m["brand"]
    handoff = M.validate_manifest(m, mode="handoff")
    assert handoff.ok
    assert any("brand is absent" in w for w in handoff.warnings)


def test_warn_delivery_targets_empty(tmp_path):
    m = _make_example_manifest(tmp_path)
    handoff = M.validate_manifest(m, mode="handoff")
    assert any("delivery_targets" in w for w in handoff.warnings)


def test_warn_source_audio_with_no_aroll(tmp_path):
    m = _make_example_manifest(tmp_path)
    audio_dir = tmp_path / "Audio - Lav"
    audio_dir.mkdir()
    m["sources"] = [s for s in m["sources"] if s["kind"] != "aroll"]
    m["project"]["project_type"] = "event"
    M.add_source(m, path=str(audio_dir), display_name="Audio - Lav", kind="source_audio")
    handoff = M.validate_manifest(m, mode="handoff")
    assert any("no aroll source to sync" in w for w in handoff.warnings)


def test_warn_zero_media_files(tmp_path):
    m = _make_example_manifest(tmp_path)
    m["sources"][1]["media"] = {
        "video_count": 0, "audio_count": 0, "image_count": 0,
        "other_count": 0, "total_bytes": 0,
    }
    handoff = M.validate_manifest(m, mode="handoff")
    assert any("zero media files" in w for w in handoff.warnings)


def test_warn_unsupported_files_use_verbatim_reason(tmp_path):
    m = _make_example_manifest(tmp_path)
    handoff = M.validate_manifest(m, mode="handoff")
    assert any(
        "2 .lrf file(s) skipped (unsupported extension; expected audio, "
        "video, or image)." in w
        for w in handoff.warnings
    )


# ---------------------------------------------------------------------------
# Unsupported-file categorization (contract §4.3) — verbatim reuse
# ---------------------------------------------------------------------------

def test_categorize_unsupported_reuses_harvested_reason_verbatim():
    from posthouse.harvest.auto_include import unsupported_reason
    entry = M.categorize_unsupported(".pdf", 3)
    assert entry["category"] == "document"
    harvested = unsupported_reason(Path("x.pdf"))
    assert harvested in entry["reason"]


def test_categorize_unsupported_lut_category():
    entry = M.categorize_unsupported(".cube", 1)
    assert entry["category"] == "lut"


def test_categorize_unsupported_text_category():
    entry = M.categorize_unsupported(".txt", 1)
    assert entry["category"] == "text"


def test_categorize_unsupported_layered_image_category():
    entry = M.categorize_unsupported(".psd", 1)
    assert entry["category"] == "layered_image"


def test_categorize_unsupported_unknown_extension_generic_message():
    entry = M.categorize_unsupported(".lrf", 2)
    assert entry["category"] == "unknown_extension"
    assert entry["reason"] == (
        "2 .lrf file(s) skipped (unsupported extension; expected audio, "
        "video, or image)."
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_validate_intake_exits_zero_even_with_warnings(tmp_path):
    m = _make_example_manifest(tmp_path)
    m["sources"][0]["path"] = str(tmp_path / "gone")  # would be fatal at handoff
    path = tmp_path / "manifest.json"
    M.save_manifest(m, path)

    result = subprocess.run(
        [sys.executable, "-m", "posthouse.manifest", "validate", str(path), "--mode", "intake"],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
        env={**os.environ},
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"


def test_cli_validate_handoff_exits_nonzero_and_prints_all_errors(tmp_path):
    m = _make_example_manifest(tmp_path)
    m["sources"][0]["path"] = str(tmp_path / "gone")
    m["sources"][0]["id"] = "BAD ID"
    path = tmp_path / "manifest.json"
    M.save_manifest(m, path)

    result = subprocess.run(
        [sys.executable, "-m", "posthouse.manifest", "validate", str(path), "--mode", "handoff"],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
        env={**os.environ},
    )
    assert result.returncode != 0
    assert "does not exist" in result.stderr
    assert "pattern" in result.stderr


def test_cli_validate_handoff_exits_zero_on_a_clean_manifest(tmp_path):
    m = _make_example_manifest(tmp_path)
    path = tmp_path / "manifest.json"
    M.save_manifest(m, path)

    result = subprocess.run(
        [sys.executable, "-m", "posthouse.manifest", "validate", str(path), "--mode", "handoff"],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
        env={**os.environ},
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"


def test_cli_exits_nonzero_on_bad_json(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("{ not json", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "posthouse.manifest", "validate", str(path)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
        env={**os.environ},
    )
    assert result.returncode != 0
    assert result.stderr.strip()
