#!/usr/bin/env python3
"""B-Roll Buddy app backend — Drop 2.

Adds project-awareness and auto-chained pipeline stages to the Drop 1 model.

COMMAND_SCHEMA (stdin → backend):
    {"type": "ping"}

    Project lifecycle:
    {"type": "list_projects"}
    {"type": "create_project", "name": "..."}
    {"type": "load_project", "name": "..."}
    {"type": "delete_project", "name": "..."}

    Source management (requires a loaded project):
    {"type": "add_source", "path": "/abs/path", "kind": "aroll|broll|audio"}
    {"type": "remove_source", "path": "/abs/path"}
    {"type": "get_project_state"}

    Pipeline execution:
    {"type": "run_pipeline", "job_id": "run-1",
     "run_proxies": true, "run_transcription": true,
     "run_tagging": true, "run_audio_index": true}
    {"type": "cancel_job", "job_id": "run-1"}

    Misc:
    {"type": "shutdown"}

    Post House Task 1.1 -- Project Manager (independent of the project
    lifecycle above; no _current_project required):
    {"type": "organize_project", "root_dir": "...", "client_name": "...",
     "project_name": "...", "project_type": "interview|property_tour|...",
     "sources": [{"path": "...", "kind": "aroll|broll|source_audio|assets"}],
     "brand_assets_source_dir": "..."?}

    (This docstring already drifted from HANDLERS before Task 1.1 --
    analyze/plan_directed/story_generate/export_timelines etc. were added
    in later drops without updating it. Not fixed here; out of scope for
    this task. HANDLERS is the source of truth.)

EVENT_SCHEMA (backend → stdout):
    {"type": "ready", "version": "0.2.0-drop2"}
    {"type": "projects_list", "projects": [...]}
    {"type": "project_created", "project": {...}}
    {"type": "project_loaded", "project": {...}}
    {"type": "project_state", "project": {...}}
    {"type": "source_added", "source": {...}}
    {"type": "source_removed", "path": "..."}
    {"type": "project_organized", "manifest": {...}, "manifest_path": "...",
     "is_new_project": bool, "added_source_ids": [...],
     "staged_asset_files": [...], "warnings": [...]}

    {"type": "pipeline_started", "job_id", "project", "stages": {...}}
    {"type": "pipeline_scan_complete", "job_id", "counts": {...}}
    {"type": "stage_started", "job_id", "stage", "total"}
    {"type": "file_done", "job_id", "stage", "file", "status", ...}
    {"type": "stage_complete", "job_id", "stage", ...}
    {"type": "stage_error", "job_id", "stage", "message"}
    {"type": "pipeline_complete", "job_id", "ok", "cancelled"}

    {"type": "error", "message", "job_id"?, "traceback"?}
    {"type": "log", "level": "info|warn|error", "message"}
"""
import json
import os
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

# Vendored posthouse/ (Task 1.1, Project Manager tab) checks its PreCut pin
# on import, defaulting PRECUT_ROOT to "/home/user/precut" if unset -- which
# doesn't exist on this Mac and would print a scary but harmless mismatch
# warning on every launch. Point it at the real, correctly-pinned donor
# checkout instead. setdefault so an explicit env var (e.g. for testing a
# different checkout) still wins.
os.environ.setdefault("PRECUT_ROOT", str(Path.home() / "precut-checkout"))

from project import (
    Project, list_projects, delete_project, _sanitize_project_name,
    unregister_project_location,
)
from pipeline import PipelineJob, run_pipeline
from producer import (
    run_analyze, run_directed_plan, run_refine,
    list_ideas, delete_idea,
    run_generate_angles, set_angle_preset, set_angle_platform_and_aspect,
)
from settings import (
    apply_settings_to_env, get_api_key_summary, set_api_key,
    set_workspace_id,
    set_onboarding_flag,
    get_auto_include_rules, set_auto_include_rules,
    get_audience_profiles, set_audience_profiles,
)
from exporter import run_export, ExportOptions

# Task 1.1: Project Manager tab. posthouse.projectmanager is the existing,
# already-tested function (posthouse/ has its own 223-test suite in the
# pierces-post-house repo) -- reused directly here, not reimplemented.
from posthouse.projectmanager import organize_project, OrganizeError


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

# The currently-loaded project. Most commands assume one is loaded.
_current_project: Optional[Project] = None
_project_lock = threading.Lock()

# Active jobs and their cancel flags
_jobs: dict[str, "ActiveJob"] = {}
_jobs_lock = threading.Lock()

_executor = ThreadPoolExecutor(max_workers=4)
_stdout_lock = threading.Lock()


class ActiveJob:
    def __init__(self, job_id: str, cancel_flag: threading.Event):
        self.job_id = job_id
        self.cancel_flag = cancel_flag


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def emit(event: dict[str, Any]) -> None:
    with _stdout_lock:
        sys.stdout.write(json.dumps(event) + "\n")
        sys.stdout.flush()


def log(level: str, message: str) -> None:
    emit({"type": "log", "level": level, "message": message})


def err(message: str, job_id: Optional[str] = None, tb: Optional[str] = None) -> None:
    ev = {"type": "error", "message": message}
    if job_id:
        ev["job_id"] = job_id
    if tb:
        ev["traceback"] = tb
    emit(ev)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def handle_ping(cmd: dict) -> None:
    emit({"type": "pong", "timestamp": time.time()})


def handle_list_projects(cmd: dict) -> None:
    emit({"type": "projects_list", "projects": list_projects()})


def handle_create_project(cmd: dict) -> None:
    global _current_project
    name = cmd.get("name", "").strip()
    if not name:
        err("Project name is required", job_id=cmd.get("job_id"))
        return
    # Drop 4.16: optional custom root_dir chosen via the UI's folder picker
    root_dir = cmd.get("root_dir")
    if root_dir:
        root_dir = str(root_dir).strip() or None
    try:
        proj = Project.create(name, root_dir=root_dir)
    except FileExistsError:
        err(f"Project '{_sanitize_project_name(name)}' already exists. Pick a different name or open it.")
        return
    except (OSError, PermissionError) as e:
        err(f"Couldn't create project at that location: {e}")
        return
    with _project_lock:
        _current_project = proj
    emit({"type": "project_created", "project": proj.to_wire_dict()})


def handle_load_project(cmd: dict) -> None:
    global _current_project
    name = cmd.get("name", "").strip()
    if not name:
        err("Project name is required")
        return
    try:
        proj = Project.load(name)
    except FileNotFoundError:
        err(f"Project not found: {name}")
        return
    with _project_lock:
        _current_project = proj
    emit({"type": "project_loaded", "project": proj.to_wire_dict()})


def handle_load_project_from_path(cmd: dict) -> None:
    """Drop 4.16: open a project by its folder path (portability flow).

    The user picks a folder via a native dir picker. We read <folder>/project.json
    and update the known_projects registry so subsequent "Open by name" works.
    """
    global _current_project
    folder = cmd.get("path", "").strip()
    if not folder:
        err("Folder path is required")
        return
    try:
        proj = Project.load_from_path(folder)
    except FileNotFoundError as e:
        err(str(e))
        return
    except Exception as e:
        err(f"Couldn't open project: {e}")
        return
    with _project_lock:
        _current_project = proj
    emit({"type": "project_loaded", "project": proj.to_wire_dict()})


def handle_delete_project(cmd: dict) -> None:
    global _current_project
    name = cmd.get("name", "").strip()
    ok = delete_project(name)
    with _project_lock:
        if _current_project is not None and _current_project.name == _sanitize_project_name(name):
            _current_project = None
    emit({"type": "project_deleted", "name": name, "ok": ok})


def handle_forget_project(cmd: dict) -> None:
    """Drop 4.44: remove a project from the known_projects registry
    without deleting any files on disk.

    Used by the UI when the user clicks on a project that can't be
    loaded (because its folder moved or was deleted) and chooses
    "Remove from list" rather than "Locate folder".
    """
    name = cmd.get("name", "").strip()
    try:
        unregister_project_location(name)
        emit({"type": "project_forgotten", "name": name, "ok": True})
    except Exception as exc:
        err(f"Failed to forget project: {exc}", tb=traceback.format_exc())


def handle_add_source(cmd: dict) -> None:
    proj = _require_project()
    if proj is None:
        return
    path = cmd.get("path", "")
    kind = cmd.get("kind")
    if kind not in ("aroll", "broll", "audio"):
        err(f"Invalid kind: {kind}")
        return
    src = proj.add_source(path, kind)
    proj.save()
    emit({"type": "source_added", "source": src.to_dict()})


def handle_remove_source(cmd: dict) -> None:
    proj = _require_project()
    if proj is None:
        return
    path = cmd.get("path", "")
    removed = proj.remove_source(path)
    if removed:
        proj.save()
    emit({"type": "source_removed", "path": path, "ok": removed})


def handle_set_source_dual_use(cmd: dict) -> None:
    proj = _require_project()
    if proj is None:
        return
    path = cmd.get("path", "")
    dual_use = bool(cmd.get("dual_use", False))
    ok = proj.set_dual_use(path, dual_use)
    if ok:
        proj.save()
    emit({"type": "source_dual_use_set", "path": path, "dual_use": dual_use, "ok": ok})


def handle_get_project_state(cmd: dict) -> None:
    proj = _require_project()
    if proj is None:
        return
    emit({"type": "project_state", "project": proj.to_wire_dict()})


def handle_organize_project(cmd: dict) -> None:
    """Task 1.1: Project Manager tab.

    Deliberately independent of PreCut's own Project/`_current_project`
    model -- this is the minimal first version, not yet unified with the
    existing Ingest tab's source-declaration UI (see STATUS.md Task 1.1).
    Runs synchronously: `organize_project` is filesystem census + a
    manifest write, not an ML pipeline stage, so there is no job/progress
    machinery here the way `run_pipeline` needs one.
    """
    root_dir = (cmd.get("root_dir") or "").strip()
    client_name = (cmd.get("client_name") or "").strip()
    project_name = (cmd.get("project_name") or "").strip()
    project_type = (cmd.get("project_type") or "").strip()
    sources = cmd.get("sources") or []
    brand_assets_source_dir = (cmd.get("brand_assets_source_dir") or "").strip() or None
    audience_goal = (cmd.get("audience_goal") or "").strip() or None

    missing = [
        field for field, val in (
            ("root_dir", root_dir), ("client_name", client_name),
            ("project_name", project_name), ("project_type", project_type),
        ) if not val
    ]
    if missing:
        err(f"Missing required field(s): {', '.join(missing)}")
        return
    if not sources:
        err("At least one source folder is required")
        return

    try:
        result = organize_project(
            root_dir=root_dir,
            client_name=client_name,
            project_name=project_name,
            project_type=project_type,
            sources=sources,
            brand_assets_source_dir=brand_assets_source_dir,
            audience_goal=audience_goal,
        )
    except OrganizeError as e:
        err("Project Manager could not produce a valid manifest:\n"
            + "\n".join(f"  - {p}" for p in e.errors))
        return
    except Exception as exc:
        err(f"{type(exc).__name__}: {exc}", tb=traceback.format_exc())
        return

    emit({
        "type": "project_organized",
        "manifest": result.manifest,
        "manifest_path": str(result.manifest_path),
        "is_new_project": result.is_new_project,
        "added_source_ids": result.added_source_ids,
        "staged_asset_files": result.staged_asset_files,
        "warnings": result.warnings,
    })


def handle_run_pipeline(cmd: dict) -> None:
    proj = _require_project()
    if proj is None:
        return

    job_id = cmd.get("job_id") or f"run-{int(time.time())}"
    cancel_flag = threading.Event()
    job = PipelineJob(
        project=proj,
        job_id=job_id,
        cancel_flag=cancel_flag,
        run_proxies=cmd.get("run_proxies", True),
        run_transcription=cmd.get("run_transcription", True),
        run_tagging=cmd.get("run_tagging", True),
        run_audio_index=cmd.get("run_audio_index", True),
        run_audio_sync=cmd.get("run_audio_sync", True),
        force_audio_sync=cmd.get("force_audio_sync", False),
    )

    with _jobs_lock:
        _jobs[job_id] = ActiveJob(job_id, cancel_flag)

    def worker():
        try:
            run_pipeline(job, emit=emit)
        except Exception as exc:
            err(f"{type(exc).__name__}: {exc}", job_id=job_id, tb=traceback.format_exc())
        finally:
            with _jobs_lock:
                _jobs.pop(job_id, None)

    _executor.submit(worker)


def handle_cancel_job(cmd: dict) -> None:
    job_id = cmd.get("job_id")
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        log("warn", f"Cannot cancel {job_id}: not running")
        return
    job.cancel_flag.set()
    log("info", f"Cancel requested for {job_id}")


def handle_analyze(cmd: dict) -> None:
    """Run AI producer in 'analyze and recommend' mode."""
    proj = _require_project()
    if proj is None:
        return
    job_id = cmd.get("job_id") or f"analyze-{int(time.time())}"
    max_concepts = int(cmd.get("max_concepts", 5))

    cancel_flag = threading.Event()
    with _jobs_lock:
        _jobs[job_id] = ActiveJob(job_id, cancel_flag)

    def worker():
        try:
            run_analyze(proj, job_id, emit=emit, max_concepts=max_concepts)
        except Exception as exc:
            err(f"{type(exc).__name__}: {exc}", job_id=job_id, tb=traceback.format_exc())
        finally:
            with _jobs_lock:
                _jobs.pop(job_id, None)

    _executor.submit(worker)


def handle_plan_directed(cmd: dict) -> None:
    """Run AI producer in directed mode — user supplies preset + brief."""
    proj = _require_project()
    if proj is None:
        return
    preset_key = cmd.get("preset_key", "").strip()
    if not preset_key:
        err("preset_key is required")
        return
    brief = cmd.get("brief", "")
    topic_focus = cmd.get("topic_focus", "")
    job_id = cmd.get("job_id") or f"plan-{int(time.time())}"

    cancel_flag = threading.Event()
    with _jobs_lock:
        _jobs[job_id] = ActiveJob(job_id, cancel_flag)

    def worker():
        try:
            run_directed_plan(
                proj, job_id,
                preset_key=preset_key, brief=brief, topic_focus=topic_focus,
                emit=emit,
            )
        except Exception as exc:
            err(f"{type(exc).__name__}: {exc}", job_id=job_id, tb=traceback.format_exc())
        finally:
            with _jobs_lock:
                _jobs.pop(job_id, None)

    _executor.submit(worker)


def handle_refine_idea(cmd: dict) -> None:
    """Refine an existing idea with user notes."""
    proj = _require_project()
    if proj is None:
        return
    idea_id = cmd.get("idea_id", "").strip()
    user_notes = cmd.get("notes", "").strip()
    if not idea_id or not user_notes:
        err("idea_id and notes are required")
        return
    job_id = cmd.get("job_id") or f"refine-{int(time.time())}"

    cancel_flag = threading.Event()
    with _jobs_lock:
        _jobs[job_id] = ActiveJob(job_id, cancel_flag)

    def worker():
        try:
            run_refine(proj, job_id, idea_id=idea_id, user_notes=user_notes, emit=emit)
        except Exception as exc:
            err(f"{type(exc).__name__}: {exc}", job_id=job_id, tb=traceback.format_exc())
        finally:
            with _jobs_lock:
                _jobs.pop(job_id, None)

    _executor.submit(worker)


def handle_list_ideas(cmd: dict) -> None:
    proj = _require_project()
    if proj is None:
        return
    emit({"type": "ideas_list", "ideas": list_ideas(proj)})


def handle_delete_idea(cmd: dict) -> None:
    proj = _require_project()
    if proj is None:
        return
    idea_id = cmd.get("idea_id", "").strip()
    ok = delete_idea(proj, idea_id)
    emit({"type": "idea_deleted", "idea_id": idea_id, "ok": ok})


# --- Drop 4.0: Story Angle handlers ---

def handle_story_generate(cmd: dict) -> None:
    """Drop 4.0: generate N story angles from the project's transcripts.

    Accepts:
      - n_angles (int, default 3)
      - include_existing (bool, default False) — if true, feeds persisted
        angles back to the planner as 'don't repeat these' context. Used
        by the UI's 'Request More' button.
    """
    proj = _require_project()
    if proj is None:
        return
    n_angles = int(cmd.get("n_angles", 3))
    include_existing = bool(cmd.get("include_existing", False))
    job_id = cmd.get("job_id") or f"angles-{int(time.time())}"

    cancel_flag = threading.Event()
    with _jobs_lock:
        _jobs[job_id] = ActiveJob(job_id, cancel_flag)

    def worker():
        try:
            run_generate_angles(
                proj, job_id,
                emit=emit, n_angles=n_angles, include_existing=include_existing,
            )
        except Exception as exc:
            err(f"{type(exc).__name__}: {exc}", job_id=job_id, tb=traceback.format_exc())
        finally:
            with _jobs_lock:
                _jobs.pop(job_id, None)

    _executor.submit(worker)


def handle_set_angle_preset(cmd: dict) -> None:
    """Drop 4.0: update the per-angle format preset (from Idea card dropdown)."""
    proj = _require_project()
    if proj is None:
        return
    idea_id = cmd.get("idea_id", "").strip()
    preset_key = cmd.get("preset_key", "").strip()
    if not idea_id or not preset_key:
        err("idea_id and preset_key are required")
        return
    ok = set_angle_preset(proj, idea_id, preset_key)
    emit({
        "type": "angle_preset_set",
        "idea_id": idea_id,
        "preset_key": preset_key,
        "ok": ok,
    })


def handle_set_angle_platform_and_aspect(cmd: dict) -> None:
    """Drop 4.4: update per-angle platform + aspect selections.

    Either field may be empty string. Both empty means "A-roll native dims,
    no overlay" — the unconfigured default.
    """
    proj = _require_project()
    if proj is None:
        return
    idea_id = cmd.get("idea_id", "").strip()
    platform_key = (cmd.get("platform_key") or "").strip()
    aspect_key = (cmd.get("aspect_key") or "").strip()
    if not idea_id:
        err("idea_id is required")
        return
    ok = set_angle_platform_and_aspect(
        proj, idea_id,
        platform_key=platform_key,
        aspect_key=aspect_key,
    )
    emit({
        "type": "angle_platform_set",
        "idea_id": idea_id,
        "platform_key": platform_key,
        "aspect_key": aspect_key,
        "ok": ok,
    })


def handle_get_settings(cmd: dict) -> None:
    emit({"type": "settings", "settings": get_api_key_summary()})


def handle_set_api_key(cmd: dict) -> None:
    key = cmd.get("api_key", "")
    try:
        set_api_key(key)
    except Exception as exc:
        err(f"Failed to save API key: {exc}", tb=traceback.format_exc())
        return
    emit({"type": "api_key_saved", "summary": get_api_key_summary()})


def handle_set_workspace_id(cmd: dict) -> None:
    workspace_id = cmd.get("workspace_id", "")
    try:
        set_workspace_id(workspace_id)
    except Exception as exc:
        err(f"Failed to save workspace ID: {exc}", tb=traceback.format_exc())
        return
    emit({"type": "workspace_id_saved", "summary": get_api_key_summary()})


def handle_set_onboarding_flag(cmd: dict) -> None:
    """Mark welcome_seen / tour_seen / api_key_help_auto_shown as True.

    The React side calls this when the user dismisses / completes each
    piece of onboarding. We return the updated settings summary so the
    UI can reflect state without an extra round-trip.
    """
    flag = cmd.get("flag", "")
    value = cmd.get("value", True)
    try:
        set_onboarding_flag(flag, bool(value))
    except ValueError as exc:
        err(f"Invalid onboarding flag: {exc}")
        return
    except Exception as exc:
        err(f"Failed to set onboarding flag: {exc}", tb=traceback.format_exc())
        return
    emit({"type": "settings", "settings": get_api_key_summary()})


# ---------------------------------------------------------------------------
# Auto-include rules (Drop 4.46)
# ---------------------------------------------------------------------------

def handle_get_audience_profiles(cmd: dict) -> None:
    """Return the saved audience/content-goal profiles to the UI."""
    try:
        profiles = get_audience_profiles()
    except Exception as exc:
        err(f"Failed to load audience profiles: {exc}",
            tb=traceback.format_exc())
        return
    emit({"type": "audience_profiles", "profiles": profiles})


def handle_set_audience_profiles(cmd: dict) -> None:
    """Replace the entire saved audience-profile list with a new one."""
    profiles = cmd.get("profiles", [])
    if not isinstance(profiles, list):
        err("profiles must be a list")
        return
    try:
        set_audience_profiles(profiles)
    except Exception as exc:
        err(f"Failed to save audience profiles: {exc}",
            tb=traceback.format_exc())
        return
    emit({"type": "audience_profiles", "profiles": get_audience_profiles()})


def handle_get_auto_include_rules(cmd: dict) -> None:
    """Return the saved auto-include rules to the UI."""
    try:
        rules = get_auto_include_rules()
    except Exception as exc:
        err(f"Failed to load auto-include rules: {exc}",
            tb=traceback.format_exc())
        return
    emit({"type": "auto_include_rules", "rules": rules})


def handle_set_auto_include_rules(cmd: dict) -> None:
    """Replace the entire saved auto-include rule list with a new one."""
    rules = cmd.get("rules", [])
    if not isinstance(rules, list):
        err("rules must be a list")
        return

    # Per-rule sanity check using the dataclass validator. Save anyway
    # if the rule has any data the UI sent; UI is responsible for showing
    # validation errors. We just emit warnings for malformed rules so they
    # show up in the log if something's off.
    try:
        from precut_pipeline.auto_include import (
            AutoIncludeRule, validate_rule,
        )
        for r in rules:
            if not isinstance(r, dict):
                continue
            try:
                rule_obj = AutoIncludeRule.from_dict(r)
                err_msg = validate_rule(rule_obj)
                if err_msg:
                    log("warn", f"auto_include rule {r.get('id', '?')!r}: {err_msg}")
            except Exception as ve:
                log("warn", f"auto_include rule {r.get('id', '?')!r}: "
                            f"{type(ve).__name__}: {ve}")
    except ImportError:
        pass

    try:
        set_auto_include_rules(rules)
    except Exception as exc:
        err(f"Failed to save auto-include rules: {exc}",
            tb=traceback.format_exc())
        return
    emit({"type": "auto_include_rules", "rules": get_auto_include_rules()})


def handle_export_timelines(cmd: dict) -> None:
    """Run the full export pipeline for selected ideas.

    In library_only mode, idea_ids may be empty — we still produce an
    XML with the All-Synced-A-Roll reference sequence + B-roll library
    bin. This is the "I don't have an API key, just give me my footage
    in Premiere-ready form" path.
    """
    proj = _require_project()
    if proj is None:
        return

    from pathlib import Path
    output_path = cmd.get("output_path", "").strip()
    idea_ids = cmd.get("idea_ids", [])
    library_only = bool(cmd.get("library_only", False))
    if not output_path:
        err("output_path is required")
        return
    if not idea_ids and not library_only:
        err("At least one idea_id must be selected (or pass library_only=true)")
        return

    # Drop 4.46: load user-configured auto-include rules from settings.
    # Empty list if none. The exporter will silently skip missing files
    # and unknown extensions; UI is responsible for showing rule-edit
    # warnings if rules are misconfigured.
    #
    # Drop 4.47.3: per-export opt-out. The frontend sends
    # apply_auto_includes=False if the user unchecked the corresponding
    # box in ExportModal. Default is True (preserves existing behavior
    # for any caller — including legacy clients — that doesn't send the
    # flag). When False we skip loading entirely, so we don't even
    # touch settings.json on this export path.
    if bool(cmd.get("apply_auto_includes", True)):
        try:
            auto_rules = get_auto_include_rules()
        except Exception as exc:
            log("warn", f"Failed to load auto_include_rules; "
                        f"continuing without: {exc}")
            auto_rules = []
    else:
        auto_rules = []
        log("info", "apply_auto_includes=False; skipping default-includes for this export.")

    options = ExportOptions(
        output_path=Path(output_path),
        idea_ids=idea_ids,
        include_full_library=bool(cmd.get("include_full_library", True)),
        run_audio_sync=bool(cmd.get("run_audio_sync", True)),
        include_clean_mic=bool(cmd.get("include_clean_mic", True)),
        include_overlay=bool(cmd.get("include_overlay", True)),
        library_only=library_only,
        auto_include_rules=auto_rules,
        broll_target_fps=cmd.get("broll_target_fps"),
    )

    job_id = cmd.get("job_id") or f"export-{int(time.time())}"
    cancel_flag = threading.Event()
    with _jobs_lock:
        _jobs[job_id] = ActiveJob(job_id, cancel_flag)

    def worker():
        try:
            run_export(proj, job_id, options, emit=emit)
        except Exception as exc:
            err(f"{type(exc).__name__}: {exc}", job_id=job_id, tb=traceback.format_exc())
        finally:
            with _jobs_lock:
                _jobs.pop(job_id, None)

    _executor.submit(worker)


def handle_shutdown(cmd: dict) -> None:
    log("info", "Shutdown requested")
    with _jobs_lock:
        for j in _jobs.values():
            j.cancel_flag.set()
    _executor.shutdown(wait=False)
    sys.exit(0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_project() -> Optional[Project]:
    with _project_lock:
        p = _current_project
    if p is None:
        err("No project loaded. Call load_project or create_project first.")
        return None
    return p


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

HANDLERS = {
    "ping": handle_ping,
    "list_projects": handle_list_projects,
    "create_project": handle_create_project,
    "load_project": handle_load_project,
    "load_project_from_path": handle_load_project_from_path,
    "delete_project": handle_delete_project,
    "forget_project": handle_forget_project,
    "add_source": handle_add_source,
    "remove_source": handle_remove_source,
    "set_source_dual_use": handle_set_source_dual_use,
    "get_project_state": handle_get_project_state,
    # Task 1.1: Project Manager tab
    "organize_project": handle_organize_project,
    "run_pipeline": handle_run_pipeline,
    "cancel_job": handle_cancel_job,
    # AI producer
    "analyze": handle_analyze,
    "plan_directed": handle_plan_directed,
    "refine_idea": handle_refine_idea,
    "list_ideas": handle_list_ideas,
    "delete_idea": handle_delete_idea,
    # Drop 4.0: story angles
    "story_generate": handle_story_generate,
    "set_angle_preset": handle_set_angle_preset,
    "set_angle_platform_and_aspect": handle_set_angle_platform_and_aspect,
    # Settings
    "get_settings": handle_get_settings,
    "set_api_key": handle_set_api_key,
    "set_workspace_id": handle_set_workspace_id,
    "set_onboarding_flag": handle_set_onboarding_flag,
    # Drop 4.46: auto-include rules
    "get_auto_include_rules": handle_get_auto_include_rules,
    "set_auto_include_rules": handle_set_auto_include_rules,
    "get_audience_profiles": handle_get_audience_profiles,
    "set_audience_profiles": handle_set_audience_profiles,
    # Export (Drop 3)
    "export_timelines": handle_export_timelines,
    "shutdown": handle_shutdown,
}


def main() -> None:
    # Apply persisted settings to the current process env. Env vars still
    # take precedence — this only fills in what's missing.
    summary = apply_settings_to_env()
    emit({"type": "ready", "version": "0.4.43-precut-logo-cleanup",
          "settings": get_api_key_summary()})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError as e:
            err(f"Bad JSON from UI: {e}")
            continue

        cmd_type = cmd.get("type")
        handler = HANDLERS.get(cmd_type)
        if handler is None:
            err(f"Unknown command: {cmd_type}")
            continue
        try:
            handler(cmd)
        except Exception as exc:
            err(f"{type(exc).__name__}: {exc}", tb=traceback.format_exc())


if __name__ == "__main__":
    main()
