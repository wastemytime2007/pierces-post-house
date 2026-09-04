"""AI Producer wrapper for the backend.

Wraps precut_pipeline.planner.DeliverablePlanner with:
  - Event-streaming (emits start/progress/done events instead of returning)
  - Idea persistence (each idea gets a stable ID, saved to <project>/plans/)
  - Refinement loop — feed user notes back into the planner to revise an idea

An "idea" is a DeliverableConcept (from analyze mode) or a full Deliverable
(from directed mode). Both get stored as JSON in <project>/plans/ keyed by a
stable ID.
"""
from __future__ import annotations

import json
import time
import traceback
import uuid
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Callable, Optional


def _to_dict(obj) -> dict:
    """Convert a dataclass or dict to a JSON-serializable dict."""
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, dict):
        return obj
    raise TypeError(f"Can't convert {type(obj).__name__} to dict")


def _combine_transcripts(transcript_paths: list[Path]):
    """If the project has multiple A-roll transcripts, combine them.

    For now: concatenate phrases, assume the user picked related clips.
    Later we might want per-transcript concepts and/or speaker diarization.
    """
    from precut_pipeline.transcriber import Transcript, Phrase, Word

    all_phrases: list[Phrase] = []
    total_duration = 0.0
    combined_source = []
    language = "en"  # default if we can't find one

    time_offset = 0.0
    for tp in transcript_paths:
        t = Transcript.load(tp)
        combined_source.append(str(t.source_path))
        if t.language:
            language = t.language
        # Shift each phrase's timestamps by the cumulative offset. Skip
        # transcripts with no phrases (e.g. silent clips that Whisper found
        # no speech in) — they'd contribute nothing anyway.
        for p in t.phrases:
            shifted_words = [
                Word(text=w.text, start=w.start + time_offset, end=w.end + time_offset)
                for w in p.words
            ]
            shifted = Phrase(
                id=len(all_phrases),
                start=p.start + time_offset,
                end=p.end + time_offset,
                text=p.text,
                words=shifted_words,
            )
            all_phrases.append(shifted)
        time_offset += t.duration
        total_duration += t.duration

    combined = Transcript(
        source_path=" + ".join(combined_source),
        language=language,
        duration=total_duration,
        phrases=all_phrases,
    )
    return combined


def _import_planner():
    """Lazy-import the planner, return (PlannerClass, PlannerError) or raise
    a user-friendly error if anthropic isn't installed.

    This is the single place we handle the 'anthropic package missing' case,
    so callers can just try/except it and emit a clean error event.
    """
    try:
        from precut_pipeline.planner import DeliverablePlanner, PlannerError
        return DeliverablePlanner, PlannerError
    except ImportError as e:
        # The common one: `anthropic` isn't installed in the app's Python env
        msg = str(e)
        if "anthropic" in msg:
            raise RuntimeError(
                "The 'anthropic' Python package is not installed.\n"
                "Install it with: pip3 install anthropic\n"
                "(The AI producer requires this package.)"
            )
        raise RuntimeError(f"Failed to import planner: {e}")


# ---------------------------------------------------------------------------
# Analyze mode — pitch concepts
# ---------------------------------------------------------------------------

def run_analyze(
    project,
    job_id: str,
    emit: Callable[[dict], None],
    max_concepts: int = 5,
) -> None:
    """Run 'analyze and recommend' over all A-roll transcripts in a project.

    Emits:
        producer_started (mode=analyze)
        producer_concept (one per pitched concept)
        producer_done (with summary + total concepts)
        producer_error
    """
    # Gather transcript paths
    # Path.glob("*.json") matches macOS AppleDouble sidecars ("._<name>.json")
    # too, unlike shell glob — confirmed real on an external drive, 2026-09-04.
    transcript_paths = sorted(
        p for p in project.transcripts_dir().glob("*.json") if not p.name.startswith(".")
    )
    if not transcript_paths:
        emit({"type": "producer_error", "job_id": job_id,
              "message": "No transcripts found. Run the pipeline first."})
        return

    emit({
        "type": "producer_started",
        "job_id": job_id,
        "mode": "analyze",
        "transcript_count": len(transcript_paths),
    })

    try:
        DeliverablePlanner, PlannerError = _import_planner()
    except RuntimeError as e:
        emit({"type": "producer_error", "job_id": job_id, "message": str(e)})
        return

    try:
        combined = _combine_transcripts(transcript_paths)
    except Exception as e:
        emit({"type": "producer_error", "job_id": job_id,
              "message": f"Failed to load transcripts: {e}",
              "traceback": traceback.format_exc()})
        return

    try:
        planner = DeliverablePlanner()
    except PlannerError as e:
        emit({"type": "producer_error", "job_id": job_id,
              "message": str(e)})
        return

    try:
        report = planner.analyze_and_recommend(combined, max_concepts=max_concepts)
    except Exception as e:
        emit({"type": "producer_error", "job_id": job_id,
              "message": f"Claude call failed: {e}",
              "traceback": traceback.format_exc()})
        return

    # Persist each concept as an "idea" so we can refine/pick later
    plans_dir = project.plans_dir()
    plans_dir.mkdir(parents=True, exist_ok=True)

    for concept in report.concepts:
        idea_id = f"idea_{uuid.uuid4().hex[:10]}"
        idea_path = plans_dir / f"{idea_id}.json"
        payload = {
            "idea_id": idea_id,
            "kind": "concept",
            "created_at": time.time(),
            "refinement_history": [],
            "data": _to_dict(concept),
        }
        idea_path.write_text(json.dumps(payload, indent=2))

        emit({
            "type": "producer_idea",
            "job_id": job_id,
            "idea_id": idea_id,
            "kind": "concept",
            "concept": _to_dict(concept),
        })

    emit({
        "type": "producer_done",
        "job_id": job_id,
        "mode": "analyze",
        "summary": report.summary,
        "concept_count": len(report.concepts),
    })


# ---------------------------------------------------------------------------
# Directed mode — user provides a preset + brief
# ---------------------------------------------------------------------------

def run_directed_plan(
    project,
    job_id: str,
    preset_key: str,
    brief: str,
    topic_focus: str,
    emit: Callable[[dict], None],
) -> None:
    """Generate one full Deliverable for a specific preset.

    Emits:
        producer_started (mode=directed)
        producer_idea (one — the full plan)
        producer_done
        producer_error
    """
    # Path.glob("*.json") matches macOS AppleDouble sidecars ("._<name>.json")
    # too, unlike shell glob — confirmed real on an external drive, 2026-09-04.
    transcript_paths = sorted(
        p for p in project.transcripts_dir().glob("*.json") if not p.name.startswith(".")
    )
    if not transcript_paths:
        emit({"type": "producer_error", "job_id": job_id,
              "message": "No transcripts found. Run the pipeline first."})
        return

    emit({
        "type": "producer_started",
        "job_id": job_id,
        "mode": "directed",
        "preset_key": preset_key,
    })

    try:
        DeliverablePlanner, PlannerError = _import_planner()
    except RuntimeError as e:
        emit({"type": "producer_error", "job_id": job_id, "message": str(e)})
        return

    try:
        combined = _combine_transcripts(transcript_paths)
        planner = DeliverablePlanner()
        deliverable = planner.plan_deliverable(
            combined, preset_key=preset_key, brief=brief, topic_focus=topic_focus,
        )
    except PlannerError as e:
        emit({"type": "producer_error", "job_id": job_id, "message": str(e)})
        return
    except Exception as e:
        emit({"type": "producer_error", "job_id": job_id,
              "message": f"{type(e).__name__}: {e}",
              "traceback": traceback.format_exc()})
        return

    # Persist as a full plan (not just a concept)
    idea_id = f"plan_{uuid.uuid4().hex[:10]}"
    idea_path = project.plans_dir() / f"{idea_id}.json"
    payload = {
        "idea_id": idea_id,
        "kind": "deliverable",
        "created_at": time.time(),
        "refinement_history": [{"brief": brief, "topic_focus": topic_focus}],
        "data": _to_dict(deliverable),
    }
    idea_path.write_text(json.dumps(payload, indent=2))

    emit({
        "type": "producer_idea",
        "job_id": job_id,
        "idea_id": idea_id,
        "kind": "deliverable",
        "deliverable": _to_dict(deliverable),
    })
    emit({"type": "producer_done", "job_id": job_id, "mode": "directed"})


# ---------------------------------------------------------------------------
# Refinement — feed user notes into an existing idea
# ---------------------------------------------------------------------------

def run_refine(
    project,
    job_id: str,
    idea_id: str,
    user_notes: str,
    emit: Callable[[dict], None],
) -> None:
    """Take an existing idea and revise it based on user feedback.

    Loads the idea JSON, sends it back to Claude along with the notes,
    gets a revised version, persists over the same idea_id.
    """
    idea_path = project.plans_dir() / f"{idea_id}.json"
    if not idea_path.exists():
        emit({"type": "producer_error", "job_id": job_id,
              "message": f"Idea not found: {idea_id}"})
        return

    try:
        existing = json.loads(idea_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        emit({"type": "producer_error", "job_id": job_id,
              "message": f"Couldn't read idea {idea_id}: {e}"})
        return

    emit({
        "type": "producer_started",
        "job_id": job_id,
        "mode": "refine",
        "idea_id": idea_id,
    })

    try:
        DeliverablePlanner, PlannerError = _import_planner()
    except RuntimeError as e:
        emit({"type": "producer_error", "job_id": job_id, "message": str(e)})
        return

    # Path.glob("*.json") matches macOS AppleDouble sidecars ("._<name>.json")
    # too, unlike shell glob — confirmed real on an external drive, 2026-09-04.
    transcript_paths = sorted(
        p for p in project.transcripts_dir().glob("*.json") if not p.name.startswith(".")
    )
    if not transcript_paths:
        emit({"type": "producer_error", "job_id": job_id,
              "message": "No transcripts — can't refine without source material."})
        return

    try:
        combined = _combine_transcripts(transcript_paths)
        planner = DeliverablePlanner()
    except PlannerError as e:
        emit({"type": "producer_error", "job_id": job_id, "message": str(e)})
        return

    # Build a refinement brief that includes the original idea + user notes
    original_data = existing["data"]
    original_summary = (
        f"Original concept: {original_data.get('concept', original_data.get('title', '?'))}\n"
        f"Original pitch: {original_data.get('pitch', '')}\n"
    )
    refinement_brief = (
        f"You previously proposed this idea:\n\n{original_summary}\n\n"
        f"The user has the following feedback to refine it:\n\n{user_notes}\n\n"
        f"Revise your proposal to address this feedback while staying faithful "
        f"to the spirit of the original."
    )

    # If original was a concept, refine via analyze mode (returns another concept)
    # If original was a full deliverable, refine via directed mode
    try:
        if existing["kind"] == "concept":
            # Use directed plan mode with the original's preset + new brief
            preset_key = original_data.get("suggested_preset", "reel_30s")
            deliverable = planner.plan_deliverable(
                combined, preset_key=preset_key,
                brief=refinement_brief, topic_focus="",
            )
            # Upgrade idea from concept → deliverable
            existing["kind"] = "deliverable"
            existing["data"] = _to_dict(deliverable)
        else:
            preset_key = original_data.get("preset_key", "reel_30s")
            deliverable = planner.plan_deliverable(
                combined, preset_key=preset_key,
                brief=refinement_brief, topic_focus="",
            )
            existing["data"] = _to_dict(deliverable)
    except Exception as e:
        emit({"type": "producer_error", "job_id": job_id,
              "message": f"Refinement failed: {e}",
              "traceback": traceback.format_exc()})
        return

    # Append to history and persist
    existing.setdefault("refinement_history", []).append({
        "notes": user_notes,
        "at": time.time(),
    })
    idea_path.write_text(json.dumps(existing, indent=2))

    emit({
        "type": "producer_idea_refined",
        "job_id": job_id,
        "idea_id": idea_id,
        "kind": existing["kind"],
        "deliverable": existing["data"],
        "refinement_count": len(existing["refinement_history"]),
    })
    emit({"type": "producer_done", "job_id": job_id, "mode": "refine"})


# ---------------------------------------------------------------------------
# Misc — list ideas, delete
# ---------------------------------------------------------------------------

def list_ideas(project) -> list[dict]:
    """Return a summary of all ideas in the project."""
    plans_dir = project.plans_dir()
    if not plans_dir.exists():
        return []
    out = []
    for fp in sorted(plans_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            d = json.loads(fp.read_text())
            out.append({
                "idea_id": d["idea_id"],
                "kind": d["kind"],
                "created_at": d["created_at"],
                "refinement_count": len(d.get("refinement_history", [])),
                "data": d["data"],
                # Drop 4.0: legacy preset_key (mirrored to aspect_key in 4.4)
                "selected_preset_key": d.get("selected_preset_key", ""),
                # Drop 4.4: two-field selection
                "selected_platform_key": d.get("selected_platform_key", ""),
                "selected_aspect_key": d.get("selected_aspect_key", ""),
            })
        except (json.JSONDecodeError, KeyError, OSError):
            continue
    return out


def delete_idea(project, idea_id: str) -> bool:
    path = project.plans_dir() / f"{idea_id}.json"
    if path.exists():
        path.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# Drop 4.0: Story Angle mode
# ---------------------------------------------------------------------------
#
# These functions parallel the analyze/plan_directed/refine flow above but
# operate on StoryAngle (from story_planner.py) rather than DeliverableConcept.
# They persist to the same plans_dir with kind="story_angle".
#
# An angle's JSON payload stores the full StoryAngle data, plus a user-
# selected preset_key override (set via the Idea card's format dropdown).
# When the user exports, the assembler reads the angle, builds a CutList
# (phrases in source order + attached B-roll markers + Creative Brief),
# and hands off to the multi_exporter.


def _import_story_planner():
    """Import StoryAnglePlanner lazily so the backend doesn't require
    anthropic SDK on startup."""
    try:
        from precut_pipeline.story_planner import (
            StoryAnglePlanner, StoryPlannerError,
        )
    except ImportError as e:
        raise RuntimeError(
            "Couldn't import StoryAnglePlanner — is anthropic SDK installed? "
            f"({e})"
        ) from e
    return StoryAnglePlanner, StoryPlannerError


def run_generate_angles(
    project,
    job_id: str,
    emit: Callable[[dict], None],
    n_angles: int = 3,
    include_existing: bool = False,
) -> None:
    """Drop 4.0: generate story angles from the project's transcripts.

    If include_existing is True, existing persisted angles are fed back to
    the planner as context so it produces distinct NEW angles (this is the
    "Request More" button path).

    Emits:
        producer_started (mode=story_angles)
        producer_angle (one per angle produced)
        producer_done (with total count)
        producer_error
    """
    # Path.glob("*.json") matches macOS AppleDouble sidecars ("._<name>.json")
    # too, unlike shell glob — confirmed real on an external drive, 2026-09-04.
    transcript_paths = sorted(
        p for p in project.transcripts_dir().glob("*.json") if not p.name.startswith(".")
    )
    if not transcript_paths:
        emit({"type": "producer_error", "job_id": job_id,
              "message": "No transcripts found. Run the pipeline first."})
        return

    emit({
        "type": "producer_started",
        "job_id": job_id,
        "mode": "story_angles",
        "transcript_count": len(transcript_paths),
        "n_angles": n_angles,
        "include_existing": include_existing,
    })

    try:
        StoryAnglePlanner, StoryPlannerError = _import_story_planner()
    except RuntimeError as e:
        emit({"type": "producer_error", "job_id": job_id, "message": str(e)})
        return

    try:
        combined = _combine_transcripts(transcript_paths)
    except Exception as e:
        emit({"type": "producer_error", "job_id": job_id,
              "message": f"Failed to load transcripts: {e}",
              "traceback": traceback.format_exc()})
        return

    # Load existing angles if requested (for "Request More")
    existing_angles = []
    if include_existing:
        for item in list_ideas(project):
            if item.get("kind") == "story_angle":
                try:
                    existing_angles.append(_angle_from_dict(item["data"]))
                except Exception:
                    continue

    try:
        planner = StoryAnglePlanner()
    except StoryPlannerError as e:
        emit({"type": "producer_error", "job_id": job_id, "message": str(e)})
        return

    try:
        angles = planner.generate_angles(
            transcript=combined,
            n_angles=n_angles,
            existing_angles=existing_angles or None,
        )
    except Exception as e:
        emit({"type": "producer_error", "job_id": job_id,
              "message": f"Claude call failed: {e}",
              "traceback": traceback.format_exc()})
        return

    # Persist each angle
    plans_dir = project.plans_dir()
    plans_dir.mkdir(parents=True, exist_ok=True)

    for angle in angles:
        idea_id = f"idea_{uuid.uuid4().hex[:10]}"
        idea_path = plans_dir / f"{idea_id}.json"
        angle_dict = _to_dict(angle)
        payload = {
            "idea_id": idea_id,
            "kind": "story_angle",
            "created_at": time.time(),
            "refinement_history": [],
            # Drop 4.0: legacy preset_key slot, kept for back-compat
            "selected_preset_key": angle.suggested_preset,
            # Drop 4.4: two-field selection starts empty (A-roll native / no overlay)
            # until the user picks. set_angle_platform_and_aspect() overwrites.
            "selected_platform_key": "",
            "selected_aspect_key": "",
            "data": angle_dict,
        }
        idea_path.write_text(json.dumps(payload, indent=2))

        emit({
            "type": "producer_angle",
            "job_id": job_id,
            "idea_id": idea_id,
            "angle": angle_dict,
            "selected_preset_key": angle.suggested_preset,
            "selected_platform_key": "",
            "selected_aspect_key": "",
        })

    emit({
        "type": "producer_done",
        "job_id": job_id,
        "mode": "story_angles",
        "angle_count": len(angles),
    })


def set_angle_preset(project, idea_id: str, preset_key: str) -> bool:
    """Update the selected preset for an angle. Called from the UI when the
    user changes the format dropdown on an Idea card.

    Drop 4.4: legacy — the preset_key field still exists for backwards
    compat with Drop 4.3 angles. The newer set_angle_platform_and_aspect
    is preferred.
    """
    idea_path = project.plans_dir() / f"{idea_id}.json"
    if not idea_path.exists():
        return False
    try:
        payload = json.loads(idea_path.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    if payload.get("kind") != "story_angle":
        return False
    payload["selected_preset_key"] = preset_key
    idea_path.write_text(json.dumps(payload, indent=2))
    return True


def set_angle_platform_and_aspect(
    project, idea_id: str,
    platform_key: str = "",
    aspect_key: str = "",
) -> bool:
    """Drop 4.4: update the selected platform + aspect for a story angle.

    Either can be empty string (meaning 'not chosen'). Empty for BOTH means
    "A-roll native dims, no overlay" — the bare-minimum default.
    """
    idea_path = project.plans_dir() / f"{idea_id}.json"
    if not idea_path.exists():
        return False
    try:
        payload = json.loads(idea_path.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    if payload.get("kind") != "story_angle":
        return False
    payload["selected_platform_key"] = platform_key or ""
    payload["selected_aspect_key"] = aspect_key or ""
    # Keep legacy field in sync: if aspect was picked, mirror it into the
    # legacy preset slot so Drop 4.3-savvy code paths still work.
    if aspect_key:
        payload["selected_preset_key"] = aspect_key
    idea_path.write_text(json.dumps(payload, indent=2))
    return True


def _angle_from_dict(d: dict):
    """Reconstruct a StoryAngle from its persisted dict form.

    Drop 4.1: now also deserializes source_ranges. Drop 4.0 angles loaded
    from disk won't have this field; the assembler reconstructs ranges
    from phrase_ids in that case.
    """
    from precut_pipeline.cutlist import StoryAngle, CreativeBrief, TopicRange
    brief_d = d.get("brief", {}) or {}
    brief = CreativeBrief(
        title=brief_d.get("title", ""),
        hook=brief_d.get("hook", ""),
        why_it_works=brief_d.get("why_it_works", ""),
        tone=brief_d.get("tone", ""),
        target_duration_sec=float(brief_d.get("target_duration_sec", 0.0) or 0.0),
        source_phrase_ids=list(brief_d.get("source_phrase_ids", [])),
        target_audience=brief_d.get("target_audience", ""),
        call_to_action=brief_d.get("call_to_action", ""),
    )
    def _parse_ranges(raw_list):
        out = []
        for rr in raw_list or []:
            try:
                out.append(TopicRange(
                    source_file=str(rr.get("source_file", "")),
                    source_start_sec=float(rr.get("source_start_sec", 0.0)),
                    source_end_sec=float(rr.get("source_end_sec", 0.0)),
                    topic_label=str(rr.get("topic_label", "")),
                    summary=str(rr.get("summary", "")),
                ))
            except (TypeError, ValueError):
                continue
        return out

    source_ranges = _parse_ranges(d.get("source_ranges"))
    # 2026-09-04: posthouse's story_architect-only field — see
    # cutlist.StoryAngle.pool_ranges docstring. Empty for any angle that
    # doesn't set it (e.g. PreCut's own generate_angles output).
    pool_ranges = _parse_ranges(d.get("pool_ranges"))
    return StoryAngle(
        angle_id=d.get("angle_id", ""),
        brief=brief,
        source_ranges=source_ranges,
        pool_ranges=pool_ranges,
        phrase_ids=list(d.get("phrase_ids", [])),
        suggested_preset=d.get("suggested_preset", ""),
        selected_platform_key=d.get("selected_platform_key", "") or "",
        selected_aspect_key=d.get("selected_aspect_key", "") or "",
        phrase_previews=list(d.get("phrase_previews", [])),
    )


def load_angle_from_project(project, idea_id: str):
    """Load a persisted story angle + its selected preset. Returns
    (StoryAngle, preset_key) or (None, None).

    Drop 4.4: also hydrates selected_platform_key/selected_aspect_key from
    the persistence envelope onto the returned angle. The orchestrator
    reads those off the angle, not from the envelope.
    """
    idea_path = project.plans_dir() / f"{idea_id}.json"
    if not idea_path.exists():
        return None, None
    try:
        payload = json.loads(idea_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None, None
    if payload.get("kind") != "story_angle":
        return None, None
    try:
        angle = _angle_from_dict(payload.get("data") or {})
    except Exception:
        return None, None
    # Drop 4.4: platform + aspect live at envelope level (not inside data)
    # since they're user selections, not part of the LLM's output. Hydrate
    # them onto the angle here so downstream code sees one unified object.
    #
    # dataclasses.replace copies with overrides — safe even though StoryAngle
    # is currently a regular dataclass.
    from dataclasses import replace
    angle = replace(
        angle,
        selected_platform_key=payload.get("selected_platform_key", "") or "",
        selected_aspect_key=payload.get("selected_aspect_key", "") or "",
    )
    preset_key = payload.get("selected_preset_key") or angle.suggested_preset
    return angle, preset_key

