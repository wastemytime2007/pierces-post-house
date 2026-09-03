"""Stage 4: Premiere XML (FCP7 xmeml) exporter with preset-aware dimensions.

Premiere reads FCP7 XML natively via File > Import. This module produces xmeml
that opens as a sequence configured correctly for the deliverable type:

  - Vertical presets (reels/tiktok) → 1080x1920 @ 30fps, IG Reels safe-zone overlay on V3
  - Horizontal presets (ads/youtube) → 1920x1080 @ 30fps, broadcast title-safe on V3
  - Square (feed) → 1080x1080 @ 30fps

A-roll and B-roll source files keep their native frame rate — Premiere handles
the rate conform when they land on a different-rate sequence.

When source aspect doesn't match sequence aspect (e.g., 1920x1080 A-roll on a
1080x1920 vertical sequence), each clip gets a scale+center filter applied so
it fills the frame rather than letterboxing. The editor can tweak these in the
Effect Controls panel.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import quote
from xml.dom import minidom

from .cutlist import CutList, ARollPhrase, BRollShot
from .overlay import get_overlay_path


# ------------------------------------------------------------
# Frame rate math
# ------------------------------------------------------------

@dataclass
class FrameRate:
    """FCP7 XML frame rate: integer timebase + ntsc flag."""
    timebase: int
    ntsc: bool

    @property
    def fps(self) -> float:
        return self.timebase * (1000 / 1001) if self.ntsc else float(self.timebase)

    def seconds_to_frames(self, seconds: float) -> int:
        return int(round(seconds * self.fps))


def detect_frame_rate(fps_hint: float) -> FrameRate:
    """Snap a measured fps to the nearest standard frame rate.

    NTSC rates (23.976, 29.97, 59.94, 119.88, 239.76) use a tight
    tolerance so that exact-integer fps like 24.0 or 30.0 don't get
    mis-classified as NTSC (which would flip the duration math and
    cause Premiere to mark the clip offline).
    """
    if fps_hint <= 0:
        return FrameRate(30, True)
    # NTSC rates: tight tolerance (< 0.015) around the exact 1000/1001 value
    if abs(fps_hint - 23.976) < 0.015:
        return FrameRate(24, True)
    if abs(fps_hint - 29.97) < 0.015:
        return FrameRate(30, True)
    if abs(fps_hint - 59.94) < 0.02:
        return FrameRate(60, True)
    if abs(fps_hint - 119.88) < 0.05:
        return FrameRate(120, True)
    if abs(fps_hint - 239.76) < 0.1:
        return FrameRate(240, True)
    # Exact integer rates (non-NTSC)
    for rate in (24, 25, 30, 48, 50, 60, 120, 240):
        if abs(fps_hint - rate) < 0.05:
            return FrameRate(rate, False)
    return FrameRate(int(round(fps_hint)), False)


# ------------------------------------------------------------
# Path → file URL
# ------------------------------------------------------------

def path_to_url(path: str) -> str:
    """Convert absolute path to a file:// URL Premiere understands."""
    abs_path = str(Path(path).resolve())
    encoded = quote(abs_path, safe="/")
    return f"file://localhost{encoded}"


# ------------------------------------------------------------
# Aspect fit math
# ------------------------------------------------------------

def fit_scale(source_w: int, source_h: int, target_w: int, target_h: int) -> float:
    """Compute the scale (0-1000) needed to make source FILL target.

    Uses the max of horizontal/vertical scale so the source fills the frame —
    potentially cropping the other dimension. Returns FCP7's 0-1000 range
    where 100 = original size.
    """
    if source_w <= 0 or source_h <= 0 or target_w <= 0 or target_h <= 0:
        return 100.0
    scale_x = target_w / source_w
    scale_y = target_h / source_h
    return max(scale_x, scale_y) * 100.0


def needs_fit_filter(source_w: int, source_h: int, target_w: int, target_h: int) -> bool:
    """True if source doesn't already match target dimensions."""
    if source_w <= 0 or source_h <= 0:
        return False
    return (source_w, source_h) != (target_w, target_h)


# ------------------------------------------------------------
# Defaults
# ------------------------------------------------------------

# We don't store source dimensions in the CutList. 1920x1080 is the safe
# default because most proxy workflows are 1920x1080 and it's symmetric
# to the horizontal sequence default.
ASSUMED_SOURCE_WIDTH = 1920
ASSUMED_SOURCE_HEIGHT = 1080


# ------------------------------------------------------------
# XML writer
# ------------------------------------------------------------

class FCPXMLWriter:
    """Builds an FCP7 xmeml document from a CutList."""

    def __init__(
        self,
        cutlist: CutList,
        sequence_name: Optional[str] = None,
        include_overlay: bool = True,
        source_width: int = ASSUMED_SOURCE_WIDTH,
        source_height: int = ASSUMED_SOURCE_HEIGHT,
    ):
        self.cutlist = cutlist
        self.sequence_fps = cutlist.sequence_fps
        self.frame_rate = detect_frame_rate(cutlist.sequence_fps)
        self.width = cutlist.sequence_width
        self.height = cutlist.sequence_height
        self.overlay_style = cutlist.overlay_style
        self.include_overlay = include_overlay and self.overlay_style != "none"
        self.source_width = source_width
        self.source_height = source_height
        self.sequence_name = sequence_name or (
            cutlist.deliverable_concept[:60] or "B-Roll Buddy Sequence"
        )

        self.doc = minidom.Document()
        self._file_ids: dict[str, str] = {}
        self._next_file_num = 1
        self._next_clipitem_num = 1

        # Drop 4.20: id_prefix scopes auto-generated IDs (clipitem-*, file-*,
        # anchor-*) per sequence so multiple sequences in one document don't
        # collide. Default empty for back-compat with single-sequence exports.
        # Multi-exporter sets this per-writer, e.g. "s1-" for seq 1, "s2-" etc.
        self.id_prefix = ""

        # Drop 4.23: when True, the A1 camera-audio clipitem is emitted with
        # <enabled>FALSE</enabled> so Premiere shows it muted by default.
        # Set by multi_exporter when sync audio (A2+) is being added.
        self.mute_camera_audio = False

        # Drop 3.6.2: mapping source_path → (masterclipid, file_id) for library
        # linking. When set (by multi_exporter), clipitems using these paths
        # get a <masterclipid> element AND reuse the library's file_id so
        # Premiere links timeline usages to the bin master clips. Without
        # this linkage Premiere may drop the library bin on import.
        self.master_clip_map: dict[str, tuple[str, str]] = {}

        # Drop 4.45.1: when True, suppress the disabled V2 library anchor
        # track. The anchor track was a Drop 3.8.2 hack to keep B-roll
        # masters from being pruned when the timeline didn't reference them.
        # In Drop 4.45 master clips live in proper bins (Footage/B-Roll/),
        # which keeps them alive without the hack. The anchor track now
        # CAUSES rejection: the new master_clip_map contains audio and image
        # masters too, and the anchor track tries to anchor those on V2 —
        # i.e., 1-frame VIDEO clipitems referencing audio-only or image-only
        # source files. Premiere refuses the import. Disabling the anchor
        # track fixes the rejection.
        self.skip_library_anchor_track = False

        # Drop 4.12: cache of probed dimensions by absolute source path.
        # Computed lazily per-clipitem when we build the fit filter.
        # Each A-roll file can have DIFFERENT dims (multiple cameras), so
        # a single project-wide "source_width/height" is wrong. We probe
        # the real source file once per distinct path and use its own
        # dimensions for the fit-filter scale calculation on each clipitem.
        self._source_dims_cache: dict[str, tuple[int, int]] = {}

    def build(self) -> str:
        xmeml = self.doc.createElement("xmeml")
        xmeml.setAttribute("version", "5")
        self.doc.appendChild(xmeml)
        xmeml.appendChild(self._build_sequence())

        xml_str = self.doc.toprettyxml(indent="\t", encoding="UTF-8").decode("UTF-8")
        lines = xml_str.splitlines()
        if lines and lines[0].startswith("<?xml"):
            lines.insert(1, "<!DOCTYPE xmeml>")
        else:
            lines.insert(0, "<!DOCTYPE xmeml>")
            lines.insert(0, '<?xml version="1.0" encoding="UTF-8"?>')
        return "\n".join(lines) + "\n"

    # --------------- Sequence ---------------

    def _build_sequence(self) -> minidom.Element:
        seq = self._elem("sequence", id=self._safe_id(self.sequence_name))
        seq.appendChild(self._text_elem("name", self.sequence_name))
        seq.appendChild(self._text_elem(
            "duration", str(self.frame_rate.seconds_to_frames(self.cutlist.total_duration))
        ))
        seq.appendChild(self._build_rate())
        seq.appendChild(self._text_elem("timecode", "", children=[
            self._build_rate(),
            self._text_elem("string", "00:00:00:00"),
            self._text_elem("frame", "0"),
            self._text_elem("displayformat", "NDF" if self.frame_rate.ntsc else "DF"),
        ]))

        media = self._elem("media")
        media.appendChild(self._build_video())
        media.appendChild(self._build_audio())
        seq.appendChild(media)

        # Drop 3.7: sequence markers replace V2 B-roll clips. Each marker
        # represents a suggested spot where the editor should drop a B-roll
        # clip. Name = category + primary tags. Comment = full tag list
        # (editor copies it into the bin search bar to filter the library).
        for marker_el in self._build_markers():
            seq.appendChild(marker_el)

        return seq

    def _build_rate(self) -> minidom.Element:
        rate = self._elem("rate")
        rate.appendChild(self._text_elem("ntsc", "TRUE" if self.frame_rate.ntsc else "FALSE"))
        rate.appendChild(self._text_elem("timebase", str(self.frame_rate.timebase)))
        return rate

    # --------------- Video tracks ---------------

    def _build_video(self) -> minidom.Element:
        video = self._elem("video")

        format_elem = self._elem("format")
        scs = self._elem("samplecharacteristics")
        scs.appendChild(self._build_rate())
        scs.appendChild(self._text_elem("width", str(self.width)))
        scs.appendChild(self._text_elem("height", str(self.height)))
        scs.appendChild(self._text_elem("pixelaspectratio", "square"))
        scs.appendChild(self._text_elem("fielddominance", "none"))
        format_elem.appendChild(scs)
        video.appendChild(format_elem)

        # V1 — A-roll
        video.appendChild(self._build_aroll_video_track())
        # V2 — B-roll (Drop 3.7+: skip this entirely if the cutlist uses
        # markers-only mode, i.e. broll_track is empty. An empty track
        # imports fine but shows up as a confusing blank track in Premiere;
        # omitting it keeps the sequence tidy.)
        if self.cutlist.broll_track:
            video.appendChild(self._build_broll_video_track())
        # Library anchor track (Drop 3.8.2): when markers replace V2 B-roll
        # clips, no timeline clipitem references the library masterclips, so
        # Premiere drops them as orphans on import. This track holds one
        # disabled 1-frame clipitem per library clip, purely to keep the
        # masterclipid references alive. It's below V1, disabled, and locked,
        # so it stays out of the editor's way.
        # Drop 4.45.1: skip when multi_exporter sets skip_library_anchor_track
        # (the new bin structure keeps masters alive without this hack, and
        # leaving it on causes import failure when master_clip_map contains
        # audio/image masters — see __init__ comment).
        if not getattr(self, "skip_library_anchor_track", False):
            anchor_track = self._build_library_anchor_track()
            if anchor_track is not None:
                video.appendChild(anchor_track)
        # Overlay — safe-zone overlay
        if self.include_overlay:
            overlay_track = self._build_overlay_track()
            if overlay_track is not None:
                video.appendChild(overlay_track)

        return video

    def _build_aroll_video_track(self) -> minidom.Element:
        track = self._elem("track")
        for phrase in self.cutlist.aroll_track:
            track.appendChild(self._build_aroll_clipitem(phrase))
        track.appendChild(self._text_elem("enabled", "TRUE"))
        track.appendChild(self._text_elem("locked", "FALSE"))
        return track

    def _build_broll_video_track(self) -> minidom.Element:
        track = self._elem("track")
        for shot in self.cutlist.broll_track:
            track.appendChild(self._build_broll_clipitem(shot))
        track.appendChild(self._text_elem("enabled", "TRUE"))
        track.appendChild(self._text_elem("locked", "FALSE"))
        return track

    # --------------- Library anchor track (Drop 3.8.2) ---------------

    def _build_library_anchor_track(self) -> Optional[minidom.Element]:
        """Build a disabled, locked track that anchors library masterclipids.

        When Drop 3.7 replaced V2 B-roll clipitems with sequence markers,
        it left the library master clips with no timeline usage anywhere in
        the XML. Premiere's importer silently drops master clips that aren't
        referenced by any clipitem, so the entire B-Roll Library bin
        vanished from the project panel — the symptom Pierce reported as
        "no broll folder pulled in."

        Fix: emit one 1-frame <clipitem> per library master at frame 0 on a
        disabled, locked track. The clipitems each carry a <masterclipid>
        reference back to the library's master, which is enough to convince
        Premiere the masters are "used." The track is disabled so nothing
        plays through, locked so the editor can't accidentally move the
        anchors, and lives at frame 0 so it's out of the way of real content.

        Returns None if there's no master_clip_map (e.g., when called from
        a single-sequence test that doesn't involve the library bin).
        """
        if not self.master_clip_map:
            return None

        # De-duplicate: master_clip_map has multiple path keys (proxy AND
        # original) mapping to the same (master_id, file_id) pair. We only
        # want one anchor clipitem per UNIQUE master.
        unique_ids: dict[str, str] = {}  # master_id -> file_id
        for (master_id, file_id) in self.master_clip_map.values():
            if master_id not in unique_ids:
                unique_ids[master_id] = file_id

        if not unique_ids:
            return None

        track = self._elem("track")
        for master_id, file_id in unique_ids.items():
            track.appendChild(self._build_library_anchor_clipitem(master_id, file_id))
        # Disabled so it doesn't render in the sequence. Locked so the editor
        # can't nudge the anchors off frame 0 and break the linkage.
        track.appendChild(self._text_elem("enabled", "FALSE"))
        track.appendChild(self._text_elem("locked", "TRUE"))
        return track

    def _build_library_anchor_clipitem(
        self, master_id: str, file_id: str,
    ) -> minidom.Element:
        """One-frame placeholder clipitem that references a library master.

        The only purpose is to keep the master from being pruned on import.
        We stack all anchors at frame 0 (start == 0, end == 1) so they don't
        occupy any usable timeline region. Premiere treats a stack of
        1-frame clips at the same position on a disabled track as a no-op.
        """
        clipitem = self._elem("clipitem")
        clipitem.setAttribute("id", f"{self.id_prefix}anchor-{self._next_clipitem_num}")
        self._next_clipitem_num += 1

        clipitem.appendChild(self._text_elem("name", f"library-anchor-{master_id}"))
        # Enabled FALSE at clipitem level too, belt-and-suspenders.
        clipitem.appendChild(self._text_elem("enabled", "FALSE"))
        clipitem.appendChild(self._text_elem("duration", "1"))

        # Rate must match the sequence timebase
        clipitem.appendChild(self._build_rate())

        # All anchors are 1-frame at position 0 on the timeline
        clipitem.appendChild(self._text_elem("start", "0"))
        clipitem.appendChild(self._text_elem("end", "1"))
        clipitem.appendChild(self._text_elem("in", "0"))
        clipitem.appendChild(self._text_elem("out", "1"))

        # The critical piece: this binds the anchor to a library master clip.
        clipitem.appendChild(self._text_elem("masterclipid", master_id))

        # File reference — a bare id-only reference so Premiere uses the
        # file block already declared in the library bin. No media, no
        # pathurl: we don't need to re-describe the file here.
        file_el = self._elem("file")
        file_el.setAttribute("id", file_id)
        clipitem.appendChild(file_el)

        return clipitem



    def _build_markers(self) -> list[minidom.Element]:
        """Build sequence-level <marker> elements for B-roll suggestions.

        FCP7 marker schema (Premiere imports these natively):
          <marker>
            <name>Kitchen: marble, counter, island</name>
            <comment>B-roll suggestion. Copy one of these tags...</comment>
            <in>480</in>
            <out>-1</out>   <!-- -1 means a single-frame point marker -->
            <color>
              <alpha>255</alpha><red>255</red><green>140</green><blue>0</blue>
            </color>
          </marker>
        """
        # Import inside the method to avoid widening the module's import
        # graph; these helpers are only needed here.
        from .markers import format_marker_name, format_marker_comment

        elements: list[minidom.Element] = []

        # Drop 4.0: Creative Brief marker at frame 0 (locked, name+comment
        # describe the angle). Editor reads this to understand the pitch
        # before touching the timeline.
        brief = getattr(self.cutlist, "creative_brief", None)
        if brief is not None:
            brief_el = self._build_creative_brief_marker(brief)
            if brief_el is not None:
                elements.append(brief_el)

        markers = getattr(self.cutlist, "broll_markers", None) or []
        for marker in markers:
            # Drop 4.0: markers with attach_to_phrase_id are emitted INSIDE
            # the phrase's clipitem (see _build_aroll_clipitem). Skip them
            # here at the sequence level.
            if getattr(marker, "attach_to_phrase_id", None) is not None:
                continue

            frame_in = self.frame_rate.seconds_to_frames(marker.timeline_time)
            # Clamp into [0, total_duration_frames - 1] so Premiere accepts it.
            total_frames = self.frame_rate.seconds_to_frames(self.cutlist.total_duration)
            if total_frames > 0:
                frame_in = max(0, min(frame_in, total_frames - 1))

            m_el = self._elem("marker")
            m_el.appendChild(self._text_elem("name", format_marker_name(marker)))
            m_el.appendChild(self._text_elem("comment", format_marker_comment(marker)))
            m_el.appendChild(self._text_elem("in", str(frame_in)))
            # out=-1 means "point marker with no duration"
            m_el.appendChild(self._text_elem("out", "-1"))

            # Color block — Premiere respects FCP7 color on markers
            r, g, b = marker.color_rgb
            color_el = self._elem("color")
            color_el.appendChild(self._text_elem("alpha", "255"))
            color_el.appendChild(self._text_elem("red", str(int(r))))
            color_el.appendChild(self._text_elem("green", str(int(g))))
            color_el.appendChild(self._text_elem("blue", str(int(b))))
            m_el.appendChild(color_el)

            elements.append(m_el)

        elements.extend(self._build_flag_marker_elements())
        return elements

    def _build_marker_element(self, name: str, comment: str, frame_in: int,
                               frame_out: int, color_rgb) -> minidom.Element:
        """Shared <marker> builder for both point markers (frame_out=-1,
        BRollMarker's convention) and real-duration range markers
        (FlagMarker's convention, 2026-09-03)."""
        m_el = self._elem("marker")
        m_el.appendChild(self._text_elem("name", name))
        m_el.appendChild(self._text_elem("comment", comment))
        m_el.appendChild(self._text_elem("in", str(frame_in)))
        m_el.appendChild(self._text_elem("out", str(frame_out)))

        r, g, b = color_rgb
        color_el = self._elem("color")
        color_el.appendChild(self._text_elem("alpha", "255"))
        color_el.appendChild(self._text_elem("red", str(int(r))))
        color_el.appendChild(self._text_elem("green", str(int(g))))
        color_el.appendChild(self._text_elem("blue", str(int(b))))
        m_el.appendChild(color_el)
        return m_el

    def _build_flag_marker_elements(self) -> list[minidom.Element]:
        """Sequence-level <marker> elements for transcript-flagging range
        markers (FlagMarker) that aren't attached to a specific phrase.
        Real <out> frame, not the -1 point-marker convention BRollMarker
        uses — the whole point is a colored block spanning the flagged
        fragment's actual duration."""
        elements: list[minidom.Element] = []
        total_frames = self.frame_rate.seconds_to_frames(self.cutlist.total_duration)

        for marker in getattr(self.cutlist, "flag_markers", None) or []:
            if getattr(marker, "attach_to_phrase_id", None) is not None:
                continue
            frame_in = self.frame_rate.seconds_to_frames(marker.timeline_start)
            frame_out = self.frame_rate.seconds_to_frames(marker.timeline_end)
            if total_frames > 0:
                frame_in = max(0, min(frame_in, total_frames - 1))
                frame_out = max(frame_in + 1, min(frame_out, total_frames - 1))
            elements.append(self._build_marker_element(
                marker.name, marker.comment, frame_in, frame_out, marker.color_rgb,
            ))
        return elements

    def _build_attached_flag_marker_elements(self, phrase: ARollPhrase) -> list[minidom.Element]:
        """Clipitem-level <marker> elements for FlagMarkers attached to
        this phrase — same clip-relative-offset convention
        `_build_attached_markers` uses for BRollMarker, but with a real
        <out> frame instead of -1."""
        elements: list[minidom.Element] = []
        clip_frames = self.frame_rate.seconds_to_frames(
            max(0.01, phrase.source_end - phrase.source_start)
        )

        for marker in getattr(self.cutlist, "flag_markers", None) or []:
            if getattr(marker, "attach_to_phrase_id", None) != phrase.phrase_id:
                continue
            in_offset_sec = max(0.0, marker.timeline_start - phrase.timeline_start)
            out_offset_sec = max(0.0, marker.timeline_end - phrase.timeline_start)
            frame_in = self.frame_rate.seconds_to_frames(in_offset_sec)
            frame_out = self.frame_rate.seconds_to_frames(out_offset_sec)
            if clip_frames > 0:
                frame_in = max(0, min(frame_in, clip_frames - 1))
                frame_out = max(frame_in + 1, min(frame_out, clip_frames - 1))
            elements.append(self._build_marker_element(
                marker.name, marker.comment, frame_in, frame_out, marker.color_rgb,
            ))
        return elements

    def _build_creative_brief_marker(self, brief) -> Optional[minidom.Element]:
        """Drop 4.0: emit a frame-0 sequence marker with the Creative Brief.

        The name is the angle title so the editor sees "Box Truck Economics"
        at the start of the sequence. The comment holds the hook, tone,
        why-it-works, and audience — a complete creative brief the editor
        can read before making cuts.
        """
        if not getattr(brief, "title", None):
            return None

        lines: list[str] = []
        if brief.hook:
            lines.append(f"HOOK: {brief.hook}")
        if brief.tone:
            lines.append(f"TONE: {brief.tone}")
        if brief.target_audience:
            lines.append(f"AUDIENCE: {brief.target_audience}")
        if brief.why_it_works:
            lines.append(f"WHY IT WORKS: {brief.why_it_works}")
        if brief.call_to_action:
            lines.append(f"CTA: {brief.call_to_action}")
        if brief.target_duration_sec:
            lines.append(f"TARGET LENGTH: ~{brief.target_duration_sec:.0f}s")
        comment_text = "\n\n".join(lines) if lines else brief.title

        m_el = self._elem("marker")
        m_el.appendChild(self._text_elem("name", f"\u2605 BRIEF: {brief.title}"))
        m_el.appendChild(self._text_elem("comment", comment_text))
        m_el.appendChild(self._text_elem("in", "0"))
        m_el.appendChild(self._text_elem("out", "-1"))

        # Gold color for the brief marker so it visually stands out from
        # the category-colored B-roll suggestion markers.
        color_el = self._elem("color")
        color_el.appendChild(self._text_elem("alpha", "255"))
        color_el.appendChild(self._text_elem("red", "255"))
        color_el.appendChild(self._text_elem("green", "200"))
        color_el.appendChild(self._text_elem("blue", "0"))
        m_el.appendChild(color_el)

        return m_el

    def _build_overlay_track(self) -> Optional[minidom.Element]:
        # If caller (e.g. multi_exporter) has copied the overlay to the
        # export folder and set an override, use that. Otherwise fall back
        # to the bundle path.
        overlay_path = getattr(self, "overlay_path_override", None)
        if overlay_path is None:
            overlay_path = get_overlay_path(self.overlay_style)  # type: ignore
        if overlay_path is None:
            return None

        track = self._elem("track")
        track.appendChild(self._build_overlay_clipitem(overlay_path))
        track.appendChild(self._text_elem("enabled", "TRUE"))
        track.appendChild(self._text_elem("locked", "FALSE"))
        return track

    # --------------- Clipitems ---------------

    def _probe_clip_dims(self, source_path: str) -> tuple[int, int]:
        """Probe the actual dimensions of a source file via ffprobe.

        Results are cached on the writer so the same file is only probed
        once per export. Falls back to self.source_width/source_height if
        the probe fails (e.g. ffmpeg missing).
        """
        abs_path = str(Path(source_path).resolve())
        if abs_path in self._source_dims_cache:
            return self._source_dims_cache[abs_path]

        dims: Optional[tuple[int, int]] = None
        try:
            import shutil
            import subprocess
            import json as _json
            ffprobe = shutil.which("ffprobe")
            if not ffprobe:
                # Homebrew typical location
                for candidate in ("/opt/homebrew/bin/ffprobe",
                                  "/usr/local/bin/ffprobe"):
                    if Path(candidate).exists():
                        ffprobe = candidate
                        break
            if ffprobe and Path(abs_path).exists():
                result = subprocess.run(
                    [ffprobe, "-v", "error", "-select_streams", "v:0",
                     "-show_entries", "stream=width,height",
                     "-of", "json", abs_path],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    data = _json.loads(result.stdout or "{}")
                    streams = data.get("streams", [])
                    if streams and streams[0].get("width") and streams[0].get("height"):
                        dims = (int(streams[0]["width"]), int(streams[0]["height"]))
        except Exception:
            pass

        if dims is None:
            dims = (self.source_width, self.source_height)

        self._source_dims_cache[abs_path] = dims
        return dims

    def _build_aroll_clipitem(self, phrase: ARollPhrase) -> minidom.Element:
        # Drop 4.7: use the actual source filename as the clipitem name.
        # Prior drops invented names like "Topic range @ 3:57 (67s)" or
        # "A-roll phrase 303" which were friendlier to read but broke
        # Premiere's proxy attach/reconnect flow — the editor needs the
        # clipitem name to match the file stem to manage proxies.
        name = Path(phrase.source_file).name

        ci = self._build_clipitem(
            name=name,
            source_path=phrase.source_file,
            source_in_sec=phrase.source_start,
            source_out_sec=phrase.source_end,
            timeline_in_sec=phrase.timeline_start,
            timeline_out_sec=phrase.timeline_end,
        )

        # Drop 4.12: fit filter now uses THIS clip's actual source file
        # dimensions (probed via ffprobe), not a project-wide default.
        # This fixes the "auto-scale to 355%" bug where the writer was
        # using the first B-roll clip's dims as if they were A-roll dims.
        src_w, src_h = self._probe_clip_dims(phrase.source_file)
        if needs_fit_filter(src_w, src_h, self.width, self.height):
            scale = fit_scale(src_w, src_h, self.width, self.height)
            ci.appendChild(self._build_motion_filter(scale=scale))

        # Drop 4.0: emit B-roll markers attached to this phrase INSIDE its
        # clipitem. The markers move with the clip when the editor rearranges
        # the timeline — exactly what Pierce asked for.
        for marker_el in self._build_attached_markers(phrase):
            ci.appendChild(marker_el)
        for marker_el in self._build_attached_flag_marker_elements(phrase):
            ci.appendChild(marker_el)

        return ci

    def _build_attached_markers(self, phrase: ARollPhrase) -> list[minidom.Element]:
        """Build <marker> elements for any B-roll markers attached to this
        phrase. Marker in-point is expressed RELATIVE TO THE CLIP's in-point,
        which is how FCP7 clipitem-level markers work.

        When the editor drags the clipitem to a different timeline position,
        Premiere re-renders the marker relative to the clip's in-point, so
        the marker stays glued to the same frame of the source phrase.
        """
        from .markers import format_marker_name, format_marker_comment

        elements: list[minidom.Element] = []
        markers = getattr(self.cutlist, "broll_markers", None) or []

        # Clip-local frame range: 0 .. (source_end - source_start) in frames
        clip_frames = self.frame_rate.seconds_to_frames(
            max(0.01, phrase.source_end - phrase.source_start)
        )

        for marker in markers:
            attach = getattr(marker, "attach_to_phrase_id", None)
            if attach != phrase.phrase_id:
                continue

            # Convert the marker's sequence-timeline offset into a
            # clip-relative offset (seconds into the clipitem).
            clip_offset_sec = marker.timeline_time - phrase.timeline_start
            clip_offset_sec = max(0.0, clip_offset_sec)
            frame_in = self.frame_rate.seconds_to_frames(clip_offset_sec)
            # Clamp so Premiere accepts it
            if clip_frames > 0:
                frame_in = max(0, min(frame_in, clip_frames - 1))

            m_el = self._elem("marker")
            m_el.appendChild(self._text_elem("name", format_marker_name(marker)))
            m_el.appendChild(self._text_elem("comment", format_marker_comment(marker)))
            m_el.appendChild(self._text_elem("in", str(frame_in)))
            m_el.appendChild(self._text_elem("out", "-1"))

            r, g, b = marker.color_rgb
            color_el = self._elem("color")
            color_el.appendChild(self._text_elem("alpha", "255"))
            color_el.appendChild(self._text_elem("red", str(int(r))))
            color_el.appendChild(self._text_elem("green", str(int(g))))
            color_el.appendChild(self._text_elem("blue", str(int(b))))
            m_el.appendChild(color_el)

            elements.append(m_el)

        return elements

    def _build_broll_clipitem(self, shot: BRollShot) -> minidom.Element:
        ci = self._build_clipitem(
            name=f"B-roll: {Path(shot.source_file).stem}",
            source_path=shot.source_file,
            source_in_sec=shot.source_start,
            source_out_sec=shot.source_end,
            timeline_in_sec=shot.timeline_start,
            timeline_out_sec=shot.timeline_end,
        )
        # Drop 4.12: probe this B-roll's actual dims, same as A-roll path.
        src_w, src_h = self._probe_clip_dims(shot.source_file)
        if needs_fit_filter(src_w, src_h, self.width, self.height):
            scale = fit_scale(src_w, src_h, self.width, self.height)
            ci.appendChild(self._build_motion_filter(scale=scale))
        return ci

    def _build_overlay_clipitem(self, overlay_path: Path) -> minidom.Element:
        """Drop 4.8: FCP7-compliant still-image overlay clipitem.

        PNG files are stills. The FCP7 spec lists <in> and <out> as OPTIONAL
        on clipitems — for stills, they should be omitted entirely rather
        than given synthetic values. Previous attempts at <in>-1</in> and
        <in>0</in> both confused Premiere's FCP Translator.

        Correct still-image clipitem shape:
          <clipitem>
            <name/>
            <enabled>TRUE</enabled>
            <duration/>        — timeline span in frames
            <rate/>
            <start/> <end/>    — timeline position
            <file/>            — with stillframe-compatible <duration>
            <stillframe>TRUE</stillframe>   — AFTER <file> per spec order
          </clipitem>

        No <in>/<out> on the clipitem. No source seeking at all.
        """
        fr = self.frame_rate
        clipitem_id = f"{self.id_prefix}clipitem-{self._next_clipitem_num}"
        self._next_clipitem_num += 1

        ci = self._elem("clipitem", id=clipitem_id)
        ci.appendChild(self._text_elem("name", "SAFE ZONE OVERLAY (disable before export)"))
        ci.appendChild(self._text_elem("enabled", "TRUE"))

        timeline_start_frames = fr.seconds_to_frames(0.0)
        timeline_end_frames = max(timeline_start_frames + 1,
                                   fr.seconds_to_frames(self.cutlist.total_duration))
        timeline_duration = timeline_end_frames - timeline_start_frames

        ci.appendChild(self._text_elem("duration", str(timeline_duration)))
        ci.appendChild(self._build_rate())
        ci.appendChild(self._text_elem("start", str(timeline_start_frames)))
        ci.appendChild(self._text_elem("end", str(timeline_end_frames)))

        # File block with spec-compliant still-image structure
        ci.appendChild(self._build_image_file_ref(str(overlay_path)))

        # Drop 4.11: Screen blend mode treats pure black as transparent
        # during compositing, so we don't need real alpha in the PNG file.
        # Per FCP7 spec, valid compositemode values are: normal, add,
        # subtract, difference, multiply, screen, texturize, hardlight,
        # softlight, darken, lighten, mask, lumamask.
        ci.appendChild(self._text_elem("compositemode", "screen"))

        # Per FCP7 element-order spec, <stillframe> sits near the end of
        # the clipitem (after file, filter). This is what Apple's own XML
        # emits for imported still images.
        ci.appendChild(self._text_elem("stillframe", "TRUE"))

        return ci

    def _build_image_file_ref(self, source_path: str) -> minidom.Element:
        """Build a <file> block for a still image (PNG/JPG) overlay.

        Includes <anamorphic>, <fielddominance>, and <timecode> that
        Premiere's FCP Translator expects on file blocks. Without these
        the still may import as "Media offline" even if the pathurl
        resolves correctly.
        """
        abs_path = str(Path(source_path).resolve())

        # Drop 4.45.1: if this overlay path is in master_clip_map (the
        # multi_exporter pre-scan registers overlays there), emit a bare
        # <file id="X"/> reference. The master clip in Files/Overlays/
        # declares the file fully. This avoids the case where the sequence
        # would otherwise inline a full file block with a path that may
        # not match the master clip's path (e.g., bundle-path leaks past
        # the overlay_path_override mechanism).
        if abs_path in self.master_clip_map:
            _, library_file_id = self.master_clip_map[abs_path]
            self._file_ids[abs_path] = library_file_id
            return self._elem("file", id=library_file_id)
        if source_path in self.master_clip_map:
            _, library_file_id = self.master_clip_map[source_path]
            self._file_ids[abs_path] = library_file_id
            return self._elem("file", id=library_file_id)

        if abs_path in self._file_ids:
            return self._elem("file", id=self._file_ids[abs_path])

        file_id = f"{self.id_prefix}file-{self._next_file_num}"
        self._next_file_num += 1
        self._file_ids[abs_path] = file_id

        file_elem = self._elem("file", id=file_id)
        file_elem.appendChild(self._text_elem("name", Path(abs_path).name))
        file_elem.appendChild(self._text_elem("pathurl", path_to_url(abs_path)))
        file_elem.appendChild(self._build_rate())
        # 108000 frames @ 30fps = 1hr sentinel; effectively "stretch as needed"
        file_elem.appendChild(self._text_elem("duration", "108000"))

        # FCP7 file blocks expect a <timecode> element. For stills this is
        # just a 00:00:00:00 anchor — it's required even if meaningless.
        timecode = self._elem("timecode")
        timecode.appendChild(self._build_rate())
        timecode.appendChild(self._text_elem("string", "00:00:00:00"))
        timecode.appendChild(self._text_elem("frame", "0"))
        timecode.appendChild(self._text_elem("displayformat", "NDF"))
        file_elem.appendChild(timecode)

        media = self._elem("media")
        video_media = self._elem("video")
        vscs = self._elem("samplecharacteristics")
        vscs.appendChild(self._build_rate())
        vscs.appendChild(self._text_elem("width", str(self.width)))
        vscs.appendChild(self._text_elem("height", str(self.height)))
        vscs.appendChild(self._text_elem("anamorphic", "FALSE"))
        vscs.appendChild(self._text_elem("pixelaspectratio", "square"))
        vscs.appendChild(self._text_elem("fielddominance", "none"))
        video_media.appendChild(vscs)
        media.appendChild(video_media)
        file_elem.appendChild(media)

        return file_elem

    def _build_clipitem(
        self,
        name: str,
        source_path: str,
        source_in_sec: float,
        source_out_sec: float,
        timeline_in_sec: float,
        timeline_out_sec: float,
        is_audio: bool = False,
        is_image: bool = False,
        enabled: bool = True,
    ) -> minidom.Element:
        fr = self.frame_rate
        clipitem_id = f"{self.id_prefix}clipitem-{self._next_clipitem_num}"
        self._next_clipitem_num += 1

        ci = self._elem("clipitem", id=clipitem_id)
        ci.appendChild(self._text_elem("name", name))
        # Drop 4.23: emit <enabled> so callers can disable A1 camera audio
        # while keeping V1 video and A2+ sync audio enabled. Premiere shows
        # a disabled clipitem as muted/transparent but preserves it; the
        # editor can re-enable with one click if they want the camera
        # audio back (e.g. as a fallback if the lav is bad).
        ci.appendChild(self._text_elem("enabled", "TRUE" if enabled else "FALSE"))

        # Drop 4.19: compute frame boundaries FIRST, then derive duration
        # from the boundaries. Premiere's FCP7 importer is strict: it
        # requires duration == (end - start) == (out - in). In Drop 4.18
        # we were rounding each quantity independently, which could give
        # 4404 vs 4405 (off-by-one) and silently fail the import.
        start_f = fr.seconds_to_frames(timeline_in_sec)
        end_f   = fr.seconds_to_frames(timeline_out_sec)
        in_f    = fr.seconds_to_frames(source_in_sec)
        out_f   = fr.seconds_to_frames(source_out_sec)

        # Timeline span is authoritative. If source range disagrees by a
        # frame (independent rounding), snap source to match timeline so
        # duration == end-start == out-in. For ARoll clips source span
        # should always equal timeline span (pre-aligned by the builder).
        timeline_span = max(1, end_f - start_f)
        source_span   = max(1, out_f - in_f)
        if source_span != timeline_span:
            # Keep source_in fixed, pull source_out to match timeline span.
            out_f = in_f + timeline_span

        duration_frames = timeline_span

        ci.appendChild(self._text_elem("duration", str(duration_frames)))
        ci.appendChild(self._build_rate())
        ci.appendChild(self._text_elem("start", str(start_f)))
        ci.appendChild(self._text_elem("end", str(end_f)))
        ci.appendChild(self._text_elem("in", str(in_f)))
        ci.appendChild(self._text_elem("out", str(out_f)))

        # Drop 3.6.2: if this source path matches a library master clip, add
        # masterclipid to establish the affiliate relationship. Premiere
        # needs this link to display the library bin contents properly.
        abs_path = str(Path(source_path).resolve())
        if abs_path in self.master_clip_map:
            master_id, _ = self.master_clip_map[abs_path]
            ci.appendChild(self._text_elem("masterclipid", master_id))

        ci.appendChild(self._build_file_ref(source_path, is_audio=is_audio, is_image=is_image))
        return ci

    def _build_file_ref(
        self, source_path: str,
        is_audio: bool = False,
        is_image: bool = False,
    ) -> minidom.Element:
        abs_path = str(Path(source_path).resolve())

        # Drop 3.6.2: if this file exists in the library, reuse its file_id
        # and emit just a reference (not a full <file> block). This is FCP7's
        # idiom for "same file used multiple places." Premiere uses this to
        # deduplicate master clips across the bin and timeline.
        if abs_path in self.master_clip_map:
            _, library_file_id = self.master_clip_map[abs_path]
            # Track it so subsequent clipitems in this sequence also reference
            # by ID only — don't re-emit the <file> block multiple times
            self._file_ids[abs_path] = library_file_id
            return self._elem("file", id=library_file_id)

        if abs_path in self._file_ids:
            return self._elem("file", id=self._file_ids[abs_path])

        file_id = f"{self.id_prefix}file-{self._next_file_num}"
        self._next_file_num += 1
        self._file_ids[abs_path] = file_id

        file_elem = self._elem("file", id=file_id)
        file_elem.appendChild(self._text_elem("name", Path(abs_path).name))
        file_elem.appendChild(self._text_elem("pathurl", path_to_url(abs_path)))
        file_elem.appendChild(self._build_rate())

        media = self._elem("media")
        video_media = self._elem("video")
        vscs = self._elem("samplecharacteristics")
        if is_image:
            vscs.appendChild(self._text_elem("width", str(self.width)))
            vscs.appendChild(self._text_elem("height", str(self.height)))
        else:
            # Drop 4.12: use this file's actual probed dimensions so the
            # <samplecharacteristics> reflects reality. Premiere reads
            # these when interpreting timeline motion/scale — if they're
            # wrong (e.g. proxy dims stamped on an original file), the
            # clip renders at the wrong size.
            file_w, file_h = self._probe_clip_dims(source_path)
            vscs.appendChild(self._text_elem("width", str(file_w)))
            vscs.appendChild(self._text_elem("height", str(file_h)))
        video_media.appendChild(vscs)
        media.appendChild(video_media)

        if not is_image:
            audio_media = self._elem("audio")
            audio_media.appendChild(self._text_elem("channelcount", "2"))
            media.appendChild(audio_media)

        file_elem.appendChild(media)
        return file_elem

    # --------------- Motion filter (auto scale+center) ---------------

    def _build_motion_filter(
        self,
        scale: float = 100.0,
        center_x: float = 0.0,
        center_y: float = 0.0,
    ) -> minidom.Element:
        """Basic Motion filter — Premiere reads this as a Transform effect."""
        filter_elem = self._elem("filter")
        effect = self._elem("effect")
        effect.appendChild(self._text_elem("name", "Basic Motion"))
        effect.appendChild(self._text_elem("effectid", "basic"))
        effect.appendChild(self._text_elem("effectcategory", "motion"))
        effect.appendChild(self._text_elem("effecttype", "motion"))
        effect.appendChild(self._text_elem("mediatype", "video"))

        param_scale = self._elem("parameter")
        param_scale.appendChild(self._text_elem("parameterid", "scale"))
        param_scale.appendChild(self._text_elem("name", "Scale"))
        param_scale.appendChild(self._text_elem("valuemin", "0"))
        param_scale.appendChild(self._text_elem("valuemax", "1000"))
        param_scale.appendChild(self._text_elem("value", f"{scale:.2f}"))
        effect.appendChild(param_scale)

        param_center = self._elem("parameter")
        param_center.appendChild(self._text_elem("parameterid", "center"))
        param_center.appendChild(self._text_elem("name", "Center"))
        value_elem = self._elem("value")
        value_elem.appendChild(self._text_elem("horiz", f"{center_x:.4f}"))
        value_elem.appendChild(self._text_elem("vert", f"{center_y:.4f}"))
        param_center.appendChild(value_elem)
        effect.appendChild(param_center)

        filter_elem.appendChild(effect)
        return filter_elem

    # --------------- Audio ---------------

    def _build_audio(self) -> minidom.Element:
        audio = self._elem("audio")
        format_elem = self._elem("format")
        scs = self._elem("samplecharacteristics")
        scs.appendChild(self._text_elem("depth", "16"))
        scs.appendChild(self._text_elem("samplerate", "48000"))
        format_elem.appendChild(scs)
        audio.appendChild(format_elem)

        a1 = self._elem("track")
        for phrase in self.cutlist.aroll_track:
            a1.appendChild(self._build_aroll_audio_clipitem(phrase))
        a1.appendChild(self._text_elem("enabled", "TRUE"))
        a1.appendChild(self._text_elem("locked", "FALSE"))
        audio.appendChild(a1)
        return audio

    def _build_aroll_audio_clipitem(self, phrase: ARollPhrase) -> minidom.Element:
        # Drop 4.7: use the actual source filename (same as video) so
        # Premiere's proxy-reconnect flow matches paired video + audio.
        name = Path(phrase.source_file).name
        return self._build_clipitem(
            name=name,
            source_path=phrase.source_file,
            source_in_sec=phrase.source_start,
            source_out_sec=phrase.source_end,
            timeline_in_sec=phrase.timeline_start,
            timeline_out_sec=phrase.timeline_end,
            is_audio=True,
            # Drop 4.23: mute A1 when sync audio is taking over A2+. Keeps
            # the camera audio in the sequence so the editor can re-enable
            # it with one click if the lav is bad, but by default the lav
            # is what plays.
            enabled=not self.mute_camera_audio,
        )

    # --------------- Helpers ---------------

    def _elem(self, tag: str, **attrs) -> minidom.Element:
        el = self.doc.createElement(tag)
        for k, v in attrs.items():
            el.setAttribute(k, v)
        return el

    def _text_elem(self, tag: str, text: str, children: Optional[list] = None, **attrs):
        el = self._elem(tag, **attrs)
        if text:
            el.appendChild(self.doc.createTextNode(text))
        if children:
            for c in children:
                el.appendChild(c)
        return el

    @staticmethod
    def _safe_id(text: str) -> str:
        cleaned = "".join(c if c.isalnum() else "-" for c in text)
        return cleaned.strip("-") or "sequence"


# ------------------------------------------------------------
# Public entry point
# ------------------------------------------------------------

def export_to_premiere_xml(
    cutlist: CutList,
    output_path: Path,
    sequence_name: Optional[str] = None,
    include_overlay: bool = True,
    source_width: int = ASSUMED_SOURCE_WIDTH,
    source_height: int = ASSUMED_SOURCE_HEIGHT,
    # Legacy CLI overrides — override cutlist settings if explicitly passed
    fps: Optional[float] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Path:
    """Serialize a CutList to FCP7 XML and write to disk.

    Sequence dimensions come from the CutList by default (driven by the
    deliverable preset). fps/width/height are kept as overrides for when
    the editor wants to force something different.
    """
    if fps is not None:
        cutlist.sequence_fps = fps
    if width is not None:
        cutlist.sequence_width = width
    if height is not None:
        cutlist.sequence_height = height

    writer = FCPXMLWriter(
        cutlist=cutlist,
        sequence_name=sequence_name,
        include_overlay=include_overlay,
        source_width=source_width,
        source_height=source_height,
    )
    xml = writer.build()
    output_path = Path(output_path)
    output_path.write_text(xml, encoding="utf-8")
    return output_path
