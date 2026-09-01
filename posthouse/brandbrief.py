"""posthouse.brandbrief — the Brand Brief generator.

Phase 2, slice 2 (ROADMAP.md §6 Phase 2 item 3; contract
`docs/contracts/PROJECT_MANIFEST.md` §2.3). Fonts, PDFs, and plain
`.txt` files cannot ride FCP7 XML into Premiere (ROADMAP.md §7
"Fonts"; contract §4.3 "text"/"document" categories). This module
bridges that gap three ways, all rooted in one folder:

1. ``BRAND_README.txt`` — a plain-text brief, written inside
   ``assets_dir``.
2. A rendered ``brand-card.png`` — 1920x1080, importable into Premiere
   and readable in the source monitor (the "text file in Premiere," as
   an image), written **inside** ``assets_dir``.
3. The structured ``brand`` dict this module builds
   (:func:`build_brand_section`), ready to drop into the Project
   Manifest — the searchable-metadata half of the brief.

**The co-location rule is load-bearing, not cosmetic** (contract §2.3,
ROADMAP.md's Brand Brief Decision Log entry): the card and README are
written *inside* ``assets_dir``, physically alongside the fonts, PDFs,
and logos they describe, and nowhere else. Right-click the card in
Premiere's project panel -> "Reveal in Finder" is the entire mechanism
by which a human editor discovers the non-importable assets; a card
copied anywhere else breaks that silently. :func:`validate_brief_colocation`
enforces this as code, matching ``posthouse.manifest``'s rule 8 exactly
(a `card_png_path` that resolves outside `assets_dir` is a validation
failure, not a warning).

Deterministic extraction, not guessing
---------------------------------------
* **Fonts** — parsed via ``fontTools`` TTF/OTF `name` tables
  (:func:`extract_font_info`). ``family_name`` prefers nameID 16
  (Typographic Family) over 1, ``style_name`` prefers 17 over 2,
  ``postscript_name`` is nameID 6. ``extracted_by`` is ``"name_table"``
  on success; a font whose table can't be read (or that yields no usable
  family name) falls back to ``"filename"``, deriving a best-effort
  family by stripping common style words (Bold, Italic, Regular, ...)
  from the stem — see :func:`_family_from_filename`. A font is **never**
  silently dropped for being unparseable.
* **`install_status`** (:func:`install_status`) — a plain directory scan
  of the real macOS font locations (``~/Library/Fonts``,
  ``/Library/Fonts``, ``/System/Library/Fonts``), matching by family or
  postscript name against every font file found there (also parsed via
  `fontTools`, so a same-named-but-different-format match still counts).
  No `fc-list` or other subprocess — a directory scan is deterministic
  and sufficient. Returns ``"unknown"`` when the check itself can't run
  (non-macOS, no readable directory) rather than guessing.
* **Palette** (:func:`extract_palette`) — PIL, ignoring fully-transparent
  pixels, quantized with **fixed** parameters (`method=MEDIANCUT`,
  `dither=NONE`, no k-means) so the same logo always produces the same
  colors in the same order. Order is **descending pixel count, ties
  broken by ascending hex** — documented here because it's the whole
  determinism story. Roles: any colour whose HLS saturation is at or
  below :data:`NEUTRAL_SATURATION_MAX` is `neutral` (greys, near-blacks,
  near-whites); the remaining chromatic colours take `primary`,
  `secondary`, `accent` in rank order, with further ones also `accent`.
  Saturation gates neutral rather than rank alone, because on a small
  brand palette the *least frequent* colour is usually the vivid accent
  (labelling SoldFast's orange "neutral" was the bug that prompted this).
  Still a **starting point**, not an authoritative brand read; Ryan
  corrects it, same posture as `inference.agrees_with_declaration`
  elsewhere in the contract.
* **`has_alpha`** — a real PIL check (`mode` and `.info["transparency"]`),
  not a filename guess.
* **`logos[].kind` / `documents[].kind`** — best-effort from the
  filename only (contract allows this); never used for anything
  validation-critical.
* **`documents[].unsupported_reason`** — the harvested
  ``auto_include.unsupported_reason()`` string, **verbatim**, the same
  discipline ``posthouse.manifest.categorize_unsupported`` already
  follows for the same reason: PreCut and the Post House must give Ryan
  the same sentence.

Out of scope for this slice (see the module's task brief): PDF
summarization (`documents[].summarized` is always `False` — no LLM call
here), the frame-0 creative-brief marker (`brief.marker_written` is
always `False` from this module — that is written by the exporter,
Door 3, not here).

Entry points
------------
* Python API: :func:`build_brand_section`, :func:`generate_brief`,
  :func:`validate_brief_colocation`, plus the extraction primitives
  (:func:`extract_font_info`, :func:`install_status`,
  :func:`extract_palette`, :func:`has_alpha`).
* CLI: ``python -m posthouse.brandbrief build <assets_dir> [--client NAME]
  [--out-json PATH]`` — writes the README + card, prints the brand
  section as JSON, exits non-zero with every co-location problem listed
  on failure.
"""
from __future__ import annotations

import argparse
import colorsys
import functools
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .harvest.auto_include import unsupported_reason as _harvested_unsupported_reason

# ---------------------------------------------------------------------------
# Extension tables
# ---------------------------------------------------------------------------

FONT_EXTS = {".ttf": "ttf", ".otf": "otf", ".woff2": "woff2"}
# Raster formats PIL can open directly — deliberately excludes .svg (PIL
# cannot open it, and a "real check via PIL mode/transparency" is not
# possible without opening it). An .svg logo simply isn't classified by
# this slice; it is not silently mis-reported, it is out of scope.
LOGO_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif"}
# Documents this slice bridges via the Brand Brief — the contract §4.3
# "document" and "text" categories, the two that get an
# auto_include.unsupported_reason() entry every time.
DOCUMENT_EXTS = {".pdf", ".doc", ".docx", ".txt", ".rtf"}

DEFAULT_PALETTE_COLORS = 5
PALETTE_ROLE_BY_RANK = ["primary", "secondary", "accent"]  # chromatic rank >= 3 -> "accent"
# Below this HLS saturation a colour is treated as neutral (grey/black/white/
# beige) regardless of how often it appears, so a vivid accent is never
# labelled "neutral" just for being the least common colour in the logo.
NEUTRAL_SATURATION_MAX = 0.15

CARD_WIDTH = 1920
CARD_HEIGHT = 1080
# Cap per list on the card so a folder with 40 logos can't overflow the
# frame. The README is the complete inventory; the card is the summary an
# editor reads at source-monitor size.
_CARD_MAX_LIST = 6

_STYLE_WORDS = {
    "bold", "italic", "regular", "light", "medium", "semibold", "semi-bold",
    "extrabold", "extra-bold", "black", "thin", "heavy", "oblique", "book",
    "condensed", "extended", "narrow", "roman", "demibold", "demi-bold",
}


class BrandBriefError(Exception):
    """Base class for Brand Brief build/generate failures."""


# ---------------------------------------------------------------------------
# Fonts — extraction (contract §2.3 fonts[])
# ---------------------------------------------------------------------------

def _family_from_filename(font_path: Path) -> str:
    """Best-effort family name from a font's filename, used only when the
    `name` table can't be read. Strips common style words from the stem
    (split on `-`/`_`/space) and rejoins what's left. Never authoritative —
    it's the documented fallback, not a second extraction method."""
    stem = font_path.stem
    parts = [p for p in re.split(r"[-_ ]+", stem) if p]
    keep = [p for p in parts if p.lower() not in _STYLE_WORDS]
    if not keep:
        keep = parts
    family = " ".join(keep).strip()
    return family or stem


def extract_font_info(font_path: Path) -> dict:
    """Parse one font file into the extraction-half of a contract §2.3
    `fonts[]` entry: `family_name`, `style_name`?, `postscript_name`?,
    `format`, `extracted_by`. Does NOT include `file` or `install_status` —
    the caller (:func:`build_brand_section`) adds those, so this function
    stays a pure "read this file" primitive testable on its own.

    Deterministic, exhaustive-not-fail-fast at the single-font level: a
    font that can't be parsed (corrupt, truncated, unsupported table
    layout) never raises and never disappears — it degrades to
    `extracted_by: "filename"` with a best-effort family derived from the
    filename (:func:`_family_from_filename`).
    """
    font_path = Path(font_path)
    ext = font_path.suffix.lower()
    fmt = FONT_EXTS.get(ext, ext.lstrip("."))

    family: Optional[str] = None
    style: Optional[str] = None
    postscript: Optional[str] = None
    extracted_by = "filename"

    try:
        from fontTools.ttLib import TTFont

        tt = TTFont(str(font_path), lazy=True, fontNumber=0)
        name_table = tt["name"]
        family = name_table.getDebugName(16) or name_table.getDebugName(1)
        if not family:
            raise ValueError("name table has no usable family name (nameID 1/16)")
        style = name_table.getDebugName(17) or name_table.getDebugName(2)
        postscript = name_table.getDebugName(6)
        extracted_by = "name_table"
    except Exception:
        family = _family_from_filename(font_path)
        style = None
        postscript = None
        extracted_by = "filename"

    entry: dict = {"family_name": family}
    if style:
        entry["style_name"] = style
    if postscript:
        entry["postscript_name"] = postscript
    entry["format"] = fmt
    entry["extracted_by"] = extracted_by
    return entry


# ---------------------------------------------------------------------------
# Fonts — install_status (contract §2.3 fonts[].install_status)
# ---------------------------------------------------------------------------

def default_font_search_dirs() -> list[Path]:
    """The real macOS font locations, in the order they're searched.
    Only meaningful on macOS — see :func:`install_status`."""
    return [
        Path.home() / "Library" / "Fonts",
        Path("/Library/Fonts"),
        Path("/System/Library/Fonts"),
    ]


@functools.lru_cache(maxsize=16)
def _index_installed_fonts(search_dirs: tuple[str, ...]) -> frozenset[tuple[str, str]]:
    """Scan `search_dirs` (a hashable tuple, for lru_cache) once and return
    a set of (lowercased family, lowercased postscript name) pairs for
    every font file found. A directory scan, per the task brief — no
    `fc-list` or other subprocess. Any single font that can't be parsed
    is skipped, not fatal to the scan; a directory that can't be listed
    is likewise skipped."""
    from fontTools.ttLib import TTFont

    names: set[tuple[str, str]] = set()
    for d in search_dirs:
        p = Path(d)
        try:
            entries = sorted(p.iterdir())
        except OSError:
            continue
        for f in entries:
            if not f.is_file() or f.suffix.lower() not in FONT_EXTS:
                continue
            try:
                tt = TTFont(str(f), lazy=True, fontNumber=0)
                nt = tt["name"]
                fam = (nt.getDebugName(16) or nt.getDebugName(1) or "").strip().lower()
                ps = (nt.getDebugName(6) or "").strip().lower()
            except Exception:
                continue
            if fam or ps:
                names.add((fam, ps))
    return frozenset(names)


def install_status(
    family_name: Optional[str],
    postscript_name: Optional[str],
    search_dirs: Optional[list] = None,
) -> str:
    """`"installed"` / `"not_installed"` / `"unknown"` (contract §2.3).

    `search_dirs` is exposed as a parameter (rather than hardcoding
    :func:`default_font_search_dirs` unconditionally) so tests can point
    this at a fixture directory instead of Ryan's real, arbitrarily-
    populated system font folders — the real dirs are still the default
    when `search_dirs` is omitted, on macOS. Returns `"unknown"` — never
    guesses — when the check itself can't be performed: non-macOS with no
    explicit `search_dirs`, or every candidate directory unreadable/
    nonexistent (which is also how a test can force `"unknown"`
    deterministically: pass a `search_dirs` list that doesn't exist).
    """
    if search_dirs is None:
        if sys.platform != "darwin":
            return "unknown"
        search_dirs = default_font_search_dirs()

    readable = sorted({str(Path(d)) for d in search_dirs if Path(d).is_dir()})
    if not readable:
        return "unknown"

    try:
        index = _index_installed_fonts(tuple(readable))
    except Exception:
        return "unknown"

    fam = (family_name or "").strip().lower()
    ps = (postscript_name or "").strip().lower()
    if not fam and not ps:
        return "unknown"

    for idx_fam, idx_ps in index:
        if (fam and idx_fam == fam) or (ps and idx_ps == ps):
            return "installed"
    return "not_installed"


# ---------------------------------------------------------------------------
# Palette (contract §2.3 palette[])
# ---------------------------------------------------------------------------

def _is_neutral_hex(hexcode: str) -> bool:
    """True for greys, near-blacks, near-whites and other desaturated
    colours (HLS saturation <= :data:`NEUTRAL_SATURATION_MAX`)."""
    r = int(hexcode[1:3], 16) / 255.0
    g = int(hexcode[3:5], 16) / 255.0
    b = int(hexcode[5:7], 16) / 255.0
    _h, _l, s = colorsys.rgb_to_hls(r, g, b)
    return s <= NEUTRAL_SATURATION_MAX


def extract_palette(logo_path: Path, max_colors: int = DEFAULT_PALETTE_COLORS) -> list[dict]:
    """Deterministic palette extraction from one logo image.

    **The ordering/determinism rule, stated once, here:** fully-
    transparent pixels (alpha == 0) are dropped before quantization;
    quantization uses `PIL.Image.quantize(colors=max_colors,
    method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)` — fixed
    parameters, no k-means, no dithering, so the same logo always
    quantizes to the same palette. Colors are then sorted by **descending
    pixel count, ties broken by ascending hex string** — a documented,
    arbitrary-but-stable rule chosen specifically so two extractions of
    the same file are byte-identical.

    Role assignment (`primary`/`secondary`/`accent`/`neutral`) is by rank
    in that sorted order — rank 0 is `primary`, 1 `secondary`, 2 `accent`,
    everything else `neutral`. **This is a heuristic starting point Ryan
    can correct, never claimed as an authoritative brand read** — most-
    frequent-in-the-logo is not always most-important-to-the-brand (a
    logo's background fill often outnumbers its accent color).

    Returns `[]` if the image is fully transparent (nothing to extract).
    """
    from PIL import Image

    logo_path = Path(logo_path)
    with Image.open(logo_path) as im:
        rgba = im.convert("RGBA")
        pixels = list(rgba.getdata())

    opaque = [(r, g, b) for (r, g, b, a) in pixels if a > 0]
    if not opaque:
        return []

    sample = Image.new("RGB", (len(opaque), 1))
    sample.putdata(opaque)
    quantized = sample.quantize(
        colors=max_colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
    )
    counts = quantized.getcolors(maxcolors=max_colors)
    palette_table = quantized.getpalette() or []

    ranked: list[tuple[int, str]] = []
    for count, idx in counts:
        r = palette_table[idx * 3]
        g = palette_table[idx * 3 + 1]
        b = palette_table[idx * 3 + 2]
        hexcode = "#{:02X}{:02X}{:02X}".format(r, g, b)
        ranked.append((count, hexcode))

    ranked.sort(key=lambda t: (-t[0], t[1]))  # descending count, ascending hex tiebreak

    source = f"logo:{logo_path.name}"
    entries = []
    chromatic_rank = 0
    for _count, hexcode in ranked:
        # Rank alone mislabels: on a four-colour logo the least-frequent
        # colour is often the vivid accent, and calling a saturated orange
        # "neutral" is visibly wrong to anyone reading the card. Saturation
        # decides neutral-vs-chromatic; rank then orders the chromatic ones.
        # Still a heuristic Ryan corrects, just not an obviously silly one.
        if _is_neutral_hex(hexcode):
            role = "neutral"
        else:
            role = (
                PALETTE_ROLE_BY_RANK[chromatic_rank]
                if chromatic_rank < len(PALETTE_ROLE_BY_RANK)
                else "accent"
            )
            chromatic_rank += 1
        entries.append({"hex": hexcode, "role": role, "source": source})
    return entries


# ---------------------------------------------------------------------------
# Logos (contract §2.3 logos[])
# ---------------------------------------------------------------------------

def has_alpha(image_path: Path) -> bool:
    """Real transparency check via PIL — mode or an explicit `transparency`
    info key (paletted images with a transparent index carry it there
    instead of in the mode)."""
    from PIL import Image

    with Image.open(image_path) as im:
        if im.mode in ("RGBA", "LA", "PA"):
            return True
        return "transparency" in im.info


def _guess_logo_kind(filename: str) -> str:
    """Best-effort from the filename only (contract §2.3 allows this) —
    never validation-critical. `"wordmark"` and `"mark"` checked before the
    `"primary"` default; `"alt"` catches anything explicitly marked as a
    secondary variant."""
    name = filename.lower()
    if "wordmark" in name:
        return "wordmark"
    if "mark" in name:
        return "mark"
    if "alt" in name:
        return "alt"
    return "primary"


# ---------------------------------------------------------------------------
# Documents (contract §2.3 documents[])
# ---------------------------------------------------------------------------

def _guess_document_kind(filename: str) -> str:
    """Best-effort from the filename only (contract §2.3 allows this)."""
    name = filename.lower()
    if "guideline" in name or "brand" in name:
        return "brand_guidelines"
    if "script" in name:
        return "script"
    if "contract" in name:
        return "contract"
    return "other"


# ---------------------------------------------------------------------------
# build_brand_section — the top-level extraction pass
# ---------------------------------------------------------------------------

def build_brand_section(
    assets_dir,
    *,
    font_search_dirs: Optional[list] = None,
) -> dict:
    """Scan `assets_dir` and build a contract §2.3-shaped `brand` dict,
    ready to drop into a manifest (minus `brief`, which
    :func:`generate_brief` adds once the README/card are actually
    written, and `library_ref`, reserved/unused in v1).

    Every `file` value in the result is POSIX-relative to `assets_dir`
    (contract §2.3: "All `file` values below are relative to it"). Files
    are found with a recursive scan (`assets_dir` may organize fonts/
    logos/documents into subfolders) and processed in sorted path order
    so the result is deterministic regardless of filesystem iteration
    order.

    `font_search_dirs` is forwarded to :func:`install_status` for every
    font found — see its docstring for why this is a parameter rather
    than always using the real system directories.
    """
    assets_dir = Path(assets_dir)
    if not assets_dir.is_dir():
        raise BrandBriefError(f"assets_dir {assets_dir!r} is not a directory")

    all_files = sorted(p for p in assets_dir.rglob("*") if p.is_file())

    fonts: list[dict] = []
    logos: list[dict] = []
    documents: list[dict] = []

    for f in all_files:
        rel = f.relative_to(assets_dir).as_posix()
        ext = f.suffix.lower()

        if ext in FONT_EXTS:
            info = extract_font_info(f)
            status = install_status(
                info.get("family_name"), info.get("postscript_name"), font_search_dirs
            )
            fonts.append({"file": rel, **info, "install_status": status})

        elif ext in LOGO_EXTS:
            logos.append({
                "file": rel,
                "kind": _guess_logo_kind(f.name),
                "has_alpha": has_alpha(f),
            })

        elif ext in DOCUMENT_EXTS:
            doc: dict = {"file": rel, "kind": _guess_document_kind(f.name)}
            reason = _harvested_unsupported_reason(f)
            if reason:
                doc["unsupported_reason"] = reason
            doc["summarized"] = False  # PDF/doc summarization is not in this slice
            documents.append(doc)

    # Palette source: the first "primary"-kind logo (source-order stable,
    # since `logos` was built from the sorted file scan), falling back to
    # the first logo of any kind if none is marked primary, or [] if there
    # are no logos at all. Documented starting point, not a brand-file
    # confirmation from Ryan.
    palette: list[dict] = []
    primary_logos = [l for l in logos if l["kind"] == "primary"]
    palette_source = primary_logos[0] if primary_logos else (logos[0] if logos else None)
    if palette_source is not None:
        palette = extract_palette(assets_dir / palette_source["file"])

    return {
        "assets_dir": str(assets_dir),
        "fonts": fonts,
        "palette": palette,
        "logos": logos,
        "documents": documents,
    }


# ---------------------------------------------------------------------------
# generate_brief — README + brand-card PNG (the two artifacts on disk)
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _install_instruction(font_format: str) -> str:
    if font_format == "otf" or font_format == "ttf":
        return "double-click the font file in this folder, then click 'Install Font' (or drag it into Font Book)."
    return "install this font file through Font Book or your OS's font manager."


def _write_readme(brand: dict, path: Path, client_name: Optional[str], generated_at: str) -> None:
    """Plain-text README, written deterministically: every list below is
    already in the sorted-scan order :func:`build_brand_section` produced,
    and the ONLY non-reproducible content is the single `Generated:` line
    (tests normalize that one line, same technique
    `test_manifest.py` uses for manifest timestamps)."""
    # NOTE: no em dashes anywhere in generated output. The README and the
    # card are DELIVERABLES that land in Ryan's projects and get handed to
    # human editors, which puts them under his published-copy rule (see
    # the global style rules: em dashes are the most-recognized AI tell).
    # Internal code comments and repo docs are exempt; this text is not.
    title = f"BRAND BRIEF: {client_name or 'Unnamed Client'}"
    lines: list[str] = [title, "=" * len(title), f"Generated: {generated_at}", ""]

    lines.append("FONTS")
    lines.append("-----")
    fonts = brand.get("fonts") or []
    if fonts:
        for f in fonts:
            style = f" {f['style_name']}" if f.get("style_name") else ""
            lines.append(
                f"  - {f.get('family_name', '?')}{style} "
                f"({f.get('format', '?')}): {f.get('install_status', 'unknown')} "
                f"[{f.get('file')}]"
            )
            if f.get("install_status") != "installed":
                lines.append(f"      Install: {_install_instruction(f.get('format', ''))}")
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("PALETTE")
    lines.append("-------")
    palette = brand.get("palette") or []
    if palette:
        for p in palette:
            lines.append(f"  - {p['hex']}, {p['role']} (from {p['source']})")
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("LOGOS")
    lines.append("-----")
    logos = brand.get("logos") or []
    if logos:
        for l in logos:
            alpha_note = ", has transparency" if l.get("has_alpha") else ""
            lines.append(f"  - {l['file']} ({l['kind']}){alpha_note}")
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("DOCUMENTS (cannot be imported into Premiere directly)")
    lines.append("-------------------------------------------------------")
    documents = brand.get("documents") or []
    if documents:
        for d in documents:
            reason = d.get("unsupported_reason") or "no reason on record"
            lines.append(f"  - {d['file']} ({d['kind']}): {reason}")
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append(
        "All files above live in this folder, next to this README and the "
        "brand-card.png. In Premiere, right-click the brand card and choose "
        "'Reveal in Finder' to open it."
    )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_system_font(size: int):
    """Best-effort system font for the brand card, degrading gracefully.

    Tries a short, fixed list of common macOS system font paths (in
    order), falling back to `PIL.ImageFont.load_default()` — a small
    built-in bitmap font — if none of them are present. This never
    raises: a missing system font produces an uglier card, not a crash.
    """
    from PIL import ImageFont

    candidates = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/SFNSText.ttf",
        "/System/Library/Fonts/SFNS.ttf",
        "/Library/Fonts/Arial.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    for c in candidates:
        if Path(c).is_file():
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        # Older Pillow: load_default() takes no size argument.
        return ImageFont.load_default()


def _render_card(brand: dict, path: Path, client_name: Optional[str]) -> None:
    """Render the 1920x1080 brand-card PNG. Deliberately carries NO
    timestamp or other non-reproducible content — unlike the README, the
    card must be byte-identical across consecutive generations from the
    same `brand` dict (see the test suite's determinism assertion), and a
    baked-in wall-clock string would break that."""
    from PIL import Image, ImageDraw

    bg = (18, 24, 32)
    fg = (240, 240, 240)
    accent = (90, 170, 230)
    muted = (110, 120, 130)

    img = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), bg)
    draw = ImageDraw.Draw(img)

    title_font = _load_system_font(72)
    header_font = _load_system_font(40)
    body_font = _load_system_font(30)
    small_font = _load_system_font(24)

    y = 70
    draw.text((90, y), client_name or "Brand Brief", font=title_font, fill=fg)
    y += 110
    draw.line((90, y, CARD_WIDTH - 90, y), fill=muted, width=2)
    y += 45

    draw.text((90, y), "FONTS", font=header_font, fill=accent)
    y += 55
    fonts = brand.get("fonts") or []
    if fonts:
        for f in fonts[:6]:
            style = f" {f['style_name']}" if f.get("style_name") else ""
            label = (
                f"{f.get('family_name', '?')}{style} ({f.get('format', '?')}): "
                f"{f.get('install_status', 'unknown')}"
            )
            draw.text((110, y), label, font=body_font, fill=fg)
            y += 40
    else:
        draw.text((110, y), "(none)", font=body_font, fill=fg)
        y += 40
    y += 25

    draw.text((90, y), "PALETTE", font=header_font, fill=accent)
    y += 55
    swatch = 90
    x = 110
    palette = brand.get("palette") or []
    if palette:
        for p in palette[:6]:
            hexcode = p["hex"]
            rgb = tuple(int(hexcode[i:i + 2], 16) for i in (1, 3, 5))
            draw.rectangle((x, y, x + swatch, y + swatch), fill=rgb, outline=fg)
            draw.text((x, y + swatch + 8), hexcode, font=small_font, fill=fg)
            draw.text((x, y + swatch + 34), p.get("role", ""), font=small_font, fill=muted)
            x += swatch + 40
        y += swatch + 90
    else:
        draw.text((110, y), "(none)", font=body_font, fill=fg)
        y += 90

    # Logos and documents are LISTED, not counted. The whole point of the
    # card is telling an editor what is in the folder and, for documents,
    # why it could not come into Premiere with everything else. "DOCUMENTS: 1"
    # answers neither question.
    logos = brand.get("logos") or []
    draw.text((90, y), "LOGOS", font=header_font, fill=accent)
    y += 55
    if logos:
        for l in logos[:_CARD_MAX_LIST]:
            alpha_note = " (transparent)" if l.get("has_alpha") else ""
            draw.text(
                (110, y), f"{l.get('file', '?')}{alpha_note}", font=body_font, fill=fg
            )
            y += 40
        if len(logos) > _CARD_MAX_LIST:
            draw.text(
                (110, y), f"... and {len(logos) - _CARD_MAX_LIST} more",
                font=small_font, fill=muted,
            )
            y += 36
    else:
        draw.text((110, y), "(none)", font=body_font, fill=fg)
        y += 40
    y += 25

    documents = brand.get("documents") or []
    draw.text((90, y), "DOCUMENTS (not importable into Premiere)",
              font=header_font, fill=accent)
    y += 55
    if documents:
        for d in documents[:_CARD_MAX_LIST]:
            draw.text((110, y), d.get("file", "?"), font=body_font, fill=fg)
            y += 34
            reason = d.get("unsupported_reason") or "no reason on record"
            draw.text((130, y), reason, font=small_font, fill=muted)
            y += 40
        if len(documents) > _CARD_MAX_LIST:
            draw.text(
                (110, y), f"... and {len(documents) - _CARD_MAX_LIST} more",
                font=small_font, fill=muted,
            )
    else:
        draw.text((110, y), "(none)", font=body_font, fill=fg)

    # No em dash and no "->" arrow: this is on-screen text in a deliverable.
    footer = ("These files live in the same folder as this card. "
              "Right-click it in Premiere and choose Reveal in Finder.")
    draw.text((90, CARD_HEIGHT - 90), footer, font=body_font, fill=accent)

    img.save(path, format="PNG")


def generate_brief(
    brand: dict,
    assets_dir,
    *,
    client_name: Optional[str] = None,
    readme_filename: str = "BRAND_README.txt",
    card_filename: str = "brand-card.png",
    bin_path: str = "Files/Brand",
) -> dict:
    """Write `BRAND_README.txt` and the brand-card PNG **inside**
    `assets_dir` (the co-location invariant, enforced here by construction
    — there is no parameter that can point either file elsewhere) and
    return a NEW brand dict (input not mutated) with a `brief` key added,
    matching contract §2.3's `brief` shape: `{readme_path, card_png_path,
    bin_path, marker_written}`.

    `marker_written` is always `False` from this module — the frame-0
    creative-brief sequence marker is written by the exporter (Door 3),
    not here; this function only produces the two on-disk artifacts.
    """
    assets_dir = Path(assets_dir)
    assets_dir.mkdir(parents=True, exist_ok=True)

    readme_path = assets_dir / readme_filename
    card_path = assets_dir / card_filename

    _write_readme(brand, readme_path, client_name, _now_iso())
    _render_card(brand, card_path, client_name)

    new_brand = dict(brand)
    new_brand["brief"] = {
        "readme_path": readme_filename,
        "card_png_path": card_filename,
        "bin_path": bin_path,
        "marker_written": False,
    }
    return new_brand


# ---------------------------------------------------------------------------
# Co-location validation (contract §2.3 / §4.1 rule 8, mirrored here)
# ---------------------------------------------------------------------------

def validate_brief_colocation(brand: dict, assets_dir) -> list[str]:
    """Exhaustive (not fail-fast) co-location check for `brand.brief`.
    Mirrors `posthouse.manifest.validate_manifest`'s rule 8 exactly, but
    lives here too so this module can be exercised standalone (the CLI
    uses this, not the full manifest validator, since it never builds a
    manifest). Returns every problem found; `[]` means clean (including
    the case where `brand` has no `brief` at all — nothing to check yet).
    """
    problems: list[str] = []
    brief = (brand or {}).get("brief")
    if not brief:
        return problems

    assets_dir = Path(assets_dir)
    try:
        assets_resolved = assets_dir.resolve()
    except OSError:
        problems.append(f"assets_dir {assets_dir!r} could not be resolved")
        return problems

    card_rel = brief.get("card_png_path")
    if not card_rel:
        problems.append("brand.brief.card_png_path is missing")
    else:
        try:
            card_resolved = (assets_resolved / card_rel).resolve()
            card_resolved.relative_to(assets_resolved)
        except (OSError, ValueError):
            problems.append(
                f"brand.brief.card_png_path {card_rel!r} resolves outside "
                f"assets_dir {assets_dir} — the co-location rule (contract §2.3) "
                f"is violated"
            )

    readme_rel = brief.get("readme_path")
    if not readme_rel:
        problems.append("brand.brief.readme_path is missing")
    else:
        try:
            readme_resolved = (assets_resolved / readme_rel).resolve()
            readme_resolved.relative_to(assets_resolved)
        except (OSError, ValueError):
            problems.append(
                f"brand.brief.readme_path {readme_rel!r} resolves outside "
                f"assets_dir {assets_dir} — the co-location rule (contract §2.3) "
                f"is violated"
            )

    return problems


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m posthouse.brandbrief",
        description="Build the Brand Brief (README + brand-card PNG + brand JSON) "
                     "for a staged brand-assets folder.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build_p = sub.add_parser("build", help="Extract, generate, and validate the Brand Brief.")
    build_p.add_argument("assets_dir", type=Path, help="The staged brand-assets folder.")
    build_p.add_argument("--client", default=None, help="Client/brand name for the README and card.")
    build_p.add_argument("--out-json", type=Path, default=None, help="Also write the brand section JSON here.")
    args = parser.parse_args(argv)

    if args.command != "build":  # pragma: no cover - argparse enforces this
        print(f"error: unknown command {args.command!r}", file=sys.stderr)
        return 1

    try:
        brand = build_brand_section(args.assets_dir)
        brand = generate_brief(brand, args.assets_dir, client_name=args.client)
    except BrandBriefError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # pragma: no cover - defensive: never hang, never crash bare
        print(f"error: unexpected failure building the Brand Brief: "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1

    problems = validate_brief_colocation(brand, args.assets_dir)
    if problems:
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        return 1

    out = json.dumps(brand, indent=2, ensure_ascii=False)
    if args.out_json:
        args.out_json.write_text(out + "\n", encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
