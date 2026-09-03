"""Multi-timeline FCP7 XML exporter.

Wraps the single-sequence FCPXMLWriter to produce one XML file containing:
  - A top-level bin ("B-Roll Library") with every B-roll clip imported,
    each with <comments> (comma-joined tags) and <description>
    (LLaVA natural-language). These are searchable via Cmd+F in Premiere.
  - One <sequence> per selected idea, each with:
      * A-roll video + audio on V1/A1
      * B-roll cutaways on V2 (offset by matcher)
      * Clean mic audio on A2 (un-synced for now — audalign in audio_sync.py
        aligns it when its confidence is high enough)
      * Safe-zone overlay PNG on V3 for vertical formats

The existing single-sequence exporter in exporter.py is reused for the
per-sequence work. This module orchestrates it + adds the library bin.
"""
from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from xml.dom import minidom

from .cutlist import CutList
from .exporter import (
    FCPXMLWriter, detect_frame_rate, path_to_url,
    ASSUMED_SOURCE_WIDTH, ASSUMED_SOURCE_HEIGHT,
)
from .bin_builders import (
    make_bin, bin_children,
    build_aroll_master_clip, build_audio_master_clip, build_image_master_clip,
    _build_full_video_file, _build_full_audio_file, _build_full_image_file,
    _build_logginginfo_with_tags,
    get_placeholder_png_path,
    _append_text as _bb_append_text,
)


# ---------------------------------------------------------------------------
# B-roll library entry — one per clip in the index
# ---------------------------------------------------------------------------

@dataclass
class BrollLibraryEntry:
    """One clip as imported into the Premiere B-Roll Library bin.

    Metadata fields populate Premiere's searchable columns:
      - comments → "Comment" column (searchable Cmd+F)
      - description → "Description" column (searchable Cmd+F)
    """
    source_path: str          # absolute path to the PROXY file
    original_path: str        # absolute path to the original (for proxy re-link)
    display_name: str         # what appears in the bin
    comments: str             # comma-joined CLIP tags
    description: str          # LLaVA natural-language description
    duration_sec: float       # clip duration
    width: int = 1920
    height: int = 1080
    fps: float = 30.0
    # When known from direct ffprobe nb_frames, use this EXACT frame count
    # in the XML rather than duration_sec × fps (which can round up past
    # the file's actual length and cause Premiere to silently drop clips).
    frame_count: Optional[int] = None
    # Drop 4.37: real audio specs from ffprobe. When None, the file has
    # no audio and the exporter omits the <audio> block entirely.
    # When present, emit the exact {samplerate, channels, depth} that
    # ffprobe reported — not hardcoded 48000/2/16.
    audio_samplerate: Optional[int] = None
    audio_channels: Optional[int] = None
    audio_depth: Optional[int] = None
    has_audio: bool = False


def load_broll_library(broll_index_db: Path) -> list[BrollLibraryEntry]:
    """Read the project's B-roll SQLite DB and return library entries.

    Actual schema (verified in database.py):
      - clips: id, path, filename, duration_sec, width, height, fps, ...
      - frames: id, clip_id (FK), timestamp_sec, frame_path, tags (JSON), tags_text

    Tags live as JSON arrays inside each frames row. We aggregate unique
    tags across all frames of a clip into the comments field and a shorter
    subset into description (both searchable via Cmd+F in Premiere).
    """
    import json as _json

    if not broll_index_db.exists():
        return []

    entries: list[BrollLibraryEntry] = []
    conn = sqlite3.connect(str(broll_index_db))
    conn.row_factory = sqlite3.Row
    try:
        clips = conn.execute(
            "SELECT id, path, filename, duration_sec, width, height, fps, "
            "motion_tags, original_path "
            "FROM clips ORDER BY path"
        ).fetchall()

        for clip in clips:
            clip_id = clip["id"]
            clip_path = clip["path"]

            frame_rows = conn.execute(
                "SELECT tags, tags_text FROM frames "
                "WHERE clip_id = ? ORDER BY timestamp_sec",
                (clip_id,),
            ).fetchall()

            seen: set[str] = set()
            ordered_tags: list[str] = []

            # Drop 4.44: compute the authoritative camera-derived tag set
            # FIRST. We need it both for tagging (to add to the library
            # entry) and for repair (to strip obsolete camera tags that
            # older ingests may have written to frame tag lists).
            #
            # Order-of-operations bug fix: pre-4.44 code read
            # original_from_db BEFORE it was assigned, so the camera
            # heuristic ran on either the previous clip's path OR the
            # proxy path. That caused projects that were reorganized
            # after ingest to get wrong camera tags (e.g. Osmo timelapses
            # tagged "drone/aerial" because the proxy path and/or a
            # previous clip's drone path leaked in).
            original_from_db = None
            try:
                original_from_db = clip["original_path"]
            except (IndexError, KeyError):
                pass

            # Drop 3.8: motion/framing tags go FIRST so they're visible in
            # the first N tags shown in Premiere's description column.
            try:
                raw_motion = clip["motion_tags"]
            except (IndexError, KeyError):
                raw_motion = None
            if raw_motion:
                try:
                    motion_list = _json.loads(raw_motion)
                    if isinstance(motion_list, list):
                        for t in motion_list:
                            ts = str(t).strip()
                            if ts and ts not in seen:
                                seen.add(ts)
                                ordered_tags.append(ts)
                except (_json.JSONDecodeError, TypeError):
                    pass

            # Drop 4.38 + 4.44: camera / source-type tags inferred from
            # the original file path. We compute this once and reuse the
            # result both as the source-of-truth camera tag set (to add)
            # and as the whitelist for a repair pass below (to strip
            # stale tags from older ingests).
            #
            # Prefer the DB-stored original_path; fall back to the proxy
            # path only if we have nothing better. This matters: the
            # proxy lives in "proxies/" and its path never contains
            # organizational folder names like "Osmo Timelapse".
            current_camera_tags: set[str] = set()
            try:
                from precut_pipeline.camera_inference import infer_camera_tags
                ref_path = original_from_db if original_from_db else clip_path
                fresh_cam_tags = infer_camera_tags(Path(ref_path))
                current_camera_tags = set(fresh_cam_tags)
                for t in fresh_cam_tags:
                    if t not in seen:
                        seen.add(t)
                        ordered_tags.append(t)
            except Exception:
                pass

            # Drop 4.44: the universe of tags that camera_inference COULD
            # have produced in older code. Any of these that aren't in
            # the current fresh inference are stale and must be stripped.
            # Otherwise a clip that was ingested under an old (broken)
            # path but has since been reorganized would keep its wrong
            # camera tags forever.
            _CAMERA_TAG_UNIVERSE = {
                "drone", "aerial", "mavic", "avata", "fpv", "phantom",
                "inspire", "osmo", "gimbal", "pocket", "action_cam",
                "gopro", "ronin", "cinema", "sony", "canon", "timelapse",
            }
            stale_camera_tags = _CAMERA_TAG_UNIVERSE - current_camera_tags

            for row in frame_rows:
                tags_json = row["tags"]
                tags_text = row["tags_text"]
                candidates: list[str] = []
                if tags_json:
                    try:
                        parsed = _json.loads(tags_json)
                        if isinstance(parsed, list):
                            candidates = [str(t).strip() for t in parsed]
                    except (_json.JSONDecodeError, TypeError):
                        pass
                if not candidates and tags_text:
                    candidates = [t.strip() for t in str(tags_text).split(",")]
                for t in candidates:
                    if not t or t in seen:
                        continue
                    # Repair pass: skip tags from the camera-inference
                    # universe that the current fresh inference didn't
                    # produce. This strips wrongly-persisted drone/aerial
                    # on clips that were moved into an Osmo folder after
                    # ingest, etc.
                    if t.lower() in stale_camera_tags:
                        continue
                    seen.add(t)
                    ordered_tags.append(t)

            comments = ", ".join(ordered_tags) if ordered_tags else ""
            description = ", ".join(ordered_tags[:12]) if ordered_tags else ""

            # Drop 4.28: prefer the original_path stored in the DB (set by
            # the tagger when it registered the clip). Falls back to the
            # path-reconstruction heuristic for pre-4.28 databases where
            # that column is NULL. Drop 4.44: `original_from_db` is
            # already assigned at the top of this iteration — no need
            # to re-read it here.

            if original_from_db:
                original_path = Path(original_from_db)
                # Drop 4.36: DB-stored paths can still have wrong case if the
                # filesystem has been renamed since ingest, OR if the ingest
                # scan itself picked up case-mismatched paths. Run the same
                # case-correction we apply to resolver output. Without this,
                # a DB path of "DJI_0001.MP4" that's actually "DJI_0001.mp4"
                # on disk will be emitted as-is, Premiere sees case mismatch
                # when linking, marks the clip offline.
                if original_path.exists():
                    corrected = _exact_case(original_path)
                    if str(corrected) != str(original_path):
                        import sys as _sys
                        print(f"CASE-FIX: {original_path.name!r} -> {corrected.name!r} "
                              f"(parent {original_path.parent.name!r} -> {corrected.parent.name!r})",
                              file=_sys.stderr)
                    original_path = corrected
            else:
                original_path = _find_original_for_proxy(Path(clip_path))

            # Drop 3.6.6: re-probe the actual file we're going to reference
            # to catch stale DB values. Premiere opens the referenced file
            # at import and validates its duration matches — if we report
            # a wrong duration (because DB has cached a previous version or
            # probed the proxy instead of original), the clip gets silently
            # dropped with no error. Falls back to DB if probe fails.
            probe_path = Path(original_path) if original_path else Path(clip_path)
            effective_duration = clip["duration_sec"] or 0.0
            effective_width = clip["width"] or 1920
            effective_height = clip["height"] or 1080
            effective_fps = clip["fps"] or 30.0
            effective_frame_count: Optional[int] = None
            # Drop 4.37: audio info
            audio_sr: Optional[int] = None
            audio_ch: Optional[int] = None
            audio_depth: Optional[int] = None
            has_audio = False
            if probe_path.exists():
                live_info = _safe_probe(probe_path)
                if live_info is None:
                    # Probe failed → DB values (which are from proxy ingest!)
                    # will be emitted. These WILL produce offline clips.
                    # Log loudly so the user sees why.
                    import sys as _sys
                    print(
                        f"PROBE-FALLBACK: {probe_path.name:<50} "
                        f"probe failed, using proxy-DB values "
                        f"dur={effective_duration:.2f}s {effective_width}x{effective_height}@{effective_fps:.2f}fps. "
                        f"This file will likely be offline in Premiere. "
                        f"Fix: install ffprobe (brew install ffmpeg).",
                        file=_sys.stderr,
                    )
                if live_info is not None:
                    # Drop 4.34: when we have a live probe of the original file,
                    # its values are authoritative. Previously we only replaced
                    # DB values if they disagreed by > 0.5 sec, which leaked
                    # proxy-ingest values (proxy fps 30, proxy dims 1024x540)
                    # into the XML for original files that have different
                    # metadata. Always use live values when present.
                    if live_info.get("duration") and live_info["duration"] > 0:
                        effective_duration = live_info["duration"]
                    if live_info.get("width"):
                        effective_width = live_info["width"]
                    if live_info.get("height"):
                        effective_height = live_info["height"]
                    if live_info.get("fps") and live_info["fps"] > 0:
                        effective_fps = live_info["fps"]
                    # nb_frames is the exact frame count — prefer it over
                    # duration × fps calculation. For slo-mo, nb_frames is
                    # the capture frame count (correct) while duration may
                    # be playback-stretched.
                    if live_info.get("nb_frames"):
                        effective_frame_count = live_info["nb_frames"]

                    # Drop 4.37: capture audio stream info from the probe.
                    # Emitting hardcoded 48000/2/16 when the file actually has
                    # different audio specs can cause Premiere to mark the
                    # clip offline (the audio-side validation fails).
                    audio_block = live_info.get("audio")
                    if audio_block:
                        has_audio = True
                        audio_sr = audio_block.get("samplerate")
                        audio_ch = audio_block.get("channels")
                        audio_depth = audio_block.get("depth")
                    else:
                        has_audio = False

                    # DJI quirk: Mavic 2 and some other DJI cameras encode
                    # 1080p content at 1088-pixel block boundaries (1088 =
                    # 1080 + 8 for MPEG-4 macroblock alignment). The file
                    # container reports 1088 but Premiere's probe shows
                    # the active 1080 visible area. Snap back to 1080
                    # (and 2176→2160 for 4K variants).
                    if effective_height == 1088:
                        effective_height = 1080
                    if effective_height == 2176:
                        effective_height = 2160

                    # Diagnostic: log what we probed so offline issues can
                    # be traced back to concrete values.
                    import sys as _sys
                    a = live_info.get("audio") or {}
                    audio_summary = (
                        f"audio={a.get('samplerate')}/{a.get('channels')}ch/{a.get('depth')}bit"
                        if a else "audio=none"
                    )
                    print(
                        f"PROBE: {probe_path.name:<50} "
                        f"dur={live_info['duration']:.3f}s "
                        f"dims={live_info.get('width')}x{live_info.get('height')} "
                        f"fps={live_info.get('fps'):.4f} "
                        f"nb_frames={live_info.get('nb_frames')} "
                        f"{audio_summary}",
                        file=_sys.stderr,
                    )
            elif original_path:
                # Diagnostic: the original path we computed doesn't exist
                # on disk. Premiere will mark the clip offline. Log it so
                # we can see which subdirectory pattern failed.
                import sys as _sys
                print(
                    f"PROBE-MISS: {probe_path.name:<50} "
                    f"path_exists=False  resolved={probe_path}",
                    file=_sys.stderr,
                )

            # Display name uses the ORIGINAL filename so it matches the
            # pathurl. Premiere confusion from .mp4-name-on-.mov-file was
            # one cause of clip mis-identification/rejection.
            if original_path:
                display_name = original_path.name
            else:
                display_name = clip["filename"] or Path(clip_path).stem

            entries.append(BrollLibraryEntry(
                source_path=clip_path,
                original_path=str(original_path) if original_path else clip_path,
                display_name=display_name,
                comments=comments,
                description=description,
                duration_sec=effective_duration,
                width=effective_width,
                height=effective_height,
                fps=effective_fps,
                frame_count=effective_frame_count,
                audio_samplerate=audio_sr,
                audio_channels=audio_ch,
                audio_depth=audio_depth,
                has_audio=has_audio,
            ))
    finally:
        conn.close()

    return entries


def _safe_probe(path: Path) -> Optional[dict]:
    """Probe a video file for duration/dims/fps. Returns None on any failure.

    Drop 4.35: find ffprobe at common install locations when not on PATH.
    GUI-launched macOS apps don't inherit shell PATH, so ffprobe may not
    be reachable via `shutil.which` even when it's installed. Also logs
    every failure to stderr so Pierce can see WHY specific files fall
    back to DB values (which for proxies produces wrong XML metadata).
    """
    import sys as _sys

    # Try ffmpeg-python first if it's importable
    try:
        import ffmpeg as _ffmpeg  # type: ignore
        probe = _ffmpeg.probe(str(path))
    except Exception as e:
        probe = None
        # Fall through to direct ffprobe below
        last_err = f"ffmpeg-python: {type(e).__name__}: {e}"
    else:
        last_err = None

    if probe is None:
        # Find ffprobe the way proxy_manager finds ffmpeg
        import shutil as _shutil, os as _os
        ffprobe_bin = _shutil.which("ffprobe")
        if ffprobe_bin is None:
            for candidate in ("/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe",
                              "/opt/local/bin/ffprobe", "/usr/bin/ffprobe"):
                if _os.path.isfile(candidate) and _os.access(candidate, _os.X_OK):
                    ffprobe_bin = candidate
                    break
        if ffprobe_bin is None:
            print(f"PROBE-ERR: {path.name} — ffprobe not found. "
                  f"Tried PATH + /opt/homebrew, /usr/local, /opt/local, /usr",
                  file=_sys.stderr)
            return None
        try:
            import subprocess as _sp, json as _json
            result = _sp.run(
                [ffprobe_bin, "-v", "error", "-show_format", "-show_streams",
                 "-of", "json", str(path)],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                print(f"PROBE-ERR: {path.name} — ffprobe rc={result.returncode} "
                      f"stderr={result.stderr.strip()[:200]}",
                      file=_sys.stderr)
                return None
            probe = _json.loads(result.stdout)
        except Exception as e:
            print(f"PROBE-ERR: {path.name} — ffprobe exec failed: "
                  f"{type(e).__name__}: {e}",
                  file=_sys.stderr)
            return None

    fmt = probe.get("format", {}) or {}
    streams = probe.get("streams", []) or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        print(f"PROBE-ERR: {path.name} — no video stream found in probe output",
              file=_sys.stderr)
        return None

    dur = None
    if "duration" in fmt:
        try: dur = float(fmt["duration"])
        except (TypeError, ValueError): pass
    if dur is None and "duration" in video:
        try: dur = float(video["duration"])
        except (TypeError, ValueError): pass
    if dur is None or dur <= 0:
        return None

    # nb_frames from the video stream is authoritative for frame count,
    # but computing fps as nb_frames/duration breaks on slo-mo footage
    # (duration is playback-stretched, nb_frames is raw capture count).
    # So we use r_frame_rate for fps detection and nb_frames only for
    # the duration field.
    nb_frames = None
    try:
        nb_frames = int(video.get("nb_frames") or 0) or None
    except (TypeError, ValueError):
        pass

    # fps: r_frame_rate is the stream's native timebase — for cameras this
    # is what Premiere reads. For slo-mo files it's the CAPTURE rate (e.g.
    # 120 or 240), not the stretched playback rate.
    fps = None
    rate_str = video.get("r_frame_rate", "")
    if "/" in rate_str:
        try:
            num, den = rate_str.split("/")
            d = float(den)
            if d > 0:
                fps = float(num) / d
        except Exception:
            pass

    # Drop 4.40: ALSO read avg_frame_rate. DJI drone footage is often
    # variable-framerate (VFR) — the camera tags the file with a nominal
    # 60/120/240 r_frame_rate while the actual average is much lower
    # (~27fps typical for Mavic 2 MP4). Premiere's own probe computes
    # avg_frame_rate for the bin "fps" column, not r_frame_rate — so if
    # we declare 120fps in our XML but Premiere sees 27fps on playback,
    # Premiere shows 27fps and the clip plays at wrong speed. Prefer
    # avg_frame_rate when it differs significantly from r_frame_rate,
    # since that's what Premiere will adopt anyway.
    avg_fps = None
    avg_str = video.get("avg_frame_rate", "")
    if "/" in avg_str:
        try:
            num, den = avg_str.split("/")
            d = float(den)
            if d > 0:
                avg_fps = float(num) / d
        except Exception:
            pass

    if fps is None or fps <= 0:
        fps = avg_fps

    # If both present and they differ by more than ~5%, this is VFR —
    # trust avg_fps since that's what Premiere reports in its bin panel.
    if fps and avg_fps and avg_fps > 0:
        ratio = max(fps, avg_fps) / min(fps, avg_fps)
        if ratio > 1.05:
            fps = avg_fps

    # Drop 4.34: derive the MEDIA duration from nb_frames/fps when both are
    # known. Container-level duration for DJI slo-mo reports the stretched
    # PLAYBACK time (e.g. 256s for a 64s capture at 120fps rendered on a
    # 30fps timeline), while Premiere uses the true media duration
    # (nb_frames / capture_fps). If we declare the stretched duration
    # Premiere flags the clip offline when our value doesn't match its
    # own probe.
    if nb_frames and fps and fps > 0:
        derived = nb_frames / fps
        # Trust derived duration when it differs substantially from container
        # duration — this specifically catches slo-mo stretching. Within 0.5s
        # of container duration means they agree and we can use either.
        if abs(derived - dur) > 0.5:
            dur = derived

    # Drop 4.37: also read audio stream info. Previously the exporter
    # always declared <depth>16</depth><samplerate>48000</samplerate>
    # <channelcount>2</channelcount> for every file — but some cameras
    # record different audio specs (or no audio at all). When our
    # declared audio doesn't match the real file, Premiere can mark the
    # clip offline because the audio side fails validation even when
    # video is fine. Probe for real audio info and emit matching
    # characteristics (or omit the audio block if the file has no audio).
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    audio_info = None
    if audio_stream is not None:
        try:
            # samplerate from stream
            sr = int(audio_stream.get("sample_rate") or 0) or None
            # channels
            ch = int(audio_stream.get("channels") or 0) or None
            # bit depth — ffprobe gives "bits_per_raw_sample" (int) or
            # "sample_fmt" (string like "s16", "fltp", "s32p")
            depth = None
            brs = audio_stream.get("bits_per_raw_sample")
            if brs and str(brs).isdigit():
                depth = int(brs)
            if depth is None:
                sfmt = audio_stream.get("sample_fmt", "")
                if sfmt.startswith("s16"): depth = 16
                elif sfmt.startswith("s24"): depth = 24
                elif sfmt.startswith("s32") or sfmt.startswith("flt"): depth = 32
                elif sfmt.startswith("u8"): depth = 8
            audio_info = {
                "samplerate": sr,
                "channels": ch,
                "depth": depth,
            }
        except Exception:
            audio_info = None

    return {
        "duration": dur,
        "width": int(video.get("width") or 0) or None,
        "height": int(video.get("height") or 0) or None,
        "fps": fps,
        "nb_frames": nb_frames,
        "audio": audio_info,  # None = file has no audio stream
    }

def _find_original_for_proxy(proxy_path: Path) -> Optional[Path]:
    """Reverse-engineer a proxy path back to its original source file.

    Two proxy layouts exist in the wild:

    1. CURRENT (pipeline.py _compute_proxy_path):
         /src_root/proxies/<rel subdirs>/<clip>.mp4
       where /src_root/<rel subdirs>/<clip>.<orig-ext> is the original.
       'proxies' may be any ancestor, not just the immediate parent —
       files can be nested arbitrarily deep under proxies/.

    2. ALTERNATIVE (proxy_manager.py as an output_dir):
         /src_root/PreCut_Output/<kind>/<rel subdirs>/<clip>.mp4
       where /src_root/<rel subdirs>/<clip>.<orig-ext> is the original.

    Uses case-insensitive matching by directory listing (macOS and Windows
    are case-insensitive; reconstructed paths may have wrong case and
    Premiere treats different-case references to the same file as
    different clips).

    If nothing resolves, returns the BEST-GUESS constructed path so the
    XML still references where the original *ought* to be — Premiere
    will flag it as offline and prompt the user to locate it.
    """
    accepted_exts = {".mov", ".mp4", ".m4v", ".mxf"}
    target_stem_lower = proxy_path.stem.lower()

    def _scan(dir_path: Path, skip_names=("proxies", "PreCut_Output")) -> Optional[Path]:
        """Recursive case-insensitive stem match in dir_path."""
        if not dir_path.is_dir():
            return None
        # Flat scan first
        try:
            entries = list(dir_path.iterdir())
        except (OSError, PermissionError):
            return None
        for entry in entries:
            if entry.is_file() and entry.stem.lower() == target_stem_lower \
                    and entry.suffix.lower() in accepted_exts:
                return entry
        # Recurse into subdirs, skipping proxy-output trees so we don't
        # match the proxy file itself.
        for entry in entries:
            if not entry.is_dir():
                continue
            if entry.name in skip_names:
                continue
            hit = _scan(entry, skip_names)
            if hit is not None:
                return hit
        return None

    # Walk upward looking for a recognized marker directory. We want to
    # find the LOWEST ancestor named 'proxies' or 'PreCut_Output' —
    # the dir ABOVE that marker is the source root.
    markers = {"proxies", "PreCut_Output"}
    cur = proxy_path.parent
    marker_hit: Optional[Path] = None
    marker_parent: Optional[Path] = None
    for _ in range(20):  # bounded walk
        if cur.name in markers:
            marker_hit = cur
            marker_parent = cur.parent
            break
        if cur.parent == cur:
            break
        cur = cur.parent

    if marker_hit is not None and marker_parent is not None:
        # Try exact reconstructed path first (preserves subdir layout).
        # For 'proxies' layout: source_root/<rel-from-proxies>/<clip>.<orig-ext>
        # For 'PreCut_Output' layout: source_root/<rel-from-kind>/<clip>.<orig-ext>
        try:
            rel = proxy_path.relative_to(marker_hit)
            # For PreCut_Output, the first segment is <kind> (e.g. 'broll')
            # which isn't part of the source tree; drop it.
            if marker_hit.name == "PreCut_Output" and len(rel.parts) >= 2:
                rel = Path(*rel.parts[1:])
            # Try each accepted extension at that exact relative location
            for ext in (".MOV", ".mov", ".MP4", ".mp4", ".m4v", ".mxf"):
                candidate = marker_parent / rel.with_suffix(ext)
                if candidate.exists():
                    # Drop 4.33: on case-insensitive macOS, .exists() matches
                    # any case variant but returns a Path with whatever case
                    # we constructed. Premiere's media linker compares case
                    # canonically, so if we emit 'DJI_0001.MP4' but the real
                    # file is 'DJI_0001.mp4', Premiere marks it offline. Walk
                    # into the parent dir and recover the true on-disk case
                    # for both filename AND each ancestor directory.
                    return _exact_case(candidate)
            # If the exact path doesn't exist, fall through to recursive scan
        except ValueError:
            pass

        # Recursive scan of the source root as fallback
        hit = _scan(marker_parent)
        if hit is not None:
            return hit

        # Last-resort best-guess — flag as missing media in Premiere
        try:
            rel = proxy_path.relative_to(marker_hit)
            if marker_hit.name == "PreCut_Output" and len(rel.parts) >= 2:
                rel = Path(*rel.parts[1:])
            return marker_parent / rel.with_suffix(".mov")
        except ValueError:
            return marker_parent / f"{proxy_path.stem}.mov"

    # Neither layout matched — return the proxy itself unchanged.
    # (This is an ingest pattern we don't recognize — better to leave
    # it alone than silently misdirect.)
    return proxy_path


def _exact_case(path: Path) -> Path:
    """On case-insensitive filesystems (macOS HFS+/APFS default, Windows),
    `candidate.exists()` matches 'DJI_0001.mp4' even if we constructed
    'DJI_0001.MP4'. The returned Path object keeps our (possibly wrong)
    case. Premiere compares filenames canonically when linking, so
    'DJI_0001.MP4' in the XML vs 'DJI_0001.mp4' on disk = offline.

    This walks from the top down through each path component, replacing
    each with the exact case as it appears in its parent directory.
    Returns a new Path with exactly the case the filesystem reports.

    Returns the original path unchanged if any component can't be found
    (shouldn't happen since caller already verified .exists(), but
    defensive against race conditions).
    """
    if not path.is_absolute():
        return path
    parts = path.parts
    if not parts:
        return path
    # Start from the anchor (root / drive letter)
    rebuilt = Path(parts[0])
    for part in parts[1:]:
        target_lower = part.lower()
        found = None
        try:
            for entry in rebuilt.iterdir():
                if entry.name.lower() == target_lower:
                    found = entry.name
                    break
        except (OSError, PermissionError):
            return path  # Give up on errors; caller will still be close
        if found is None:
            return path  # Component missing; fall back to original
        rebuilt = rebuilt / found
    return rebuilt


# ---------------------------------------------------------------------------
# Multi-sequence export
# ---------------------------------------------------------------------------

@dataclass
class ExportRequest:
    """One chunk of work to export — a cutlist + metadata for the sequence."""
    cutlist: CutList
    sequence_name: str
    # Drop 3.6: replaces the old single-file clean-mic approach. The audio
    # sync state (from pipeline's audio_sync stage) tells the exporter,
    # per A-roll phrase, which lav files cover that specific time range
    # and at what offset. Multiple covering lavs land on parallel tracks.
    audio_sync_state: Optional[object] = None     # AudioSyncState
    # Legacy (kept for back-compat; ignored if audio_sync_state is set)
    clean_mic_path: Optional[Path] = None
    clean_mic_offset_sec: float = 0.0
    # Real source dimensions (detected from the user's footage). When set,
    # the FCPXMLWriter uses these for its fit_filter scale calculation
    # instead of the 1920x1080 assumption. Critical for 4K sources.
    source_width: Optional[int] = None
    source_height: Optional[int] = None


def export_multi_timeline(
    requests: list[ExportRequest],
    output_path: Path,
    broll_library: Optional[list[BrollLibraryEntry]] = None,
    project_name: str = "B-Roll Buddy",
    include_overlay: bool = True,
    auto_include_rules: Optional[list[dict]] = None,
) -> Path:
    """Drop 4.45: emit one XML with the full bin hierarchy Premiere honors.

    Output structure on import to Premiere:

        <project_name>/
        ├─ Seq/
        │  ├─ All Synced A-Roll       ← sibling of v1 (when generated)
        │  ├─ v1/                     ← story sequences land here
        │  └─ Final/                  ← empty placeholder bin
        ├─ Footage/
        │  ├─ A-Roll/                 ← A-roll source masters
        │  └─ B-Roll/                 ← B-roll library masters (was "B-Roll Library")
        ├─ Audio/
        │  ├─ Source Audio/           ← lav/boom WAV masters (was auto-flattened)
        │  ├─ Music/                  ← empty placeholder
        │  └─ SFX/                    ← empty placeholder
        └─ Files/
           ├─ Overlays/               ← safe-zone overlay PNG masters
           ├─ Nested Seqs/            ← empty placeholder
           └─ Colors/                 ← empty placeholder

    Drop 4.47 (current): the previous "Project Name" sub-bin wrapper
    around Seq/ has been removed. Seq/ is now a top-level sibling of
    Footage, Audio, Files. Premiere's own export idiom DOES wrap Seq
    in a project-name sub-bin, but we deliberately diverge for a
    flatter, more familiar layout.

    BACKGROUND. Drop 4.44 emitted a single flat <bin>{project_name}</bin>
    containing the library bin and sequences as siblings. Premiere's FCP7
    importer flattened any custom nested bin structure into "Recovered
    Clips", so master clips for A-roll/audio/overlays appeared at the
    project root rather than in organized bins.

    The fix (verified empirically with test_alpha and test_beta XMLs):
      * Use <project><name/><children/></project> as the root, NOT <bin>...
      * <xmeml version="4">, not "5" (matches Premiere's own export)
      * Top-level bins (Footage, Audio, Files) are SIBLINGS of "Project
        Name", direct children of <project><children>. The "Project Name"
        sub-bin contains ONLY sequences (Seq/v1, Seq/Final).
      * Master clips contain a full <media> tree with <video>/<audio>
        tracks, each with <clipitem> elements that have <masterclipid>
        self-refs and <link> blocks wiring V↔A clipitems together.
      * Master clips embed the full <file> block in their first clipitem;
        sequences emit bare <file id="..."/> self-closing references that
        resolve via Premiere's importer (forward-reference works).

    DATA FLOW. Pre-scan all requests to discover unique source paths
    (A-roll, synced audio, overlays). Allocate one masterclip-id and one
    file-id per path, document-wide. Populate `master_clip_map` so that
    sequences building via FCPXMLWriter._build_file_ref will short-circuit
    to bare refs for every path in the map. Then build sequences (which
    end up emitting bare refs everywhere). Then build master clips, each
    of which embeds its full <file> block — the late declarations resolve
    the earlier bare refs.

    Overlay PNGs are copied NEXT TO the XML file (via _overlays/ sibling
    dir) so Premiere can resolve them regardless of where the app bundle
    lives.
    """
    import shutil
    import sys as _sys
    from .overlay import get_overlay_path

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # =====================================================================
    # Phase 1: Pre-copy overlay PNGs next to the XML (best-effort).
    #
    # Drop 4.45.2: copying is now best-effort — if it fails, we still
    # create a master clip in Files/Overlays/ pointing at the bundle path.
    # Previously a copy failure suppressed the entire overlay master and
    # caused Premiere to auto-shelve the sequence's inline overlay decl
    # in the wrong bin. The overlay still works (Premiere can resolve the
    # bundle path) — it just doesn't survive moving the .app.
    # =====================================================================
    overlay_override: dict[str, Path] = {}
    overlay_bundle_paths: dict[str, Path] = {}  # style -> bundle source Path
    if include_overlay:
        overlays_dir = output_path.parent / "_overlays"
        unique_styles = {r.cutlist.overlay_style for r in requests
                         if r.cutlist.overlay_style and r.cutlist.overlay_style != "none"}
        print(f"[overlay] Phase 1: include_overlay=True, "
              f"unique_styles={sorted(unique_styles)}", file=_sys.stderr)

        for style in unique_styles:
            src = get_overlay_path(style)
            if src is None or not src.exists():
                from .overlay import _assets_dir
                print(
                    f"[overlay] WARNING: no PNG found for style '{style}' "
                    f"(looked in {_assets_dir()}). "
                    f"V3 overlay track will be skipped.",
                    file=_sys.stderr,
                )
                continue
            # Always record the bundle path (used as fallback for the master)
            overlay_bundle_paths[style] = src

            try:
                overlays_dir.mkdir(parents=True, exist_ok=True)
                dest = overlays_dir / src.name
                shutil.copyfile(src, dest)
                overlay_override[style] = dest
                print(f"[overlay] copied '{style}': {src} -> {dest}",
                      file=_sys.stderr)
            except OSError as e:
                print(
                    f"[overlay] WARNING: failed to prepare/copy overlay '{style}' "
                    f"from {src} into {overlays_dir}: {type(e).__name__}: {e}. "
                    f"Will use bundle path for master clip — overlay still works "
                    f"but won't survive moving the .app.",
                    file=_sys.stderr,
                )
                # Don't 'continue' — fall through so the bundle path still
                # ends up registered as the master's source path below.
    else:
        print(f"[overlay] Phase 1: include_overlay=False, skipping", file=_sys.stderr)

    # =====================================================================
    # Phase 2: Discovery — find all unique source paths
    # =====================================================================
    # A-roll: every unique source_file across all cutlists
    aroll_paths: list[str] = []
    aroll_seen: set[str] = set()
    for req in requests:
        for phrase in req.cutlist.aroll_track:
            try:
                p = str(Path(phrase.source_file).resolve())
            except Exception:
                p = phrase.source_file
            if p not in aroll_seen:
                aroll_seen.add(p)
                aroll_paths.append(p)

    # Audio: every unique audio_file across all sync states
    # Note: we collect from .pairs (the universe of registered audio files),
    # not just .groups, so silent groups still get a master clip in the bin.
    audio_paths: list[str] = []
    audio_seen: set[str] = set()
    for req in requests:
        if req.audio_sync_state is None:
            continue
        for pair in getattr(req.audio_sync_state, "pairs", []):
            af = getattr(pair, "audio_file", None)
            if not af:
                continue
            try:
                p = str(Path(af).resolve())
            except Exception:
                p = af
            if p not in audio_seen:
                audio_seen.add(p)
                audio_paths.append(p)

    # Overlays.
    # Drop 4.45.2: walk overlay_bundle_paths (always populated when an
    # overlay style is in use) instead of overlay_override (which only
    # has copy-success entries). When the copy succeeded we use the
    # COPIED path as the master's source (survives moving the .app);
    # when it failed we fall back to the BUNDLE path. Either way the
    # master clip gets created in Files/Overlays/.
    #
    # We also register both the COPIED path AND the BUNDLE path as keys in
    # master_clip_map so however the writer resolves the overlay path
    # (overlay_path_override → copied; fallback → bundle) it hits the map
    # and emits a bare <file> ref linked to our master.
    overlay_paths: list[str] = []        # canonical path used by master clip's <file>
    overlay_seen: set[str] = set()
    overlay_aliases: dict[str, str] = {}  # other_path → canonical path
    for style, bundle_src in overlay_bundle_paths.items():
        copied = overlay_override.get(style)
        if copied is not None:
            try:
                canonical = str(Path(copied).resolve())
            except Exception:
                canonical = str(copied)
        else:
            try:
                canonical = str(Path(bundle_src).resolve())
            except Exception:
                canonical = str(bundle_src)

        if canonical not in overlay_seen:
            overlay_seen.add(canonical)
            overlay_paths.append(canonical)

        # Register the OTHER path as an alias so writer's path resolution
        # of either bundle or copied lands on the same master.
        if copied is not None:
            try:
                bp = str(Path(bundle_src).resolve())
            except Exception:
                bp = str(bundle_src)
            if bp != canonical:
                overlay_aliases[bp] = canonical

    print(f"[overlay] Phase 2: overlay_paths={overlay_paths}, "
          f"overlay_aliases={overlay_aliases}", file=_sys.stderr)

    # =====================================================================
    # Phase 3: Allocate document-wide master_id and file_id for every path
    # =====================================================================
    # Order: B-roll library first (preserves existing IDs for diff stability
    # vs older drops), then A-roll, audio, overlays. The file_id is a single
    # counter shared across all categories.
    master_clip_map: dict[str, tuple[str, str]] = {}
    next_master = 1
    next_file = 1

    # B-roll library entries
    broll_lib_ids: list[tuple[str, str]] = []  # parallel to broll_library
    if broll_library:
        for entry in broll_library:
            mid = f"masterclip-{next_master}"
            fid = f"file-{next_file}"
            broll_lib_ids.append((mid, fid))
            # Register both proxy and original paths so the link works
            # whether the library or the timeline references either.
            for p in {entry.original_path, entry.source_path}:
                if not p:
                    continue
                try:
                    rp = str(Path(p).resolve())
                except Exception:
                    rp = p
                master_clip_map[rp] = (mid, fid)
            next_master += 1
            next_file += 1

    # A-roll
    aroll_ids: dict[str, tuple[str, str]] = {}
    for p in aroll_paths:
        if p in master_clip_map:
            # Post House 2026-09-03: this used to share the same master
            # with B-roll ("a clip that's also in B-roll library — share
            # the same master"), which was correct for the original
            # meaning (the same physical file happens to appear in both
            # lists) but wrong for dual_use sources specifically, where
            # Ryan's own requirement is two SEPARATE Project-panel items
            # -- one native (A-roll), one interpreted (B-roll), never a
            # shared clip. Mint a fresh, dedicated id for the A-roll
            # usage instead. Deliberately do NOT touch master_clip_map[p]
            # here -- it stays pointing at the B-roll registration, so
            # nothing else that resolves an id through master_clip_map
            # changes behavior; only this path's OWN A-roll master gets
            # built as its own thing.
            mid = f"masterclip-{next_master}"
            fid = f"file-{next_file}"
            aroll_ids[p] = (mid, fid)
            next_master += 1
            next_file += 1
            continue
        mid = f"masterclip-{next_master}"
        fid = f"file-{next_file}"
        aroll_ids[p] = (mid, fid)
        master_clip_map[p] = (mid, fid)
        next_master += 1
        next_file += 1

    # Audio
    audio_ids: dict[str, tuple[str, str]] = {}
    for p in audio_paths:
        if p in master_clip_map:
            audio_ids[p] = master_clip_map[p]
            continue
        mid = f"masterclip-{next_master}"
        fid = f"file-{next_file}"
        audio_ids[p] = (mid, fid)
        master_clip_map[p] = (mid, fid)
        next_master += 1
        next_file += 1

    # Overlays
    overlay_ids: dict[str, tuple[str, str]] = {}
    for p in overlay_paths:
        if p in master_clip_map:
            overlay_ids[p] = master_clip_map[p]
            continue
        mid = f"masterclip-{next_master}"
        fid = f"file-{next_file}"
        overlay_ids[p] = (mid, fid)
        master_clip_map[p] = (mid, fid)
        next_master += 1
        next_file += 1

    # Drop 4.45.1: also register bundle-path aliases for each overlay so
    # the sequence's overlay clipitem resolves to the master regardless of
    # whether the writer used the copied path or fell back to the bundle path.
    for bundle_path, copied_path in overlay_aliases.items():
        if copied_path in master_clip_map and bundle_path not in master_clip_map:
            master_clip_map[bundle_path] = master_clip_map[copied_path]

    # =====================================================================
    # Phase 4: Build the document
    # =====================================================================
    doc = minidom.Document()
    xmeml = doc.createElement("xmeml")
    # Drop 4.45: version "4" matches Premiere's own export. Drop 4.44 used
    # "5" but that was paired with the wrong root structure anyway, so we
    # snap back to the known-good value.
    xmeml.setAttribute("version", "4")
    doc.appendChild(xmeml)

    # <project> root with children
    project = doc.createElement("project")
    _bb_append_text(doc, project, "name", project_name)
    proj_children = doc.createElement("children")
    project.appendChild(proj_children)
    xmeml.appendChild(project)

    # ---- Seq bin (top-level, sibling of Footage/Audio/Files) ------------
    # Drop 4.47: the previous "Project Name" sub-bin wrapper has been
    # removed. Seq/ now lives directly under <project><children/>, so
    # the project panel shows Seq, Footage, Audio, Files as four
    # top-level bins rather than nesting Seq inside a "Project Name"
    # bin alongside the others. (Premiere's own export idiom DOES nest
    # Seq inside a project-name bin; we deliberately diverge for a
    # flatter user preference.)
    seq_bin = make_bin(doc, "Seq")
    v1_bin = make_bin(doc, "v1")
    final_bin = make_bin(doc, "Final")
    bin_children(seq_bin).appendChild(v1_bin)
    bin_children(seq_bin).appendChild(final_bin)
    proj_children.appendChild(seq_bin)
    # Kept for backward compat with the auto-include helper signature;
    # no longer references a real bin in the document. Helpers that
    # used to descend into this bin via path matching now treat the
    # absence as "no project-name special case" and walk like normal.
    proj_name_bin = None

    # ---- Build all sequences. master_clip_map ensures bare <file> refs --
    # Sequences land in v1/. The existing FCPXMLWriter._build_file_ref
    # short-circuits any path in master_clip_map to a bare <file id="..."/>
    # reference, so we don't need to modify that code.
    for i, req in enumerate(requests):
        writer_kwargs = dict(
            cutlist=req.cutlist,
            sequence_name=req.sequence_name,
            include_overlay=include_overlay,
        )
        if req.source_width and req.source_height:
            writer_kwargs["source_width"] = req.source_width
            writer_kwargs["source_height"] = req.source_height
        writer = FCPXMLWriter(**writer_kwargs)
        # Share the doc so created elements have the correct owner
        writer.doc = doc
        # Pass the master clip map so clipitems link via ID
        writer.master_clip_map = master_clip_map
        # Drop 4.45.1: bin structure keeps masters alive; anchor track is
        # both unnecessary AND breaks import (see exporter.py comment).
        writer.skip_library_anchor_track = True

        # Drop 4.20: namespace this writer's auto-generated IDs so they
        # don't collide with other sequences in the same document. Each
        # sequence's clipitem IDs use the sN- prefix; master clip clipitem
        # IDs (built later) use the mc- prefix — different namespaces.
        writer.id_prefix = f"s{i+1}-"

        # Drop 4.23: when sync audio is coming, mute A1 camera audio so
        # only the lav/boom tracks are heard. Editor can re-enable in one
        # click if they want camera audio as a fallback.
        writer.mute_camera_audio = req.audio_sync_state is not None

        override = overlay_override.get(req.cutlist.overlay_style)
        if override is not None:
            writer.overlay_path_override = override

        seq_elem = writer._build_sequence()

        # Drop 3.6: per-phrase lav coverage → parallel audio tracks.
        # If audio_sync_state is provided, ignore the legacy single-mic path.
        if req.audio_sync_state is not None:
            _append_synced_audio_tracks(doc, seq_elem, writer,
                                        req.cutlist, req.audio_sync_state)
        elif req.clean_mic_path and req.clean_mic_path.exists():
            _append_clean_mic_track(
                doc, seq_elem, writer,
                req.clean_mic_path, req.clean_mic_offset_sec,
            )

        # Drop 4.45.5: the "All Synced A-Roll" reference sequence lives
        # directly in Seq/ (sibling of v1), not inside Seq/v1/. Story
        # sequences (the actual angle exports) go in Seq/v1/. We detect
        # the All-Synced-A-Roll request by its phrase_id range — those
        # phrases are minted with phrase_id >= 2_000_000 by the backend's
        # _build_all_aroll_sequences. Story phrases use small ids (1, 2, ...).
        is_all_synced_aroll = any(
            getattr(p, "phrase_id", 0) >= 2_000_000
            for p in req.cutlist.aroll_track
        )
        if is_all_synced_aroll:
            bin_children(seq_bin).appendChild(seq_elem)
        else:
            bin_children(v1_bin).appendChild(seq_elem)

    # ---- Footage / A-Roll, B-Roll ---------------------------------------
    footage_bin = make_bin(doc, "Footage")
    aroll_bin = make_bin(doc, "A-Roll")
    broll_bin = make_bin(doc, "B-Roll")
    bin_children(footage_bin).appendChild(aroll_bin)
    bin_children(footage_bin).appendChild(broll_bin)
    proj_children.appendChild(footage_bin)

    # Master clip clipitem IDs use a doc-wide counter with mc- prefix so
    # they never collide with sequence clipitems (sN-clipitem-X) or each
    # other.
    mc_clipitem_counter = [0]
    def _next_mc_clipitem():
        mc_clipitem_counter[0] += 1
        return f"mc-clipitem-{mc_clipitem_counter[0]}"

    # ---- A-roll master clips
    for path in aroll_paths:
        master_id, file_id = aroll_ids[path]
        # Skip if this path already got a master via B-roll library
        if broll_library and any(broll_lib_ids[i][0] == master_id
                                  for i in range(len(broll_lib_ids))):
            continue
        master = _build_aroll_master_for_path(
            doc, path, master_id, file_id, _next_mc_clipitem,
        )
        bin_children(aroll_bin).appendChild(master)

    # ---- B-roll library master clips
    if broll_library:
        for i, entry in enumerate(broll_library):
            master_id, file_id = broll_lib_ids[i]
            master = _build_broll_master_for_entry(
                doc, entry, master_id, file_id, _next_mc_clipitem,
            )
            bin_children(broll_bin).appendChild(master)

    # ---- Audio / Source Audio, Music, SFX -------------------------------
    audio_top_bin = make_bin(doc, "Audio")
    src_audio_bin = make_bin(doc, "Source Audio")
    music_bin = make_bin(doc, "Music")
    sfx_bin = make_bin(doc, "SFX")
    bin_children(audio_top_bin).appendChild(src_audio_bin)
    bin_children(audio_top_bin).appendChild(music_bin)
    bin_children(audio_top_bin).appendChild(sfx_bin)
    proj_children.appendChild(audio_top_bin)

    for path in audio_paths:
        master_id, file_id = audio_ids[path]
        # Skip if already covered (e.g., audio file also tagged in library)
        if any(broll_lib_ids[i][0] == master_id for i in range(len(broll_lib_ids))) \
                if broll_library else False:
            continue
        master = _build_audio_master_for_path(
            doc, path, master_id, file_id, _next_mc_clipitem,
        )
        bin_children(src_audio_bin).appendChild(master)

    # ---- Files / Overlays, Nested Seqs, Colors --------------------------
    files_bin = make_bin(doc, "Files")
    overlays_bin = make_bin(doc, "Overlays")
    nested_bin = make_bin(doc, "Nested Seqs")
    colors_bin = make_bin(doc, "Colors")
    bin_children(files_bin).appendChild(overlays_bin)
    bin_children(files_bin).appendChild(nested_bin)
    bin_children(files_bin).appendChild(colors_bin)
    proj_children.appendChild(files_bin)

    # Overlay master clips. Use the first request's sequence dims as the
    # overlay's reported size (overlays are sized to match the output).
    if requests:
        first_w = requests[0].cutlist.sequence_width
        first_h = requests[0].cutlist.sequence_height
    else:
        first_w, first_h = 1920, 1080

    for path in overlay_paths:
        master_id, file_id = overlay_ids[path]
        master = _build_overlay_master_for_path(
            doc, path, master_id, file_id, _next_mc_clipitem,
            first_w, first_h,
        )
        bin_children(overlays_bin).appendChild(master)

    # =====================================================================
    # Phase 4b: Auto-include rules (Drop 4.46)
    # =====================================================================
    # User-configured "always include these files in every export" rules.
    # See precut_pipeline.auto_include for the data model. Each rule
    # specifies a source (file or folder), a bin path, and optional glob.
    # We resolve rules to a flat list of files and inject each as a
    # master clip in the user-specified bin (creating intermediate bins
    # if needed).
    #
    # Runs BEFORE Phase 4c so user-included content counts toward
    # has_content and suppresses placeholder injection in those bins.
    if auto_include_rules:
        from .auto_include import AutoIncludeRule, expand_rules
        rule_objs = [AutoIncludeRule.from_dict(r) for r in auto_include_rules]
        # Drop 1.0.0-beta.2: expand_rules now returns (files, warnings)
        # so the user gets a clear log line for each skipped file
        # (e.g., a .cube LUT) explaining why it was rejected.
        expanded, ai_warnings = expand_rules(rule_objs)
        print(f"[auto-include] {len(rule_objs)} rule(s) expanded to "
              f"{len(expanded)} file(s)", file=_sys.stderr)
        for w in ai_warnings:
            # stderr → captured by backend.py → re-emitted as user-visible
            # log lines tagged [auto-include]. Visible in the LogView panel.
            print(f"[auto-include] {w}", file=_sys.stderr)

        for src_path, bin_segments, kind in expanded:
            target_bin = _find_or_create_bin_at_path(
                doc, proj_children, bin_segments, project_name,
                project_name_bin=proj_name_bin,
            )
            if target_bin is None:
                print(f"[auto-include] WARNING: could not resolve bin path "
                      f"{'/'.join(bin_segments)!r} for {src_path}; skipping",
                      file=_sys.stderr)
                continue

            mid = f"masterclip-{next_master}"
            fid = f"file-{next_file}"
            next_master += 1
            next_file += 1

            try:
                if kind == "audio":
                    master = _build_audio_master_for_path(
                        doc, str(src_path), mid, fid, _next_mc_clipitem,
                    )
                elif kind == "video":
                    master = _build_aroll_master_for_path(
                        doc, str(src_path), mid, fid, _next_mc_clipitem,
                    )
                elif kind == "image":
                    master = _build_overlay_master_for_path(
                        doc, str(src_path), mid, fid, _next_mc_clipitem,
                        first_w, first_h,
                    )
                else:
                    print(f"[auto-include] WARNING: unknown kind {kind!r} for "
                          f"{src_path}; skipping", file=_sys.stderr)
                    continue
            except Exception as e:
                print(f"[auto-include] WARNING: failed to build master for "
                      f"{src_path}: {type(e).__name__}: {e}; skipping",
                      file=_sys.stderr)
                continue

            bin_children(target_bin).appendChild(master)
            print(f"[auto-include] added {src_path.name} ({kind}) -> "
                  f"{'/'.join(bin_segments)}", file=_sys.stderr)

    # =====================================================================
    # Phase 4c: Populate empty placeholder bins (Drop 4.45.3 / 4.45.4)
    # =====================================================================
    # Premiere's importer drops bins with no real master clip in them, even
    # if the bin is structurally complete (<n>, <labels>, <children/>).
    # Each empty placeholder bin gets a 1x1 transparent PNG master clip
    # named "(placeholder - delete me)" so the bin survives import.
    #
    # Drop 4.45.4: each placeholder slot uses its OWN distinct PNG file.
    # In 4.45.3 we shared one placeholder.png across all empty bins;
    # Premiere's importer dedupes master clips by file URL, so all six
    # placeholder masters got merged into one (kept in Final, the rest
    # vanished along with their bins). Distinct files = distinct masters.
    placeholder_eligible = [
        # (bin_element, slot_name) -- slot_name maps to placeholder_<slot>.png
        (final_bin, "final"),
        (broll_bin, "broll"),         # only empty if no library
        (music_bin, "music"),
        (sfx_bin, "sfx"),
        (nested_bin, "nested_seqs"),
        (colors_bin, "colors"),
    ]
    for empty_bin, slot in placeholder_eligible:
        children_el = bin_children(empty_bin)
        has_content = any(
            c.nodeType == c.ELEMENT_NODE and c.tagName in ("clip", "sequence", "bin")
            for c in children_el.childNodes
        )
        if has_content:
            continue
        placeholder_png = get_placeholder_png_path(slot)
        if placeholder_png is None:
            print(f"[placeholder] WARNING: bundled placeholder_{slot}.png "
                  f"not found. The '{slot}' bin will likely be culled by "
                  f"Premiere on import.", file=_sys.stderr)
            continue
        mid = f"masterclip-{next_master}"
        fid = f"file-{next_file}"
        next_master += 1
        next_file += 1
        full_file = _build_full_image_file(
            doc, fid, path_to_url(str(placeholder_png)),
            placeholder_png.name,
            width=1, height=1, timebase=30, ntsc=False,
        )
        master = build_image_master_clip(
            doc, mid, fid, "(placeholder \u2014 delete me)",
            timebase=30, ntsc=False,
            next_clipitem_id_fn=_next_mc_clipitem,
            file_ref_first_use=full_file,
        )
        children_el.appendChild(master)

    # =====================================================================
    # Phase 5: Serialize
    # =====================================================================
    xml_str = doc.toprettyxml(indent="\t", encoding="UTF-8").decode("UTF-8")
    lines = xml_str.splitlines()
    if lines and lines[0].startswith("<?xml"):
        lines.insert(1, "<!DOCTYPE xmeml>")
    else:
        lines.insert(0, "<!DOCTYPE xmeml>")
        lines.insert(0, '<?xml version="1.0" encoding="UTF-8"?>')

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


# ---------------------------------------------------------------------------
# Per-category master clip builders
# ---------------------------------------------------------------------------

def _build_aroll_master_for_path(
    doc: minidom.Document,
    path: str,
    master_id: str,
    file_id: str,
    next_clipitem_id_fn,
) -> minidom.Element:
    """Build a master clip for an A-roll source video file.

    Uses _safe_probe to extract real fps/dims/audio specs. Falls back to
    sensible defaults if the file can't be probed (e.g., probe disabled,
    file moved). The fallback values match Premiere's defaults so the
    clip still imports — it just may have wrong duration/specs until the
    editor relinks.
    """
    info = _safe_probe(Path(path))
    if info:
        fps = info.get("fps") or 30.0
        fr = detect_frame_rate(fps)
        timebase = fr.timebase
        ntsc_flag = fr.ntsc

        nb_frames = info.get("nb_frames")
        if nb_frames and nb_frames > 0:
            duration_frames = nb_frames
        else:
            duration_frames = fr.seconds_to_frames(info.get("duration") or 0.0)
        duration_frames = max(2, duration_frames)

        width = int(info.get("width") or 1920)
        height = int(info.get("height") or 1080)

        audio_info = info.get("audio")
        if audio_info:
            has_audio = True
            audio_channels = max(1, int(audio_info.get("channels") or 2))
            audio_samplerate = int(audio_info.get("samplerate") or 48000)
            audio_depth = int(audio_info.get("depth") or 16)
        else:
            has_audio = False
            audio_channels = 0
            audio_samplerate = 48000
            audio_depth = 16
    else:
        # Probe failed — emit something Premiere can at least open
        timebase, ntsc_flag = 30, False
        duration_frames = 1800
        width, height = 1920, 1080
        has_audio = True
        audio_channels = 2
        audio_samplerate = 48000
        audio_depth = 16

    full_file = _build_full_video_file(
        doc, file_id, path_to_url(path), Path(path).name,
        duration_frames=duration_frames,
        timebase=timebase, ntsc=ntsc_flag,
        width=width, height=height,
        has_audio=has_audio,
        audio_samplerate=audio_samplerate,
        audio_channels=max(1, audio_channels),
        audio_depth=audio_depth,
    )
    master = build_aroll_master_clip(
        doc, master_id, file_id, Path(path).name,
        duration_frames=duration_frames,
        timebase=timebase, ntsc=ntsc_flag,
        # If file has no audio, audio_track_count=0 → no audio block in master
        audio_track_count=audio_channels if has_audio else 0,
        next_clipitem_id_fn=next_clipitem_id_fn,
        file_ref_first_use=full_file,
    )
    return master


def _build_broll_master_for_entry(
    doc: minidom.Document,
    entry: BrollLibraryEntry,
    master_id: str,
    file_id: str,
    next_clipitem_id_fn,
) -> minidom.Element:
    """Build a master clip for a B-roll library entry.

    Uses the metadata already on the BrollLibraryEntry (no re-probe needed —
    that's what load_broll_library does). Surfaces tags in the Description
    and Log Note columns via <logginginfo>.
    """
    fr = detect_frame_rate(entry.fps or 30.0)
    timebase, ntsc_flag = fr.timebase, fr.ntsc

    if entry.frame_count and entry.frame_count > 0:
        duration_frames = entry.frame_count
    else:
        duration_frames = fr.seconds_to_frames(entry.duration_sec or 0.0)
    duration_frames = max(2, duration_frames)

    # Reference the original file URL so Premiere resolves to the full-res
    # source, not the proxy. (The proxy lives in proxies/ inside the
    # PreCut output dir; the original is the user's actual footage file.)
    ref_path = entry.original_path or entry.source_path

    full_file = _build_full_video_file(
        doc, file_id, path_to_url(ref_path), Path(ref_path).name,
        duration_frames=duration_frames,
        timebase=timebase, ntsc=ntsc_flag,
        width=entry.width, height=entry.height,
        has_audio=entry.has_audio,
        audio_samplerate=entry.audio_samplerate or 48000,
        audio_channels=entry.audio_channels or 2,
        audio_depth=entry.audio_depth or 16,
    )

    audio_count = (entry.audio_channels or 2) if entry.has_audio else 0

    # Build the master, then swap in a populated logginginfo so the tags
    # appear in Premiere's Description and Log Note columns.
    master = build_aroll_master_clip(
        doc, master_id, file_id, entry.display_name,
        duration_frames=duration_frames,
        timebase=timebase, ntsc=ntsc_flag,
        audio_track_count=audio_count,
        next_clipitem_id_fn=next_clipitem_id_fn,
        file_ref_first_use=full_file,
    )

    # Replace the empty logginginfo with one carrying the tags
    if entry.description or entry.comments:
        new_li = _build_logginginfo_with_tags(
            doc,
            description=entry.description or "",
            lognote=entry.comments or "",
        )
        for child in list(master.childNodes):
            if (child.nodeType == child.ELEMENT_NODE
                    and child.tagName == "logginginfo"):
                master.replaceChild(new_li, child)
                break

    # Also emit <comments><mastercomment1> for Premiere's Comment column
    # (preserves Drop 4.44 behavior for tag visibility)
    if entry.comments:
        comments = doc.createElement("comments")
        _bb_append_text(doc, comments, "mastercomment1", entry.comments)
        _bb_append_text(doc, comments, "mastercomment2", "")
        # Append at end of master clip — Premiere reads it regardless of
        # position among the trailing metadata blocks
        master.appendChild(comments)

    return master


def _build_audio_master_for_path(
    doc: minidom.Document,
    path: str,
    master_id: str,
    file_id: str,
    next_clipitem_id_fn,
) -> minidom.Element:
    """Build a master clip for an audio-only WAV file (lav/boom)."""
    dur_sec = _probe_audio_duration(Path(path)) or 1.0
    # Audio masters use a 30 NTSC timebase; the reference XML's audio
    # masters do the same. The exact timebase doesn't affect playback
    # (audio is sample-accurate), it just sets the duration display.
    timebase, ntsc_flag = 30, True
    fr = detect_frame_rate(timebase * (1000.0/1001.0))
    duration_frames = max(2, fr.seconds_to_frames(dur_sec))

    # We could probe samplerate/channels/depth via ffprobe, but for now
    # default to 48kHz mono 16-bit (standard WAV). If specific files differ,
    # Premiere will adapt on load — the master clip's declared specs are
    # informational, not authoritative.
    samplerate, channels, depth = 48000, 1, 16

    full_file = _build_full_audio_file(
        doc, file_id, path_to_url(path), Path(path).name,
        duration_frames=duration_frames,
        timebase=timebase, ntsc=ntsc_flag,
        samplerate=samplerate, channels=channels, depth=depth,
    )
    master = build_audio_master_clip(
        doc, master_id, file_id, Path(path).name,
        duration_frames=duration_frames,
        timebase=timebase, ntsc=ntsc_flag,
        channel_count=channels,
        next_clipitem_id_fn=next_clipitem_id_fn,
        file_ref_first_use=full_file,
    )
    return master


def _build_overlay_master_for_path(
    doc: minidom.Document,
    path: str,
    master_id: str,
    file_id: str,
    next_clipitem_id_fn,
    width: int,
    height: int,
) -> minidom.Element:
    """Build a master clip for an overlay PNG (still image)."""
    full_file = _build_full_image_file(
        doc, file_id, path_to_url(path), Path(path).name,
        width=width, height=height, timebase=30, ntsc=False,
    )
    master = build_image_master_clip(
        doc, master_id, file_id, Path(path).name,
        timebase=30, ntsc=False,
        next_clipitem_id_fn=next_clipitem_id_fn,
        file_ref_first_use=full_file,
    )
    return master


def _bin_name_text(bin_el: minidom.Element) -> str:
    """Return the text content of a <bin>'s <name> child, or ''."""
    for child in bin_el.childNodes:
        if (child.nodeType == child.ELEMENT_NODE
                and child.tagName == "name"
                and child.firstChild is not None):
            return (child.firstChild.nodeValue or "").strip()
    return ""


def _find_or_create_bin_at_path(
    doc: minidom.Document,
    project_children: minidom.Element,
    segments: list[str],
    project_name: str,
    project_name_bin: Optional[minidom.Element] = None,
) -> Optional[minidom.Element]:
    """Walk a slash-delimited bin path through the project tree, creating
    intermediate bins as needed. Return the leaf bin element, or None if
    segments is empty.

    The first segment is matched against the direct children of
    project_children (the top-level bins under <project><children>).
    Subsequent segments are matched against direct sub-bin children of
    the previous level.

    Matching is case-insensitive: if the user typed 'audio/music' but the
    existing bin is named 'Audio', we descend into the existing bin
    rather than creating a new one. The existing bin's name is preserved.

    For new bins (intermediate or leaf), we use the user's segment string
    verbatim — preserving their preferred casing for any new structure.

    The project_name_bin parameter was used in earlier drops when the
    sequence tree lived inside a "Project Name" sub-bin. Drop 4.47 moved
    Seq/ to be a direct top-level bin, so callers now pass None and
    this parameter is effectively dead. Kept on the signature for
    backward compat with any callers that still pass it.
    """
    if not segments:
        return None

    # Helper: find a direct child <bin> by name (case-insensitive).
    def _find_child_bin(parent: minidom.Element, name: str) -> Optional[minidom.Element]:
        target_lower = name.strip().lower()
        # parent might be a <children> wrapper or a <bin> with a <children>
        # wrapper inside it. Normalize to the children container.
        if parent.tagName == "bin":
            container = bin_children(parent)
        else:
            container = parent
        for c in container.childNodes:
            if (c.nodeType == c.ELEMENT_NODE
                    and c.tagName == "bin"
                    and _bin_name_text(c).lower() == target_lower):
                return c
        return None

    # Step 1: handle the first segment against project_children.
    # Special-case: if it matches the project_name, descend into project_name_bin.
    first_seg = segments[0].strip()
    current: minidom.Element

    if (project_name_bin is not None
            and first_seg.lower() == project_name.strip().lower()):
        current = project_name_bin
    else:
        existing = _find_child_bin(project_children, first_seg)
        if existing is not None:
            current = existing
        else:
            # Create a new top-level bin
            new_bin = make_bin(doc, first_seg)
            project_children.appendChild(new_bin)
            current = new_bin

    # Step 2..N: descend through remaining segments
    for seg in segments[1:]:
        seg = seg.strip()
        if not seg:
            continue
        existing = _find_child_bin(current, seg)
        if existing is not None:
            current = existing
        else:
            new_bin = make_bin(doc, seg)
            bin_children(current).appendChild(new_bin)
            current = new_bin

    return current


def _build_library_bin(doc: minidom.Document, entries: list[BrollLibraryEntry]) -> minidom.Element:
    """Create a <bin> element named 'B-Roll Library' with all clips.

    Each clip is a FCP7-compliant master clip that Premiere will recognize
    and place in the project panel as a standalone, independent bin item.

    Required elements per Apple FCP7 XML spec (order matters for Premiere):
      name, duration, rate, in, out, masterclipid, ismasterclip, file, timecode
    Optional but useful:
      comments (mastercomment1), labels, logginginfo
    """
    bin_el = doc.createElement("bin")
    name_el = doc.createElement("name")
    name_el.appendChild(doc.createTextNode("B-Roll Library"))
    bin_el.appendChild(name_el)

    children = doc.createElement("children")
    bin_el.appendChild(children)

    for i, entry in enumerate(entries):
        # FCP7 idiom: the <clip> element's id attribute is unique per clip
        # element, while <masterclipid> is a separate identifier shared
        # across the master clip AND all its timeline affiliates. This is
        # how Premiere links "timeline usage" back to "bin master."
        clip_id = f"broll_lib_clip_{i+1}"
        master_id = f"masterclip-{i+1}"
        file_id = f"broll_lib_file_{i+1}"
        clip_uuid = str(uuid.uuid4()).upper()
        frame_rate = detect_frame_rate(entry.fps)

        # Drop 4.30: prefer the exact nb_frames from the live probe when
        # available. Previously we computed duration = duration_sec × fps
        # and rounded, but that can produce a duration that disagrees with
        # Premiere's own internal probe by a frame or two — and Premiere
        # marks the clip offline rather than tolerating the mismatch.
        # nb_frames is the authoritative frame count straight from the
        # video stream header, so using it keeps us in lockstep with what
        # Premiere sees when it opens the file.
        if entry.frame_count and entry.frame_count > 0:
            duration_frames = entry.frame_count
        else:
            duration_frames = frame_rate.seconds_to_frames(entry.duration_sec)

        clip_el = doc.createElement("clip")
        clip_el.setAttribute("id", clip_id)

        # ---- Required elements, in the order Premiere expects ----

        # 0. uuid — Premiere generates one per clip; including it helps it
        # recognize the clip as a real browser/bin item rather than a
        # loose reference.
        _child_text(doc, clip_el, "uuid", clip_uuid)

        # 1. name
        _child_text(doc, clip_el, "name", entry.display_name)

        # 2. duration (in frames). Guard against zero/negative durations
        # from DB rows with missing metadata — Premiere drops clips with
        # duration <= 0. We use minimum 2 so the out-point below can be
        # >= 1 while still strictly less than duration.
        duration_frames = max(2, duration_frames)
        _child_text(doc, clip_el, "duration", str(duration_frames))

        # 3. rate (timebase + ntsc)
        rate_el = doc.createElement("rate")
        _child_text(doc, rate_el, "timebase", str(frame_rate.timebase))
        _child_text(doc, rate_el, "ntsc", "TRUE" if frame_rate.ntsc else "FALSE")
        clip_el.appendChild(rate_el)

        # 4. in / out. FCP7 convention: in/out are inclusive frame indices,
        # so out must be strictly less than duration. For a clip with
        # duration=78, valid range is 0-77 (78 total frames, 0-indexed).
        # Premiere is strict about this for short clips — if out >= duration,
        # the clip is silently rejected as invalid.
        _child_text(doc, clip_el, "in", "0")
        _child_text(doc, clip_el, "out", str(duration_frames - 1))

        # 5. masterclipid — shared identifier that links this master clip
        # to its timeline affiliates. Timeline <clipitem> elements will
        # emit the SAME masterclipid value (but keep their own unique id
        # attribute). This is the critical piece for Premiere to show
        # the library bin: without proper master/affiliate linkage,
        # Premiere treats master clips as orphans and drops them.
        _child_text(doc, clip_el, "masterclipid", master_id)

        # 6. ismasterclip — explicit TRUE tells Premiere this should be a
        # top-level master in the bin, not merged with any timeline usage.
        _child_text(doc, clip_el, "ismasterclip", "TRUE")

        # 7. logginginfo — this is where FCP7 stores clip-level description,
        # scene, shot/take, and lognote. Premiere maps these to its own
        # metadata columns with the SAME names, so editors can see them
        # without extra work.
        if entry.description or entry.comments:
            logging = doc.createElement("logginginfo")
            if entry.description:
                _child_text(doc, logging, "description", entry.description)
            # scene and shotTake can be empty; Premiere shows the columns
            _child_text(doc, logging, "scene", "")
            _child_text(doc, logging, "shottake", "")
            if entry.comments:
                # lognote is the field Premiere displays in its "Log Note"
                # column — most searchable of the logging fields.
                _child_text(doc, logging, "lognote", entry.comments)
            clip_el.appendChild(logging)

        # 8. labels — allow color-coding if needed; empty label2 is fine
        labels = doc.createElement("labels")
        _child_text(doc, labels, "label2", "")
        clip_el.appendChild(labels)

        # 9. comments — mastercomment1 appears in Premiere's "Comment" column
        if entry.comments:
            comments = doc.createElement("comments")
            _child_text(doc, comments, "mastercomment1", entry.comments)
            # mastercomment2 leaves room for future secondary tags
            _child_text(doc, comments, "mastercomment2", "")
            clip_el.appendChild(comments)

        # 10. file — source file reference with media characteristics
        file_el = doc.createElement("file")
        file_el.setAttribute("id", file_id)
        # Reference the ORIGINAL file (not the proxy). The proxy is an
        # internal preview artifact; Premiere should see the full-res file
        # as the master source. This also makes masterclipid linking work —
        # timeline clipitems use the original path, so the library must too.
        display_path = entry.original_path or entry.source_path
        _child_text(doc, file_el, "name", Path(display_path).name)
        _child_text(doc, file_el, "pathurl", path_to_url(display_path))

        # File-level rate required (same timebase as the clip)
        file_rate = doc.createElement("rate")
        _child_text(doc, file_rate, "timebase", str(frame_rate.timebase))
        _child_text(doc, file_rate, "ntsc", "TRUE" if frame_rate.ntsc else "FALSE")
        file_el.appendChild(file_rate)

        _child_text(doc, file_el, "duration", str(duration_frames))

        # Media characteristics — VIDEO + AUDIO.
        # Premiere's own FCP7 export for a ProRes .MOV declares both video
        # AND audio characteristics. If we only declare video, Premiere may
        # treat the clip as malformed when it opens the file and finds
        # audio streams we didn't mention.
        media = doc.createElement("media")

        # Video
        video = doc.createElement("video")
        samp = doc.createElement("samplecharacteristics")
        samp_rate = doc.createElement("rate")
        _child_text(doc, samp_rate, "timebase", str(frame_rate.timebase))
        _child_text(doc, samp_rate, "ntsc", "TRUE" if frame_rate.ntsc else "FALSE")
        samp.appendChild(samp_rate)
        _child_text(doc, samp, "width", str(entry.width))
        _child_text(doc, samp, "height", str(entry.height))
        _child_text(doc, samp, "anamorphic", "FALSE")
        _child_text(doc, samp, "pixelaspectratio", "square")
        _child_text(doc, samp, "fielddominance", "none")
        video.appendChild(samp)
        media.appendChild(video)

        # Audio. Premiere expects this block even if the file has no audio;
        # omitting it can cause silent rejection for some files where the
        # actual file does have audio (ProRes camera footage usually does).
        # Drop 4.37: only emit <audio> block if the file actually has an
        # audio stream, AND use the real specs from ffprobe instead of
        # hardcoded 48000/2/16. Declaring audio that doesn't match the
        # real file is a common cause of "offline on import". When the
        # file has no audio stream at all, omit the block entirely so
        # Premiere doesn't look for audio that isn't there.
        if entry.has_audio:
            audio = doc.createElement("audio")
            a_samp = doc.createElement("samplecharacteristics")
            if entry.audio_depth:
                _child_text(doc, a_samp, "depth", str(entry.audio_depth))
            else:
                _child_text(doc, a_samp, "depth", "16")
            if entry.audio_samplerate:
                _child_text(doc, a_samp, "samplerate", str(entry.audio_samplerate))
            else:
                _child_text(doc, a_samp, "samplerate", "48000")
            audio.appendChild(a_samp)
            if entry.audio_channels:
                _child_text(doc, audio, "channelcount", str(entry.audio_channels))
            else:
                _child_text(doc, audio, "channelcount", "2")
            media.appendChild(audio)

        file_el.appendChild(media)

        clip_el.appendChild(file_el)
        children.appendChild(clip_el)

    return bin_el


def _child_text(doc, parent, tag, text):
    """Helper: append a <tag>text</tag> child element."""
    el = doc.createElement(tag)
    el.appendChild(doc.createTextNode(str(text)))
    parent.appendChild(el)


def _append_clean_mic_track(
    doc: minidom.Document,
    seq_elem: minidom.Element,
    writer: FCPXMLWriter,
    clean_mic_path: Path,
    offset_sec: float,
) -> None:
    """Add the clean mic audio file as an extra audio track on the sequence.

    Goes in as A2 (after A1 from the A-roll). Offset is in seconds — positive
    means the clean mic starts LATER than A-roll, negative means earlier
    (clamped to 0 since the timeline can't go negative).

    Probes the audio file duration via ffprobe so Premiere sees a sane
    end point. Without duration, Premiere refuses to load the clipitem.
    """
    # Probe the clean mic file for duration. If ffprobe is missing or the
    # file is unreadable, we skip the track rather than write a broken one.
    audio_duration = _probe_audio_duration(clean_mic_path)
    if audio_duration is None or audio_duration <= 0:
        return

    # Find the <audio> child directly under <media>. getElementsByTagName
    # descends through ALL descendants, which can return audio elements from
    # nested clipitem files. We want ONLY the sequence-level <audio>.
    media = None
    for child in seq_elem.childNodes:
        if child.nodeType == child.ELEMENT_NODE and child.tagName == "media":
            media = child
            break
    if media is None:
        return
    audio = None
    for child in media.childNodes:
        if child.nodeType == child.ELEMENT_NODE and child.tagName == "audio":
            audio = child
            break
    if audio is None:
        return

    frame_rate = writer.frame_rate
    start_frames = max(0, frame_rate.seconds_to_frames(offset_sec))
    duration_frames = frame_rate.seconds_to_frames(audio_duration)
    end_frames = start_frames + duration_frames

    track = doc.createElement("track")
    track.setAttribute("TL.SQTrackAudioKeyframeStyle", "0")

    clipitem = doc.createElement("clipitem")
    clipitem.setAttribute("id", f"{writer.id_prefix}cleanmic-{writer._next_clipitem_num}")
    writer._next_clipitem_num += 1

    # Required clipitem children in FCP7 XML order:
    # name, enabled, duration, rate, start, end, in, out, file
    _append_text(doc, clipitem, "name", f"Clean mic: {clean_mic_path.name}")
    _append_text(doc, clipitem, "enabled", "TRUE")
    _append_text(doc, clipitem, "duration", str(duration_frames))

    rate_el = doc.createElement("rate")
    _append_text(doc, rate_el, "timebase", str(int(round(writer.sequence_fps))))
    _append_text(doc, rate_el, "ntsc", "TRUE" if frame_rate.ntsc else "FALSE")
    clipitem.appendChild(rate_el)

    _append_text(doc, clipitem, "start", str(start_frames))
    _append_text(doc, clipitem, "end", str(end_frames))
    _append_text(doc, clipitem, "in", "0")
    _append_text(doc, clipitem, "out", str(duration_frames))

    # File reference
    file_el = doc.createElement("file")
    file_el.setAttribute("id", f"{writer.id_prefix}cleanmic-file-{writer._next_clipitem_num}")
    _append_text(doc, file_el, "name", clean_mic_path.name)
    _append_text(doc, file_el, "pathurl", path_to_url(str(clean_mic_path)))
    _append_text(doc, file_el, "duration", str(duration_frames))

    # File-level media with audio characteristics (48kHz/16-bit is a safe
    # default; Premiere overrides from the actual file on import).
    media_el = doc.createElement("media")
    audio_media = doc.createElement("audio")
    samp = doc.createElement("samplecharacteristics")
    _append_text(doc, samp, "depth", "16")
    _append_text(doc, samp, "samplerate", "48000")
    audio_media.appendChild(samp)
    media_el.appendChild(audio_media)
    file_el.appendChild(media_el)

    clipitem.appendChild(file_el)
    track.appendChild(clipitem)
    _append_text(doc, track, "enabled", "TRUE")
    _append_text(doc, track, "locked", "FALSE")
    audio.appendChild(track)


def _append_synced_audio_tracks(
    doc: minidom.Document,
    seq_elem: minidom.Element,
    writer: FCPXMLWriter,
    cutlist,
    audio_sync_state,
) -> None:
    """Drop 3.6: build parallel audio tracks from sync state.

    For each A-roll phrase in the cutlist, looks up which lav files cover
    its source time range (with score >= 10 and geometric coverage). Each
    covering file's TrackGroup gets a persistent track slot; clipitems
    land on those tracks pre-cut and pre-offset so Premiere sees fully
    aligned audio on import — no manual sync required.
    """
    from precut_pipeline.audio_sync import find_covering_audio_for_phrase

    # Find the <audio> element directly under <media> (immediate child only)
    media = None
    for child in seq_elem.childNodes:
        if child.nodeType == child.ELEMENT_NODE and child.tagName == "media":
            media = child
            break
    if media is None:
        return
    audio_parent = None
    for child in media.childNodes:
        if child.nodeType == child.ELEMENT_NODE and child.tagName == "audio":
            audio_parent = child
            break
    if audio_parent is None:
        return

    # First pass: collect all coverage decisions + determine audio durations
    # Coverage decision per phrase: which group_ids cover it, and where.
    # group_id -> list of (phrase, covering_entry) for clipitems on this track
    group_clips: dict[str, list] = {}
    group_display: dict[str, str] = {}
    group_audio_files: dict[str, set[str]] = {}

    # Build group_id -> display_name lookup from state.groups
    for g in audio_sync_state.groups:
        group_display[g.group_id] = g.display_name

    # Drop 4.21: diagnostic — when a phrase gets no covering audio, log
    # every candidate pair we considered so we can diagnose mismatches.
    # This fires only for the All-Synced-A-Roll reference sequence
    # (phrase_id >= 2_000_000) to keep story-sequence exports quiet.
    import sys as _sys

    for phrase in cutlist.aroll_track:
        covs = find_covering_audio_for_phrase(
            phrase.source_file,
            phrase.source_start,
            phrase.source_end,
            audio_sync_state,
        )
        if not covs:
            # Diagnostic: emit details to stderr so backend.py surfaces it
            is_full_aroll = getattr(phrase, "phrase_id", 0) >= 2_000_000
            if is_full_aroll:
                from pathlib import Path as _P
                af_name = _P(phrase.source_file).name
                print(
                    f"AUDIO-SYNC DIAG: No coverage for {af_name} "
                    f"(phrase {phrase.source_start:.1f}-{phrase.source_end:.1f}s). "
                    f"Candidates:",
                    file=_sys.stderr,
                )
                for p in audio_sync_state.pairs:
                    if p.aroll_file != phrase.source_file:
                        continue
                    coverage_start = p.offset_sec
                    coverage_end = coverage_start + p.audio_duration_sec
                    reason = []
                    if p.score < 10.0:
                        reason.append(f"score {p.score:.1f} < 10")
                    if phrase.source_start < coverage_start:
                        reason.append(
                            f"phrase starts {phrase.source_start:.1f} before "
                            f"audio coverage {coverage_start:.1f}")
                    if phrase.source_end > coverage_end:
                        reason.append(
                            f"phrase ends {phrase.source_end:.1f} after "
                            f"audio coverage {coverage_end:.1f}")
                    status = "; ".join(reason) or "accepted"
                    print(
                        f"AUDIO-SYNC DIAG:   {_P(p.audio_file).name}: "
                        f"score={p.score:.1f} offset={p.offset_sec:.1f} "
                        f"coverage={coverage_start:.1f}-{coverage_end:.1f}  "
                        f"=> {status}",
                        file=_sys.stderr,
                    )
            continue
        # Drop 4.22: accept multiple files per group if they cover
        # NON-OVERLAPPING parts of the phrase (rollover handling). Prior
        # drops dedupe'd aggressively (one file per group per phrase)
        # because coverage was required to be complete — with partial
        # coverage we need both rollover files to cover the full phrase.
        #
        # Track per-group A-roll ranges already accepted; skip a cov only
        # if its A-roll window overlaps an already-accepted one.
        accepted_aroll_ranges: dict[str, list[tuple[float, float]]] = {}
        for cov in covs:
            aw_start = getattr(cov, "aroll_start_sec", phrase.source_start)
            aw_end = getattr(cov, "aroll_end_sec", phrase.source_end)
            existing = accepted_aroll_ranges.setdefault(cov.group_id, [])
            overlaps = any(
                aw_start < e and aw_end > s
                for (s, e) in existing
            )
            if overlaps:
                continue
            existing.append((aw_start, aw_end))
            group_clips.setdefault(cov.group_id, []).append((phrase, cov))
            group_audio_files.setdefault(cov.group_id, set()).add(cov.audio_file)

    # Stable track order: sort by display name so exports are deterministic
    ordered_groups = sorted(
        group_clips.keys(),
        key=lambda gid: group_display.get(gid, gid),
    )

    # For each group, build one Premiere audio track containing clipitems
    # for each phrase it covers.
    for track_index, group_id in enumerate(ordered_groups):
        entries = group_clips[group_id]
        if not entries:
            continue

        # Probe durations for each unique file in this group (we need these
        # per-clipitem to set the <file>'s duration correctly)
        audio_durations: dict[str, float] = {}
        for af in group_audio_files[group_id]:
            dur = _probe_audio_duration(Path(af))
            if dur is not None:
                audio_durations[af] = dur

        display = group_display.get(group_id, group_id)
        track = doc.createElement("track")
        track.setAttribute("TL.SQTrackAudioKeyframeStyle", "0")

        # Track-level metadata: name (shows in Premiere track header)
        # (FCP7 XML doesn't have a proper track-name slot — we encode it
        # via the first clipitem's name prefix instead; Premiere uses that.)

        clipitem_seq = 0
        file_id_cache: dict[str, str] = {}  # audio_file → FCP file id

        for phrase, cov in entries:
            clipitem_seq += 1
            _append_synced_clipitem(
                doc, track, writer,
                phrase, cov, display, group_id,
                track_index, clipitem_seq,
                audio_durations, file_id_cache,
            )

        _append_text(doc, track, "enabled", "TRUE")
        _append_text(doc, track, "locked", "FALSE")
        audio_parent.appendChild(track)


def _append_synced_clipitem(
    doc: minidom.Document,
    track: minidom.Element,
    writer: FCPXMLWriter,
    phrase,
    cov,
    group_display: str,
    group_id: str,
    track_index: int,
    seq_idx: int,
    audio_durations: dict[str, float],
    file_id_cache: dict[str, str],
) -> None:
    """Write one <clipitem> for a synced audio slice."""
    frame_rate = writer.frame_rate

    # Drop 4.22: use the overlap window from coverage (cov.aroll_start_sec /
    # cov.aroll_end_sec) rather than the full phrase window. For full-coverage
    # pairs these equal the phrase range; for partial coverage they're clamped
    # to the lav's actual recording window.
    # Convert A-roll time to timeline time: the phrase runs on the timeline
    # from phrase.timeline_start, starting at phrase.source_start in the
    # source. So:
    #   timeline_offset_sec = (aroll_time - phrase.source_start)
    #   timeline_pos_sec    = phrase.timeline_start + timeline_offset_sec
    aroll_start = getattr(cov, "aroll_start_sec", 0.0) or phrase.source_start
    aroll_end = getattr(cov, "aroll_end_sec", 0.0) or phrase.source_end
    tl_start_sec = phrase.timeline_start + (aroll_start - phrase.source_start)
    tl_end_sec = phrase.timeline_start + (aroll_end - phrase.source_start)

    start_frames = max(0, frame_rate.seconds_to_frames(tl_start_sec))
    end_frames = max(start_frames + 1,
                     frame_rate.seconds_to_frames(tl_end_sec))
    duration_frames = end_frames - start_frames
    in_frames = max(0, frame_rate.seconds_to_frames(cov.audio_start_sec))
    out_frames = max(in_frames + 1,
                     frame_rate.seconds_to_frames(cov.audio_end_sec))

    # Drop 4.19: Premiere requires (end - start) == (out - in) == duration.
    # Independent rounding can produce a 1-frame mismatch that causes the
    # whole XML import to silent-fail. Snap out to match timeline span.
    if (out_frames - in_frames) != duration_frames:
        out_frames = in_frames + duration_frames

    clipitem = doc.createElement("clipitem")
    clipitem.setAttribute("id", f"{writer.id_prefix}sync-{group_id}-{seq_idx}")

    # Drop 4.7: use the actual lav filename as the clipitem name so
    # Premiere's proxy-reconnect flow can match the clip to its source.
    # Previous convention was "DJI_06: phrase 1000000" — friendlier label
    # but opaque when the editor needs to find the source on disk.
    name = Path(cov.audio_file).name
    _append_text(doc, clipitem, "name", name)
    _append_text(doc, clipitem, "enabled", "TRUE")
    _append_text(doc, clipitem, "duration", str(duration_frames))

    rate_el = doc.createElement("rate")
    _append_text(doc, rate_el, "timebase", str(int(round(writer.sequence_fps))))
    _append_text(doc, rate_el, "ntsc", "TRUE" if frame_rate.ntsc else "FALSE")
    clipitem.appendChild(rate_el)

    _append_text(doc, clipitem, "start", str(start_frames))
    _append_text(doc, clipitem, "end", str(end_frames))
    _append_text(doc, clipitem, "in", str(in_frames))
    _append_text(doc, clipitem, "out", str(out_frames))

    # File reference — first clipitem referencing this file gets the full
    # <file> block; subsequent clipitems just reference the id. This is
    # FCP7's idiom for multiple cuts from the same source.
    audio_path = cov.audio_file

    # Drop 4.45.1: if this audio path is in the writer's master_clip_map
    # (populated by export_multi_timeline's pre-scan), emit a bare <file>
    # ref + <masterclipid> linking to the master in Audio/Source Audio/.
    # Without this, the lav clipitems mint their own per-sequence file IDs
    # and the Source Audio bin masters end up orphaned (visible in the bin
    # but not connected to any timeline usage). The masterclipid linkage is
    # what makes the bin master clickable as the source of the timeline clip.
    try:
        resolved_path = str(Path(audio_path).resolve())
    except Exception:
        resolved_path = audio_path
    mc_entry = (writer.master_clip_map.get(resolved_path)
                or writer.master_clip_map.get(audio_path))
    if mc_entry is not None:
        master_id, file_id = mc_entry
        _append_text(doc, clipitem, "masterclipid", master_id)
        file_el = doc.createElement("file")
        file_el.setAttribute("id", file_id)
        clipitem.appendChild(file_el)
    elif audio_path in file_id_cache:
        file_el = doc.createElement("file")
        file_el.setAttribute("id", file_id_cache[audio_path])
        clipitem.appendChild(file_el)
    else:
        file_id = f"{writer.id_prefix}sync-file-{group_id}-{seq_idx}"
        file_id_cache[audio_path] = file_id
        file_el = doc.createElement("file")
        file_el.setAttribute("id", file_id)
        _append_text(doc, file_el, "name", Path(audio_path).name)
        _append_text(doc, file_el, "pathurl", path_to_url(audio_path))
        # Duration = full audio file duration (Premiere uses this to know
        # the file's content length; our in/out points carve out the slice)
        file_dur_sec = audio_durations.get(audio_path, cov.audio_end_sec + 1.0)
        file_dur_frames = frame_rate.seconds_to_frames(file_dur_sec)
        _append_text(doc, file_el, "duration", str(file_dur_frames))

        # Audio characteristics (Premiere overrides from file on import,
        # but these need to be present or FCP7 complains)
        media_el = doc.createElement("media")
        audio_media = doc.createElement("audio")
        samp = doc.createElement("samplecharacteristics")
        _append_text(doc, samp, "depth", "16")
        _append_text(doc, samp, "samplerate", "48000")
        audio_media.appendChild(samp)
        media_el.appendChild(audio_media)
        file_el.appendChild(media_el)

        clipitem.appendChild(file_el)

    track.appendChild(clipitem)


def _append_text(doc, parent, tag, text):
    """Helper: append a <tag>text</tag> child."""
    el = doc.createElement(tag)
    el.appendChild(doc.createTextNode(str(text)))
    parent.appendChild(el)


def _probe_audio_duration(path: Path) -> Optional[float]:
    """Return the audio file's duration in seconds via ffprobe. None on error."""
    try:
        from audio_indexer import find_ffprobe, probe_audio
    except ImportError:
        return None
    ffprobe_bin = find_ffprobe()
    if not ffprobe_bin:
        return None
    try:
        info = probe_audio(path, ffprobe_bin)
        return info.duration_sec if info else None
    except Exception:
        return None
