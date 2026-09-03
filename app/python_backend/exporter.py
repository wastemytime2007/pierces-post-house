"""Export orchestrator — turns selected Ideas into a multi-timeline XML.

For each selected idea:
  1. Load the stored Deliverable from plans/<idea_id>.json
  2. Run the matcher against the B-roll LanceDB index to find clips
  3. Assemble a CutList with A-roll + matched B-roll cutaways
  4. Collect paths to clean mic audio for audio_sync in the pipeline

Then for the whole project:
  5. Load the complete B-roll library (every clip + its tags/descriptions)
  6. Optionally run audalign to compute clean-mic offsets
  7. Hand off to multi_exporter to write the XML

Emits streaming events so the UI can show progress:
    export_started
    export_matching (idea_id, idea_name)
    export_matched (idea_id, clips_used)
    export_sync_started
    export_sync_result (pairs, overall confidence)
    export_writing
    export_complete (xml_path, sequences, broll_library_size)
    export_error
"""
from __future__ import annotations

import json
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from project import Project


@dataclass
class ExportOptions:
    """Configuration for a single export job."""
    output_path: Path                     # where to write the XML
    idea_ids: list[str]                   # which deliverables to include
    include_full_library: bool = True     # all B-roll as library bin (else used-only)
    include_overlay: bool = True          # safe-zone overlay PNG on V3
    # Drop 4.44: "library only" mode — no AI-generated ideas needed.
    # When true, idea_ids is ignored (may be empty), the matcher is
    # skipped entirely, and the output contains just the All-Synced-
    # A-Roll reference sequence + B-roll library bin. Used by the
    # "Export library only" button on the Ideas tab, which lets users
    # without an API key still get a useful XML out of their footage.
    library_only: bool = False
    # Kept for IPC backward compat — audio sync now runs as a pipeline stage
    # and is always consulted if state is available. These flags are ignored.
    run_audio_sync: bool = True
    include_clean_mic: bool = True
    # Drop 4.46: user-configured auto-include rules. Each rule routes
    # files (or folder contents) into a specific bin in every export.
    # Loaded from settings.json by the backend command handler. List of
    # dicts shaped like AutoIncludeRule.to_dict(). Empty list = no rules.
    auto_include_rules: Optional[list[dict]] = None


def run_export(
    project: Project,
    job_id: str,
    options: ExportOptions,
    emit: Callable[[dict], None],
) -> None:
    """Main entry point. Runs the whole export pipeline and emits events."""
    emit({
        "type": "export_started",
        "job_id": job_id,
        "output_path": str(options.output_path),
        "idea_count": len(options.idea_ids),
        "library_only": bool(options.library_only),
    })

    # ----- Step 1: load all selected ideas, verify they're deliverables -----
    #
    # Skipped entirely in library_only mode — no ideas needed for a
    # library-only export (Drop 4.44).

    ideas: list[dict] = []
    if not options.library_only:
        for iid in options.idea_ids:
            idea_path = project.plans_dir() / f"{iid}.json"
            if not idea_path.exists():
                emit({"type": "export_error", "job_id": job_id,
                      "message": f"Idea not found: {iid}"})
                return
            try:
                data = json.loads(idea_path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                emit({"type": "export_error", "job_id": job_id,
                      "message": f"Couldn't read idea {iid}: {e}"})
                return
            if data.get("kind") not in ("deliverable", "story_angle"):
                emit({
                    "type": "export_error", "job_id": job_id,
                    "message": f"Idea {iid} is a concept, not a full plan. "
                               "Refine it first (give it feedback) to upgrade.",
                })
                return
            ideas.append(data)

    # ----- Step 2: load B-roll index -----

    broll_db_path = project.broll_index_dir() / "precut.db"
    if options.include_full_library and not broll_db_path.exists():
        emit({
            "type": "log", "level": "warn",
            "message": "No B-roll index found — skipping library bin.",
        })

    # Drop 4.44: library_only preflight. If neither A-roll (for the
    # All-Synced reference sequence) nor B-roll library exists, there's
    # nothing meaningful to export — bail with a clear error rather
    # than writing an empty XML.
    if options.library_only:
        has_aroll = bool(project.sources_by_kind("aroll"))
        has_broll_library = broll_db_path.exists()
        if not has_aroll and not has_broll_library:
            emit({"type": "export_error", "job_id": job_id,
                  "message": "Nothing to export — no A-roll and no B-roll "
                             "index found. Add footage and run the pipeline first."})
            return

    # ----- Step 3: run matcher per idea -----
    #
    # In library_only mode we skip this whole section — no ideas to
    # match, no cutlists to build. `cutlists` stays empty and Step 6
    # below falls back to just the All-Synced-A-Roll reference
    # sequence + library bin.
    from precut_pipeline.cutlist import CutList
    cutlists: list[tuple[str, CutList]] = []  # (sequence_name, cutlist)

    if not options.library_only:
        try:
            from precut_pipeline.matcher import Matcher
            from precut_pipeline.database import Database
            from precut_pipeline.embedder import CLIPEmbedder
            from precut_pipeline.deliverable import Deliverable
            from precut_pipeline.transcriber import Transcript
        except ImportError as e:
            emit({"type": "export_error", "job_id": job_id,
                  "message": f"Failed to import matcher: {e}",
                  "traceback": traceback.format_exc()})
            return

        # Matcher needs the combined transcript + B-roll index
        transcript_paths = sorted(project.transcripts_dir().glob("*.json"))
        if not transcript_paths:
            emit({"type": "export_error", "job_id": job_id,
                  "message": "No A-roll transcripts found."})
            return

        try:
            combined_transcript, phrase_source_map = _combine_transcripts(transcript_paths)
        except Exception as e:
            emit({"type": "export_error", "job_id": job_id,
                  "message": f"Couldn't load transcripts: {e}",
                  "traceback": traceback.format_exc()})
            return

        # Build a per-source-file time-offset map. When we combined transcripts
        # in order, each phrase got its .start/.end shifted by the cumulative
        # duration of prior transcripts. We need to reverse that shift when
        # writing source_start/source_end into the XML (those values must be
        # relative to the ORIGINAL file's own timeline, not the combined one).
        source_offset_map = _build_source_offset_map(transcript_paths)

        # Resolve every transcript path back to the user's original source file
        # (not the proxy). We use project.sources to find the original that
        # produced this proxy, matching by filename stem.
        source_to_original = _build_proxy_to_original_map(project, transcript_paths)

        if broll_db_path.exists():
            db = Database(project.broll_index_dir())
            embedder = CLIPEmbedder()
        else:
            db = None
            embedder = None

        # Match each idea to produce a CutList
        for idea_data in ideas:
            d = idea_data["data"]
            idea_id = idea_data["idea_id"]
            idea_kind = idea_data.get("kind", "deliverable")

            # Drop 4.0: story_angle ideas route through story_assembler (no
            # CLIP matching, no pacing — phrases in source order + brief).
            if idea_kind == "story_angle":
                try:
                    from precut_pipeline.story_assembler import (
                        assemble_cut_from_angle,
                    )
                    from producer import _angle_from_dict  # reuse helper
                except ImportError as e:
                    emit({"type": "export_error", "job_id": job_id,
                          "message": f"Failed to import story_assembler: {e}",
                          "traceback": traceback.format_exc()})
                    return

                try:
                    angle = _angle_from_dict(d)
                except Exception as e:
                    emit({"type": "export_error", "job_id": job_id,
                          "message": f"Bad story_angle data in {idea_id}: {e}"})
                    return

                # Drop 4.4: two-field selection — platform + aspect. Pull from
                # the idea envelope (where set_angle_platform_and_aspect saves them).
                # Empty strings signal "A-roll native" for aspect and "no overlay"
                # for platform.
                selected_platform_key = idea_data.get("selected_platform_key") or ""
                selected_aspect_key = idea_data.get("selected_aspect_key") or ""

                # Drop 4.6: if EITHER new-style field is present in the envelope
                # (even as empty string, meaning user explicitly picked "None"),
                # treat this as a Drop 4.4-managed angle and DO NOT fall back to
                # the legacy suggested_preset. The user's "None / None" choice
                # must propagate all the way to A-roll native dims + no overlay.
                is_drop44_managed = (
                    "selected_platform_key" in idea_data
                    or "selected_aspect_key" in idea_data
                )
                if is_drop44_managed:
                    selected_preset_key = ""
                else:
                    selected_preset_key = (
                        idea_data.get("selected_preset_key")
                        or angle.suggested_preset
                    )

                sequence_name = _sanitize_sequence_name(angle.brief.title or "Story Angle")

                emit({
                    "type": "export_matching",
                    "job_id": job_id,
                    "idea_id": idea_id,
                    "sequence_name": sequence_name,
                    "kind": "story_angle",
                })

                try:
                    cutlist = assemble_cut_from_angle(
                        angle=angle,
                        transcript=combined_transcript,
                        db=db,
                        preset_key=selected_preset_key,
                        source_offset_map=source_offset_map,
                        source_to_original=source_to_original,
                        aspect_key=selected_aspect_key,
                        platform_key=selected_platform_key,
                    )
                except Exception as e:
                    emit({"type": "export_error", "job_id": job_id,
                          "message": f"Assembler failed on {idea_id}: {e}",
                          "traceback": traceback.format_exc()})
                    return

                # Drop 4.2: the assembler now resolves source files itself (needed
                # for multi-file projects and proxy-to-original mapping). Skip
                # the legacy _fixup_aroll_sources call — it's keyed on real Whisper
                # phrase_ids and would no-op on the synthesized range IDs anyway.
                _fixup_broll_sources(cutlist, project)

                cutlists.append((sequence_name, cutlist))
                emit({
                    "type": "export_matched",
                    "job_id": job_id,
                    "idea_id": idea_id,
                    "aroll_phrase_count": len(cutlist.aroll_track),
                    "broll_marker_count": len(cutlist.broll_markers),
                    "kind": "story_angle",
                })
                continue

            # Deliverable path (legacy / pre-4.0 — still supported)
            concept = d.get("concept", "Untitled")
            sequence_name = _sanitize_sequence_name(concept)

            emit({
                "type": "export_matching",
                "job_id": job_id,
                "idea_id": idea_id,
                "sequence_name": sequence_name,
            })

            try:
                deliverable = Deliverable.from_dict(d)
            except Exception as e:
                emit({"type": "export_error", "job_id": job_id,
                      "message": f"Bad deliverable data in {idea_id}: {e}"})
                return

            try:
                if db is None:
                    # No B-roll index — produce a CutList with just A-roll
                    cutlist = _cutlist_from_deliverable_no_broll(
                        deliverable, combined_transcript,
                    )
                else:
                    matcher = Matcher(database=db, embedder=embedder)
                    cutlist = matcher.match(deliverable, combined_transcript)
            except Exception as e:
                emit({"type": "export_error", "job_id": job_id,
                      "message": f"Matcher failed on {idea_id}: {e}",
                      "traceback": traceback.format_exc()})
                return

            # ---- Post-match fixup (Drop 3.4) ----
            # The Matcher writes transcript.source_path into every ARollPhrase.
            # For multi-file projects that's a useless joined string. And every
            # phrase's source_start/source_end is offset by where its file sits
            # in the combined timeline. We fix both here + walk proxies back to
            # originals so the XML references what the user can attach proxies to.
            _fixup_aroll_sources(cutlist, phrase_source_map, source_offset_map, source_to_original)
            _fixup_broll_sources(cutlist, project)

            cutlists.append((sequence_name, cutlist))
            # Use the real CutList attributes (see cli.py's reporting block).
            broll_shot_count = len(getattr(cutlist, "broll_track", []))
            broll_marker_count = len(getattr(cutlist, "broll_markers", []))
            aroll_phrase_count = len(getattr(cutlist, "aroll_track", []))
            emit({
                "type": "export_matched",
                "job_id": job_id,
                "idea_id": idea_id,
                "aroll_phrases": aroll_phrase_count,
                "broll_cutaways": broll_shot_count,
                "broll_markers": broll_marker_count,  # Drop 3.7+: marker count
            })

    # ----- Step 4: load cached audio sync state (Drop 3.6) -----
    #
    # The audio_sync pipeline stage ran earlier and cached its results in
    # project.audio_sync. Here we just deserialize that state so the XML
    # writer can do per-phrase coverage lookups. If no sync has run, this
    # is None and the exporter falls through without lav tracks (camera
    # audio only on A1).

    audio_sync_state = None
    if getattr(project, "audio_sync", None):
        try:
            from precut_pipeline.audio_sync import AudioSyncState
            audio_sync_state = AudioSyncState.from_dict(project.audio_sync)
            reliable = sum(1 for p in audio_sync_state.pairs if p.is_reliable)
            emit({"type": "log", "level": "info",
                  "message": f"Audio sync: {reliable} reliable pair(s), "
                             f"{len(audio_sync_state.groups)} track group(s)"})
        except Exception as e:
            emit({"type": "log", "level": "warn",
                  "message": f"Couldn't load audio sync state: {e}"})
            audio_sync_state = None
    else:
        emit({"type": "log", "level": "info",
              "message": "No audio sync — run pipeline to enable synced lav tracks."})

    # ----- Step 5: load B-roll library -----

    library: list = []
    if options.include_full_library and broll_db_path.exists():
        try:
            from precut_pipeline.multi_exporter import load_broll_library
            library = load_broll_library(broll_db_path)
            emit({"type": "log", "level": "info",
                  "message": f"Loaded {len(library)} B-roll clips for library bin"})
        except Exception as e:
            emit({"type": "log", "level": "warn",
                  "message": f"Couldn't load B-roll library: {e}"})

    # ----- Step 6: write multi-timeline XML -----

    emit({"type": "export_writing", "job_id": job_id})

    try:
        from precut_pipeline.multi_exporter import (
            export_multi_timeline, ExportRequest,
        )

        # Detect source dimensions — critical for 4K sources. Prefer B-roll
        # DB (populated by tagging stage); fall back to ffprobing the first
        # A-roll original.
        src_w, src_h = _detect_source_dimensions(project, broll_db_path)
        if src_w and src_h:
            emit({"type": "log", "level": "info",
                  "message": f"Source dimensions: {src_w}x{src_h}"})
        else:
            emit({"type": "log", "level": "warn",
                  "message": "Couldn't detect source dimensions; assuming 1920x1080"})

        requests = [
            ExportRequest(
                cutlist=cl,
                sequence_name=name,
                audio_sync_state=audio_sync_state,
                source_width=src_w,
                source_height=src_h,
            )
            for name, cl in cutlists
        ]

        # Drop 4.18: one combined "All Synced A-Roll" sequence with every
        # A-roll clip laid end-to-end (was N per-file sequences in 4.17).
        try:
            all_aroll_requests = _build_all_aroll_sequences(
                project, audio_sync_state, src_w, src_h, emit,
            )
            if all_aroll_requests:
                # Always exactly one request now — but future-proof with
                # a count so we'd notice if that ever changes.
                phrase_count = len(all_aroll_requests[0].cutlist.aroll_track)
                emit({"type": "log", "level": "info",
                      "message": f"Adding combined 'All Synced A-Roll' reference "
                                 f"sequence ({phrase_count} A-roll clip"
                                 f"{'s' if phrase_count != 1 else ''})"})
                requests.extend(all_aroll_requests)
        except Exception as e:
            # Non-fatal: if the all-A-roll builder trips, keep going with
            # just the story sequences. Log a warning so it's diagnosable.
            emit({"type": "log", "level": "warn",
                  "message": f"Couldn't build All-A-Roll sequence: {e}"})

        written = export_multi_timeline(
            requests=requests,
            output_path=options.output_path,
            broll_library=library if options.include_full_library else None,
            project_name=project.name,
            include_overlay=options.include_overlay,
            auto_include_rules=options.auto_include_rules,
        )
    except Exception as e:
        emit({"type": "export_error", "job_id": job_id,
              "message": f"XML writer failed: {e}",
              "traceback": traceback.format_exc()})
        return

    # Build completion event summary
    reliable_sync_pairs = 0
    if audio_sync_state:
        reliable_sync_pairs = sum(1 for p in audio_sync_state.pairs if p.is_reliable)

    emit({
        "type": "export_complete",
        "job_id": job_id,
        "xml_path": str(written),
        "sequences": len(cutlists),
        "broll_library_size": len(library),
        "audio_sync_pairs": reliable_sync_pairs,
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_source_dimensions(project, broll_db_path: Path) -> tuple[Optional[int], Optional[int]]:
    """Fallback source-dimension hint for the writer.

    Drop 4.12: only used when per-clip ffprobe fails. Each clipitem now
    probes its own source file and uses those dims for its fit-filter
    scale. This function exists just to seed a reasonable default.

    Order of preference:
      1. ffprobe on the first A-roll original we can find (actual talking-
         head footage dims — what V1 fit-scaling should assume)
      2. B-roll DB (only if no A-roll files probe cleanly; may be wrong
         if A-roll and B-roll have different dimensions)

    Returns (None, None) if nothing is detectable. Writer then uses
    ASSUMED_SOURCE_WIDTH/HEIGHT = 1920x1080.
    """
    import sqlite3 as _sqlite3

    # 1. ffprobe A-roll originals — the source dims that MATTER for V1
    for src in project.sources_by_kind("aroll"):
        for file_path_str in list(src.files.keys())[:3]:  # check up to 3
            file_path = Path(file_path_str)
            if not file_path.exists():
                continue
            dims = _probe_video_dimensions(file_path)
            if dims:
                return dims

    # 2. Last resort: B-roll DB. These dims apply only to B-roll clips,
    # but if we have nothing else, it's better than ASSUMED defaults.
    if broll_db_path.exists():
        try:
            conn = _sqlite3.connect(str(broll_db_path))
            conn.row_factory = _sqlite3.Row
            row = conn.execute(
                "SELECT width, height FROM clips WHERE width > 0 AND height > 0 LIMIT 1"
            ).fetchone()
            conn.close()
            if row and row["width"] and row["height"]:
                return int(row["width"]), int(row["height"])
        except Exception:
            pass

    return None, None


def _probe_video_dimensions(path: Path) -> Optional[tuple[int, int]]:
    """Use ffprobe to read video width x height. None if it fails."""
    try:
        from proxy_manager import find_ffmpeg
    except ImportError:
        return None
    ffmpeg_bin = find_ffmpeg()
    if not ffmpeg_bin:
        return None
    # ffprobe sits next to ffmpeg in Homebrew layouts
    ffprobe_bin = ffmpeg_bin.replace("/ffmpeg", "/ffprobe")
    if not Path(ffprobe_bin).exists():
        return None
    import subprocess
    try:
        result = subprocess.run(
            [ffprobe_bin, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=s=x:p=0", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        out = result.stdout.strip()
        if "x" in out:
            parts = out.split("x")
            return int(parts[0]), int(parts[1])
    except (subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return None


def _build_source_offset_map(transcript_paths: list[Path]) -> dict[str, float]:
    """For each transcript file, compute how much its phrases got time-shifted
    when combined. Matches the cumulative-offset approach in _combine_transcripts.

    Returns source_path -> offset_seconds (to SUBTRACT to get back to per-file
    time).
    """
    from precut_pipeline.transcriber import Transcript
    offsets: dict[str, float] = {}
    cursor = 0.0
    for tp in transcript_paths:
        try:
            t = Transcript.load(tp)
        except Exception:
            continue
        offsets[t.source_path] = cursor
        cursor += t.duration
    return offsets


def _build_proxy_to_original_map(project, transcript_paths: list[Path]) -> dict[str, str]:
    """Map the transcript's source_path (which is typically the proxy path —
    we transcribe proxies for speed) back to the ORIGINAL camera file.

    We do this by walking project.sources for A-roll kinds and matching by
    filename stem. If no original is found, fall back to the proxy path (so
    worst case Premiere sees the proxy as a regular clip rather than offline).
    """
    from precut_pipeline.transcriber import Transcript

    # Every A-roll file the user added. Key by stem for fast lookup.
    stem_to_original: dict[str, str] = {}
    for src in project.sources_by_kind("aroll"):
        for file_path_str in src.files.keys():
            # file_path_str is the ORIGINAL path; the proxy lives elsewhere
            stem = Path(file_path_str).stem
            stem_to_original[stem] = file_path_str

    result: dict[str, str] = {}
    for tp in transcript_paths:
        try:
            t = Transcript.load(tp)
        except Exception:
            continue
        transcript_src = t.source_path
        # The transcript's source_path may point at either the original
        # (rare — only if no proxy existed at transcription time) or the
        # proxy. Either way, match by stem.
        stem = Path(transcript_src).stem
        if stem in stem_to_original:
            result[transcript_src] = stem_to_original[stem]
        else:
            result[transcript_src] = transcript_src  # fallback
    return result


def _fixup_aroll_sources(
    cutlist,
    phrase_source_map: dict[int, str],
    source_offset_map: dict[str, float],
    source_to_original: dict[str, str],
) -> None:
    """Correct every ARollPhrase in-place so the XML references the right
    file with the right per-file timestamps.

    Three corrections:
      1. source_file → the ORIGINAL camera file (not proxy, not joined string)
      2. source_start / source_end → subtract the combined-transcript offset
         so values are relative to the original file's own 0:00 start
      3. Matcher may have mangled paths through Path normalization — restore.
    """
    from dataclasses import replace
    fixed: list = []
    for phrase in cutlist.aroll_track:
        transcript_src = phrase_source_map.get(phrase.phrase_id)
        if transcript_src is None:
            # Can't fix this one — leave as-is rather than silently corrupt
            fixed.append(phrase)
            continue
        offset = source_offset_map.get(transcript_src, 0.0)
        original = source_to_original.get(transcript_src, transcript_src)
        fixed.append(replace(
            phrase,
            source_file=original,
            source_start=max(0.0, phrase.source_start - offset),
            source_end=max(0.0, phrase.source_end - offset),
        ))
    cutlist.aroll_track = fixed


def _fixup_broll_sources(cutlist, project) -> None:
    """Walk each BRollShot and rewrite source_file to the user's ORIGINAL
    footage rather than the proxy that LanceDB indexed.

    B-roll proxies live at <source_folder>/proxies/<stem>.mp4. We already
    have multi_exporter._find_original_for_proxy — use it.
    """
    from dataclasses import replace
    from precut_pipeline.multi_exporter import _find_original_for_proxy
    fixed: list = []
    for shot in cutlist.broll_track:
        proxy_path = Path(shot.source_file)
        original = _find_original_for_proxy(proxy_path)
        if original is not None and str(original) != str(proxy_path):
            fixed.append(replace(shot, source_file=str(original)))
        else:
            fixed.append(shot)
    cutlist.broll_track = fixed


def _sanitize_sequence_name(name: str) -> str:
    """Premiere accepts almost any sequence name. We just cap length."""
    name = (name or "Sequence").strip()
    # Premiere shows names in bins — keep them digestible
    return name[:80] if len(name) > 80 else name


def _combine_transcripts(transcript_paths: list[Path]):
    """Re-uses the same merging logic as producer.py AND returns a
    phrase_id → source_file map so we can fix up ARollPhrase.source_file
    after matching (since the Matcher uses Transcript.source_path which
    in the combined case is a nonsense joined string).
    """
    from precut_pipeline.transcriber import Transcript, Phrase, Word

    all_phrases: list[Phrase] = []
    total_duration = 0.0
    combined_source = []
    language = "en"
    phrase_source_map: dict[int, str] = {}  # phrase_id -> real file path

    time_offset = 0.0
    for tp in transcript_paths:
        t = Transcript.load(tp)
        combined_source.append(str(t.source_path))
        if t.language:
            language = t.language
        for p in t.phrases:
            shifted_words = [
                Word(text=w.text, start=w.start + time_offset, end=w.end + time_offset)
                for w in p.words
            ]
            new_id = len(all_phrases)
            all_phrases.append(Phrase(
                id=new_id,
                start=p.start + time_offset,
                end=p.end + time_offset,
                text=p.text,
                words=shifted_words,
            ))
            phrase_source_map[new_id] = t.source_path
        time_offset += t.duration
        total_duration += t.duration

    combined = Transcript(
        source_path=" + ".join(combined_source),
        language=language,
        duration=total_duration,
        phrases=all_phrases,
    )
    return combined, phrase_source_map


def _cutlist_from_deliverable_no_broll(deliverable, transcript):
    """Build a CutList with only A-roll — no B-roll cutaways.

    Used when there's no B-roll index available (e.g. tagging skipped or Ollama
    was down). Still produces a valid XML importable into Premiere.

    The real Matcher's output has separate aroll_track + broll_track lists;
    we mirror that API here with an empty broll_track.
    """
    from precut_pipeline.cutlist import CutList, ARollPhrase
    from precut_pipeline.presets import get_preset

    preset = get_preset(deliverable.preset_key)
    phrase_by_id = {p.id: p for p in transcript.phrases}

    # Walk every phrase the plan references, in plan-segment order, and lay
    # them end-to-end on the timeline. Matches Matcher._build_aroll_track's
    # approach without the B-roll overlay logic.
    aroll_track: list[ARollPhrase] = []
    timeline_cursor = 0.0

    for seg_plan in deliverable.segments:
        for phrase_id in (seg_plan.phrase_ids or []):
            phrase = phrase_by_id.get(phrase_id)
            if phrase is None:
                continue
            phrase_duration = phrase.end - phrase.start
            if phrase_duration <= 0:
                continue
            aroll_track.append(ARollPhrase(
                phrase_id=phrase_id,
                source_file=transcript.source_path,
                source_start=phrase.start,
                source_end=phrase.end,
                timeline_start=timeline_cursor,
                timeline_end=timeline_cursor + phrase_duration,
                text=phrase.text,
            ))
            timeline_cursor += phrase_duration

    return CutList(
        deliverable_concept=deliverable.concept,
        deliverable_preset=deliverable.preset_key,
        total_duration=timeline_cursor,
        aroll_track=aroll_track,
        broll_track=[],
        sequence_width=preset.sequence_width,
        sequence_height=preset.sequence_height,
        sequence_fps=preset.sequence_fps,
        overlay_style=preset.overlay_style,
    )




def _build_all_aroll_sequences(
    project,
    audio_sync_state,
    src_w,
    src_h,
    emit,
):
    """Drop 4.17: build one "All Synced A-Roll" export request per A-roll file.

    Each returned request represents a full-length sequence of one A-roll
    original clip, with the clip on V1 and any covering lav audio on A2/A3+
    (handled by the existing _append_synced_audio_tracks downstream).

    Returns a list of ExportRequest. Empty if no A-roll originals found or
    none have a readable duration.
    """
    from precut_pipeline.multi_exporter import ExportRequest
    from precut_pipeline.cutlist import CutList, ARollPhrase

    aroll_sources = project.sources_by_kind("aroll")
    if not aroll_sources:
        return []

    # Gather unique A-roll original file paths
    aroll_files = []  # [(path, display_name)]
    seen = set()
    for src in aroll_sources:
        for file_path in src.files.keys():
            if file_path in seen:
                continue
            seen.add(file_path)
            aroll_files.append((file_path, Path(file_path).name))

    if not aroll_files:
        return []

    # For each file, probe its duration (prefer the transcript file's
    # duration if available — no subprocess needed). If we can't figure
    # out a duration, skip the file.
    transcript_duration_by_path: dict[str, float] = {}
    for tp in project.transcripts_dir().glob("*.json"):
        try:
            with open(tp) as f:
                data = json.load(f)
            sp = data.get("source_path")
            dur = float(data.get("duration") or 0.0)
            if sp and dur > 0:
                transcript_duration_by_path[sp] = dur
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            continue

    # Drop 4.18: build ONE combined sequence, not N per-file sequences.
    # All A-roll clips lay end-to-end on V1. Each file's covering lav
    # audio attaches at its own timeline position (not at source t=0),
    # because each phrase's timeline_start is its running-cursor offset.
    all_phrases = []
    timeline_cursor = 0.0
    combined_fps = None
    combined_w = None
    combined_h = None

    for file_path, display_name in sorted(aroll_files, key=lambda x: x[1]):
        # Drop 4.19: prefer ffprobe for ACTUAL file duration. Transcript
        # duration can be wrong (a transcript with one 1-second phrase
        # can report duration=1 even for a 10-minute file if the STT
        # stage bailed early or the phrase aggregator mis-summed).
        # For the full-A-roll reference we want to see the whole file.
        dims = _probe_video_full(Path(file_path))
        duration = (dims or {}).get("duration") or 0.0
        if duration <= 0:
            # Fall back to transcript duration if probe fails
            duration = transcript_duration_by_path.get(file_path) or 0.0
        if duration <= 0:
            emit({"type": "log", "level": "warn",
                  "message": f"Skipping {display_name} from All-A-Roll "
                             f"(couldn't determine duration)"})
            continue

        # Establish sequence dims from the FIRST file that probes cleanly
        if combined_w is None and dims:
            combined_w = int(dims.get("width") or src_w or 1920)
            combined_h = int(dims.get("height") or src_h or 1080)
            combined_fps = float(dims.get("fps") or 30.0)

        # phrase_id in 2_000_000+ range marks full-file reference phrases
        # (distinct from Whisper phrase IDs and story-topic range IDs).
        # Use running index so phrases sharing source_file don't collide.
        phrase_id = 2_000_000 + len(all_phrases)
        all_phrases.append(ARollPhrase(
            phrase_id=phrase_id,
            source_file=file_path,
            source_start=0.0,
            source_end=duration,
            timeline_start=timeline_cursor,
            timeline_end=timeline_cursor + duration,
            text=f"All A-roll: {display_name}",
        ))
        timeline_cursor += duration

    if not all_phrases:
        return []

    # Default dims if nothing probed (fallback)
    if combined_w is None:
        combined_w = src_w or 1920
        combined_h = src_h or 1080
        combined_fps = 30.0

    cutlist = CutList(
        deliverable_concept="All Synced A-Roll",
        deliverable_preset="aroll_native",
        total_duration=timeline_cursor,
        aroll_track=all_phrases,
        broll_track=[],
        sequence_width=combined_w,
        sequence_height=combined_h,
        sequence_fps=combined_fps,
        overlay_style="none",  # no safezone overlay on reference sequence
    )

    return [ExportRequest(
        cutlist=cutlist,
        sequence_name="All Synced A-Roll",
        audio_sync_state=audio_sync_state,
        source_width=combined_w,
        source_height=combined_h,
    )]


def _probe_video_full(path: Path):
    """Full probe returning width/height/fps/duration. None on failure."""
    try:
        from proxy_manager import find_ffmpeg
    except ImportError:
        return None
    ffmpeg_bin = find_ffmpeg()
    if not ffmpeg_bin:
        return None
    ffprobe_bin = ffmpeg_bin.replace("/ffmpeg", "/ffprobe")
    if not Path(ffprobe_bin).exists():
        return None
    import subprocess
    try:
        result = subprocess.run(
            [ffprobe_bin, "-v", "error",
             "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate:format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout or "{}")
        streams = data.get("streams") or []
        fmt = data.get("format") or {}
        if not streams:
            return None
        s = streams[0]
        fps = None
        rate_str = s.get("r_frame_rate", "")
        if "/" in rate_str:
            num, den = rate_str.split("/", 1)
            try:
                num_f, den_f = float(num), float(den)
                if den_f > 0:
                    fps = num_f / den_f
            except (TypeError, ValueError):
                pass
        dur = None
        if "duration" in fmt:
            try: dur = float(fmt["duration"])
            except (TypeError, ValueError): pass
        return {
            "width": int(s.get("width") or 0),
            "height": int(s.get("height") or 0),
            "fps": fps or 30.0,
            "duration": dur or 0.0,
        }
    except Exception:
        return None
