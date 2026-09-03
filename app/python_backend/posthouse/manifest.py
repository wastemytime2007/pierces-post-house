"""posthouse.manifest — the Project Manifest builder and validator.

This is the Project Manager's hard deliverable (ROADMAP.md §6 Phase 2,
Decision Log "Build order by role"): ``manifest.json``, the one file every
later role reads *blind*. The contract is fully specified and ratified in
``docs/contracts/PROJECT_MANIFEST.md`` — every field, key order, and
validation rule in this module is an implementation of that document, not
a reinterpretation of it. Read the contract before touching this file.

Scope (deliberately narrow, per ROADMAP.md ground rule 7 "Roles before
shell" and this slice's brief): this module builds and validates the
manifest from **already-decided, structured input**. It does not run an
interactive intake conversation, does not organize files on disk, and does
not stage brand assets — those are later Phase 2 deliverables (or later
phases). Given intake answers as plain Python data, :func:`build_manifest`
produces a manifest dict; given a manifest, :func:`validate_manifest`
checks it; :func:`load_manifest` / :func:`save_manifest` round-trip it to
disk with the same atomic-write discipline PreCut's own
``project.py:Project.save()`` uses.

Two-moment validation (contract §4)
------------------------------------
One rule set, two postures. ``mode="intake"``: the PM is still talking to
Ryan, so every rule — including everything in §4.1's REJECT list — comes
back as a warning; the function never raises and ``errors`` is always
empty. ``mode="handoff"``: the same rules run, but §4.1 violations land in
``errors`` (fatal) while §4.2 stays advisory. Validation is **exhaustive,
not fail-fast** — every offender is collected, matching
``posthouse.coldfootage``'s pattern. Callers who want "handoff mode with
teeth" (raise on a fatal error) do that themselves, e.g. the CLI here,
which exits non-zero and prints every error.

Source IDs are minted once and frozen (contract §5): :func:`mint_source_id`
computes a fresh id from a kind + display name + the ids already in play;
:func:`add_source` uses it to extend an *existing* manifest without ever
touching a prior source's id, because renaming `B - Interior` to
`B - Interior Rooms` at 11pm must never orphan a downstream artifact that
already cited the old id.

Entry points
------------
* Python API: :func:`build_manifest`, :func:`load_manifest`,
  :func:`save_manifest`, :func:`validate_manifest`, :func:`add_source`,
  :func:`mint_source_id`, :func:`mint_delivery_target_id`, :func:`slugify`.
* CLI: ``python -m posthouse.manifest validate <path> [--mode intake|handoff]``
  — exits non-zero with every error on stderr in handoff mode.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .harvest.auto_include import unsupported_reason as _harvested_unsupported_reason
from .precut_bridge import import_precut

CONTRACT_VERSION = 1

_PACKAGE_DIR = Path(__file__).resolve().parent
_PIN_FILE = _PACKAGE_DIR / "PRECUT_PIN"

# ---------------------------------------------------------------------------
# Enums (contract §4.1 rule 6: kind/project_type/status/role/action)
# ---------------------------------------------------------------------------

VALID_SOURCE_KINDS = {"aroll", "broll", "source_audio", "assets"}
VALID_PROJECT_TYPES = {
    "interview", "property_tour", "renovation", "event", "product", "other",
}
VALID_PEOPLE_ROLES = {"subject", "agent", "host", "other"}
VALID_DELIVERY_TARGET_STATUS = {"proposed", "confirmed"}
VALID_HANDOFF_ROLES = {
    "project_manager", "assistant_editor", "creative_editor",
    "supervisor", "exporter",
}
VALID_HANDOFF_ACTIONS = {"emitted", "consumed", "revised", "returned"}

# contract §5: source id kind segment is hyphenated; sources[].kind uses
# "source_audio" (underscore) because it's a Python-friendly enum value,
# not an id fragment — bridge code must map, never assume (contract §2.4).
SOURCE_KIND_TO_ID_KIND = {
    "aroll": "aroll",
    "broll": "broll",
    "source_audio": "source-audio",
    "assets": "assets",
}

SOURCE_ID_RE = re.compile(
    r"^(aroll|broll|source-audio|assets)-[a-z0-9]+(-[a-z0-9]+)*-[0-9]{2}$"
)

# contract §4.3: extension -> category. Reasons for these categories come
# verbatim from the harvested auto_include.unsupported_reason() — never
# reimplemented or paraphrased here.
UNSUPPORTED_CATEGORY_BY_EXT = {
    ".cube": "lut", ".look": "lut", ".3dl": "lut",
    ".pdf": "document", ".doc": "document", ".docx": "document",
    ".txt": "text", ".rtf": "text",
    ".ai": "layered_image", ".psd": "layered_image",
}

# ---------------------------------------------------------------------------
# Canonical key order, per contract §2's field tables (read carefully: the
# table order is specified for readability/diffability, and the golden
# master test locks this in).
# ---------------------------------------------------------------------------

TOP_LEVEL_ORDER = [
    "contract_version", "manifest_id", "revision", "created_at", "updated_at",
    "generator", "project", "brand", "sources", "delivery_targets",
    "default_includes", "handoffs", "validation",
]
GENERATOR_ORDER = ["name", "version", "precut_pin"]
PROJECT_ORDER = [
    "name", "slug", "root_dir", "client", "project_type", "shoot_dates",
    "locations", "people", "notes",
]
CLIENT_ORDER = ["name", "contact", "notes"]
LOCATION_ORDER = ["label", "address"]
PERSON_ORDER = ["id", "name", "role"]
BRAND_ORDER = [
    "assets_dir", "fonts", "palette", "logos", "documents", "brief",
    "library_ref",
]
FONT_ORDER = [
    "file", "family_name", "style_name", "postscript_name", "format",
    "extracted_by", "install_status",
]
PALETTE_ORDER = ["hex", "role", "source"]
LOGO_ORDER = ["file", "kind", "has_alpha"]
DOCUMENT_ORDER = ["file", "kind", "unsupported_reason", "summarized"]
BRIEF_ORDER = ["readme_path", "card_png_path", "bin_path", "marker_written"]
SOURCE_ORDER = [
    "id", "path", "display_name", "kind", "is_file", "dual_use",
    "subject_ids", "notes", "added_at", "media", "unsupported", "inference",
]
MEDIA_ORDER = [
    "video_count", "audio_count", "image_count", "other_count", "total_bytes",
]
UNSUPPORTED_ITEM_ORDER = ["ext", "count", "category", "reason"]
INFERENCE_ORDER = ["camera_tags", "method", "agrees_with_declaration"]
DELIVERY_TARGET_ORDER = [
    "id", "label", "aspect_key", "platform_key", "preset_key",
    "target_duration_sec", "duration_tolerance_sec", "status",
]
DEFAULT_INCLUDE_ORDER = [
    "id", "type", "source_path", "bin_path", "file_glob", "origin",
]
HANDOFF_ORDER = ["role", "action", "at", "revision", "agent", "note"]
VALIDATION_ORDER = ["ran_at", "mode", "errors", "warnings"]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ManifestError(Exception):
    """Base class for manifest build/load/save failures."""


@dataclass
class ValidationResult:
    """The result of :func:`validate_manifest`. Never carries partial state
    — either both lists are fully populated (exhaustive, not fail-fast) or
    the manifest was structurally unreadable before validation could run
    (raised as :class:`ManifestError` instead)."""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# ---------------------------------------------------------------------------
# slugify + id minting (contract §5)
# ---------------------------------------------------------------------------

def slugify(text: str, max_len: int = 40) -> str:
    """NFKD-normalize, drop combining marks, lowercase, keep ASCII
    alphanumerics, collapse every other run to a single '-', strip
    leading/trailing '-', truncate to ``max_len`` chars at a '-' boundary,
    empty -> "folder". Exactly the algorithm in contract §5 — deliberately
    stricter than PreCut's ``_sanitize_project_name()``, which preserves
    spaces because it names display folders, not ids."""
    normalized = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in normalized if not unicodedata.combining(c))
    lowered = stripped.lower()
    chars = [c if (c.isascii() and c.isalnum()) else "-" for c in lowered]
    collapsed = re.sub(r"-+", "-", "".join(chars)).strip("-")
    if not collapsed:
        return "folder"
    if len(collapsed) <= max_len:
        return collapsed
    truncated = collapsed[:max_len]
    if "-" in truncated:
        last_hyphen = truncated.rfind("-")
        if last_hyphen > 0:
            truncated = truncated[:last_hyphen]
    truncated = truncated.strip("-")
    return truncated or "folder"


def _mint_numbered_id(prefix: str, existing_ids: list[str], min_n: int = 1) -> str:
    """Shared NN-suffix minting for source ids, delivery-target ids, and
    person ids: find the highest existing `-NN` under this exact prefix and
    increment. Never reuses, never renumbers a prior id.

    ``min_n`` is the lowest number this caller may mint — 1 for source and
    delivery-target ids (whose `-NN` is always present, so the first is
    `-01`), 2 for person ids (where the bare, unsuffixed slug is implicitly
    01, so the first *collision* is `-02`).
    """
    pattern = re.compile(rf"^{re.escape(prefix)}([0-9]{{2}})$")
    max_n = min_n - 1
    for existing in existing_ids:
        m = pattern.match(existing)
        if m:
            max_n = max(max_n, int(m.group(1)))
    next_n = max_n + 1
    if next_n > 99:
        raise ManifestError(
            f"id overflow: more than 99 ids already minted under prefix {prefix!r}"
        )
    return f"{prefix}{next_n:02d}"


def mint_source_id(kind: str, display_name: str, existing_ids: list[str]) -> str:
    """Mint a fresh ``<kind>-<slug>-<NN>`` source id (contract §5).

    ``existing_ids`` is every source id already in the manifest (any kind)
    — only ids matching this exact kind+slug prefix affect the NN chosen,
    but passing the full list is the caller's job so a second "B - Interior"
    correctly becomes -02 without the caller pre-filtering.
    """
    if kind not in SOURCE_KIND_TO_ID_KIND:
        raise ManifestError(
            f"unknown source kind {kind!r}; expected one of {sorted(VALID_SOURCE_KINDS)}"
        )
    id_kind = SOURCE_KIND_TO_ID_KIND[kind]
    slug = slugify(display_name)
    return _mint_numbered_id(f"{id_kind}-{slug}-", existing_ids)


def mint_delivery_target_id(label: str, existing_ids: list[str]) -> str:
    """Mint a fresh ``dt-<slug>-NN`` delivery-target id (contract §2.5)."""
    slug = slugify(label)
    return _mint_numbered_id(f"dt-{slug}-", existing_ids)


def _mint_person_id(name: str, existing_ids: list[str]) -> str:
    """Mint a project.people[].id. The contract only specifies 'slugified
    name, unique in the project' (§2.2) and doesn't define a collision
    scheme the way it does for §5's numbered ids — two people named "Sam"
    is a real possibility. Judgment call: reuse the same -NN suffix scheme
    source ids use, starting the FIRST collision at -02 (the bare slug is
    id 01, implicitly) so two Sams become "sam" and "sam-02", not
    "sam-01"/"sam-02" (the bare form stays unsuffixed, matching how a
    single, uncontested person keeps a clean id)."""
    base = slugify(name)
    if base not in existing_ids:
        return base
    return _mint_numbered_id(f"{base}-", existing_ids, min_n=2)


# ---------------------------------------------------------------------------
# Unsupported-file categorization (contract §4.3) — verbatim reuse of the
# harvested auto_include.unsupported_reason(), never paraphrased.
# ---------------------------------------------------------------------------

def categorize_unsupported(ext: str, count: int) -> dict:
    """Build one ``sources[].unsupported[]`` entry (contract §4.3) for
    ``count`` files sharing extension ``ext``. Known categories (lut,
    document, text, layered_image) embed the harvested per-file reason
    verbatim; anything else is ``unknown_extension`` with the generic
    message, matching the worked example's ``.lrf`` entry exactly:
    "2 .lrf file(s) skipped (unsupported extension; expected audio, video,
    or image)."."""
    ext = ext.lower()
    category = UNSUPPORTED_CATEGORY_BY_EXT.get(ext)
    if category is None:
        reason = (
            f"{count} {ext} file(s) skipped (unsupported extension; "
            f"expected audio, video, or image)."
        )
        category = "unknown_extension"
    else:
        harvested = _harvested_unsupported_reason(Path(f"x{ext}"))
        reason = f"{count} {ext} file(s) skipped ({harvested})"
    return {"ext": ext, "count": count, "category": category, "reason": reason}


# ---------------------------------------------------------------------------
# Reorder / canonicalize (round-trip + golden-master serialization)
# ---------------------------------------------------------------------------

def _reorder(d: dict, order: list[str]) -> dict:
    """Rebuild ``d`` with keys in ``order`` first (only those present),
    then any remaining keys (forward-compat with additive-only future
    fields this version doesn't know about) in their original order."""
    out = {}
    for k in order:
        if k in d:
            out[k] = d[k]
    for k in d:
        if k not in order:
            out[k] = d[k]
    return out


def canonicalize_manifest(manifest: dict) -> dict:
    """Return a new dict with every level's keys in contract §2 order.
    Never mutates the input. This is what makes the JSON serialization
    deterministic and diffable regardless of how the in-memory dict was
    assembled."""
    m = dict(manifest)

    if "generator" in m:
        m["generator"] = _reorder(m["generator"], GENERATOR_ORDER)

    if "project" in m:
        proj = dict(m["project"])
        if "client" in proj:
            proj["client"] = _reorder(proj["client"], CLIENT_ORDER)
        if "locations" in proj:
            proj["locations"] = [_reorder(l, LOCATION_ORDER) for l in proj["locations"]]
        if "people" in proj:
            proj["people"] = [_reorder(p, PERSON_ORDER) for p in proj["people"]]
        m["project"] = _reorder(proj, PROJECT_ORDER)

    if m.get("brand"):
        b = dict(m["brand"])
        if "fonts" in b:
            b["fonts"] = [_reorder(f, FONT_ORDER) for f in b["fonts"]]
        if "palette" in b:
            b["palette"] = [_reorder(p, PALETTE_ORDER) for p in b["palette"]]
        if "logos" in b:
            b["logos"] = [_reorder(l, LOGO_ORDER) for l in b["logos"]]
        if "documents" in b:
            b["documents"] = [_reorder(d, DOCUMENT_ORDER) for d in b["documents"]]
        if "brief" in b:
            b["brief"] = _reorder(b["brief"], BRIEF_ORDER)
        m["brand"] = _reorder(b, BRAND_ORDER)

    if "sources" in m:
        new_sources = []
        for s in m["sources"]:
            s = dict(s)
            if "media" in s:
                s["media"] = _reorder(s["media"], MEDIA_ORDER)
            if "unsupported" in s:
                s["unsupported"] = [_reorder(u, UNSUPPORTED_ITEM_ORDER) for u in s["unsupported"]]
            if "inference" in s:
                s["inference"] = _reorder(s["inference"], INFERENCE_ORDER)
            new_sources.append(_reorder(s, SOURCE_ORDER))
        m["sources"] = new_sources

    if "delivery_targets" in m:
        m["delivery_targets"] = [_reorder(dt, DELIVERY_TARGET_ORDER) for dt in m["delivery_targets"]]

    if "default_includes" in m:
        m["default_includes"] = [_reorder(di, DEFAULT_INCLUDE_ORDER) for di in m["default_includes"]]

    if "handoffs" in m:
        m["handoffs"] = [_reorder(h, HANDOFF_ORDER) for h in m["handoffs"]]

    if "validation" in m:
        m["validation"] = _reorder(m["validation"], VALIDATION_ORDER)

    return _reorder(m, TOP_LEVEL_ORDER)


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_precut_pin() -> Optional[str]:
    if not _PIN_FILE.exists():
        return None
    text = _PIN_FILE.read_text(encoding="utf-8").strip()
    return text or None


def _build_source_dict(spec: dict, existing_ids: list[str]) -> dict:
    """Build one canonical source dict from a raw spec (contract §2.4).
    ``existing_ids`` is mutated (appended to) so callers building a list of
    sources one at a time get correct -NN collision handling across calls.
    """
    kind = spec.get("kind")
    if kind not in VALID_SOURCE_KINDS:
        raise ManifestError(
            f"source spec for {spec.get('path')!r}: unknown kind {kind!r}; "
            f"expected one of {sorted(VALID_SOURCE_KINDS)}"
        )
    path = spec.get("path")
    if not path:
        raise ManifestError("source spec is missing 'path'")
    display_name = spec.get("display_name") or Path(path).name

    sid = spec.get("id") or mint_source_id(kind, display_name, existing_ids)
    existing_ids.append(sid)

    d: dict = {
        "id": sid,
        "path": path,
        "display_name": display_name,
        "kind": kind,
    }
    if spec.get("is_file"):
        d["is_file"] = True
    if spec.get("dual_use"):
        d["dual_use"] = True
    if spec.get("subject_ids"):
        d["subject_ids"] = list(spec["subject_ids"])
    if spec.get("notes"):
        d["notes"] = spec["notes"]
    d["added_at"] = spec.get("added_at") or _now_iso()
    if spec.get("media") is not None:
        d["media"] = dict(spec["media"])
    if spec.get("unsupported"):
        d["unsupported"] = [dict(u) for u in spec["unsupported"]]
    if spec.get("inference") is not None:
        d["inference"] = dict(spec["inference"])
    return _reorder(d, SOURCE_ORDER)


def build_manifest(
    *,
    project_name: str,
    root_dir: str,
    client_name: str,
    project_type: str,
    client_contact: Optional[str] = None,
    client_notes: Optional[str] = None,
    shoot_dates: Optional[list[str]] = None,
    locations: Optional[list[dict]] = None,
    people: Optional[list[dict]] = None,
    project_notes: Optional[str] = None,
    sources: Optional[list[dict]] = None,
    brand: Optional[dict] = None,
    default_includes: Optional[list[dict]] = None,
    generator_name: str = "posthouse.pm",
    generator_version: str = "0.1.0",
    precut_pin: Optional[str] = None,
) -> dict:
    """Build a brand-new manifest dict from already-decided intake answers.

    ``people`` is ``[{"name": ..., "role": ...}]``; ids are minted per
    contract §2.2. ``sources`` is a list of raw specs matching
    :func:`_build_source_dict`'s inputs (``path``, ``kind`` required;
    ``display_name``, ``is_file``, ``dual_use``, ``subject_ids``, ``notes``,
    ``media``, ``unsupported``, ``inference``, ``added_at`` optional) — ids
    are minted fresh here, in list order, per kind.

    ``delivery_targets`` is deliberately NOT a parameter: contract §2.5's
    ratified ruling is that the PM never proposes delivery targets, so the
    key is omitted entirely from a freshly built manifest (not written as
    ``[]``) until the Creative Editor (Phase 6) adds it. This is structural
    enforcement of that ruling, not an oversight.

    Returns a canonicalized (key-ordered) manifest dict — not yet written
    to disk. Call :func:`save_manifest` to persist it.
    """
    if project_type not in VALID_PROJECT_TYPES:
        raise ManifestError(
            f"project_type {project_type!r} is not one of {sorted(VALID_PROJECT_TYPES)}"
        )

    now = _now_iso()
    manifest_id = str(uuid.uuid4())

    people_ids: list[str] = []
    people_out = []
    for p in (people or []):
        role = p.get("role", "other")
        if role not in VALID_PEOPLE_ROLES:
            raise ManifestError(
                f"person {p.get('name')!r}: role {role!r} is not one of "
                f"{sorted(VALID_PEOPLE_ROLES)}"
            )
        pid = p.get("id") or _mint_person_id(p["name"], people_ids)
        people_ids.append(pid)
        people_out.append(_reorder(
            {"id": pid, "name": p["name"], "role": role}, PERSON_ORDER
        ))

    source_ids: list[str] = []
    sources_out = [_build_source_dict(s, source_ids) for s in (sources or [])]

    client = {"name": client_name}
    if client_contact:
        client["contact"] = client_contact
    if client_notes:
        client["notes"] = client_notes

    project: dict = {
        "name": project_name,
        "slug": slugify(project_name),
        "root_dir": root_dir,
        "client": _reorder(client, CLIENT_ORDER),
        "project_type": project_type,
    }
    if shoot_dates:
        project["shoot_dates"] = list(shoot_dates)
    if locations:
        project["locations"] = [_reorder(dict(l), LOCATION_ORDER) for l in locations]
    if people_out:
        project["people"] = people_out
    if project_notes:
        project["notes"] = project_notes

    manifest: dict = {
        "contract_version": CONTRACT_VERSION,
        "manifest_id": manifest_id,
        "revision": 1,
        "created_at": now,
        "updated_at": now,
        "generator": {
            "name": generator_name,
            "version": generator_version,
            "precut_pin": precut_pin or _read_precut_pin() or "",
        },
        "project": _reorder(project, PROJECT_ORDER),
        "sources": sources_out,
        "default_includes": [
            _reorder(dict(di), DEFAULT_INCLUDE_ORDER) for di in (default_includes or [])
        ],
        "handoffs": [],
        "validation": {
            "ran_at": now, "mode": "intake", "errors": [], "warnings": [],
        },
    }
    if brand:
        manifest["brand"] = dict(brand)

    return canonicalize_manifest(manifest)


def add_source(manifest: dict, **spec) -> str:
    """Add a source to an EXISTING manifest, minting a fresh id from the
    manifest's current source ids. Every prior source's id is left
    completely untouched — this is the frozen-id half of contract §5 (the
    other half, minting for a brand-new manifest, is
    :func:`build_manifest`). Mutates ``manifest["sources"]`` in place and
    returns the new source's id. Does not touch ``revision``/``updated_at``
    — that happens in :func:`save_manifest`, once per write."""
    existing_ids = [s["id"] for s in manifest.get("sources", [])]
    new_source = _build_source_dict(spec, existing_ids)
    manifest.setdefault("sources", []).append(new_source)
    return new_source["id"]


# ---------------------------------------------------------------------------
# Load / save (atomic write, per contract §1's Encoding row, modeled on
# precut_pipeline's project.py:Project.save())
# ---------------------------------------------------------------------------

def load_manifest(path: Path) -> dict:
    """Load and return a manifest dict. Raises :class:`ManifestError` if the
    file's ``contract_version`` is missing or not the one this module
    supports (contract §1's versioning rule: a reader encountering an
    unknown contract_version must refuse to run, not guess)."""
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    cv = data.get("contract_version")
    if cv != CONTRACT_VERSION:
        raise ManifestError(
            f"unsupported contract_version {cv!r} in {path}; "
            f"posthouse.manifest supports version {CONTRACT_VERSION}"
        )
    return data


def save_manifest(manifest: dict, path: Path) -> Path:
    """Write ``manifest`` to ``path`` atomically (tempfile then
    ``os.replace``, exactly as ``project.py:Project.save()`` does — a crash
    mid-write never leaves a corrupt manifest).

    ``revision`` is bumped (and ``updated_at`` refreshed) on every write
    EXCEPT the very first one to a given path — a freshly built manifest
    from :func:`build_manifest` is already ``revision: 1``, and writing it
    for the first time shouldn't advance that. Every subsequent save to the
    same path is treated as a content-changing write and increments
    ``revision``, per contract §1: "revision ... bumped on every write."
    ``manifest_id`` and ``created_at`` are never touched here — they're set
    once, in :func:`build_manifest`, and this function has no way to change
    them short of the caller doing it directly (which it shouldn't).
    """
    path = Path(path)
    out = canonicalize_manifest(manifest)

    is_first_write = not path.exists()
    if not is_first_write:
        out["revision"] = int(out.get("revision", 1)) + 1
    out["updated_at"] = _now_iso()
    out = canonicalize_manifest(out)  # re-order after the revision/updated_at touch

    text = json.dumps(out, indent=2, ensure_ascii=False) + "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=".manifest.", suffix=".json.tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    manifest.clear()
    manifest.update(out)
    return path


# ---------------------------------------------------------------------------
# Validation (contract §4) — exhaustive, not fail-fast.
# ---------------------------------------------------------------------------

def _volume_of(path: str) -> Optional[int]:
    try:
        return os.stat(path).st_dev
    except OSError:
        return None


def _nesting_pair(path_a: str, path_b: str) -> tuple[Optional[str], Optional[str]]:
    """If one of these resolved paths contains the other, return
    ``(inner, outer)``; otherwise ``(None, None)``.

    Compared as path parts, not string prefixes — ``/proj/A-roll`` must not
    read as nested inside ``/proj/A`` just because the string starts the
    same way. Equal paths are NOT nesting (the caller's exact-match branch
    already handles that case).
    """
    a_parts = Path(path_a).parts
    b_parts = Path(path_b).parts
    if a_parts == b_parts:
        return (None, None)
    if len(a_parts) > len(b_parts) and a_parts[: len(b_parts)] == b_parts:
        return (path_a, path_b)
    if len(b_parts) > len(a_parts) and b_parts[: len(a_parts)] == a_parts:
        return (path_b, path_a)
    return (None, None)


def validate_manifest(manifest: dict, mode: str = "intake") -> ValidationResult:
    """Run every rule in contract §4 against ``manifest`` and return a
    :class:`ValidationResult`. Never raises on a bad manifest — a caller
    that wants "handoff mode with teeth" checks ``result.ok`` / raises
    itself (the CLI here does exactly that).

    ``mode="intake"``: every rule (including everything in §4.1's REJECT
    list) is reported as a warning; ``errors`` is always ``[]``.
    ``mode="handoff"``: §4.1 violations land in ``errors`` (fatal); §4.2
    stays advisory in ``warnings``. Both modes are exhaustive — every
    offender is collected, not just the first.
    """
    if mode not in ("intake", "handoff"):
        raise ValueError(f"mode must be 'intake' or 'handoff', got {mode!r}")

    fatal: list[str] = []
    warnings: list[str] = []

    def reject(msg: str) -> None:
        fatal.append(msg)

    def warn(msg: str) -> None:
        warnings.append(msg)

    # --- Rule 1: contract_version ------------------------------------
    cv = manifest.get("contract_version")
    if cv != CONTRACT_VERSION:
        reject(f"unrecognized contract_version {cv!r}; expected {CONTRACT_VERSION}")

    project = manifest.get("project") or {}
    root_dir = project.get("root_dir")

    # --- Rule 2: project.root_dir --------------------------------------
    if not root_dir:
        reject("project.root_dir is missing")
    else:
        p = Path(root_dir)
        if not p.is_dir():
            reject(f"project.root_dir {root_dir!r} is not a directory")
        elif not os.access(root_dir, os.W_OK):
            reject(f"project.root_dir {root_dir!r} is not writable")

    # --- Rule 6 (project_type) -----------------------------------------
    project_type = project.get("project_type")
    if project_type is not None and project_type not in VALID_PROJECT_TYPES:
        reject(f"project.project_type {project_type!r} is not a recognized enum value")

    # --- Rule 6 (people role) -------------------------------------------
    for person in project.get("people") or []:
        if person.get("role") not in VALID_PEOPLE_ROLES:
            reject(
                f"project.people[{person.get('id')}]: role "
                f"{person.get('role')!r} is not a recognized enum value"
            )

    # --- Sources: rules 3, 4, 5, 6, 9 and warn rules --------------------
    sources = manifest.get("sources") or []
    if not sources:
        reject("sources is empty")

    seen_ids: set[str] = set()
    seen_paths: dict[str, tuple[str, str]] = {}
    has_aroll = False
    has_source_audio = False
    aroll_video_total = 0

    for src in sources:
        sid = src.get("id", "")
        spath = src.get("path")
        kind = src.get("kind")
        label = sid or spath or "<unknown source>"

        # Rule 3
        if not spath:
            reject(f"source {label}: missing path")
        else:
            sp = Path(spath)
            if not sp.exists():
                reject(f"source {label}: path {spath!r} does not exist")
            elif not os.access(spath, os.R_OK):
                reject(f"source {label}: path {spath!r} is not readable")

        # Rule 4
        if not SOURCE_ID_RE.match(sid or ""):
            reject(f"source {label}: id {sid!r} does not match the required "
                    f"pattern {SOURCE_ID_RE.pattern}")
        if sid:
            if sid in seen_ids:
                reject(f"source id {sid!r} is used by more than one source")
            seen_ids.add(sid)

        # Rule 6 (kind)
        if kind not in VALID_SOURCE_KINDS:
            reject(f"source {label}: kind {kind!r} is not a recognized enum value")

        if kind == "aroll":
            has_aroll = True
            media = src.get("media") or {}
            aroll_video_total += int(media.get("video_count", 0) or 0)
        if kind == "source_audio":
            has_source_audio = True

        # Rule 5: kind conflict — the same resolved path twice, OR one
        # source nested inside another. Contract §4.1 rule 5 rejects the
        # nested case when kinds differ; §4.2 warns when they match
        # (double-counted footage, not fatal).
        if spath:
            try:
                resolved = str(Path(spath).resolve())
            except OSError:
                resolved = spath
            if resolved in seen_paths:
                other_id, other_kind = seen_paths[resolved]
                if other_kind != kind:
                    reject(
                        f"source {sid!r} and {other_id!r} both resolve to "
                        f"{resolved!r} with different kinds ({kind!r} vs {other_kind!r})"
                    )
                else:
                    warn(
                        f"source {sid!r} and {other_id!r} both resolve to "
                        f"{resolved!r} with the same kind — double-counted, not fatal"
                    )
            else:
                for other_path, (other_id, other_kind) in seen_paths.items():
                    inner, outer = _nesting_pair(resolved, other_path)
                    if inner is None:
                        continue
                    inner_id = sid if inner == resolved else other_id
                    outer_id = other_id if inner == resolved else sid
                    if other_kind != kind:
                        reject(
                            f"source {inner_id!r} ({inner!r}) is nested inside "
                            f"{outer_id!r} ({outer!r}) with a different kind "
                            f"({kind!r} vs {other_kind!r})"
                        )
                    else:
                        warn(
                            f"source {inner_id!r} ({inner!r}) is nested inside "
                            f"{outer_id!r} ({outer!r}) with the same kind — "
                            f"double-counted, not fatal"
                        )
                seen_paths[resolved] = (sid, kind)

        # WARN rules (§4.2)
        if src.get("dual_use") and kind != "aroll":
            warn(f"source {sid}: dual_use is true but kind is {kind!r}, not "
                 f"'aroll' — ignored downstream")

        inference = src.get("inference")
        if inference is not None and inference.get("agrees_with_declaration") is False:
            warn(f"source {sid}: inferred camera tags disagree with the declared kind")

        if root_dir and spath:
            src_vol = _volume_of(spath)
            root_vol = _volume_of(root_dir)
            if src_vol is not None and root_vol is not None and src_vol != root_vol:
                warn(f"source {sid}: lives on a different volume than "
                     f"project.root_dir — may become unmountable later")

        media = src.get("media")
        if media is not None:
            total_files = sum(
                int(media.get(k, 0) or 0)
                for k in ("video_count", "audio_count", "image_count", "other_count")
            )
            if total_files == 0:
                warn(f"source {sid}: has zero media files")

        for u in (src.get("unsupported") or []):
            reason = u.get("reason", "")
            warn(f"{sid}: {reason}" if reason else f"{sid}: unsupported files present")

    # Rule 9: interview needs a non-empty A-roll with video
    if project_type == "interview" and (not has_aroll or aroll_video_total == 0):
        reject(
            "project_type is 'interview' but there is no A-roll source with "
            "at least one video file"
        )

    if has_source_audio and not has_aroll:
        warn("source_audio is present but there is no aroll source to sync it to")

    # --- Brand (rule 8 + warn) -------------------------------------------
    brand = manifest.get("brand")
    if not brand:
        warn("brand is absent — no brand assets staged for this project")
    else:
        assets_dir = brand.get("assets_dir")
        for font in brand.get("fonts") or []:
            if font.get("install_status") != "installed":
                warn(
                    f"brand.fonts: {font.get('family_name', '?')!r} "
                    f"install_status is {font.get('install_status')!r}, not 'installed'"
                )
        brief = brand.get("brief")
        if brief and assets_dir and brief.get("card_png_path"):
            try:
                assets_resolved = Path(assets_dir).resolve()
                card_resolved = (assets_resolved / brief["card_png_path"]).resolve()
                card_resolved.relative_to(assets_resolved)
            except (OSError, ValueError):
                reject(
                    "brand.brief.card_png_path resolves outside assets_dir — "
                    "the co-location rule (contract §2.3) is violated"
                )

    # --- Delivery targets (rules 6, 7) -----------------------------------
    # Deliberately NO "delivery_targets is empty" warning: contract §4.2 as
    # amended 2026-09-01. Open Q 1 made the field Creative-Editor-owned and
    # absent at PM handoff, so warning here flags the *correct* state and
    # teaches readers to tune warnings out. Phase 6's Creative Editor owns
    # that check, where an empty list actually means something is missing.
    dts = manifest.get("delivery_targets") or []

    presets_mod = None
    if dts:
        try:
            presets_mod = import_precut("precut_pipeline.presets")
        except Exception as e:  # pragma: no cover - defensive
            reject(f"could not load precut_pipeline.presets to validate "
                    f"delivery_targets: {type(e).__name__}: {e}")

    for dt in dts:
        dt_id = dt.get("id")
        status = dt.get("status")
        if status not in VALID_DELIVERY_TARGET_STATUS:
            reject(f"delivery_targets[{dt_id}]: status {status!r} is not a "
                    f"recognized enum value")

        if presets_mod is None:
            continue

        aspect_key = dt.get("aspect_key")
        platform_key = dt.get("platform_key")
        preset_key = dt.get("preset_key")

        if aspect_key not in presets_mod.ASPECT_PRESET_KEYS:
            reject(f"delivery_targets[{dt_id}]: aspect_key {aspect_key!r} is "
                    f"unknown to presets.py")

        if platform_key and platform_key != "none":
            platform = presets_mod.PLATFORMS_BY_KEY.get(platform_key)
            if platform is None:
                reject(f"delivery_targets[{dt_id}]: platform_key "
                        f"{platform_key!r} is unknown to presets.py")
            elif aspect_key not in platform.allowed_aspects:
                reject(f"delivery_targets[{dt_id}]: aspect_key {aspect_key!r} "
                        f"is not allowed for platform_key {platform_key!r}")

        if preset_key and preset_key not in presets_mod.PRESETS_BY_KEY:
            reject(f"delivery_targets[{dt_id}]: preset_key {preset_key!r} is "
                    f"unknown to presets.py")

    # --- Handoffs (rule 6) ------------------------------------------------
    for h in manifest.get("handoffs") or []:
        if h.get("role") not in VALID_HANDOFF_ROLES:
            reject(f"handoffs: role {h.get('role')!r} is not a recognized enum value")
        if h.get("action") not in VALID_HANDOFF_ACTIONS:
            reject(f"handoffs: action {h.get('action')!r} is not a recognized enum value")

    if mode == "intake":
        # Everything is a warning while Ryan is still talking to the PM —
        # the manifest is a legal draft (contract §4).
        return ValidationResult(errors=[], warnings=warnings + fatal)
    return ValidationResult(errors=fatal, warnings=warnings)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m posthouse.manifest",
        description="Build/validate a Project Manifest (manifest.json).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    validate_p = sub.add_parser("validate", help="Validate a manifest.json file.")
    validate_p.add_argument("path", type=Path, help="Path to manifest.json.")
    validate_p.add_argument(
        "--mode", choices=["intake", "handoff"], default="intake",
        help="Validation posture (default: intake).",
    )
    args = parser.parse_args(argv)

    if args.command == "validate":
        try:
            manifest = load_manifest(args.path)
        except (ManifestError, OSError, json.JSONDecodeError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

        result = validate_manifest(manifest, mode=args.mode)
        for w in result.warnings:
            print(f"warning: {w}")
        if result.errors:
            for e in result.errors:
                print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"OK — {args.path} is valid ({args.mode} mode)")
        return 0

    print(f"error: unknown command {args.command!r}", file=sys.stderr)  # pragma: no cover
    return 1


if __name__ == "__main__":
    sys.exit(_main())
