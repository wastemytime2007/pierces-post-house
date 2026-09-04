"""Pipeline orchestrator for B-Roll Buddy.

The orchestrator is the brain that sits above individual job types and
decides what runs when. Its rules:

  1. A-roll proxies finish → transcribe each A-roll proxy
  2. B-roll proxies finish → tag each B-roll proxy (CLIP + LLaVA)
  3. Audio doesn't need proxies — it's indexed in parallel via ffprobe

Each stage emits its own progress events through the same JSON-Lines pipe
as proxies. The UI routes them to appropriate panels.

The orchestrator also updates the Project model's file status so we can
show "proxied / transcribed / tagged" state in the UI without re-scanning.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from project import Project, SourceFolder, SourceKind, proxy_dir_for
from proxy_manager import (
    VIDEO_EXTENSIONS, AUDIO_EXTENSIONS, UNSUPPORTED_EXTENSIONS,
    find_ffmpeg,
)
from audio_indexer import find_ffprobe, probe_audio, save_audio_index


# ---------------------------------------------------------------------------
# Pipeline job — one user-initiated "process everything" run
# ---------------------------------------------------------------------------

@dataclass
class PipelineJob:
    """A full processing pass over a project's current sources."""
    project: Project
    job_id: str
    cancel_flag: threading.Event = field(default_factory=threading.Event)

    # Which stages to run. Auto-transcribe/tag are enabled by default
    # but can be turned off for quick re-runs.
    run_proxies: bool = True
    run_transcription: bool = True
    run_tagging: bool = True
    run_audio_index: bool = True
    run_audio_sync: bool = True  # Drop 3.6: new final stage
    # 2026-09-03: exhaustive transcript reading + audience-relevance
    # tagging (posthouse.transcript_coverage / audience_relevance).
    # Automatic, per Ryan's standing preference against manual
    # multi-step UI (the sync-coverage rescue went the same way — see
    # ROADMAP.md Decision Log, "coverage rescue made automatic"). A
    # no-op, not an error, when the project has no audience_goal set.
    run_transcript_flagging: bool = True
    # 2026-09-03: sync_project() caches by a hash of the declared source
    # paths (see audio_sync.compute_source_hash) -- nothing about a
    # re-run changes that hash, so every "Run pipeline" click with
    # run_audio_sync on was silently returning the identical cached
    # result. Ryan: "is there a way to re-run the sync process? Clicking
    # run pipeline is still giving the same output." This flag bypasses
    # the cache for one run by clearing project.audio_sync first.
    force_audio_sync: bool = False


# Drop 4.44: pretty display names for log banners. Keeps the banner
# text consistent regardless of where in the pipeline a stage starts,
# and avoids spelling "aroll_proxies" style internal names in the UI.
_STAGE_LABELS = {
    "aroll_proxies":   "Proxies — A-roll",
    "broll_proxies":   "Proxies — B-roll",
    "transcribe":      "Transcription",
    "tag":             "Tagging",
    "audio_index":     "Audio indexing",
    "audio_sync":      "Audio sync",
    "transcript_flagging": "Transcript flagging",
}


def _emit_stage_start(
    emit: Callable[[dict], None],
    job_id: str,
    stage: str,
    total: Optional[int] = None,
) -> None:
    """Emit a stage_started event AND a human-readable log banner.

    The structured stage_started event drives the visual progress panels
    in ProjectView. The log banner gives users a linear, readable trail
    in the text log so they can see exactly when each stage kicks off —
    previously the log showed raw pip/ffmpeg/etc. output and users
    couldn't tell when one stage ended and the next began.
    """
    ev = {"type": "stage_started", "job_id": job_id, "stage": stage}
    if total is not None:
        ev["total"] = total
    emit(ev)

    label = _STAGE_LABELS.get(stage, stage)
    emit({
        "type": "log",
        "level": "accent",
        "message": f"━━ {label} ━━",
    })


def run_pipeline(
    job: PipelineJob,
    emit: Callable[[dict], None],
) -> None:
    """Main entry point. Runs all stages in dependency order.

    Stages execute concurrently where possible:
      - A-roll proxies (parallel file encodes)
      - B-roll proxies (parallel file encodes, independent of A-roll)
      - Audio indexing (parallel, depends on nothing)

    Once A-roll proxies finish for any file, that file enters the
    transcription queue. Once B-roll proxies finish, tagging queue.
    """
    proj = job.project
    emit({
        "type": "pipeline_started",
        "job_id": job.job_id,
        "project": proj.name,
        "stages": {
            "proxies": job.run_proxies,
            "transcription": job.run_transcription,
            "tagging": job.run_tagging,
            "audio_index": job.run_audio_index,
        },
    })

    # Drop 4.44: surface the run plan in the text log, not just the
    # structured pipeline_started event. Users unchecking a stage
    # previously couldn't tell from the log whether their choice took
    # effect.
    #
    # Proxies are NOT listed as a user-toggleable stage in the banner
    # unless this is a "Proxies only" run. That's because transcription
    # and tagging both operate on proxy files — proxies are a hidden
    # prerequisite, and the UI no longer exposes a "skip proxies"
    # checkbox. Showing "✓ Proxies" in every banner would be noise;
    # showing "⊘ Proxies" would be a lie (they'd still get made).
    is_proxies_only = (
        job.run_proxies
        and not job.run_transcription
        and not job.run_tagging
        and not job.run_audio_index
        and not job.run_audio_sync
        and not job.run_transcript_flagging
    )
    if is_proxies_only:
        summary = "✓ Proxies only (no downstream stages)"
    else:
        plan_parts = [
            ("Transcription", job.run_transcription),
            ("Tagging",       job.run_tagging),
            ("Audio index",   job.run_audio_index),
            ("Audio sync",    job.run_audio_sync),
            ("Transcript flagging", job.run_transcript_flagging),
        ]
        # Unicode marks: ✓ for will-run, ⊘ for skipped. If any terminal/
        # log viewer can't render these, they fall back to the level color.
        summary = " · ".join(
            f"{'✓' if enabled else '⊘'} {name}" for name, enabled in plan_parts
        )
    emit({
        "type": "log",
        "level": "accent",
        "message": f"━━ Pipeline plan ━━  {summary}",
    })

    ffmpeg = find_ffmpeg()
    if ffmpeg is None and (job.run_proxies):
        emit({
            "type": "error",
            "job_id": job.job_id,
            "message": "FFmpeg not found. Install: brew install ffmpeg",
        })
        emit({"type": "pipeline_complete", "job_id": job.job_id, "ok": False})
        return

    # ---- Scan sources → per-kind file lists ----
    aroll_videos = _collect_videos(proj, "aroll")
    broll_videos = _collect_videos(proj, "broll")
    audio_files = _collect_audio(proj)

    emit({
        "type": "pipeline_scan_complete",
        "job_id": job.job_id,
        "counts": {
            "aroll_videos": len(aroll_videos),
            "broll_videos": len(broll_videos),
            "audio_files": len(audio_files),
        },
    })

    # Each stage runs in its own thread and posts events as it goes.
    # The orchestrator waits for all threads to finish before declaring
    # the pipeline complete.
    stage_threads: list[threading.Thread] = []

    # ---- Audio indexing (fast, no dependency) ----
    if job.run_audio_index and audio_files:
        t = threading.Thread(
            target=_run_audio_indexing,
            args=(job, audio_files, emit),
            daemon=True,
        )
        t.start()
        stage_threads.append(t)

    # ---- A-roll: proxies + (optionally) transcription ----
    if aroll_videos:
        t = threading.Thread(
            target=_run_video_pipeline,
            args=(job, aroll_videos, "aroll", ffmpeg, emit),
            daemon=True,
        )
        t.start()
        stage_threads.append(t)

    # ---- B-roll: proxies + (optionally) tagging ----
    if broll_videos:
        t = threading.Thread(
            target=_run_video_pipeline,
            args=(job, broll_videos, "broll", ffmpeg, emit),
            daemon=True,
        )
        t.start()
        stage_threads.append(t)

    # Wait for all stages
    for t in stage_threads:
        t.join()

    # ---- Audio sync (Drop 3.6) ----
    # Runs AFTER transcription + audio indexing so we have proxies for the
    # A-rolls and the audio files are confirmed present. Must be serial
    # (not a thread) so it finishes before pipeline_complete fires.
    if job.run_audio_sync and not job.cancel_flag.is_set():
        try:
            _run_audio_sync(job, emit)
        except Exception as e:
            import traceback
            emit({"type": "audio_sync_error",
                  "job_id": job.job_id,
                  "message": f"{type(e).__name__}: {e}",
                  "traceback": traceback.format_exc()})

    # ---- Transcript flagging (2026-09-03) ----
    # Runs AFTER transcription so transcripts exist. Serial, same reason
    # as audio sync: must finish before pipeline_complete fires, and it
    # makes real API calls so shouldn't race other stages for the
    # backend's attention.
    if job.run_transcript_flagging and not job.cancel_flag.is_set():
        try:
            _run_transcript_flagging(job, aroll_videos, emit)
        except Exception as e:
            import traceback
            emit({"type": "log", "level": "error",
                  "message": f"Transcript flagging failed: {type(e).__name__}: {e}"})
            emit({"type": "error", "job_id": job.job_id,
                  "message": f"Transcript flagging failed: {e}",
                  "traceback": traceback.format_exc()})

    proj.save()  # persist final status
    emit({
        "type": "pipeline_complete",
        "job_id": job.job_id,
        "ok": not job.cancel_flag.is_set(),
        "cancelled": job.cancel_flag.is_set(),
    })


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def _collect_videos(proj: Project, kind: SourceKind) -> list[tuple[Path, SourceFolder]]:
    """Find all video files across sources of a given kind.

    B-roll additionally includes dual_use A-roll sources (Post House
    Project Manifest contract §2.3: the subject keeps talking while the
    shooter grabs coverage from the same footage). Proxy encoding is
    identical regardless of kind (_encode_proxy takes no kind parameter)
    and keyed by output path next to the source, so re-collecting an
    already-proxied dual_use file here is a safe, idempotent skip, not a
    conflicting second encode.
    """
    sources = list(proj.sources_by_kind(kind))
    if kind == "broll":
        sources += [s for s in proj.sources_by_kind("aroll") if s.dual_use]

    out: list[tuple[Path, SourceFolder]] = []
    for src in sources:
        root = Path(src.root_path)
        if not root.exists():
            continue
        if src.is_file:
            if root.suffix.lower() in VIDEO_EXTENSIONS:
                out.append((root, src))
        else:
            for p in _walk_files(root):
                if p.suffix.lower() in VIDEO_EXTENSIONS:
                    out.append((p, src))
    return out


def _collect_audio(proj: Project) -> list[tuple[Path, SourceFolder]]:
    """Find all audio files across audio-kind sources."""
    out: list[tuple[Path, SourceFolder]] = []
    for src in proj.sources_by_kind("audio"):
        root = Path(src.root_path)
        if not root.exists():
            continue
        if src.is_file:
            if root.suffix.lower() in AUDIO_EXTENSIONS:
                out.append((root, src))
        else:
            for p in _walk_files(root):
                if p.suffix.lower() in AUDIO_EXTENSIONS:
                    out.append((p, src))
    return out


def _walk_files(root: Path):
    """Yield all non-hidden files under root."""
    import os
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # Skip hidden dirs AND our own 'proxies' dirs (don't recurse into our own output)
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "proxies"]
        for fn in filenames:
            if fn.startswith("."):
                continue
            yield Path(dirpath) / fn


# ---------------------------------------------------------------------------
# Audio indexing stage
# ---------------------------------------------------------------------------

def _run_audio_sync(
    job: PipelineJob,
    emit: Callable[[dict], None],
) -> None:
    """Drop 3.6 stage: cross-reference every (A-roll, audio) pair and cache.

    Depends on: proxies finished, audio files present. Because we run this
    after thread joins, those are guaranteed.

    Emits progress events with stage='audio_sync'. On completion, writes
    the results to project.audio_sync (persisted by run_pipeline's save()).
    """
    from precut_pipeline.audio_sync import sync_project

    if job.force_audio_sync:
        job.project.audio_sync = None

    _emit_stage_start(emit, job.job_id, "audio_sync")

    def proxy_emit(ev: dict) -> None:
        """Forward audio_sync module events with stage + job_id injected."""
        ev = dict(ev)
        ev.setdefault("job_id", job.job_id)
        ev.setdefault("stage", "audio_sync")
        emit(ev)

    state = sync_project(job.project, emit_progress=proxy_emit)

    # 2026-09-03: Ryan -- "This is feeling extremely overcomplicated...
    # There shouldnt be extra steps needed. We should just be able to
    # sync things." The prior design made rescuing a pair PreCut's own
    # whole-file pass couldn't confidently sync (subject leaves the
    # room, comes back -- see posthouse/sync_coverage.py) a manual,
    # per-pair, analyze-then-apply flow. That's the extra steps. Folded
    # in here instead: every unreliable pair gets tried automatically,
    # as part of the same sync action, before Ryan ever sees a result.
    # No separate button, no separate click. A pair with no rescuable
    # match just stays as PreCut originally scored it -- this never
    # invents a sync that isn't there.
    unreliable = [p for p in state.pairs if not p.is_reliable]
    rescued_count = 0
    if unreliable and not job.cancel_flag.is_set():
        from posthouse.sync_coverage import analyze_pair_coverage

        emit({
            "type": "log", "level": "accent", "job_id": job.job_id,
            "message": f"━━ Rescuing {len(unreliable)} weak sync pair(s) — checking for usable stretches ━━",
        })
        for i, pair in enumerate(unreliable):
            if job.cancel_flag.is_set():
                break
            try:
                coverage = analyze_pair_coverage(
                    Path(pair.aroll_proxy), Path(pair.audio_file),
                    cancel_flag=job.cancel_flag,
                )
            except Exception as exc:
                emit({"type": "log", "level": "warn", "job_id": job.job_id,
                      "message": f"  Rescue failed for {Path(pair.audio_file).name}: {exc}"})
                continue
            aroll_name = Path(pair.aroll_file).name
            audio_name = Path(pair.audio_file).name
            if coverage.accepted_offset_sec is not None:
                pair.offset_sec = coverage.accepted_offset_sec
                pair.promoted_via_consistency = True
                rescued_count += 1
                emit({"type": "log", "level": "success", "job_id": job.job_id,
                      "message": f"  ✓ {aroll_name} ↔ {audio_name}: found a usable stretch, offset {coverage.accepted_offset_sec:+.2f}s"})
            else:
                emit({"type": "log", "level": "dim", "job_id": job.job_id,
                      "message": f"  ✗ {aroll_name} ↔ {audio_name}: no usable stretch found ({i + 1}/{len(unreliable)})"})

    # Persist on the project object — save() call in run_pipeline handles disk
    job.project.audio_sync = state.to_dict()

    emit({
        "type": "stage_complete",
        "job_id": job.job_id,
        "stage": "audio_sync",
        "pair_count": len(state.pairs),
        "reliable_count": sum(1 for p in state.pairs if p.is_reliable),
        "group_count": len(state.groups),
        "rescued_count": rescued_count,
    })


def _run_transcript_flagging(
    job: PipelineJob,
    aroll_videos: list[tuple[Path, SourceFolder]],
    emit: Callable[[dict], None],
) -> None:
    """Exhaustive transcript reading + audience-relevance tagging
    (2026-09-03) — see posthouse/transcript_coverage.py and
    posthouse/audience_relevance.py for the actual mechanisms; this is
    just the pipeline-stage wiring.

    A no-op, not an error, when: there's no Post House manifest.json for
    this project yet (older projects, or ones created outside PMTab),
    or the manifest has no audience_goal set (nothing to score fragments
    against — Project Manager intake's dropdown, not this stage, is
    where that gets decided).

    Idempotent like transcription: a project.dir()/flags/<stem>.json
    that already exists is skipped, not re-generated (and re-scored) on
    every pipeline run — this makes real, billed API calls, so silently
    repeating them on an unrelated re-run would be a real cost surprise.
    """
    from posthouse.manifest import load_manifest
    from posthouse.transcript_coverage import extract_exhaustive_fragments
    from posthouse.audience_relevance import score_fragments_for_audience, save_tagged_fragments
    from precut_pipeline.transcriber import Transcript

    project_dir = job.project.dir()
    manifest_path = project_dir / "manifest.json"
    if not manifest_path.exists():
        return

    try:
        manifest = load_manifest(manifest_path)
    except Exception:
        return

    audience_goal = (manifest.get("project") or {}).get("audience_goal")
    if not audience_goal:
        emit({"type": "log", "level": "info",
              "message": "Transcript flagging skipped — no audience/content "
                         "goal set for this project (Project Manager intake)."})
        return

    transcript_dir = job.project.transcripts_dir()
    transcript_files = sorted(transcript_dir.glob("*.json"))
    if not transcript_files:
        return

    # Transcripts are saved with the PROXY path as their own source_path
    # (Transcriber.transcribe(proxy_path)), but export-time matching
    # needs the ORIGINAL A-roll file path. Both are saved under the same
    # stem (transcribe stage: `transcript_dir / f"{source_file.stem}.json"`)
    # — resolve original paths by stem instead of trusting the saved
    # source_path field.
    original_by_stem = {p.stem: p for p, _src in aroll_videos}

    flags_dir = project_dir / "flags"
    total = len(transcript_files)
    _emit_stage_start(emit, job.job_id, "transcript_flagging", total=total)

    completed = 0
    for tp in transcript_files:
        if job.cancel_flag.is_set():
            break
        completed += 1
        out_path = flags_dir / f"{tp.stem}.json"
        if out_path.exists():
            emit({"type": "file_done", "job_id": job.job_id,
                  "stage": "transcript_flagging", "file": tp.name,
                  "status": "skipped", "completed": completed, "total": total})
            continue

        original_path = original_by_stem.get(tp.stem)
        if original_path is None:
            # Transcript exists for a file no longer declared as an
            # A-roll source (e.g. removed after transcription) — nothing
            # to attach a flag marker to at export time either way.
            continue

        try:
            transcript = Transcript.load(tp)
            fragments, coverage = extract_exhaustive_fragments(transcript)
            tagged = score_fragments_for_audience(audience_goal, fragments) if fragments else []
            save_tagged_fragments(out_path, str(original_path), audience_goal, tagged)
            emit({"type": "log", "level": "info",
                  "message": f"Flagged {original_path.name}: {len(tagged)} "
                             f"fragments, {coverage.coverage_fraction:.0%} coverage"})
            emit({"type": "file_done", "job_id": job.job_id,
                  "stage": "transcript_flagging", "file": tp.name,
                  "status": "done", "completed": completed, "total": total})
        except Exception as e:
            emit({"type": "log", "level": "warn",
                  "message": f"Transcript flagging failed for {original_path.name}: {e}"})
            emit({"type": "file_done", "job_id": job.job_id,
                  "stage": "transcript_flagging", "file": tp.name,
                  "status": "failed", "completed": completed, "total": total})

    emit({"type": "stage_complete", "job_id": job.job_id,
          "stage": "transcript_flagging"})


def _run_audio_indexing(
    job: PipelineJob,
    files: list[tuple[Path, SourceFolder]],
    emit: Callable[[dict], None],
) -> None:
    """Probe each audio file, write sidecar, update project status."""
    ffprobe = find_ffprobe()
    if ffprobe is None:
        emit({
            "type": "stage_error",
            "job_id": job.job_id,
            "stage": "audio_index",
            "message": "ffprobe not found. Install: brew install ffmpeg",
        })
        return

    _emit_stage_start(emit, job.job_id, "audio_index", total=len(files))

    index_dir = job.project.audio_index_dir()
    success_count = 0
    failed_count = 0
    for i, (file_path, src) in enumerate(files, 1):
        if job.cancel_flag.is_set():
            break
        info = probe_audio(file_path, ffprobe)
        if info is None:
            failed_count += 1
            emit({
                "type": "file_done",
                "job_id": job.job_id,
                "stage": "audio_index",
                "file": file_path.name,
                "status": "failed",
                "error": "ffprobe could not read this file",
                "completed": i,
                "total": len(files),
            })
            job.project.update_file_status(
                str(file_path), audio_index_status="failed"
            )
            continue

        save_audio_index(info, index_dir)
        success_count += 1
        job.project.update_file_status(
            str(file_path),
            audio_index_status="done",
            duration_sec=info.duration_sec,
            sample_rate=info.sample_rate,
            channels=info.channels,
        )
        emit({
            "type": "file_done",
            "job_id": job.job_id,
            "stage": "audio_index",
            "file": file_path.name,
            "status": "success",
            "duration_sec": info.duration_sec,
            "completed": i,
            "total": len(files),
        })

    emit({
        "type": "stage_complete",
        "job_id": job.job_id,
        "stage": "audio_index",
        "success": success_count,
        "failed": failed_count,
        "skipped": 0,
    })


# ---------------------------------------------------------------------------
# Video pipeline stage (proxies → transcribe OR tag)
# ---------------------------------------------------------------------------

def _run_video_pipeline(
    job: PipelineJob,
    files: list[tuple[Path, SourceFolder]],
    kind: str,  # "aroll" or "broll"
    ffmpeg: str,
    emit: Callable[[dict], None],
) -> None:
    """For each video in this list, generate proxy, then trigger downstream stage.

    We use a thread pool to encode multiple proxies concurrently. As each
    proxy finishes, we immediately hand off the proxy path to the downstream
    queue (transcribe for aroll, tag for broll).
    """
    from proxy_manager import _encode_proxy  # reuse existing function
    import multiprocessing

    cpu_count = multiprocessing.cpu_count()
    proxy_workers = max(1, min(cpu_count - 2, 6))

    stage_label = f"{kind}_proxies"
    _emit_stage_start(emit, job.job_id, stage_label, total=len(files))

    # Proxy output goes in a 'proxies' dir next to the source file
    # E.g. /foot/Celeste Intv/A001.mov → /foot/Celeste Intv/proxies/A001.mp4
    #
    # If the source folder has nested structure, we mirror it inside 'proxies':
    #   /foot/Celeste Intv/day2/A005.mov → /foot/Celeste Intv/proxies/day2/A005.mp4

    # Submit all proxy encodes to a thread pool
    proxy_done_queue: list[tuple[Path, Path]] = []  # (source_file, proxy_path)
    proxy_done_lock = threading.Lock()
    downstream_trigger = threading.Event()

    completed = 0
    success = failed = skipped = 0
    counters_lock = threading.Lock()

    def proxy_worker(source_file: Path, src_folder: SourceFolder):
        nonlocal completed, success, failed, skipped
        if job.cancel_flag.is_set():
            return

        import time
        start = time.time()

        # Figure out proxy output path, mirroring subfolder structure if any
        proxy_path = _compute_proxy_path(source_file, src_folder)

        # Skip if already up to date. Validated, not just present/nonzero
        # (see proxy_manager.proxy_is_valid's docstring — a real corrupt
        # proxy was found and silently trusted here on 2026-09-03).
        from proxy_manager import proxy_is_valid
        if proxy_path.exists() and proxy_path.stat().st_size > 0 and proxy_is_valid(proxy_path):
            result = {
                "status": "skipped",
                "file": source_file.name,
                "elapsed_sec": 0.0,
                "output_path": str(proxy_path),
                "source_path": str(source_file),
                "error": None,
            }
        else:
            proxy_path.parent.mkdir(parents=True, exist_ok=True)
            result = _encode_proxy(
                source_file, proxy_path, job.cancel_flag, start, ffmpeg
            )

        # Update counters + project status
        with counters_lock:
            completed += 1
            if result["status"] == "success":
                success += 1
            elif result["status"] == "skipped":
                skipped += 1
            else:
                failed += 1

        job.project.update_file_status(
            str(source_file),
            proxy_status=result["status"],
            proxy_path=result.get("output_path"),
            proxy_error=result.get("error"),
        )

        emit({
            "type": "file_done",
            "job_id": job.job_id,
            "stage": stage_label,
            "file": result["file"],
            "status": result["status"],
            "elapsed_sec": result["elapsed_sec"],
            "source_path": result["source_path"],
            "output_path": result.get("output_path"),
            "error": result.get("error"),
            "completed": completed,
            "total": len(files),
        })

        # If proxy succeeded or already existed, hand off to downstream
        if result["status"] in ("success", "skipped") and result.get("output_path"):
            with proxy_done_lock:
                proxy_done_queue.append(
                    (source_file, Path(result["output_path"]))
                )
                downstream_trigger.set()

    with ThreadPoolExecutor(max_workers=proxy_workers) as pool:
        futures = [pool.submit(proxy_worker, fp, sf) for fp, sf in files]

        # Also spin up the downstream consumer in parallel.
        # It drains the queue as proxies arrive and runs transcribe/tag
        # on each in a separate executor.
        downstream_done = threading.Event()

        def downstream_consumer():
            if kind == "aroll" and job.run_transcription:
                _consume_and_transcribe(
                    job, proxy_done_queue, proxy_done_lock,
                    downstream_trigger, downstream_done, emit, len(files),
                )
            elif kind == "broll" and job.run_tagging:
                _consume_and_tag(
                    job, proxy_done_queue, proxy_done_lock,
                    downstream_trigger, downstream_done, emit, len(files),
                )
            else:
                # No downstream stage requested — just drain + exit
                downstream_done.set()

        consumer_thread = threading.Thread(target=downstream_consumer, daemon=True)
        consumer_thread.start()

        # Wait for all proxy encodes
        for f in futures:
            f.result()

        # Signal no more proxies are coming
        downstream_trigger.set()  # wake consumer if it's idle
        # Consumer will exit its loop once the queue is drained AND proxies are done
        # We signal that by setting a "producers done" sentinel:
        proxy_done_queue.append(None)  # sentinel
        downstream_trigger.set()

        consumer_thread.join()

    emit({
        "type": "stage_complete",
        "job_id": job.job_id,
        "stage": stage_label,
        "success": success,
        "skipped": skipped,
        "failed": failed,
    })


def _compute_proxy_path(source_file: Path, src_folder: SourceFolder) -> Path:
    """Compute proxy output path, mirroring subfolder structure.

    If source was added as a FILE: /foot/clip.mov → /foot/proxies/clip.mp4
    If source was added as a FOLDER /foot/Celeste Intv with A001.mov inside,
        A001.mov → /foot/Celeste Intv/proxies/A001.mp4
        day2/A005.mov → /foot/Celeste Intv/proxies/day2/A005.mp4
    """
    root = Path(src_folder.root_path)
    if src_folder.is_file:
        return root.parent / "proxies" / f"{root.stem}.mp4"
    else:
        try:
            rel = source_file.relative_to(root)
        except ValueError:
            # File is outside the registered root — shouldn't happen, but be safe
            rel = Path(source_file.name)
        return root / "proxies" / rel.with_suffix(".mp4")


# ---------------------------------------------------------------------------
# Downstream: transcription consumer
# ---------------------------------------------------------------------------

def _consume_and_transcribe(
    job: PipelineJob,
    queue: list,
    queue_lock: threading.Lock,
    trigger: threading.Event,
    done: threading.Event,
    emit: Callable[[dict], None],
    total: int,
) -> None:
    """Pull (source, proxy) pairs off queue, transcribe each proxy, update project."""
    # Lazy import — whisper is heavy and we don't want to import it
    # if transcription is disabled for this run.
    try:
        from precut_pipeline.transcriber import Transcriber
    except ImportError as e:
        emit({
            "type": "stage_error",
            "job_id": job.job_id,
            "stage": "transcribe",
            "message": f"Failed to import transcriber: {e}",
        })
        done.set()
        return

    transcriber = None  # lazy init on first file
    completed = 0
    success_count = 0
    skipped_count = 0
    failed_count = 0
    transcript_dir = job.project.transcripts_dir()
    transcript_dir.mkdir(parents=True, exist_ok=True)

    _emit_stage_start(emit, job.job_id, "transcribe", total=total)

    # Keep track of what we've already transcribed so we don't double-process
    seen: set[str] = set()

    while not job.cancel_flag.is_set():
        trigger.wait(timeout=1.0)

        # Drain everything currently in the queue
        while True:
            with queue_lock:
                if not queue:
                    break
                item = queue.pop(0)

            if item is None:
                # Sentinel — proxies are done, queue is empty
                # But there may still have been items popped just before
                done.set()
                emit({
                    "type": "stage_complete",
                    "job_id": job.job_id,
                    "stage": "transcribe",
                    "completed": completed,
                    "success": success_count,
                    "skipped": skipped_count,
                    "failed": failed_count,
                })
                return

            source_file, proxy_path = item
            key = str(proxy_path)
            if key in seen:
                continue
            seen.add(key)

            # Lazy-init transcriber on first real file
            if transcriber is None:
                try:
                    transcriber = Transcriber()
                except Exception as e:
                    emit({
                        "type": "stage_error",
                        "job_id": job.job_id,
                        "stage": "transcribe",
                        "message": f"Whisper init failed: {e}",
                    })
                    done.set()
                    return

            # Output path: transcripts/<source_stem>.json
            out_path = transcript_dir / f"{source_file.stem}.json"

            # Skip if transcript already exists (idempotency)
            if out_path.exists():
                completed += 1
                skipped_count += 1
                emit({
                    "type": "file_done",
                    "job_id": job.job_id,
                    "stage": "transcribe",
                    "file": source_file.name,
                    "status": "skipped",
                    "output_path": str(out_path),
                    "completed": completed,
                    "total": total,
                })
                job.project.update_file_status(
                    str(source_file),
                    transcript_status="done",
                    transcript_path=str(out_path),
                )
                continue

            import time
            start = time.time()
            try:
                transcript = transcriber.transcribe(proxy_path)
                transcript.save(out_path)
                elapsed = time.time() - start
                job.project.update_file_status(
                    str(source_file),
                    transcript_status="done",
                    transcript_path=str(out_path),
                    transcript_phrase_count=len(transcript.phrases),
                )
                completed += 1
                success_count += 1
                emit({
                    "type": "file_done",
                    "job_id": job.job_id,
                    "stage": "transcribe",
                    "file": source_file.name,
                    "status": "success",
                    "elapsed_sec": round(elapsed, 1),
                    "phrase_count": len(transcript.phrases),
                    "output_path": str(out_path),
                    "completed": completed,
                    "total": total,
                })
            except Exception as e:
                completed += 1
                failed_count += 1
                job.project.update_file_status(
                    str(source_file),
                    transcript_status="failed",
                    transcript_error=str(e),
                )
                emit({
                    "type": "file_done",
                    "job_id": job.job_id,
                    "stage": "transcribe",
                    "file": source_file.name,
                    "status": "failed",
                    "error": str(e)[:200],
                    "completed": completed,
                    "total": total,
                })

        trigger.clear()

    done.set()


# ---------------------------------------------------------------------------
# Downstream: tagging consumer
# ---------------------------------------------------------------------------

def _consume_and_tag(
    job: PipelineJob,
    queue: list,
    queue_lock: threading.Lock,
    trigger: threading.Event,
    done: threading.Event,
    emit: Callable[[dict], None],
    total: int,
) -> None:
    """Pull (source, proxy) pairs off queue, extract+tag+embed each proxy.

    This is adapted from precut_pipeline.ingest.ingest_folder but
    processes files one at a time as they stream in from the proxy producer.
    """
    try:
        from precut_pipeline.ingest import _process_clip
        from precut_pipeline.database import Database
        from precut_pipeline.embedder import CLIPEmbedder
        from precut_pipeline.tagger import OllamaTagger
        from precut_pipeline.config import FRAMES_DIR
    except ImportError as e:
        emit({
            "type": "stage_error",
            "job_id": job.job_id,
            "stage": "tag",
            "message": f"Failed to import tagger modules: {e}",
        })
        done.set()
        return

    index_dir = job.project.broll_index_dir()
    index_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = index_dir / FRAMES_DIR
    frames_dir.mkdir(parents=True, exist_ok=True)

    db = None  # lazy init
    embedder = None
    tagger = None

    completed = 0
    success_count = 0
    skipped_count = 0
    failed_count = 0
    seen: set[str] = set()

    _emit_stage_start(emit, job.job_id, "tag", total=total)

    while not job.cancel_flag.is_set():
        trigger.wait(timeout=1.0)
        while True:
            with queue_lock:
                if not queue:
                    break
                item = queue.pop(0)

            if item is None:
                done.set()
                emit({
                    "type": "stage_complete",
                    "job_id": job.job_id,
                    "stage": "tag",
                    "completed": completed,
                    "success": success_count,
                    "skipped": skipped_count,
                    "failed": failed_count,
                })
                return

            source_file, proxy_path = item
            key = str(proxy_path)
            if key in seen:
                continue
            seen.add(key)

            # Lazy init on first real file — CLIP loading is ~150MB download
            if db is None:
                try:
                    db = Database(index_dir)
                    emit({"type": "log", "level": "info",
                          "message": "Loading CLIP model (first run downloads ~150MB)…"})
                    embedder = CLIPEmbedder()

                    # Drop 3.8: prefer Claude Sonnet vision for tagging
                    # (accurate, non-hallucinating) and fall back to LLaVA
                    # via Ollama if no Anthropic API key is configured. If
                    # neither is available, proceed with CLIP embeddings
                    # only — tagging is optional, matching still works.
                    from precut_pipeline.claude_tagger import (
                        ClaudeVisionTagger,
                    )
                    claude_tagger = ClaudeVisionTagger()
                    if claude_tagger.is_available():
                        tagger = claude_tagger
                        emit({
                            "type": "log", "level": "info",
                            "message": f"VLM tagger: {tagger.model} (Anthropic vision)",
                        })
                    else:
                        ollama_tagger = OllamaTagger()
                        if ollama_tagger.is_available():
                            tagger = ollama_tagger
                            emit({
                                "type": "log", "level": "info",
                                "message": f"VLM tagger: {tagger.model} via Ollama "
                                           f"(no Anthropic key — using local fallback)",
                            })
                        else:
                            emit({
                                "type": "log", "level": "warn",
                                "message": "No VLM tagger available — proceeding "
                                           "with CLIP embeddings only",
                            })
                            tagger = None
                except Exception as e:
                    emit({
                        "type": "stage_error",
                        "job_id": job.job_id,
                        "stage": "tag",
                        "message": f"Tagger init failed: {e}",
                    })
                    done.set()
                    return

            # Idempotency: skip if clip already indexed, unchanged, AND was
            # tagged by the current tagger. A clip tagged with an older model
            # (e.g. llava:7b before Drop 3.8) gets re-processed to upgrade.
            required_tagger_id = getattr(tagger, "TAGGER_ID", None) if tagger else None
            try:
                mtime = proxy_path.stat().st_mtime
                if db.clip_exists_unchanged(
                    str(proxy_path), mtime, required_tagger_id=required_tagger_id,
                ):
                    completed += 1
                    skipped_count += 1
                    emit({
                        "type": "file_done",
                        "job_id": job.job_id,
                        "stage": "tag",
                        "file": source_file.name,
                        "status": "skipped",
                        "completed": completed,
                        "total": total,
                    })
                    job.project.update_file_status(
                        str(source_file), tag_status="done"
                    )
                    continue
            except OSError:
                pass

            import time
            start = time.time()
            try:
                frame_count = _process_clip(
                    proxy_path, db, frames_dir, embedder, tagger,
                    original_path=source_file,
                )
                elapsed = time.time() - start
                if frame_count > 0:
                    job.project.update_file_status(
                        str(source_file),
                        tag_status="done",
                        tag_frame_count=frame_count,
                    )
                    completed += 1
                    success_count += 1
                    emit({
                        "type": "file_done",
                        "job_id": job.job_id,
                        "stage": "tag",
                        "file": source_file.name,
                        "status": "success",
                        "elapsed_sec": round(elapsed, 1),
                        "frame_count": frame_count,
                        "completed": completed,
                        "total": total,
                    })
                else:
                    job.project.update_file_status(
                        str(source_file),
                        tag_status="failed",
                        tag_error="ffprobe could not read file",
                    )
                    completed += 1
                    failed_count += 1
                    emit({
                        "type": "file_done",
                        "job_id": job.job_id,
                        "stage": "tag",
                        "file": source_file.name,
                        "status": "failed",
                        "error": "Could not probe or extract frames",
                        "completed": completed,
                        "total": total,
                    })
            except Exception as e:
                completed += 1
                failed_count += 1
                job.project.update_file_status(
                    str(source_file),
                    tag_status="failed",
                    tag_error=str(e),
                )
                emit({
                    "type": "file_done",
                    "job_id": job.job_id,
                    "stage": "tag",
                    "file": source_file.name,
                    "status": "failed",
                    "error": str(e)[:200],
                    "completed": completed,
                    "total": total,
                })

        trigger.clear()

    done.set()
