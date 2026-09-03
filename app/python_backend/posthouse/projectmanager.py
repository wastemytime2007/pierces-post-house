"""posthouse.projectmanager — the Project Manager role, headless.

Phase 2's final slice (ROADMAP.md §6 Phase 2 item 2; docs/STATUS.md
"Next slice of Phase 2"). This is where the pieces already built —
``posthouse.manifest`` (the contract builder/validator) and
``posthouse.brandbrief`` (the non-importable-asset bridge) — become the
actual Project Manager: raw footage folders plus structured intake
answers go in, an organized, validated, handoff-ready project comes out.
No conversational intake and no UI (ROADMAP.md ground rule 7 "Roles
before shell") — the intake answers arrive as structured arguments to
:func:`organize_project`, exactly as ``manifest.py``'s own docstring
already scopes this package.

The load-bearing ruling (contract §2.3, ROADMAP.md's footage-portability
Decision Log entry), restated because it drives this module's structure
--------------------------------------------------------------------------
**Footage is never copied or moved.** ``project.root_dir`` is (or
contains) wherever the footage folders already sit; this module stages
``assets_dir`` as a NEW sibling directory under that same root and copies
brand files into it — never anything from a declared source. The only
call in this file that touches ``shutil.copytree`` is
:func:`_stage_brand_assets`, and it is the only place in the module that
writes bytes anywhere under a declared source's own folder. Every other
touch of a source path is read-only (``Path.rglob``, ``Path.stat``) for
the intake census and shoot-date derivation. :func:`_stage_brand_assets`
additionally asserts the brand-assets source directory does not equal or
nest inside (or contain) any declared footage source before copying a
single byte, so a caller who accidentally points ``--assets`` at a
footage folder gets a hard failure, not silently duplicated media.

Per-source census (contract §2.4 ``media{}``)
------------------------------------------------
Counted by **extension classification** via the harvested
``auto_include.kind_for_path`` (video/audio/image), never by decoding or
probing each file with ffprobe. A card dump can be thousands of files;
this is an intake snapshot ("how much stuff, roughly what kind"), not
per-file analysis — that's the Assistant Editor's job in Phase 4, on
files this role has already organized. Files ``kind_for_path`` can't
classify count toward ``other_count`` and are aggregated into
``unsupported[]`` by extension, reusing
``posthouse.manifest.categorize_unsupported`` (which itself reuses the
harvested ``auto_include.unsupported_reason()`` verbatim) — the exact
function ``manifest.py`` already uses for the same purpose, so this
module adds no second reason-string implementation.

Shoot dates (contract §2.2, ratified: read, no confirmation step)
--------------------------------------------------------------------
Derived from the video files across every declared source (excluding
``assets``), using each file's **creation timestamp**:
``os.stat(...).st_birthtime`` where the platform provides it (macOS —
Ryan's Mac, where real footage work happens), falling back to
``st_mtime`` everywhere else (Linux has no birthtime in ``os.stat_result``
without a separate ``os.statx`` call this module deliberately doesn't
make, since ``st_mtime`` is always present and "when this file was last
written" is a reasonable proxy for "when it was shot" for camera-original
footage that is never edited after ingest). The distinct set of resulting
calendar dates (local time, matching how a camera's clock and folder
dates are normally read) is sorted and written as ISO ``YYYY-MM-DD``
strings. Recomputed from every source's current files on every run (not
cached), so late footage naturally extends ``project.shoot_dates`` on a
re-run without special-casing it.

Idempotent re-runs / late footage (ratified: late footage = new revision)
------------------------------------------------------------------------
If ``<root_dir>/manifest.json`` already exists, :func:`organize_project`
loads it and treats the run as an update: a declared source whose
resolved path already matches an existing source is left completely
untouched (no re-mint, no recompute) — only genuinely new source paths
are appended via :func:`posthouse.manifest.add_source`, which mints a
fresh frozen id without touching any prior id. Brand assets are always
re-staged (``shutil.copytree(..., dirs_exist_ok=True)`` overwrites files
of the same name in place — "refreshed, not duplicated"). ``revision``
is left to :func:`posthouse.manifest.save_manifest`'s own existing rule
(bumped on every write except the very first).

The final gate
---------------
Before anything is written, the assembled manifest (handoff entry
included) is run through
``posthouse.manifest.validate_manifest(mode="handoff")``. If it does not
pass, :func:`organize_project` raises :class:`OrganizeError` carrying
every error, and writes nothing — an existing ``manifest.json`` on disk
is left exactly as it was.

Entry points
------------
* Python API: :func:`organize_project`, returning an :class:`OrganizeResult`.
* CLI: ``python -m posthouse.projectmanager organize --root DIR --client
  NAME --project NAME --type TYPE --source PATH:KIND[:dual_use]
  [--source ...] [--assets DIR]`` — exits non-zero listing every problem
  on failure.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import brandbrief as B
from . import manifest as M
from .harvest import auto_include as _auto_include
from .harvest import camera_inference as _camera_inference
from .precut_bridge import PIN_FILE

# Sibling directory brand/other small assets are staged into, under
# project.root_dir. Matches the contract's own worked example (§3:
# "Company Branding" under the project root) verbatim, so a hand-inspection
# of the contract's example and this module's default never diverge.
# Renamed from "Brand Assets" 2026-09-03 (Ryan, after Task 1.1's real run).
DEFAULT_ASSETS_SUBDIR = "Company Branding"

MANIFEST_FILENAME = "manifest.json"

# Camera/shot-type tags that are inconsistent with a source DECLARED as
# spoken-word footage (aroll / source_audio) — a locked-off interview
# does not come from a drone, and a lav recorder does not shoot a
# timelapse. This is a judgment call the ratified contract leaves open
# (§2.4 defines the `inference.agrees_with_declaration` FIELD, not its
# comparison rule): broll and assets are compatible with any camera tag
# by definition (broll IS coverage, including aerial/timelapse), so only
# aroll/source_audio are checked against this set. Recorded, never
# authoritative — the user's declared kind always wins (contract §2.4).
_TAGS_INCONSISTENT_WITH_SPOKEN_WORD = {"drone", "aerial", "timelapse"}


class OrganizeError(Exception):
    """Raised when :func:`organize_project` cannot produce a valid,
    handoff-ready manifest. ``errors`` carries every problem found (the
    manifest validator is exhaustive, not fail-fast) — nothing is ever
    written to disk when this is raised."""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors) or "organize_project failed")


@dataclass
class OrganizeResult:
    """What :func:`organize_project` produced."""
    manifest: dict
    manifest_path: Path
    is_new_project: bool
    added_source_ids: list[str] = field(default_factory=list)
    staged_asset_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Precut pin (read via precut_bridge, never hardcoded)
# ---------------------------------------------------------------------------

def _read_precut_pin() -> str:
    if not PIN_FILE.exists():
        return ""
    return PIN_FILE.read_text(encoding="utf-8").strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Per-source census (contract §2.4 media{} + unsupported[])
# ---------------------------------------------------------------------------

# Directories PreCut itself writes next to originals and that must never be
# counted as footage. Same names multi_exporter._find_original_for_proxy's
# _scan skips (`skip_names=("proxies", "PreCut_Output")`), harvested rather
# than re-invented so the PM and the exporter agree on what "not footage"
# means. Found the hard way on the first real-footage run (Runnells Day 1):
# the census reported 6 videos for a 2-clip shoot and a phantom July shoot
# date, because it walked into Osmo/proxies/ and counted PreCut's proxies
# plus their macOS `._*` AppleDouble sidecars (which also end in .mp4).
_SKIP_DIR_NAMES = frozenset({"proxies", "PreCut_Output"})


def _is_hidden(p: Path) -> bool:
    """macOS `._*` AppleDouble sidecars, `.DS_Store`, and any other dotfile:
    metadata, never media."""
    return p.name.startswith(".")


def _iter_files(path: Path) -> list[Path]:
    if not path.exists():
        return []
    if path.is_file():
        return [] if _is_hidden(path) else [path]
    out: list[Path] = []
    for p in path.rglob("*"):
        if not p.is_file() or _is_hidden(p):
            continue
        rel_parts = p.relative_to(path).parts[:-1]
        if any(part in _SKIP_DIR_NAMES or part.startswith(".") for part in rel_parts):
            continue
        out.append(p)
    return out


def census_source(path: Path) -> tuple[dict, list[dict], list[Path]]:
    """Build ``(media, unsupported, video_files)`` for one source folder
    (or single file). Extension classification only (see module
    docstring) — never ffprobe per file. ``video_files`` is returned so
    the caller can fold it into shoot-date derivation without a second
    filesystem walk.
    """
    counts = {"video_count": 0, "audio_count": 0, "image_count": 0, "other_count": 0}
    total_bytes = 0
    unsupported_ext_counts: dict[str, int] = {}
    video_files: list[Path] = []

    for f in _iter_files(path):
        try:
            total_bytes += f.stat().st_size
        except OSError:
            pass

        kind = _auto_include.kind_for_path(f)
        if kind == "video":
            counts["video_count"] += 1
            video_files.append(f)
        elif kind == "audio":
            counts["audio_count"] += 1
        elif kind == "image":
            counts["image_count"] += 1
        else:
            counts["other_count"] += 1
            ext = f.suffix.lower() or "(no extension)"
            unsupported_ext_counts[ext] = unsupported_ext_counts.get(ext, 0) + 1

    media = dict(counts)
    media["total_bytes"] = total_bytes
    unsupported = [
        M.categorize_unsupported(ext, count)
        for ext, count in sorted(unsupported_ext_counts.items())
    ]
    return media, unsupported, video_files


# ---------------------------------------------------------------------------
# Inference (contract §2.4 inference{}) — recorded, never authoritative.
# ---------------------------------------------------------------------------

def infer_source(path: Path, declared_kind: str, precut_pin: str) -> dict:
    """Build one ``sources[].inference`` entry. Calls the harvested
    ``camera_inference.infer_camera_tags`` (pure path/filename pattern
    matching — no file I/O of its own) against the source folder's path,
    which also lets it catch camera-model subfolders (e.g. a `Mavic 2/`
    folder), exactly as PreCut's own docstring for that function
    describes. ``agrees_with_declaration`` uses the judgment-call rule at
    the top of this module; the declared ``kind`` always wins downstream,
    this field is diagnostic only.
    """
    tags = _camera_inference.infer_camera_tags(path)
    agrees = True
    if declared_kind in ("aroll", "source_audio"):
        agrees = not (set(tags) & _TAGS_INCONSISTENT_WITH_SPOKEN_WORD)
    return {
        "camera_tags": tags,
        "method": f"camera_inference@{precut_pin}",
        "agrees_with_declaration": agrees,
    }


# ---------------------------------------------------------------------------
# Shoot dates (contract §2.2, ratified: read, no confirmation)
# ---------------------------------------------------------------------------

def _file_creation_date(path: Path):
    st = path.stat()
    try:
        ts = st.st_birthtime  # macOS: true creation time.
    except AttributeError:
        ts = st.st_mtime  # Fallback where the platform has no birthtime.
    return datetime.fromtimestamp(ts).date()


def derive_shoot_dates(video_files: list[Path]) -> list[str]:
    """Distinct, sorted ISO ``YYYY-MM-DD`` dates from ``video_files``'
    creation timestamps (see module docstring for the birthtime/mtime
    fallback). Deterministic for a fixed set of files with fixed
    timestamps; empty list if there are no video files at all."""
    dates = set()
    for f in video_files:
        try:
            dates.add(_file_creation_date(f).isoformat())
        except OSError:
            continue
    return sorted(dates)


# ---------------------------------------------------------------------------
# Asset staging (the ONLY place this module copies bytes)
# ---------------------------------------------------------------------------

def _assert_no_footage_overlap(brand_source: Path, source_paths: list[Path]) -> None:
    """Refuse to stage assets if ``brand_source`` equals, contains, or is
    contained by any declared footage source. This is the code-level
    guarantee behind "footage is never copied": if this assertion cannot
    be satisfied, staging does not proceed."""
    brand_parts = brand_source.parts
    for sp in source_paths:
        sp_parts = sp.parts
        if brand_parts == sp_parts:
            raise OrganizeError([
                f"brand assets source {brand_source} is the same path as "
                f"declared footage source {sp}; refusing to stage it as "
                f"an asset (footage is never copied, contract §2.3)"
            ])
        shorter, longer = (
            (brand_parts, sp_parts) if len(brand_parts) <= len(sp_parts)
            else (sp_parts, brand_parts)
        )
        if longer[: len(shorter)] == shorter:
            raise OrganizeError([
                f"brand assets source {brand_source} and footage source "
                f"{sp} are nested inside one another; refusing to stage "
                f"(footage is never copied, contract §2.3)"
            ])


def _stage_brand_assets(
    brand_source_dir: Path, assets_dir: Path, source_paths: list[Path]
) -> list[str]:
    """Copy ``brand_source_dir`` into ``assets_dir`` (a NEW sibling under
    ``project.root_dir``, never a declared footage source), preserving
    filenames and subfolder structure. The only ``shutil`` call in this
    module. Returns the list of relative paths staged.
    """
    brand_resolved = brand_source_dir.resolve()
    if not brand_resolved.is_dir():
        raise OrganizeError([f"brand assets source {brand_source_dir} is not a directory"])

    _assert_no_footage_overlap(brand_resolved, [p.resolve() for p in source_paths])

    assets_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(brand_resolved, assets_dir, dirs_exist_ok=True)

    staged = sorted(
        str(p.relative_to(assets_dir).as_posix())
        for p in assets_dir.rglob("*") if p.is_file()
    )

    # Belt-and-braces: prove nothing under a declared footage source ended
    # up inside assets_dir. copytree only ever reads from brand_resolved
    # (already proven disjoint above), so this can only fail if a caller
    # bypasses this function entirely — which nothing in this module does.
    assets_resolved = assets_dir.resolve()
    for sp in source_paths:
        sp_resolved = sp.resolve()
        assert not str(assets_resolved).startswith(str(sp_resolved) + "/"), (
            "assets_dir must never be staged inside a footage source"
        )
        assert not str(sp_resolved).startswith(str(assets_resolved) + "/"), (
            "a footage source must never be staged inside assets_dir"
        )

    return staged


# ---------------------------------------------------------------------------
# organize_project — the PM's entry point
# ---------------------------------------------------------------------------

def organize_project(
    *,
    root_dir,
    client_name: str,
    project_name: str,
    project_type: str,
    sources: list[dict],
    brand_assets_source_dir=None,
    people: Optional[list[dict]] = None,
    default_includes: Optional[list[dict]] = None,
    client_contact: Optional[str] = None,
    client_notes: Optional[str] = None,
    locations: Optional[list[dict]] = None,
    project_notes: Optional[str] = None,
    audience_goal: Optional[str] = None,
    assets_subdir: str = DEFAULT_ASSETS_SUBDIR,
    generator_name: str = "posthouse.pm",
    generator_version: str = "0.1.0",
) -> OrganizeResult:
    """Organize a project: census + infer every declared source, stage
    brand assets (if any) alongside the footage, build or extend
    ``manifest.json``, and emit it once it passes handoff validation.

    ``sources`` is a list of ``{"path": str, "kind": "aroll"|"broll"|
    "source_audio"|"assets", "dual_use"?: bool, "notes"?: str,
    "display_name"?: str, "subject_ids"?: [str]}`` — the structured
    intake answers (no conversation happens here, ROADMAP.md ground rule
    7). Every source's ``media``, ``unsupported``, and ``inference`` are
    computed by this function; callers never pass those in.

    Re-running against an existing ``<root_dir>/manifest.json`` is an
    update, not a rebuild (see module docstring): sources already present
    (by resolved path) are left untouched, new ones are appended with
    fresh ids, and ``project.shoot_dates`` is recomputed from whatever
    video footage is on disk right now.

    Raises :class:`OrganizeError` (never writes anything) if the
    assembled manifest fails
    ``posthouse.manifest.validate_manifest(mode="handoff")``.
    """
    root_dir = Path(root_dir)
    manifest_path = root_dir / MANIFEST_FILENAME
    precut_pin = _read_precut_pin()

    is_new_project = not manifest_path.exists()
    existing_manifest: Optional[dict] = None
    if not is_new_project:
        existing_manifest = M.load_manifest(manifest_path)

    existing_source_paths: dict[str, str] = {}
    if existing_manifest:
        for s in existing_manifest.get("sources", []):
            try:
                resolved = str(Path(s["path"]).resolve())
            except OSError:
                resolved = s["path"]
            existing_source_paths[resolved] = s["id"]

    # --- Census + inference for every NEW declared source ------------------
    new_specs: list[dict] = []
    all_video_files: list[Path] = []
    declared_source_paths: list[Path] = []

    for raw in sources:
        spath = Path(raw["path"])
        declared_source_paths.append(spath)
        try:
            resolved = str(spath.resolve())
        except OSError:
            resolved = str(spath)

        if resolved in existing_source_paths:
            # Already declared in a prior run. Identity is FROZEN (id,
            # added_at, dual_use, notes, display_name, subject_ids never
            # change here), but the snapshot fields REFRESH: media census,
            # unsupported[], and inference are re-read from disk. A re-run
            # is exactly the moment a stale snapshot should update; found
            # on Runnells Day 1, where revision 2 kept a wrong video_count
            # from revision 1 while shoot_dates (also disk-derived) had
            # already corrected itself. Cost is identical either way: the
            # folder was being walked for shoot dates regardless.
            media, unsupported, vids = census_source(spath)
            all_video_files.extend(vids)
            existing_id = existing_source_paths[resolved]
            for s in (existing_manifest or {}).get("sources", []):
                if s.get("id") == existing_id:
                    s["media"] = media
                    if unsupported:
                        s["unsupported"] = unsupported
                    else:
                        s.pop("unsupported", None)
                    s["inference"] = infer_source(spath, s.get("kind", raw["kind"]), precut_pin)
                    break
            continue

        kind = raw["kind"]
        media, unsupported, video_files = census_source(spath)
        all_video_files.extend(video_files)
        inference = infer_source(spath, kind, precut_pin)

        spec: dict = {
            "path": str(spath),
            "kind": kind,
            "media": media,
            "unsupported": unsupported,
            "inference": inference,
        }
        if raw.get("display_name"):
            spec["display_name"] = raw["display_name"]
        if raw.get("dual_use"):
            spec["dual_use"] = True
        if raw.get("notes"):
            spec["notes"] = raw["notes"]
        if raw.get("subject_ids"):
            spec["subject_ids"] = raw["subject_ids"]
        if raw.get("is_file"):
            spec["is_file"] = True
        new_specs.append(spec)

    # Also fold in video files from sources already on the manifest from a
    # PRIOR run that weren't in this call's `sources` list at all (late
    # footage additions shouldn't shrink shoot_dates just because the
    # caller only passed the new folder this time).
    if existing_manifest:
        this_call_paths_resolved = set()
        for raw in sources:
            try:
                this_call_paths_resolved.add(str(Path(raw["path"]).resolve()))
            except OSError:
                this_call_paths_resolved.add(str(raw["path"]))
        for s in existing_manifest.get("sources", []):
            try:
                resolved = str(Path(s["path"]).resolve())
            except OSError:
                resolved = s["path"]
            if resolved not in this_call_paths_resolved:
                _, _, vids = census_source(Path(s["path"]))
                all_video_files.extend(vids)

    shoot_dates = derive_shoot_dates(all_video_files)

    # --- Build or extend the manifest --------------------------------------
    added_source_ids: list[str] = []

    if is_new_project:
        people_specs = [{"name": p["name"], "role": p.get("role", "other")} for p in (people or [])]
        manifest = M.build_manifest(
            project_name=project_name,
            root_dir=str(root_dir),
            client_name=client_name,
            client_contact=client_contact,
            client_notes=client_notes,
            project_type=project_type,
            shoot_dates=shoot_dates,
            locations=locations,
            people=people_specs,
            project_notes=project_notes,
            audience_goal=audience_goal,
            sources=new_specs,
            default_includes=default_includes,
            generator_name=generator_name,
            generator_version=generator_version,
            precut_pin=precut_pin,
        )
        added_source_ids = [s["id"] for s in manifest["sources"]]
    else:
        manifest = existing_manifest
        for spec in new_specs:
            new_id = M.add_source(manifest, **spec)
            added_source_ids.append(new_id)
        manifest.setdefault("project", {})["shoot_dates"] = shoot_dates
        if people:
            existing_people = manifest["project"].setdefault("people", [])
            existing_ids = [p["id"] for p in existing_people]
            existing_names = {p["name"] for p in existing_people}
            for p in people:
                if p["name"] in existing_names:
                    continue
                pid = M._mint_person_id(p["name"], existing_ids)
                existing_ids.append(pid)
                existing_people.append({"id": pid, "name": p["name"], "role": p.get("role", "other")})
        if default_includes:
            manifest["default_includes"] = default_includes

    # --- Stage brand assets --------------------------------------------------
    staged_asset_files: list[str] = []
    if brand_assets_source_dir is not None:
        assets_dir = root_dir / assets_subdir
        overlap_check_paths = list(declared_source_paths) + [
            Path(p) for p in existing_source_paths
        ]
        staged_asset_files = _stage_brand_assets(
            Path(brand_assets_source_dir), assets_dir, overlap_check_paths
        )
        brand = B.build_brand_section(assets_dir)
        brand = B.generate_brief(brand, assets_dir, client_name=client_name)
        colocation_problems = B.validate_brief_colocation(brand, assets_dir)
        if colocation_problems:
            raise OrganizeError(colocation_problems)
        manifest["brand"] = brand

    # --- Handoff record (append-only) + the final gate ----------------------
    target_revision = 1 if is_new_project else int(existing_manifest.get("revision", 1)) + 1
    manifest.setdefault("handoffs", []).append({
        "role": "project_manager",
        "action": "emitted",
        "at": _now_iso(),
        "revision": target_revision,
        "agent": f"{generator_name}/{generator_version}",
    })

    result = M.validate_manifest(manifest, mode="handoff")
    if not result.ok:
        raise OrganizeError(result.errors)

    manifest["validation"] = {
        "ran_at": _now_iso(),
        "mode": "handoff",
        "errors": result.errors,
        "warnings": result.warnings,
    }

    M.save_manifest(manifest, manifest_path)

    return OrganizeResult(
        manifest=manifest,
        manifest_path=manifest_path,
        is_new_project=is_new_project,
        added_source_ids=added_source_ids,
        staged_asset_files=staged_asset_files,
        warnings=result.warnings,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_source_arg(raw: str) -> dict:
    parts = raw.split(":")
    if len(parts) == 2:
        path, kind = parts
        dual_use = False
    elif len(parts) == 3:
        path, kind, flag = parts
        if flag != "dual_use":
            raise argparse.ArgumentTypeError(
                f"unknown source flag {flag!r} in {raw!r}; expected 'dual_use'"
            )
        dual_use = True
    else:
        raise argparse.ArgumentTypeError(
            f"--source must be PATH:KIND[:dual_use], got {raw!r}"
        )
    spec = {"path": path, "kind": kind}
    if dual_use:
        spec["dual_use"] = True
    return spec


def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m posthouse.projectmanager",
        description="Organize a project: stage assets, census + infer sources, emit manifest.json.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    org = sub.add_parser("organize", help="Organize a project and emit its manifest.")
    org.add_argument("--root", required=True, type=Path, help="Project root_dir.")
    org.add_argument("--client", required=True, help="Client name.")
    org.add_argument("--project", required=True, help="Project name.")
    org.add_argument("--type", required=True, dest="project_type", help="project_type enum value.")
    org.add_argument(
        "--source", action="append", default=[], dest="sources",
        type=_parse_source_arg, help="PATH:KIND[:dual_use] (repeatable).",
    )
    org.add_argument("--assets", default=None, type=Path, help="Brand assets source directory.")
    args = parser.parse_args(argv)

    if args.command != "organize":  # pragma: no cover - argparse enforces this
        print(f"error: unknown command {args.command!r}", file=sys.stderr)
        return 1

    if not args.sources:
        print("error: at least one --source is required", file=sys.stderr)
        return 1

    try:
        result = organize_project(
            root_dir=args.root,
            client_name=args.client,
            project_name=args.project,
            project_type=args.project_type,
            sources=args.sources,
            brand_assets_source_dir=args.assets,
        )
    except OrganizeError as e:
        for err in e.errors:
            print(f"error: {err}", file=sys.stderr)
        return 1
    except Exception as e:  # pragma: no cover - defensive: never hang, never crash bare
        print(f"error: unexpected failure organizing the project: "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1

    for w in result.warnings:
        print(f"warning: {w}")
    print(f"OK - wrote {result.manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
