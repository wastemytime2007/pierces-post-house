"""Command-line interface for B-Roll Buddy.

Commands:
    bb ingest <footage_dir>     Scan and index a folder of video proxies
    bb list                     Show all indexed clips
    bb search <query>           Text search against indexed B-roll
    bb stats                    Show library stats

Stage 2 (transcription):
    bb transcribe <aroll_file>  Transcribe A-roll with Whisper, save JSON

Stage 2.5 (planner):
    bb analyze <transcript.json>      Analyze & Recommend deliverable concepts
    bb plan <transcript.json> <preset>  Generate a specific deliverable plan
    bb presets                        List available deliverable presets

Stage 3 (matching):
    bb match <plan.json> <transcript.json>  Match B-roll to a plan, output cut list

Stage 4 (Premiere export):
    bb export <cutlist.json>     Write Premiere-compatible FCP7 XML
"""
import json
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown

from .ingest import ingest_folder
from .database import Database
from .embedder import CLIPEmbedder
from .config import FRAMES_DIR


console = Console()

# Default project dir — can be overridden per command
DEFAULT_PROJECT = Path.home() / ".precut"


@click.group()
@click.option(
    "--project", "-p",
    type=click.Path(path_type=Path),
    default=DEFAULT_PROJECT,
    help="Project directory (where the database lives)."
)
@click.pass_context
def cli(ctx, project: Path):
    """B-Roll Buddy — local-first B-roll indexer for Premiere Pro."""
    ctx.ensure_object(dict)
    ctx.obj["project"] = project


@cli.command()
@click.argument("footage_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--skip-tagging", is_flag=True,
              help="Skip VLM descriptive tagging (only embed). Faster, no Ollama needed.")
@click.pass_context
def ingest(ctx, footage_dir: Path, skip_tagging: bool):
    """Scan a folder of video proxies and index them."""
    ingest_folder(
        footage_dir=footage_dir,
        project_dir=ctx.obj["project"],
        skip_tagging=skip_tagging,
    )


@cli.command(name="list")
@click.pass_context
def list_clips(ctx):
    """List all indexed clips."""
    db = Database(ctx.obj["project"])
    clips = db.get_all_clips()

    if not clips:
        console.print("[yellow]No clips indexed yet. Run:[/] bb ingest <folder>")
        return

    table = Table(title="Indexed Clips")
    table.add_column("ID", justify="right")
    table.add_column("Filename")
    table.add_column("Duration", justify="right")
    table.add_column("Frames", justify="right")
    table.add_column("Status")

    for c in clips:
        dur = f"{c['duration_sec']:.1f}s" if c["duration_sec"] else "?"
        status_style = {
            "done": "green",
            "error": "red",
            "pending": "yellow",
            "tagging": "cyan",
        }.get(c["status"], "white")
        table.add_row(
            str(c["id"]),
            c["filename"],
            dur,
            str(c["frame_count"]),
            f"[{status_style}]{c['status']}[/]",
        )

    console.print(table)


@cli.command()
@click.argument("query", nargs=-1, required=True)
@click.option("--limit", "-n", default=5, help="Number of results.")
@click.pass_context
def search(ctx, query: tuple, limit: int):
    """Search for B-roll matching a text description.

    This is a preview of the matching engine (Stage 3). Useful now to verify
    your indexed library is actually finding relevant clips.

    Example: bb search "sunset over the ocean"
    """
    query_text = " ".join(query)
    db = Database(ctx.obj["project"])

    console.print(f"[bold cyan]Query:[/] {query_text}")
    console.print("[dim]Loading CLIP...[/]")
    embedder = CLIPEmbedder()

    query_vec = embedder.embed_text(query_text)
    results = db.search_vectors(query_vec, limit=limit)

    if not results:
        console.print("[yellow]No results. Have you ingested anything yet?[/]")
        return

    # Hydrate with clip info + tags
    table = Table(title=f"Top {len(results)} matches")
    table.add_column("Score", justify="right")
    table.add_column("Clip")
    table.add_column("Timestamp", justify="right")
    table.add_column("Tags")

    with db.connect() as conn:
        for r in results:
            frame_id = r["frame_id"]
            # LanceDB returns a _distance field; lower is closer.
            distance = r.get("_distance", 0)
            score = 1.0 - distance  # rough similarity

            row = conn.execute("""
                SELECT f.timestamp_sec, f.tags, c.filename
                FROM frames f JOIN clips c ON f.clip_id = c.id
                WHERE f.id = ?
            """, (frame_id,)).fetchone()

            if row is None:
                continue

            tags = json.loads(row["tags"]) if row["tags"] else []
            tag_preview = ", ".join(tags[:4]) + ("..." if len(tags) > 4 else "")

            table.add_row(
                f"{score:.3f}",
                row["filename"],
                f"{row['timestamp_sec']:.1f}s",
                tag_preview or "[dim]no tags[/]",
            )

    console.print(table)


@cli.command()
@click.pass_context
def stats(ctx):
    """Show library statistics."""
    db = Database(ctx.obj["project"])
    s = db.get_stats()
    console.print(f"[bold]Project:[/] {ctx.obj['project']}")
    console.print(f"Clips indexed: [bold]{s['clips_indexed']}[/] / {s['clips_total']}")
    console.print(f"Frames indexed: [bold]{s['frames_indexed']}[/]")
    console.print(f"Hours of footage: [bold]{s['hours_indexed']:.2f}[/]")


# ============================================================
# Stage 2 — Transcription
# ============================================================

@cli.command()
@click.argument("aroll_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None,
              help="Where to save transcript JSON. Defaults to <aroll>_transcript.json.")
@click.option("--model", default=None,
              help="Whisper model: tiny/base/small/medium/large. Default from config.")
@click.option("--language", default=None,
              help="Force language (e.g. 'en'). Default: auto-detect.")
@click.pass_context
def transcribe(ctx, aroll_file: Path, output, model, language):
    """Transcribe an A-roll file with word-level timestamps."""
    from .transcriber import Transcriber

    if output is None:
        output = aroll_file.with_name(aroll_file.stem + "_transcript.json")

    console.print(f"[bold cyan]Transcribing[/] {aroll_file.name}")
    console.print("[dim]First run downloads Whisper model weights (~150MB for 'base')[/]")

    transcriber_kwargs = {}
    if model:
        transcriber_kwargs["model_name"] = model
    tr = Transcriber(**transcriber_kwargs)
    console.print(f"  Model: [green]{tr.model_name}[/] on [green]{tr.device}[/]")

    transcript = tr.transcribe(aroll_file, language=language)
    transcript.save(output)

    console.print()
    console.print("[bold green]✓ Transcription complete[/]")
    console.print(f"  Language: [bold]{transcript.language}[/]")
    console.print(f"  Duration: [bold]{transcript.duration:.1f}s[/]")
    console.print(f"  Phrases: [bold]{len(transcript.phrases)}[/]")
    console.print(f"  Saved to: [cyan]{output}[/]")

    # Preview
    console.print()
    console.print("[bold]Preview[/] (first 5 phrases):")
    for p in transcript.phrases[:5]:
        console.print(f"  [dim]{p.start:5.1f}-{p.end:5.1f}s[/] {p.text}")


# ============================================================
# Stage 2.5 — Deliverable Planner
# ============================================================

@cli.command()
def presets():
    """List built-in deliverable presets."""
    from .presets import ALL_PRESETS

    table = Table(title="Deliverable Presets")
    table.add_column("Key", style="cyan")
    table.add_column("Name")
    table.add_column("Category")
    table.add_column("Target", justify="right")

    for p in ALL_PRESETS:
        if p.target_duration_sec < 0:
            target = "full"
        else:
            target = f"{p.target_duration_sec:.0f}s"
        table.add_row(p.key, p.display_name, p.category, target)

    console.print(table)
    console.print()
    console.print(
        "[dim]Use any preset key with:[/] "
        "bb plan <transcript.json> <preset_key> --brief \"your brief\""
    )


@cli.command()
@click.argument("transcript_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None,
              help="Where to save the analysis report. Defaults to <transcript>_analysis.json.")
@click.option("--max-concepts", default=5, help="Maximum deliverable concepts to pitch.")
@click.option("--presets", "preset_filter", multiple=True,
              help="Only consider these preset keys (can repeat). Default: all.")
def analyze(transcript_file: Path, output, max_concepts: int, preset_filter):
    """Analyze A-roll and recommend deliverable concepts."""
    from .transcriber import Transcript
    from .planner import DeliverablePlanner

    if output is None:
        output = transcript_file.with_name(
            transcript_file.stem.replace("_transcript", "") + "_analysis.json"
        )

    transcript = Transcript.load(transcript_file)
    console.print(f"[bold cyan]Analyzing[/] {transcript_file.name}")
    console.print(f"  Source duration: [bold]{transcript.duration:.1f}s[/]")
    console.print(f"  Phrases: [bold]{len(transcript.phrases)}[/]")
    console.print()

    try:
        planner = DeliverablePlanner()
    except Exception as e:
        console.print(f"[red]Planner init failed:[/] {e}")
        console.print("[yellow]Set ANTHROPIC_API_KEY environment variable.[/]")
        return

    preset_list = list(preset_filter) if preset_filter else None

    with console.status("[cyan]Calling Claude to analyze...[/]"):
        report = planner.analyze_and_recommend(
            transcript,
            available_preset_keys=preset_list,
            max_concepts=max_concepts,
        )

    # Print report
    console.print(Panel(report.summary, title="Summary", border_style="cyan"))
    console.print()
    console.print(f"[bold]Recommended deliverables ({len(report.concepts)}):[/]")
    console.print()

    for i, c in enumerate(report.concepts, 1):
        header = f"[bold]{i}. {c.concept}[/]"
        body = (
            f"[cyan]Preset:[/] {c.suggested_preset}  "
            f"[cyan]Duration:[/] ~{c.estimated_duration:.0f}s  "
            f"[cyan]Tone:[/] {c.tone}\n\n"
            f"{c.pitch}\n\n"
            f"[dim]Why it works:[/] {c.why_it_works}\n"
            f"[dim]Key phrase IDs:[/] {c.key_phrase_ids}"
        )
        console.print(Panel(body, title=header, border_style="blue"))
        console.print()

    report.save(output)
    console.print(f"[green]✓ Report saved to[/] [cyan]{output}[/]")
    console.print()
    console.print(
        "[dim]To execute one of these concepts:[/] "
        "bb plan <transcript.json> <preset_key> --brief \"...\""
    )


@cli.command()
@click.argument("transcript_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("preset_key")
@click.option("--brief", default="", help="Creative brief (freeform).")
@click.option("--topic", default="", help="Topic focus (what the deliverable is ABOUT).")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None,
              help="Where to save the deliverable plan JSON.")
def plan(transcript_file: Path, preset_key: str, brief: str, topic: str, output):
    """Generate a specific deliverable plan from a transcript + preset."""
    from .transcriber import Transcript
    from .planner import DeliverablePlanner
    from .presets import get_preset

    try:
        preset = get_preset(preset_key)
    except KeyError as e:
        console.print(f"[red]{e}[/]")
        console.print("[dim]Run `bb presets` to see available keys.[/]")
        return

    transcript = Transcript.load(transcript_file)

    if output is None:
        stem = transcript_file.stem.replace("_transcript", "")
        output = transcript_file.with_name(f"{stem}_{preset_key}_plan.json")

    console.print(f"[bold cyan]Planning[/] {preset.display_name}")
    if brief:
        console.print(f"  Brief: [italic]{brief}[/]")
    if topic:
        console.print(f"  Topic: [italic]{topic}[/]")
    console.print()

    try:
        planner = DeliverablePlanner()
    except Exception as e:
        console.print(f"[red]Planner init failed:[/] {e}")
        return

    with console.status("[cyan]Claude is planning your cut...[/]"):
        deliverable = planner.plan_deliverable(
            transcript=transcript,
            preset_key=preset_key,
            brief=brief,
            topic_focus=topic,
        )

    # Display plan
    header = f"[bold]{deliverable.suggested_title or deliverable.concept}[/]"
    overview = (
        f"[cyan]Concept:[/] {deliverable.concept}\n"
        f"[cyan]Tone:[/] {deliverable.tone}\n"
        f"[cyan]Target:[/] {deliverable.target_duration:.0f}s  "
        f"[cyan]Actual:[/] {deliverable.actual_duration:.1f}s\n\n"
        f"{deliverable.pitch}\n\n"
        f"[dim]Opening hook:[/] {deliverable.opening_hook}"
    )
    console.print(Panel(overview, title=header, border_style="green"))
    console.print()

    console.print(f"[bold]Segments ({len(deliverable.segments)}):[/]")
    for seg in deliverable.segments:
        seg_title = (
            f"[bold]#{seg.order + 1} — {seg.role}[/]  "
            f"[dim]{seg.source_start:.1f}-{seg.source_end:.1f}s "
            f"({seg.duration:.1f}s)[/]"
        )
        body = (
            f"[italic]\"{seg.text}\"[/]\n\n"
            f"[cyan]B-roll themes:[/] {', '.join(seg.broll_themes) or '[dim]none[/]'}\n"
            f"[cyan]Pacing:[/] {seg.broll_pacing}  "
            f"[cyan]Cutaway density:[/] {seg.cutaway_density:.1f}\n"
        )
        if seg.editorial_notes:
            body += f"[dim]Notes:[/] {seg.editorial_notes}"
        console.print(Panel(body, title=seg_title, border_style="blue"))

    deliverable.save(output)
    console.print()
    console.print(f"[green]✓ Plan saved to[/] [cyan]{output}[/]")
    console.print()
    console.print(
        "[dim]Next (Stage 3): match B-roll clips to these segments \u2192 "
        "then export Premiere XML in Stage 4.[/]"
    )


# ============================================================
# Stage 3 — Matching Engine
# ============================================================

@cli.command()
@click.argument("plan_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("transcript_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None,
              help="Where to save the cut list JSON.")
@click.pass_context
def match(ctx, plan_file: Path, transcript_file: Path, output):
    """Match B-roll clips to a deliverable plan, produce a cut list.

    The cut list is Stage 3's output: for every segment in the plan, specific
    B-roll clips are selected and timed. Stage 4 will turn this into a
    Premiere-importable XML.
    """
    from .transcriber import Transcript
    from .deliverable import Deliverable
    from .matcher import Matcher

    if output is None:
        output = plan_file.with_name(plan_file.stem.replace("_plan", "") + "_cutlist.json")

    # Load plan and transcript
    plan_data = json.loads(plan_file.read_text())
    deliverable = Deliverable.from_dict(plan_data)
    transcript = Transcript.load(transcript_file)

    console.print(f"[bold cyan]Matching B-roll for[/] {deliverable.concept}")
    console.print(f"  Preset: [bold]{deliverable.preset_key}[/]  "
                  f"Segments: [bold]{len(deliverable.segments)}[/]")
    console.print()

    # Open DB + embedder
    db = Database(ctx.obj["project"])
    stats = db.get_stats()
    if stats["frames_indexed"] == 0:
        console.print("[red]No B-roll indexed yet. Run `bb ingest` first.[/]")
        return
    console.print(f"  Searching [bold]{stats['frames_indexed']}[/] frames "
                  f"across [bold]{stats['clips_indexed']}[/] clips")

    console.print("[dim]Loading CLIP for text queries...[/]")
    matcher = Matcher(database=db)

    with console.status("[cyan]Matching...[/]"):
        cutlist = matcher.match(deliverable, transcript)

    # Report
    console.print()
    console.print("[bold green]✓ Matching complete[/]")
    console.print(f"  Final cut duration: [bold]{cutlist.total_duration:.1f}s[/]")
    console.print(f"  B-roll shots placed: [bold]{len(cutlist.broll_track)}[/]")
    console.print(f"  Segments with B-roll: [green]{cutlist.segments_with_broll}[/]")
    console.print(f"  Segments on speaker: [dim]{cutlist.segments_without_broll}[/]")
    if cutlist.unmatched_segments:
        console.print(f"  [yellow]⚠  No matches above threshold for segment(s):[/] "
                      f"{cutlist.unmatched_segments}")
    console.print()

    # Show per-shot breakdown
    if cutlist.broll_track:
        table = Table(title="B-roll placements")
        table.add_column("Seg", justify="right")
        table.add_column("Timeline", justify="right")
        table.add_column("Duration", justify="right")
        table.add_column("Clip")
        table.add_column("Theme", style="dim")
        table.add_column("Score", justify="right")
        for shot in cutlist.broll_track:
            table.add_row(
                str(shot.segment_order),
                f"{shot.timeline_start:.1f}s",
                f"{shot.timeline_duration:.2f}s",
                Path(shot.source_file).name,
                shot.matched_theme[:28] + ("…" if len(shot.matched_theme) > 28 else ""),
                f"{shot.match_score:.2f}",
            )
        console.print(table)

    cutlist.save(output)
    console.print()
    console.print(f"[green]✓ Cut list saved to[/] [cyan]{output}[/]")
    console.print()
    console.print("[dim]Next (Stage 4): `bb export <cutlist.json>` → Premiere XML[/]")


# ============================================================
# Stage 4 — Premiere XML Export
# ============================================================

@cli.command()
@click.argument("cutlist_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None,
              help="Where to save the XML. Defaults to <cutlist>_premiere.xml.")
@click.option("--fps", type=float, default=None,
              help="Override sequence fps. Default: use preset setting.")
@click.option("--width", type=int, default=None,
              help="Override sequence width. Default: use preset setting.")
@click.option("--height", type=int, default=None,
              help="Override sequence height. Default: use preset setting.")
@click.option("--name", default=None,
              help="Sequence name shown in Premiere. Default: auto-generated.")
@click.option("--no-overlay", is_flag=True, default=False,
              help="Skip the safe-zone overlay on V3.")
@click.option("--source-width", type=int, default=1920,
              help="Assumed source footage width for auto-scale math. Default: 1920.")
@click.option("--source-height", type=int, default=1080,
              help="Assumed source footage height for auto-scale math. Default: 1080.")
def export(cutlist_file: Path, output, fps, width, height, name,
           no_overlay, source_width, source_height):
    """Export a cut list to FCP7 XML that Premiere can import.

    Sequence dimensions come from the deliverable preset by default — vertical
    for reels/tiktok, horizontal for ads/youtube. A safe-zone overlay is
    placed on V3 so you can see where platform UI will obscure content.
    """
    from .cutlist import CutList
    from .exporter import export_to_premiere_xml

    if output is None:
        output = cutlist_file.with_name(
            cutlist_file.stem.replace("_cutlist", "") + "_premiere.xml"
        )

    cutlist = CutList.load(cutlist_file)

    # Figure out effective sequence settings for console output
    eff_fps = fps if fps is not None else cutlist.sequence_fps
    eff_w = width if width is not None else cutlist.sequence_width
    eff_h = height if height is not None else cutlist.sequence_height
    aspect = _aspect_str(eff_w, eff_h)

    console.print(f"[bold cyan]Exporting to Premiere XML[/]")
    console.print(f"  Concept: [italic]{cutlist.deliverable_concept}[/]")
    console.print(f"  Preset: [bold]{cutlist.deliverable_preset}[/]")
    console.print(f"  Sequence: [bold]{eff_w}x{eff_h}[/] ({aspect}) "
                  f"@ [bold]{eff_fps} fps[/]")
    overlay_label = "none" if no_overlay else cutlist.overlay_style
    console.print(f"  Overlay: [bold]{overlay_label}[/]")
    if source_width != eff_w or source_height != eff_h:
        scale_pct = max(eff_w / source_width, eff_h / source_height) * 100
        console.print(
            f"  Auto-fit: source [bold]{source_width}x{source_height}[/] "
            f"→ scale [bold]{scale_pct:.0f}%[/] to fill frame"
        )
    console.print(f"  A-roll: [bold]{len(cutlist.aroll_track)} clips[/]  "
                  f"B-roll: [bold]{len(cutlist.broll_track)} shots[/]")
    console.print()

    path = export_to_premiere_xml(
        cutlist=cutlist,
        output_path=output,
        fps=fps,
        width=width,
        height=height,
        sequence_name=name,
        include_overlay=not no_overlay,
        source_width=source_width,
        source_height=source_height,
    )

    console.print("[bold green]✓ XML written[/]")
    console.print(f"  Path: [cyan]{path}[/]")
    console.print()
    console.print("[bold]To open in Premiere:[/]")
    console.print("  1. Open Premiere Pro (empty project is fine)")
    console.print("  2. File → Import")
    console.print(f"  3. Pick {path.name}")
    console.print("  4. Double-click the new sequence in Project panel")
    console.print()
    if not no_overlay:
        console.print("[yellow]⚠  Safe-zone overlay is on V3.[/] "
                      "Disable or delete that track before final export — "
                      "you don't want the overlay baked into your delivered video.")
    console.print()
    console.print("[dim]If clips show as offline: right-click in Project panel → Link Media.[/]")


def _aspect_str(w: int, h: int) -> str:
    if h == 0:
        return f"{w}x{h}"
    r = w / h
    if abs(r - 16 / 9) < 0.01: return "16:9"
    if abs(r - 9 / 16) < 0.01: return "9:16"
    if abs(r - 1.0) < 0.01:    return "1:1"
    return f"{w}:{h}"


if __name__ == "__main__":
    cli()
