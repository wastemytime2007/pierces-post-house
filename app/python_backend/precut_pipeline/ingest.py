"""Main ingest pipeline: scan folder -> extract frames -> embed + tag -> store."""
import time
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

from .config import VIDEO_EXTENSIONS, FRAMES_DIR
from .database import Database
from .extractor import probe_clip, extract_all_frames
from .embedder import CLIPEmbedder
from .tagger import OllamaTagger
from .camera_inference import infer_camera_tags


console = Console()


def find_video_files(root: Path) -> list[Path]:
    """Recursively find all video files under root."""
    files = []
    for ext in VIDEO_EXTENSIONS:
        files.extend(root.rglob(f"*{ext}"))
        files.extend(root.rglob(f"*{ext.upper()}"))
    # Dedupe (rglob can return dupes on case-insensitive filesystems)
    seen = set()
    unique = []
    for f in files:
        resolved = f.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(f)
    return sorted(unique)


def ingest_folder(
    footage_dir: Path,
    project_dir: Path,
    skip_tagging: bool = False,
) -> dict:
    """Main entry point: ingest all videos in a folder.

    Args:
        footage_dir: folder of video proxies to scan
        project_dir: where database + extracted frames get stored
        skip_tagging: if True, only embed (useful if Ollama isn't set up yet)

    Returns dict with summary stats.
    """
    footage_dir = Path(footage_dir).resolve()
    project_dir = Path(project_dir).resolve()

    if not footage_dir.exists():
        raise FileNotFoundError(f"Footage folder not found: {footage_dir}")

    console.print(f"[bold cyan]Scanning[/] {footage_dir}")
    video_files = find_video_files(footage_dir)
    console.print(f"Found [bold]{len(video_files)}[/] video files")

    if not video_files:
        return {"clips_processed": 0, "frames_extracted": 0}

    # Initialize everything
    db = Database(project_dir)
    frames_dir = project_dir / FRAMES_DIR

    console.print("[bold cyan]Loading CLIP model[/] (first run downloads ~150MB)...")
    embedder = CLIPEmbedder()
    console.print(f"  Running on: [green]{embedder.device}[/]")

    tagger = None
    if not skip_tagging:
        tagger = OllamaTagger()
        if not tagger.is_available():
            console.print("[yellow]⚠  Ollama not reachable or model not pulled.[/]")
            console.print(f"   Run: [dim]ollama pull {tagger.model}[/]")
            console.print("   Continuing with CLIP embeddings only (no descriptive tags).")
            tagger = None
        else:
            console.print(f"  VLM tagger: [green]{tagger.model}[/] via Ollama")

    # Process each clip
    stats = {"clips_processed": 0, "clips_skipped": 0, "clips_failed": 0, "frames_extracted": 0}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Ingesting", total=len(video_files))

        for video_path in video_files:
            progress.update(task, description=f"Processing [dim]{video_path.name}[/]")

            try:
                mtime = video_path.stat().st_mtime
                if db.clip_exists_unchanged(str(video_path), mtime):
                    stats["clips_skipped"] += 1
                    progress.advance(task)
                    continue

                frames = _process_clip(video_path, db, frames_dir, embedder, tagger)
                if frames > 0:
                    stats["clips_processed"] += 1
                    stats["frames_extracted"] += frames
                else:
                    stats["clips_failed"] += 1

            except Exception as e:
                console.print(f"[red]Error processing {video_path.name}:[/] {e}")
                stats["clips_failed"] += 1

            progress.advance(task)

    # Final summary
    db_stats = db.get_stats()
    console.print()
    console.print("[bold green]✓ Ingest complete[/]")
    console.print(f"  New clips processed: [bold]{stats['clips_processed']}[/]")
    console.print(f"  Skipped (unchanged): [dim]{stats['clips_skipped']}[/]")
    if stats["clips_failed"]:
        console.print(f"  Failed: [red]{stats['clips_failed']}[/]")
    console.print(f"  Frames extracted: [bold]{stats['frames_extracted']}[/]")
    console.print(f"  Library total: {db_stats['clips_indexed']} clips, "
                  f"{db_stats['frames_indexed']} frames, "
                  f"{db_stats['hours_indexed']:.1f} hours")

    return stats


def _process_clip(
    video_path: Path,
    db: Database,
    frames_dir: Path,
    embedder: CLIPEmbedder,
    tagger: Optional[OllamaTagger],
    original_path: Optional[Path] = None,
) -> int:
    """Process a single clip. Returns number of frames successfully indexed.

    Drop 4.28: `original_path` is the full-res camera file (vs `video_path`
    which is the proxy we actually index). Stored in the DB so the XML
    exporter can emit pathurls pointing at the full-res source rather
    than the proxy.
    """
    info = probe_clip(video_path)
    if info is None:
        console.print(f"[yellow]  Skipping (couldn't probe):[/] {video_path.name}")
        return 0

    stat = video_path.stat()
    now = time.time()

    # Register the clip
    clip_id = db.upsert_clip(
        path=str(video_path),
        filename=video_path.name,
        duration_sec=info.duration_sec,
        width=info.width,
        height=info.height,
        fps=info.fps,
        file_size=stat.st_size,
        mtime=stat.st_mtime,
        ingested_at=now,
        original_path=str(original_path) if original_path else None,
    )
    db.set_clip_status(clip_id, "tagging")
    # Clear any old frames (handles re-ingest of updated files)
    db.delete_frames_for_clip(clip_id)

    # Extract frames
    frame_data = extract_all_frames(video_path, frames_dir, clip_id)
    if not frame_data:
        db.set_clip_status(clip_id, "error")
        return 0

    # Batch-embed all frames at once (much faster than one at a time)
    frame_paths = [fp for _, fp in frame_data]
    embeddings = embedder.embed_images_batch(frame_paths)

    # Tag each frame individually (VLM doesn't batch well through Ollama HTTP API)
    frames_indexed = 0
    for (timestamp, frame_path), embedding in zip(frame_data, embeddings):
        tags = []
        if tagger is not None:
            tags = tagger.tag_image(frame_path)

        db.insert_frame(
            clip_id=clip_id,
            timestamp_sec=timestamp,
            frame_path=str(frame_path),
            tags=tags,
            embedding=embedding,
        )
        frames_indexed += 1

    # Drop 3.8: record which tagger produced these tags so we can detect
    # when a clip needs re-tagging after a model upgrade.
    if tagger is not None:
        tagger_id = getattr(tagger, "TAGGER_ID", None) or getattr(tagger, "model", "unknown")
        try:
            db.set_clip_tagger(clip_id, str(tagger_id))
        except Exception:
            pass

    # Drop 3.8: motion/framing heuristics. Pure-compute, no ML, no hallucination.
    # Runs once per clip and stores its tags in the clip row (not per-frame).
    try:
        from .motion_analyzer import analyze_clip
        motion_tags = analyze_clip(video_path, info.duration_sec)
    except Exception:
        # Motion analysis is a nice-to-have, never a hard failure
        motion_tags = []

    # Drop 4.38: camera / source-type tags. Inferred from the original file
    # name + the folder path the user organized their footage into. These
    # become searchable keywords in Premiere's Description column so an
    # editor can Cmd+F "drone" or "aerial" without needing the bin
    # structure (which is flat after XML import).
    try:
        cam_tags = infer_camera_tags(original_path or video_path)
        for t in cam_tags:
            if t not in motion_tags:
                motion_tags.append(t)
    except Exception:
        pass

    if motion_tags:
        try:
            db.set_clip_motion_tags(clip_id, motion_tags)
        except Exception:
            pass

    db.set_clip_status(clip_id, "done", frame_count=frames_indexed)
    return frames_indexed
