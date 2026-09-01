"""Tests for posthouse.brandbrief — the Brand Brief generator.

Extends the Phase 0 safety net the same way test_manifest.py and
test_coldfootage.py do: hermetic (fixtures built programmatically in
tmp_path, no committed binary fonts/images), same BLESS=1 golden-master
mechanism for `build_brand_section`'s JSON output. `posthouse` is
imported directly as a sibling top-level package, per the "run pytest
from the repo root" convention `safety_net/run_safety_net.sh` uses.

Fixture fonts are built with fontTools' FontBuilder rather than committed
as binary files (same reasoning as the fixture media README: hermetic,
diffable, no binary blobs in the repo). Fixture logos are built with PIL.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from posthouse import brandbrief as B

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = Path(__file__).parent.parent / "golden" / "expected_brand_section.json"
ACTUAL_PATH = Path(__file__).parent.parent / "golden" / "actual_brand_section.json"

_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _make_font(path: Path, family: str, style: str = "Regular") -> None:
    """A tiny, valid TTF built with fontTools' FontBuilder — no committed
    binary font file needed."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef", "A"])
    fb.setupCharacterMap({65: "A"})
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((0, 500))
    pen.lineTo((500, 500))
    pen.lineTo((500, 0))
    pen.closePath()
    glyph = pen.glyph()
    fb.setupGlyf({".notdef": glyph, "A": glyph})
    fb.setupHorizontalMetrics({".notdef": (600, 0), "A": (600, 0)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    ps_name = f"{family}-{style}".replace(" ", "")
    fb.setupNameTable({
        "familyName": family, "styleName": style, "psName": ps_name,
        "uniqueFontIdentifier": f"{ps_name};1.0", "fullName": f"{family} {style}",
        "version": "1.0",
    })
    fb.setupOS2()
    fb.setupPost()
    fb.save(str(path))


def _make_logo_with_alpha(path: Path) -> None:
    """100x100 RGBA: left half blue, right half red, one fully-transparent
    corner pixel (must be ignored by palette extraction)."""
    from PIL import Image

    im = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
    for x in range(50):
        for y in range(100):
            im.putpixel((x, y), (0, 0, 255, 255))
    im.putpixel((0, 0), (0, 255, 0, 0))  # fully transparent — must be ignored
    im.save(path)


def _make_logo_no_alpha(path: Path) -> None:
    from PIL import Image

    im = Image.new("RGB", (50, 50), (10, 20, 30))
    im.save(path)


def _make_assets_dir(root: Path) -> Path:
    assets = root / "Brand Assets"
    assets.mkdir()
    _make_font(assets / "Gilroy-Bold.otf", "Gilroy", "Bold")
    _make_logo_with_alpha(assets / "mendez-logo.png")
    _make_logo_no_alpha(assets / "wordmark.jpg")
    (assets / "Brand_Guidelines.pdf").write_bytes(b"%PDF-1.4 fake pdf content\n")
    (assets / "notes.txt").write_text("some brand notes\n", encoding="utf-8")
    return assets


# ---------------------------------------------------------------------------
# Font extraction: name-table path
# ---------------------------------------------------------------------------

def test_extract_font_info_name_table_path(tmp_path):
    font_path = tmp_path / "Gilroy-Bold.otf"
    _make_font(font_path, "Gilroy", "Bold")
    info = B.extract_font_info(font_path)
    assert info["family_name"] == "Gilroy"
    assert info["style_name"] == "Bold"
    assert info["postscript_name"] == "Gilroy-Bold"
    assert info["format"] == "otf"
    assert info["extracted_by"] == "name_table"


def test_extract_font_info_ttf_format(tmp_path):
    font_path = tmp_path / "SourceSerif-Regular.ttf"
    _make_font(font_path, "Source Serif 4", "Regular")
    info = B.extract_font_info(font_path)
    assert info["format"] == "ttf"
    assert info["family_name"] == "Source Serif 4"


# ---------------------------------------------------------------------------
# Font extraction: fallback path (corrupt/truncated font)
# ---------------------------------------------------------------------------

def test_extract_font_info_corrupt_font_falls_back_to_filename(tmp_path):
    font_path = tmp_path / "Totally-Broken-Bold.ttf"
    font_path.write_bytes(b"this is not a font file, just garbage bytes" * 5)
    info = B.extract_font_info(font_path)
    assert info["extracted_by"] == "filename"
    # style words stripped, family derived from what's left
    assert info["family_name"] == "Totally Broken"
    assert "style_name" not in info
    assert "postscript_name" not in info
    assert info["format"] == "ttf"


def test_extract_font_info_never_raises_on_corrupt_font(tmp_path):
    font_path = tmp_path / "empty.otf"
    font_path.write_bytes(b"")
    info = B.extract_font_info(font_path)  # must not raise
    assert info["extracted_by"] == "filename"
    assert info["family_name"]  # never empty/None


def test_build_brand_section_never_drops_a_font(tmp_path):
    """A corrupt font must still show up in fonts[] — degraded, not gone."""
    assets = tmp_path / "Brand Assets"
    assets.mkdir()
    _make_font(assets / "Good-Font.ttf", "Good Font")
    (assets / "Bad-Font.ttf").write_bytes(b"garbage" * 10)
    brand = B.build_brand_section(assets, font_search_dirs=[])
    files = {f["file"] for f in brand["fonts"]}
    assert files == {"Good-Font.ttf", "Bad-Font.ttf"}
    by_file = {f["file"]: f for f in brand["fonts"]}
    assert by_file["Good-Font.ttf"]["extracted_by"] == "name_table"
    assert by_file["Bad-Font.ttf"]["extracted_by"] == "filename"


# ---------------------------------------------------------------------------
# install_status
# ---------------------------------------------------------------------------

def test_install_status_installed_when_matching_font_present(tmp_path):
    search_dir = tmp_path / "fake_fonts"
    search_dir.mkdir()
    _make_font(search_dir / "Gilroy-Bold.otf", "Gilroy", "Bold")
    status = B.install_status("Gilroy", "Gilroy-Bold", search_dirs=[search_dir])
    assert status == "installed"


def test_install_status_not_installed_when_absent(tmp_path):
    search_dir = tmp_path / "fake_fonts_empty"
    search_dir.mkdir()
    status = B.install_status("Some Font", "Some-Font-Regular", search_dirs=[search_dir])
    assert status == "not_installed"


def test_install_status_unknown_when_no_readable_dirs(tmp_path):
    nonexistent = tmp_path / "does-not-exist-at-all"
    status = B.install_status("Anything", "Anything-Regular", search_dirs=[nonexistent])
    assert status == "unknown"


def test_install_status_unknown_with_no_name_to_check():
    status = B.install_status(None, None, search_dirs=[Path.home()])
    assert status == "unknown"


def test_install_status_matches_by_postscript_name_alone(tmp_path):
    search_dir = tmp_path / "fake_fonts2"
    search_dir.mkdir()
    _make_font(search_dir / "X.otf", "Totally Different Family", "Bold")
    # family differs, but postscript name matches exactly
    status = B.install_status(
        "Nonmatching Family", "TotallyDifferentFamily-Bold", search_dirs=[search_dir]
    )
    assert status == "installed"


# ---------------------------------------------------------------------------
# Palette determinism
# ---------------------------------------------------------------------------

def test_palette_extraction_is_deterministic_across_calls(tmp_path):
    logo = tmp_path / "logo.png"
    _make_logo_with_alpha(logo)
    p1 = B.extract_palette(logo)
    p2 = B.extract_palette(logo)
    assert p1 == p2


def test_palette_ordering_rule_descending_count_ascending_hex_tiebreak(tmp_path):
    """Blue covers more pixels (50x100=5000, minus one transparent corner
    pixel) than red (50x100=5000 minus 0) — actually equal counts except
    the transparent pixel is inside the blue half, so red (5000) ranks
    ahead of blue (4999). Assert the documented ordering rule directly."""
    logo = tmp_path / "logo.png"
    _make_logo_with_alpha(logo)
    palette = B.extract_palette(logo)
    hexes = [p["hex"] for p in palette]
    assert hexes == ["#FF0000", "#0000FF"]
    assert palette[0]["role"] == "primary"
    assert palette[1]["role"] == "secondary"


def test_palette_ignores_fully_transparent_pixels(tmp_path):
    from PIL import Image

    logo = tmp_path / "half_transparent.png"
    im = Image.new("RGBA", (10, 10), (0, 255, 0, 0))  # entirely transparent green
    for x in range(5):
        for y in range(10):
            im.putpixel((x, y), (200, 100, 50, 255))  # opaque orange-ish, half the image
    im.save(logo)
    palette = B.extract_palette(logo)
    # the fully-transparent green must never appear
    assert all(p["hex"] != "#00FF00" for p in palette)
    assert palette[0]["hex"] == "#C86432"


def test_palette_fully_transparent_image_returns_empty():
    from PIL import Image
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        logo = Path(d) / "invisible.png"
        im = Image.new("RGBA", (10, 10), (10, 20, 30, 0))
        im.save(logo)
        assert B.extract_palette(logo) == []


# ---------------------------------------------------------------------------
# has_alpha
# ---------------------------------------------------------------------------

def test_has_alpha_true_for_rgba_logo(tmp_path):
    logo = tmp_path / "alpha.png"
    _make_logo_with_alpha(logo)
    assert B.has_alpha(logo) is True


def test_has_alpha_false_for_rgb_logo(tmp_path):
    logo = tmp_path / "noalpha.jpg"
    _make_logo_no_alpha(logo)
    assert B.has_alpha(logo) is False


# ---------------------------------------------------------------------------
# Documents: harvested unsupported_reason, verbatim
# ---------------------------------------------------------------------------

def test_documents_carry_harvested_reason_verbatim(tmp_path):
    from posthouse.harvest.auto_include import unsupported_reason as harvested

    assets = _make_assets_dir(tmp_path)
    brand = B.build_brand_section(assets, font_search_dirs=[])
    by_file = {d["file"]: d for d in brand["documents"]}

    pdf_expected = harvested(Path("x.pdf"))
    txt_expected = harvested(Path("x.txt"))
    assert by_file["Brand_Guidelines.pdf"]["unsupported_reason"] == pdf_expected
    assert by_file["notes.txt"]["unsupported_reason"] == txt_expected
    assert by_file["Brand_Guidelines.pdf"]["summarized"] is False
    assert by_file["notes.txt"]["summarized"] is False


def test_document_kind_guess_from_filename(tmp_path):
    assets = tmp_path / "Brand Assets"
    assets.mkdir()
    (assets / "Brand_Guidelines_2026.pdf").write_bytes(b"%PDF fake")
    (assets / "voiceover_script.txt").write_text("hi")
    (assets / "vendor_contract.doc").write_bytes(b"fake doc")
    brand = B.build_brand_section(assets, font_search_dirs=[])
    by_file = {d["file"]: d["kind"] for d in brand["documents"]}
    assert by_file["Brand_Guidelines_2026.pdf"] == "brand_guidelines"
    assert by_file["voiceover_script.txt"] == "script"
    assert by_file["vendor_contract.doc"] == "contract"


# ---------------------------------------------------------------------------
# Co-location
# ---------------------------------------------------------------------------

def test_validate_brief_colocation_rejects_path_outside_assets_dir(tmp_path):
    assets = tmp_path / "Brand Assets"
    assets.mkdir()
    brand = {
        "brief": {
            "readme_path": "BRAND_README.txt",
            "card_png_path": "../../etc/outside.png",
            "bin_path": "Files/Brand",
            "marker_written": False,
        }
    }
    problems = B.validate_brief_colocation(brand, assets)
    assert problems
    assert any("card_png_path" in p and "co-location" in p for p in problems)


def test_validate_brief_colocation_accepts_generated_brief(tmp_path):
    assets = _make_assets_dir(tmp_path)
    brand = B.build_brand_section(assets, font_search_dirs=[])
    brand = B.generate_brief(brand, assets, client_name="Mendez Realty")
    assert B.validate_brief_colocation(brand, assets) == []


def test_validate_brief_colocation_no_brief_is_not_a_problem(tmp_path):
    assets = tmp_path / "Brand Assets"
    assets.mkdir()
    assert B.validate_brief_colocation({}, assets) == []


def test_generate_brief_writes_files_inside_assets_dir(tmp_path):
    assets = _make_assets_dir(tmp_path)
    brand = B.build_brand_section(assets, font_search_dirs=[])
    brand = B.generate_brief(brand, assets, client_name="Mendez Realty")
    readme = assets / brand["brief"]["readme_path"]
    card = assets / brand["brief"]["card_png_path"]
    assert readme.is_file()
    assert card.is_file()
    # both must resolve INSIDE assets_dir
    assert readme.resolve().is_relative_to(assets.resolve())
    assert card.resolve().is_relative_to(assets.resolve())


# ---------------------------------------------------------------------------
# README content
# ---------------------------------------------------------------------------

def test_readme_content(tmp_path):
    assets = _make_assets_dir(tmp_path)
    # An empty (but real/readable) search dir -> deterministic "not_installed",
    # as opposed to font_search_dirs=[] which means "no dirs to check" ->
    # "unknown" (see test_install_status_unknown_when_no_readable_dirs).
    empty_fonts_dir = tmp_path / "empty_fonts"
    empty_fonts_dir.mkdir()
    brand = B.build_brand_section(assets, font_search_dirs=[empty_fonts_dir])
    brand = B.generate_brief(brand, assets, client_name="Mendez Realty")
    text = (assets / brand["brief"]["readme_path"]).read_text(encoding="utf-8")

    assert "Mendez Realty" in text
    assert "Gilroy" in text
    assert "not_installed" in text
    assert "#FF0000" in text and "#0000FF" in text
    assert "Brand_Guidelines.pdf" in text
    assert "PDFs aren't importable as Premiere project items." in text
    assert "notes.txt" in text
    assert "Text files aren't importable as Premiere project items." in text
    assert "Reveal in Finder" in text
    # exactly one Generated: line, matching the normalizable-timestamp rule
    generated_lines = [l for l in text.splitlines() if l.startswith("Generated:")]
    assert len(generated_lines) == 1
    assert _TS_RE.search(generated_lines[0])


def test_generated_copy_contains_no_em_dashes(tmp_path):
    """The README and the card are DELIVERABLES: they land in Ryan's
    projects and get handed to human editors, which puts them under his
    published-copy rule (no em dashes in captions, on-screen text, or
    anything shipped). Repo docs and code comments are exempt; this
    generated text is not. Caught by eyeballing a real card, not by the
    original tests, so it gets an explicit guard here."""
    assets = _make_assets_dir(tmp_path)
    brand = B.build_brand_section(assets)
    brand = B.generate_brief(brand, assets, client_name="Mendez Realty")

    readme = (assets / brand["brief"]["readme_path"]).read_text(encoding="utf-8")
    assert "—" not in readme, "em dash in generated README copy"

    # The card's text lives in the module's own strings; assert the source
    # of the rendered copy is clean rather than OCRing a PNG. The function's
    # docstring is prose about the code, not copy that ships on the card, so
    # it's excluded — everything else in the body is fair game.
    import inspect
    card_src = inspect.getsource(B._render_card)
    doc = B._render_card.__doc__ or ""
    if doc:
        card_src = card_src.replace(doc, "")
    rendered_literals = re.findall(r'"([^"]*)"', card_src) + re.findall(r"'([^']*)'", card_src)
    offenders = [s for s in rendered_literals if "—" in s]
    assert not offenders, f"em dash in card copy: {offenders}"


def test_readme_and_card_list_documents_by_name_not_just_counts(tmp_path):
    """A card that says "DOCUMENTS: 1" tells an editor neither what is in
    the folder nor why it could not come into Premiere. Both artifacts
    must name the file and carry the harvested reason."""
    assets = _make_assets_dir(tmp_path)
    brand = B.build_brand_section(assets)
    brand = B.generate_brief(brand, assets, client_name="Mendez Realty")
    text = (assets / brand["brief"]["readme_path"]).read_text(encoding="utf-8")

    for doc in brand["documents"]:
        assert doc["file"] in text
        assert doc["unsupported_reason"] in text
    for logo in brand["logos"]:
        assert logo["file"] in text


def test_saturated_color_is_never_labeled_neutral(tmp_path):
    """Rank alone mislabels: on a small brand palette the least-frequent
    colour is usually the vivid accent. SoldFast's orange (#F4690B) came
    out as "neutral" before the saturation gate, which is visibly wrong on
    a card an editor reads."""
    from PIL import Image, ImageDraw

    logo = tmp_path / "brand.png"
    img = Image.new("RGBA", (100, 100), (3, 52, 89, 255))       # navy, dominant
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 99, 20], fill=(244, 105, 11, 255))       # orange, least frequent
    d.rectangle([0, 60, 99, 75], fill=(128, 128, 128, 255))     # true grey
    img.save(logo)

    by_hex = {p["hex"]: p["role"] for p in B.extract_palette(logo)}
    assert by_hex.get("#F4690B") != "neutral", by_hex
    assert by_hex.get("#808080") == "neutral", by_hex


def test_is_neutral_hex_classifies_greys_and_chromatics():
    assert B._is_neutral_hex("#000000")
    assert B._is_neutral_hex("#FFFFFF")
    assert B._is_neutral_hex("#7F7F7F")
    assert not B._is_neutral_hex("#F4690B")
    assert not B._is_neutral_hex("#0391D8")


# ---------------------------------------------------------------------------
# Brand-card PNG: structural properties, not a golden image
# ---------------------------------------------------------------------------

def test_card_png_structural_properties(tmp_path):
    from PIL import Image

    assets = _make_assets_dir(tmp_path)
    brand = B.build_brand_section(assets, font_search_dirs=[])
    brand = B.generate_brief(brand, assets, client_name="Mendez Realty")
    card_path = assets / brand["brief"]["card_png_path"]

    assert card_path.is_file()
    with Image.open(card_path) as im:
        assert im.size == (1920, 1080)
        assert im.mode == "RGB"
        colors = im.getcolors(maxcolors=1_000_000)
        assert colors is not None
        assert len(colors) > 1  # not blank


def test_card_png_byte_identical_across_consecutive_generations(tmp_path):
    """Determinism proof: same brand dict, same env, two generations ->
    identical bytes. Explicitly NOT a cross-version golden image (PIL/font
    rendering drifts across environments) — see module docstring."""
    assets = _make_assets_dir(tmp_path)
    brand = B.build_brand_section(assets, font_search_dirs=[])
    brand = B.generate_brief(brand, assets, client_name="Mendez Realty")
    card_path = assets / brand["brief"]["card_png_path"]
    first_bytes = card_path.read_bytes()

    # regenerate over the same file
    B._render_card(brand, card_path, "Mendez Realty")
    second_bytes = card_path.read_bytes()

    assert first_bytes == second_bytes


# ---------------------------------------------------------------------------
# Golden master: build_brand_section output
# ---------------------------------------------------------------------------

def _normalize_brand_text(raw_text: str, assets_dir: Path) -> str:
    text = raw_text.replace(str(assets_dir), "{ASSETS_DIR}")
    text = text.replace(str(assets_dir.resolve()), "{ASSETS_DIR}")
    return text


def test_brand_section_matches_golden_master(tmp_path):
    assets = _make_assets_dir(tmp_path)
    brand = B.build_brand_section(assets, font_search_dirs=[])
    raw = json.dumps(brand, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    normalized = _normalize_brand_text(raw, assets)

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
        fromfile="golden/expected_brand_section.json", tofile="actual (this run)",
        lineterm="",
    ))
    excerpt = "\n".join(diff[:60])
    raise AssertionError(
        f"build_brand_section output no longer matches the blessed golden master.\n"
        f"Full actual output written to {ACTUAL_PATH} for inspection.\n"
        f"First ~60 diff lines:\n{excerpt}"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_build_writes_readme_and_card_and_prints_json(tmp_path):
    assets = _make_assets_dir(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "posthouse.brandbrief", "build", str(assets),
         "--client", "Mendez Realty"],
        capture_output=True, text=True, timeout=60, cwd=str(REPO_ROOT),
        env={**os.environ},
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert (assets / "BRAND_README.txt").is_file()
    assert (assets / "brand-card.png").is_file()
    data = json.loads(result.stdout)
    assert data["assets_dir"] == str(assets)
    assert "brief" in data


def test_cli_build_writes_out_json(tmp_path):
    assets = _make_assets_dir(tmp_path)
    out_json = tmp_path / "brand.json"
    result = subprocess.run(
        [sys.executable, "-m", "posthouse.brandbrief", "build", str(assets),
         "--out-json", str(out_json)],
        capture_output=True, text=True, timeout=60, cwd=str(REPO_ROOT),
        env={**os.environ},
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert out_json.is_file()
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["fonts"]


def test_cli_exits_nonzero_on_nonexistent_assets_dir(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "posthouse.brandbrief", "build",
         str(tmp_path / "does-not-exist")],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
        env={**os.environ},
    )
    assert result.returncode != 0
    assert result.stderr.strip()
