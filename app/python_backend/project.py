"""B-Roll Buddy project model.

A project is the top-level organizing unit. It tracks:
  - Source folders the user dropped in (aroll / broll / audio)
  - The kind classification for each folder
  - Per-file processing status (proxy done, transcript done, tagged, etc.)
  - Generated artifacts (plans, cutlists, exports)

Projects live on disk at:
    ~/Library/Application Support/Post House/projects/<project_name>/project.json

The source footage is NOT stored in the project dir — only references to it.
This way project metadata stays small and portable while footage stays wherever
the editor keeps it.

Concurrency: project.json is saved with a write-to-tempfile-then-atomic-rename
pattern so a crash mid-save never leaves a corrupt file.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Literal, Optional


# ---- Location helpers ------------------------------------------------------

def app_support_dir() -> Path:
    """Where project data lives. macOS convention.

    Deliberately "Post House", not "PreCut": this is a fork running
    alongside the real, separately-installed PreCut.app. Sharing its
    Application Support directory would mean writing into the same
    settings.json and projects/ that Ryan's production tool depends on.
    """
    # Note: HOME env var works cross-platform; on macOS this resolves to
    # /Users/<name>/Library/Application Support/Post House
    base = Path.home() / "Library" / "Application Support" / "Post House"
    base.mkdir(parents=True, exist_ok=True)
    (base / "projects").mkdir(exist_ok=True)
    return base


def projects_dir() -> Path:
    return app_support_dir() / "projects"


# Drop 4.16: portable projects via known_projects.json registry.
# Maps project name -> absolute custom root_dir chosen at creation time.
# Projects without an entry here live at the default projects_dir().

def _known_projects_file() -> Path:
    return app_support_dir() / "known_projects.json"


def _load_known_projects() -> dict:
    path = _known_projects_file()
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save_known_projects(registry: dict) -> None:
    path = _known_projects_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                                prefix=".known_projects.", suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(registry, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def register_project_location(name: str, root_dir: str) -> None:
    """Add/update a name -> root_dir entry so load_project(name) resolves it."""
    reg = _load_known_projects()
    reg[name] = str(Path(root_dir).expanduser().resolve())
    _save_known_projects(reg)


def unregister_project_location(name: str) -> None:
    """Remove an entry. No-op if not present."""
    reg = _load_known_projects()
    if name in reg:
        del reg[name]
        _save_known_projects(reg)


def project_dir_for(name: str, root_dir: Optional[str] = None) -> Path:
    """Path to a named project's data folder. Creates if missing.

    If `root_dir` is provided, that's used directly. Otherwise we consult
    the known_projects registry first (for projects created with a custom
    location), then fall back to the default app-support location.

    In all cases the required subfolders (transcripts, broll_index, etc.)
    are created as children of the resolved project dir.
    """
    if root_dir:
        p = Path(root_dir).expanduser().resolve()
    else:
        reg = _load_known_projects()
        if name in reg:
            p = Path(reg[name]).expanduser().resolve()
        else:
            safe = _sanitize_project_name(name)
            p = projects_dir() / safe
    p.mkdir(parents=True, exist_ok=True)
    for sub in ("transcripts", "broll_index", "audio_index", "plans", "exports"):
        (p / sub).mkdir(exist_ok=True)
    return p


def _sanitize_project_name(name: str) -> str:
    """Turn arbitrary user input into a safe folder name.

    Preserves spaces (users like 'Celeste Interview Week 1') but strips
    characters that would break on macOS.
    """
    name = name.strip() or "Untitled Project"
    # Disallow path separators and hidden-file prefix
    bad = '/\\:*?"<>|'
    cleaned = "".join(c if c not in bad else "-" for c in name)
    cleaned = cleaned.lstrip(".")
    return cleaned[:100]  # avoid absurdly long paths


# ---- Data model ------------------------------------------------------------

SourceKind = Literal["aroll", "broll", "audio"]


@dataclass
class SourceFolder:
    """One folder or file the user added to a zone.

    `root_path` is the user-added path. If it's a directory, all videos
    inside are proxied; if it's a single file, just that file.

    `kind` reflects the zone it was dropped into. Once classified, we
    never re-prompt — the zone chose the kind.
    """
    root_path: str              # absolute path to folder or file
    kind: SourceKind
    added_at: float             # unix timestamp
    display_name: str           # what UI shows (usually basename of path)
    is_file: bool               # True if single file, False if folder

    # Post House Project Manifest contract's dual_use flag (§2.3): this
    # A-roll source is ALSO processed as B-roll (the subject keeps talking
    # while the shooter grabs coverage). Only meaningful when kind=="aroll".
    # Added 2026-09-03 after Ryan found run_pipeline never tagged dual-use
    # footage: add_source is path-keyed with exactly one kind per path (see
    # its own docstring), so a dual-use folder never appeared in
    # sources_by_kind("broll") for pipeline.py's B-roll collection to find.
    # This flag lets that collection include it without needing a second,
    # conflicting SourceFolder record for the same path.
    dual_use: bool = False

    # Per-file status, keyed by absolute source file path
    # value is a dict with keys: proxy_status, proxy_path, transcript_status,
    # tag_status, errors. Not all stages apply to all kinds.
    files: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SourceFolder":
        return cls(
            root_path=d["root_path"],
            kind=d["kind"],
            added_at=d["added_at"],
            display_name=d["display_name"],
            is_file=d["is_file"],
            dual_use=d.get("dual_use", False),
            files=d.get("files", {}),
        )


@dataclass
class Project:
    """Top-level project. Persists as project.json."""
    name: str
    created_at: float
    updated_at: float
    version: int = 1

    # Source footage the user added
    sources: list[SourceFolder] = field(default_factory=list)

    # Selected deliverable preset (for Drop 2.5 / 3)
    current_plan_id: Optional[str] = None

    # Free-form settings (future use)
    settings: dict = field(default_factory=dict)

    # Drop 3.6 audio sync state (AudioSyncState.to_dict(), or None).
    # Populated by the audio_sync pipeline stage; consumed by the exporter
    # to place lav slices on parallel audio tracks.
    audio_sync: Optional[dict] = None

    # Drop 4.16: optional custom storage root_dir. When set, all project
    # data (project.json, transcripts/, broll_index/, plans/, exports/)
    # lives at this path instead of the default app-support location.
    # Makes projects portable — move the folder to another Mac + open
    # via "Open project from folder" and everything works.
    root_dir: Optional[str] = None

    # ----- Paths -----

    def dir(self) -> Path:
        return project_dir_for(self.name, self.root_dir)

    def project_file(self) -> Path:
        return self.dir() / "project.json"

    def transcripts_dir(self) -> Path:
        return self.dir() / "transcripts"

    def broll_index_dir(self) -> Path:
        return self.dir() / "broll_index"

    def broll_clip_count(self) -> int:
        """Count of indexed B-roll clips in this project's library.

        Reads from the project's broll_index/precut.db SQLite file
        (the same one load_broll_library() consumes at export time).
        Used by the frontend to label the "Include full B-roll library"
        export option with an actual number rather than a "?" placeholder.

        Drop 4.47.3: previously the frontend referenced a
        `broll_clip_count` field that was never set, so it always
        appeared as 0 / "?" even when the library contained clips.

        This method is intentionally defensive — any failure
        (missing DB, schema mismatch, locked file, IO error) returns 0
        rather than raising, since the count is purely a UI nicety and
        the export still works regardless.
        """
        db_path = self.broll_index_dir() / "precut.db"
        if not db_path.exists():
            return 0
        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute("SELECT COUNT(*) FROM clips").fetchone()
                return int(row[0]) if row and row[0] is not None else 0
            finally:
                conn.close()
        except Exception:
            return 0

    def audio_index_dir(self) -> Path:
        return self.dir() / "audio_index"

    def plans_dir(self) -> Path:
        return self.dir() / "plans"

    def exports_dir(self) -> Path:
        return self.dir() / "exports"

    # ----- Serialization -----

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
            "sources": [s.to_dict() for s in self.sources],
            "current_plan_id": self.current_plan_id,
            "settings": self.settings,
            "audio_sync": self.audio_sync,
            "root_dir": self.root_dir,
        }

    def to_wire_dict(self) -> dict:
        """Serialize for sending to the frontend. Adds live, derived
        fields that should NOT be persisted to project.json — like
        broll_clip_count, which is computed fresh from the SQLite DB
        every time and would otherwise go stale.

        Drop 4.47.3: split from to_dict() so save() (which writes to
        disk) doesn't bake derived counts into the project file. Use
        to_wire_dict() for any payload sent to the UI.
        """
        d = self.to_dict()
        d["broll_clip_count"] = self.broll_clip_count()
        # 2026-09-03: root_dir above is the RAW field -- None unless a
        # custom location was chosen at creation, which left PMTab's
        # "Project folder" picker with nothing to pre-fill for any
        # project using the default (hidden, Application-Support)
        # storage location. Real bug this caused: re-running Organize
        # on an already-open project with an empty picker field wrote a
        # brand-new, disconnected manifest.json wherever the user
        # happened to browse to, silently orphaned from the real
        # project. resolved_dir is always the TRUE working directory
        # (self.dir() falls back to the registry/default location),
        # so the frontend can always pre-fill correctly.
        d["resolved_dir"] = str(self.dir())
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        return cls(
            name=d["name"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            version=d.get("version", 1),
            sources=[SourceFolder.from_dict(s) for s in d.get("sources", [])],
            current_plan_id=d.get("current_plan_id"),
            settings=d.get("settings", {}),
            audio_sync=d.get("audio_sync"),
            root_dir=d.get("root_dir"),
        )

    def save(self) -> None:
        """Write atomically — tempfile then rename."""
        self.updated_at = time.time()
        target = self.project_file()
        target.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: if we crash mid-write, old file is intact
        fd, tmp_path = tempfile.mkstemp(
            dir=str(target.parent),
            prefix=".project.",
            suffix=".json.tmp",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.to_dict(), f, indent=2)
            os.replace(tmp_path, target)
        except Exception:
            # Clean up on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @classmethod
    def load(cls, name: str) -> "Project":
        """Load by name. Consults known_projects registry for custom-location
        projects; falls back to the default app-support location.

        Drop 4.44: fail fast without side-effects. Previously,
        project_dir_for() was called during load, which would mkdir()
        the registered path even if it pointed at a dead location, and
        then raise only when project.json was missing. That left empty
        stub folders on disk every time a user clicked a broken card.

        Now we:
          1. Resolve the path via the registry OR default location
             WITHOUT calling mkdir.
          2. If project.json doesn't exist at the resolved path, and we
             got that path from the registry, try the default location
             as a fallback (auto-heals drift where the registered path
             is stale but a default-location copy still exists).
          3. If BOTH fail, raise FileNotFoundError with a message the
             UI can use to offer "locate folder" / "remove from list".
        """
        # First, figure out where the registry says it is (if anywhere)
        reg = _load_known_projects()
        registered_path: Optional[Path] = None
        if name in reg:
            registered_path = Path(reg[name]).expanduser().resolve()

        # Candidate paths, in order of preference
        candidates: list[Path] = []
        if registered_path is not None:
            candidates.append(registered_path)
        # Also consider the default location — might exist as a legacy
        # folder from before the user moved it, or simply as a
        # non-portable default-location project.
        safe = _sanitize_project_name(name)
        default_path = projects_dir() / safe
        if default_path not in candidates:
            candidates.append(default_path)

        for candidate in candidates:
            pj = candidate / "project.json"
            if pj.exists():
                # Found. If this wasn't where the registry said, quietly
                # heal the registry so next time we don't walk the list.
                if registered_path is not None and candidate != registered_path:
                    reg[name] = str(candidate)
                    _save_known_projects(reg)
                with open(pj) as f:
                    return cls.from_dict(json.load(f))

        # No candidate yielded a project.json. Raise with a detail
        # payload the backend can surface to the UI.
        attempted = ", ".join(str(c) for c in candidates)
        raise FileNotFoundError(
            f"Project '{name}' not found. Looked in: {attempted}. "
            f"The folder may have been moved or deleted."
        )

    @classmethod
    def load_from_path(cls, folder: str) -> "Project":
        """Drop 4.16: open a project by browsing to its folder.

        Reads <folder>/project.json and updates the known_projects registry
        so subsequent "Open" by name finds it at the new location. This is
        the portability flow: user moves the project folder to another Mac
        and opens via "Open project from folder…".
        """
        folder_path = Path(folder).expanduser().resolve()
        pj = folder_path / "project.json"
        if not pj.exists():
            raise FileNotFoundError(
                f"No project.json found at {folder_path}. "
                f"Pick the folder that contains project.json."
            )
        with open(pj) as f:
            proj = cls.from_dict(json.load(f))

        # Force the root_dir to the folder we just opened from — the
        # original value stored in project.json might be stale (different
        # machine / different path). The user's action of opening from
        # this folder is authoritative.
        proj.root_dir = str(folder_path)

        # Register so load(name) finds it here next time
        register_project_location(proj.name, str(folder_path))

        # Save back with the corrected root_dir
        proj.save()
        return proj

    @classmethod
    def create(cls, name: str, root_dir: Optional[str] = None) -> "Project":
        """Create a new project. Raises if one with that name exists.

        If `root_dir` is provided (Drop 4.16+), the project is created
        there and registered so load(name) finds it. Otherwise the project
        lives at the default app-support location.
        """
        name = _sanitize_project_name(name)

        # Check for collisions at the target location
        target_dir = project_dir_for(name, root_dir)
        existing_file = target_dir / "project.json"
        if existing_file.exists():
            raise FileExistsError(f"Project already exists: {name}")

        now = time.time()
        proj = cls(
            name=name,
            created_at=now,
            updated_at=now,
            root_dir=str(Path(root_dir).expanduser().resolve()) if root_dir else None,
        )
        if root_dir:
            register_project_location(name, root_dir)
        proj.save()
        return proj

    # ----- Source management -----

    def add_source(
        self,
        path: str,
        kind: SourceKind,
    ) -> SourceFolder:
        """Register a new source folder/file. Idempotent by path."""
        abs_path = str(Path(path).resolve())
        # If already present with the same kind, reuse. If different kind,
        # we reclassify (user dragged the same folder into a different zone).
        for src in self.sources:
            if src.root_path == abs_path:
                if src.kind != kind:
                    src.kind = kind
                return src

        is_file = Path(abs_path).is_file()
        display = Path(abs_path).name or abs_path

        src = SourceFolder(
            root_path=abs_path,
            kind=kind,
            added_at=time.time(),
            display_name=display,
            is_file=is_file,
            files={},
        )
        self.sources.append(src)
        return src

    def remove_source(self, path: str) -> bool:
        abs_path = str(Path(path).resolve())
        before = len(self.sources)
        self.sources = [s for s in self.sources if s.root_path != abs_path]
        return len(self.sources) < before

    def set_dual_use(self, path: str, dual_use: bool) -> bool:
        """Flag/unflag an A-roll source as also-B-roll. Only valid on
        kind=="aroll" sources -- returns False (no-op) otherwise, since
        dual_use only means anything relative to A-roll's own primary use.
        """
        abs_path = str(Path(path).resolve())
        for src in self.sources:
            if src.root_path == abs_path:
                if src.kind != "aroll":
                    return False
                src.dual_use = dual_use
                return True
        return False

    def sources_by_kind(self, kind: SourceKind) -> list[SourceFolder]:
        return [s for s in self.sources if s.kind == kind]

    def find_source_for_file(self, file_path: str) -> Optional[SourceFolder]:
        """Given a source file path, find which SourceFolder it belongs to."""
        abs_path = Path(file_path).resolve()
        for src in self.sources:
            root = Path(src.root_path).resolve()
            if src.is_file:
                if abs_path == root:
                    return src
            else:
                try:
                    abs_path.relative_to(root)
                    return src
                except ValueError:
                    continue
        return None

    # ----- File status tracking -----

    def update_file_status(
        self,
        file_path: str,
        **updates,
    ) -> None:
        """Merge status updates for a specific source file."""
        src = self.find_source_for_file(file_path)
        if src is None:
            return
        abs_path = str(Path(file_path).resolve())
        existing = src.files.get(abs_path, {})
        existing.update(updates)
        src.files[abs_path] = existing


# ---- Registry operations ---------------------------------------------------

def list_projects() -> list[dict]:
    """Return a lightweight summary of all projects, sorted by updated_at desc.

    Drop 4.16: merges projects from both locations:
      1. Default: ~/Library/Application Support/Post House/projects/*/
      2. Custom: each entry in known_projects.json → read its project.json

    If an entry in known_projects.json points to a missing or deleted folder
    (e.g. user deleted the folder in Finder), it's silently skipped but not
    un-registered. The user can re-open from a folder to update the registry.
    """
    result = []
    seen_names: set[str] = set()

    # 1. Known-location projects (custom root_dirs)
    known = _load_known_projects()
    for name, root_dir in known.items():
        pj = Path(root_dir) / "project.json"
        if not pj.exists():
            # Stale entry — skip but don't remove (user might remount a drive)
            continue
        try:
            with open(pj) as f:
                d = json.load(f)
            result.append({
                "name": d["name"],
                "created_at": d["created_at"],
                "updated_at": d["updated_at"],
                "source_count": len(d.get("sources", [])),
                "root_dir": root_dir,
                "is_portable": True,
            })
            seen_names.add(d["name"])
        except Exception:
            continue

    # 2. Default-location projects (scan the projects_dir)
    for pdir in projects_dir().iterdir():
        if not pdir.is_dir():
            continue
        pj = pdir / "project.json"
        if not pj.exists():
            continue
        try:
            with open(pj) as f:
                d = json.load(f)
            # Skip if already accounted for via the registry (a project with
            # a custom root_dir happens to ALSO have a default-location
            # sibling folder — should never happen, but defensive)
            if d["name"] in seen_names:
                continue
            result.append({
                "name": d["name"],
                "created_at": d["created_at"],
                "updated_at": d["updated_at"],
                "source_count": len(d.get("sources", [])),
                "root_dir": None,
                "is_portable": False,
            })
        except Exception:
            continue

    result.sort(key=lambda r: r["updated_at"], reverse=True)
    return result


def delete_project(name: str) -> bool:
    """Delete a project's metadata. Does NOT touch source footage or proxies.

    Drop 4.16: also removes the project from the known_projects registry
    if it was registered as a custom-location project.

    Drop 4.44: hardened against stale-registry + missing-folder cases.
    If the project folder doesn't exist on disk (external drive unplugged,
    user deleted the folder in Finder, etc.), we still clean up the registry
    entry so the user's click on the × button results in the card
    disappearing from the list. Previously this path silently returned
    False and left the card stranded, giving the impression that delete
    didn't work.

    Returns True if anything was cleaned up (folder removed OR registry
    entry removed), False only if there was no trace of the project at all.
    """
    import shutil

    cleaned_something = False

    # 1. Look up the registered path DIRECTLY from the registry, before
    #    calling project_dir_for(). project_dir_for() has a side effect
    #    where it mkdir-creates the folder if missing — that would cause
    #    us to rmtree an empty folder we just created and then confusingly
    #    report success. Going through the registry directly avoids that
    #    round-trip.
    registry = _load_known_projects()
    registered_path: Optional[Path] = None
    if name in registry:
        registered_path = Path(registry[name]).expanduser().resolve()

    # Also consider the default-location path even if nothing's registered,
    # because a project without a custom root_dir lives at projects_dir()/<safe_name>/.
    default_path = projects_dir() / _sanitize_project_name(name)

    # 2. Try to delete whichever path(s) exist on disk.
    for candidate in (registered_path, default_path):
        if candidate is None:
            continue
        if candidate.exists() and candidate.is_dir():
            try:
                shutil.rmtree(candidate)
                cleaned_something = True
            except OSError:
                # Something's wrong with permissions or the drive is
                # read-only. Don't give up yet — we can still unregister.
                pass

    # 3. ALWAYS clean up the registry entry, even if no folder was
    #    deletable. This is the "user's drive isn't mounted, they
    #    just want the card gone" case.
    if name in registry:
        unregister_project_location(name)
        cleaned_something = True

    return cleaned_something


def proxy_dir_for(source_file: Path) -> Path:
    """Where proxies go for a given source file.

    Convention: <parent_of_file>/proxies/<file_stem>.mp4
    For a file at /foot/Celeste Intv/A001.mov, proxies go at
    /foot/Celeste Intv/proxies/A001.mp4.
    """
    return Path(source_file).parent / "proxies"
