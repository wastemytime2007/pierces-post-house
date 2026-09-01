"""Shared fixtures for the Phase 0 safety net.

Responsibilities:
  1. Put $PRECUT_ROOT/python_backend on sys.path so tests can
     `from precut_pipeline import ...` exactly like PreCut's own backend does.
  2. Stub out the handful of heavy ML/vector-db packages that the exporter
     chain does NOT need for its own logic but that one lazily-imported
     module (`precut_pipeline.markers`) pulls in transitively the moment a
     CutList carries any BRollMarker. See "The markers.py surprise" below.
  3. Build the one synthetic PreCut project (media + SQLite B-roll index +
     CutLists + ExportRequests) that both the golden-master test and the
     FCP7-quirk tests exercise, so there is exactly one code path that
     assembles the export input and both test files just inspect its output.

The markers.py surprise
------------------------
The scope decision for this safety net (see the task brief / ROADMAP.md
Phase 0) says the exporter chain is stdlib-only: multi_exporter, exporter,
bin_builders, cutlist, overlay, presets, markers, theme_categories. Seven of
those eight import cleanly with nothing but the standard library. `markers`
does NOT: it does `from .database import Database` and `from .transcriber
import Phrase` at module scope, and those pull in lancedb + numpy + pyarrow
(database.py) and torch (transcriber.py) — none of which are installed in a
lightweight cloud session, and none of which `markers.format_marker_name` /
`format_marker_comment` (the only two functions the exporter actually calls)
touch at runtime.

Concretely: `exporter.py`'s `_build_markers()` and `_build_attached_markers()`
both do `from .markers import format_marker_name, format_marker_comment`
lazily, INSIDE the method — so the exporter chain is only stdlib-only for
CutLists with an EMPTY `broll_markers` list. The moment a cut carries a
BRollMarker (which the brief explicitly asks the golden master to exercise),
importing `precut_pipeline.exporter` transitively requires lancedb/torch to
be installed, in this environment, ~1.5GB+ of packages this safety net has
no business needing just to format two strings.

This is flagged in the final report as a real discovery, not swept under
the rug. The fix here is a deliberate, narrowly-scoped test-only technique:
inject minimal placeholder modules into `sys.modules` for `lancedb`,
`pyarrow`, `numpy`, and `torch` — ONLY if the real package isn't already
importable (so this is a no-op on Ryan's Mac, where the real venv has all
of them). The stubs are inert: they exist purely to satisfy `import` and
`np.ndarray` type-annotation resolution in `database.py`; nothing in this
safety net calls a single method on them. `test_import_gate.py` still
records, unstubbed, that `markers` fails to import in a stdlib-only
environment — the stub lives ONLY here, for the export path that needs it.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import types
from pathlib import Path
from xml.dom import minidom

import pytest

PRECUT_ROOT = Path(os.environ.get("PRECUT_ROOT", "/home/user/precut"))
BACKEND_DIR = PRECUT_ROOT / "python_backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

FIXTURES_MEDIA = Path(__file__).parent / "fixtures" / "media"
GOLDEN_DIR = Path(__file__).parent / "golden"


# ---------------------------------------------------------------------------
# Stub injection for markers.py's transitive heavy deps (see module docstring)
# ---------------------------------------------------------------------------

def _install_stub_if_missing(name: str, build: "callable[[], types.ModuleType]") -> None:
    try:
        __import__(name)
        return  # real package is installed (e.g. Ryan's Mac venv) — use it
    except ImportError:
        pass
    sys.modules[name] = build()


def _install_marker_dependency_stubs() -> None:
    def _numpy_stub() -> types.ModuleType:
        mod = types.ModuleType("numpy")
        mod.ndarray = type("ndarray", (), {})  # only used as a type annotation
        mod.float32 = "float32"
        return mod

    def _empty_stub(name: str) -> types.ModuleType:
        return types.ModuleType(name)

    _install_stub_if_missing("numpy", _numpy_stub)
    _install_stub_if_missing("pyarrow", lambda: _empty_stub("pyarrow"))
    _install_stub_if_missing("lancedb", lambda: _empty_stub("lancedb"))
    _install_stub_if_missing("torch", lambda: _empty_stub("torch"))


_install_marker_dependency_stubs()


# ---------------------------------------------------------------------------
# Normalization helpers (shared by golden + quirk tests)
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
)


def normalize_xml_text(raw_text: str, root_dir: Path) -> str:
    """Make an exported XML deterministic for a byte-for-byte golden compare.

    Neutralizes, in order:
      1. The temp project root, both as a plain filesystem path AND as it
         appears percent-encoded inside file:// URLs (path_to_url() runs
         urllib.parse.quote() over the whole absolute path, so the root
         segment itself gets percent-escaped along with everything after
         it — a plain string replace of the unencoded root would miss
         those occurrences entirely).
      2. PRECUT_ROOT itself. Discovery: the five empty-bin placeholders
         (Final/Music/SFX/Nested Seqs/Colors) reference bundled PNGs
         living INSIDE the precut checkout
         (.../precut_pipeline/placeholders/placeholder_*.png), not inside
         our temp root — export_multi_timeline never copies them out. That
         path is PRECUT_ROOT-dependent (different on every machine/checkout
         location), so it has to be neutralized exactly like the temp root
         or the golden master would only ever match on the machine it was
         blessed on.
      3. UUIDs (every master <clip> gets a fresh uuid.uuid4() each run).
      4. Re-serializes through minidom with a fixed indent so incidental
         whitespace differences (e.g. across Python patch versions) don't
         register as a diff.

    The overlay-copy directory (`_overlays/` next to the XML) lives INSIDE
    root_dir in this test's layout, so normalizing root_dir also covers it;
    no separate substitution is needed as long as callers keep it that way.
    """
    from urllib.parse import quote

    text = raw_text
    for base in {root_dir, root_dir.resolve(), PRECUT_ROOT, PRECUT_ROOT.resolve()}:
        base_str = str(base)
        token = "{PRECUT_ROOT}" if base in (PRECUT_ROOT, PRECUT_ROOT.resolve()) else "{ROOT}"
        # Percent-encoded form, as produced by path_to_url()'s quote(..., safe="/")
        text = text.replace(quote(base_str, safe="/"), token)
        # Plain form
        text = text.replace(base_str, token)

    text = _UUID_RE.sub("{UUID}", text)

    # Re-pretty-print for a canonical comparison surface. The exporter's own
    # output already uses toprettyxml(indent="\t"); redo it explicitly so the
    # golden compare never depends on incidental whitespace.
    # The leading "<?xml ...?>\n<!DOCTYPE xmeml>\n" lines aren't part of the
    # DOM the writer built (they're inserted as raw text lines), so strip and
    # re-add them around the reparsed body.
    lines = text.splitlines()
    assert lines[0].startswith("<?xml"), "expected an XML declaration on line 1"
    assert lines[1].strip() == "<!DOCTYPE xmeml>", "expected a bare xmeml DOCTYPE on line 2"
    body = "\n".join(lines[2:])

    dom = minidom.parseString(body)
    pretty = dom.toprettyxml(indent="\t")
    # toprettyxml re-adds its own <?xml?> declaration; drop it, we supply ours.
    pretty_lines = [l for l in pretty.splitlines() if l.strip()]
    if pretty_lines[0].startswith("<?xml"):
        pretty_lines = pretty_lines[1:]

    return "\n".join(
        ['<?xml version="1.0" encoding="UTF-8"?>', "<!DOCTYPE xmeml>", *pretty_lines]
    ) + "\n"


# ---------------------------------------------------------------------------
# The synthetic project: media copy-in, SQLite B-roll index, cutlists, export
# ---------------------------------------------------------------------------

def _make_broll_index_db(db_path: Path, entries: list[dict]) -> None:
    """Build a SQLite DB matching EXACTLY the schema/queries that
    multi_exporter.load_broll_library reads (see that function's docstring
    and body): a `clips` table with (id, path, filename, duration_sec,
    width, height, fps, motion_tags, original_path) and a `frames` table
    with (id, clip_id, timestamp_sec, tags, tags_text). No other columns
    or tables it reads exist in the real schema (database.py has more —
    status, tagger_id, file_size, mtime, etc. — but load_broll_library
    never selects them, so they're deliberately omitted here).
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE clips (
                id INTEGER PRIMARY KEY,
                path TEXT NOT NULL,
                filename TEXT NOT NULL,
                duration_sec REAL,
                width INTEGER,
                height INTEGER,
                fps REAL,
                motion_tags TEXT,
                original_path TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE frames (
                id INTEGER PRIMARY KEY,
                clip_id INTEGER NOT NULL,
                timestamp_sec REAL NOT NULL,
                tags TEXT,
                tags_text TEXT
            )
            """
        )
        for i, e in enumerate(entries, start=1):
            conn.execute(
                "INSERT INTO clips (id, path, filename, duration_sec, width, "
                "height, fps, motion_tags, original_path) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    i, e["path"], e["filename"],
                    e.get("duration_sec", 0.0), e.get("width", 0), e.get("height", 0),
                    e.get("fps", 0.0),
                    json.dumps(e["motion_tags"]) if e.get("motion_tags") else None,
                    e.get("original_path"),
                ),
            )
            for ts, tags in enumerate(e.get("frame_tags", [])):
                conn.execute(
                    "INSERT INTO frames (clip_id, timestamp_sec, tags, tags_text) "
                    "VALUES (?,?,?,?)",
                    (i, float(ts), json.dumps(tags), None),
                )
        conn.commit()
    finally:
        conn.close()


def _build_synthetic_project(root: Path) -> dict:
    """Lay out the synthetic project and run the real export_multi_timeline().

    Layout (mirrors the two proxy conventions _find_original_for_proxy
    understands — we use the CURRENT one, /src_root/proxies/<clip>.mp4):

        root/media/stable.mp4                 (A-roll interview source)
        root/media/shaky.mp4                  (B-roll original)
        root/media/blurred.mp4                (B-roll original)
        root/media/underexposed.mp4           (B-roll original)
        root/media/overexposed.mp4            (B-roll original)
        root/media/AROLL_01.MOV               (B-roll original, uppercase ext,
                                                audio stream)
        root/media/proxies/shaky.mp4          (proxy; explicit original_path
        root/media/proxies/blurred.mp4         in the DB for these four, so
        root/media/proxies/underexposed.mp4    the exporter takes the
        root/media/proxies/overexposed.mp4     "Drop 4.28" fast path)
        root/media/proxies/aroll_01.mp4       (proxy; DIFFERENT case + no
                                                original_path in the DB, so
                                                the exporter must fall back to
                                                _find_original_for_proxy's
                                                case-insensitive scan — quirk 1)
        root/precut.db                        (B-roll SQLite index)
        root/export/multi.xml                 (export_multi_timeline output)

    Deliberately NOT exercised here (see safety_net/README.md "Scoped out"):
      * lav sync (audio_sync_state stays None — synthetic sine-tone audio
        won't clear audio_sync.py's MFCC match-score floor, so exercising
        it here would silently bless an unsynced-looking XML instead of
        proving anything about real sync).
      * more than one overlay style per export (Python set iteration order
        over `unique_styles` would then drive masterclip-N/file-N id
        assignment — see multi_exporter.export_multi_timeline Phase 1/3).
    """
    from precut_pipeline.cutlist import ARollPhrase, BRollMarker, CutList
    from precut_pipeline.multi_exporter import ExportRequest, export_multi_timeline
    from precut_pipeline.theme_categories import get_category

    media = root / "media"
    proxies = media / "proxies"
    proxies.mkdir(parents=True, exist_ok=True)
    export_dir = root / "export"
    export_dir.mkdir(parents=True, exist_ok=True)

    import shutil

    def copy_in(name: str, dest_name: str | None = None) -> Path:
        dest = media / (dest_name or name)
        shutil.copyfile(FIXTURES_MEDIA / name, dest)
        return dest

    stable = copy_in("stable.mp4")
    shaky = copy_in("shaky.mp4")
    blurred = copy_in("blurred.mp4")
    under = copy_in("underexposed.mp4")
    over = copy_in("overexposed.mp4")
    aroll_original = copy_in("AROLL_01.MOV")  # exact on-disk case: AROLL_01.MOV

    # Proxies. Four of them share stem+extension case with their original
    # (the common case) and carry an explicit original_path in the DB.
    def copy_proxy(src_name: str) -> Path:
        dest = proxies / src_name
        shutil.copyfile(FIXTURES_MEDIA / src_name, dest)
        return dest

    proxy_shaky = copy_proxy("shaky.mp4")
    proxy_blurred = copy_proxy("blurred.mp4")
    proxy_under = copy_proxy("underexposed.mp4")
    proxy_over = copy_proxy("overexposed.mp4")

    # The quirk-1 proxy: lowercase stem+ext, deliberately NOT matching
    # AROLL_01.MOV's on-disk case, and no original_path in the DB row — this
    # forces _find_original_for_proxy's case-insensitive directory scan
    # (see DECISIONS.md quirk 1) rather than the exact-path fast path.
    proxy_aroll = proxies / "aroll_01.mp4"
    shutil.copyfile(FIXTURES_MEDIA / "AROLL_01.MOV", proxy_aroll)

    db_path = root / "precut.db"
    _make_broll_index_db(db_path, [
        {
            "path": str(proxy_shaky), "filename": "shaky.mp4",
            "original_path": str(shaky),
            # Deliberately stale DB dims/fps/duration: multi_exporter must
            # re-probe the real original and NOT trust these.
            "duration_sec": 1.0, "width": 32, "height": 18, "fps": 9.0,
            "motion_tags": ["static"],
            "frame_tags": [["kitchen", "cabinets"], ["kitchen", "island"]],
        },
        {
            "path": str(proxy_blurred), "filename": "blurred.mp4",
            "original_path": str(blurred),
            "duration_sec": 1.0, "width": 32, "height": 18, "fps": 9.0,
            "motion_tags": ["static"],
            "frame_tags": [["bedroom", "bed"]],
        },
        {
            "path": str(proxy_under), "filename": "underexposed.mp4",
            "original_path": str(under),
            "duration_sec": 1.0, "width": 32, "height": 18, "fps": 9.0,
            "motion_tags": ["static"],
            "frame_tags": [["exterior", "yard"]],
        },
        {
            "path": str(proxy_over), "filename": "overexposed.mp4",
            "original_path": str(over),
            "duration_sec": 1.0, "width": 32, "height": 18, "fps": 9.0,
            "motion_tags": ["static"],
            "frame_tags": [["bathroom", "sink"]],
        },
        {
            "path": str(proxy_aroll), "filename": "aroll_01.mp4",
            "original_path": None,  # forces _find_original_for_proxy
            "duration_sec": 1.0, "width": 32, "height": 18, "fps": 9.0,
            "motion_tags": None,
            "frame_tags": [["kitchen", "stove"], ["living_room", "sofa"]],
        },
    ])

    seq_kwargs = dict(
        sequence_width=1920, sequence_height=1080, sequence_fps=30.0,
        overlay_style="horizontal_1920x1080",  # ONE style for the whole doc
    )

    cutlist_1 = CutList(
        deliverable_concept="Angle One",
        deliverable_preset="custom",
        total_duration=2.0,
        aroll_track=[ARollPhrase(
            phrase_id=1, source_file=str(stable),
            source_start=0.5, source_end=2.5,
            timeline_start=0.0, timeline_end=2.0,
            text="the kitchen is amazing",
        )],
        broll_track=[],
        broll_markers=[BRollMarker(
            timeline_time=0.5,
            primary_tags=["kitchen", "cabinets"],
            all_tags=["kitchen", "cabinets", "island", "stove"],
            theme_category="kitchen",
            color_rgb=get_category("kitchen").color_rgb,
            phrase_id=1, segment_order=0,
        )],
        **seq_kwargs,
    )

    cutlist_2 = CutList(
        deliverable_concept="Angle Two",
        deliverable_preset="custom",
        total_duration=2.5,
        aroll_track=[ARollPhrase(
            phrase_id=2, source_file=str(stable),
            source_start=1.0, source_end=3.5,
            timeline_start=0.0, timeline_end=2.5,
            text="and the living room too",
        )],
        broll_track=[],
        broll_markers=[
            BRollMarker(
                timeline_time=0.4,
                primary_tags=["living_room", "sofa"],
                all_tags=["living_room", "sofa"],
                theme_category="living_room",
                color_rgb=get_category("living_room").color_rgb,
                phrase_id=2, segment_order=0,
            ),
            BRollMarker(
                timeline_time=1.6,
                primary_tags=["bedroom", "bed"],
                all_tags=["bedroom", "bed"],
                theme_category="bedroom",
                color_rgb=get_category("bedroom").color_rgb,
                phrase_id=2, segment_order=0,
            ),
        ],
        **seq_kwargs,
    )

    requests = [
        ExportRequest(cutlist=cutlist_1, sequence_name="Angle One"),
        ExportRequest(cutlist=cutlist_2, sequence_name="Angle Two"),
    ]

    from precut_pipeline.multi_exporter import load_broll_library
    broll_library = load_broll_library(db_path)
    assert len(broll_library) == 5, (
        f"expected 5 B-roll library entries, got {len(broll_library)} — "
        f"the synthetic DB or load_broll_library's query shape has drifted"
    )

    output_path = export_multi_timeline(
        requests=requests,
        output_path=export_dir / "multi.xml",
        broll_library=broll_library,
        project_name="Post House Fixture Project",
        include_overlay=True,
        auto_include_rules=None,
    )

    return {
        "root": root,
        "output_path": output_path,
        "raw_text": output_path.read_text(encoding="utf-8"),
        "broll_library": broll_library,
        "aroll_original": aroll_original,   # AROLL_01.MOV, exact on-disk case
        "proxy_aroll": proxy_aroll,
    }


@pytest.fixture(scope="session")
def synthetic_project(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("precut_safety_net_project")
    return _build_synthetic_project(root)


@pytest.fixture(scope="session")
def normalized_xml(synthetic_project) -> str:
    return normalize_xml_text(synthetic_project["raw_text"], synthetic_project["root"])


@pytest.fixture(scope="session")
def exported_dom(synthetic_project):
    return minidom.parseString(synthetic_project["raw_text"].encode("utf-8"))
