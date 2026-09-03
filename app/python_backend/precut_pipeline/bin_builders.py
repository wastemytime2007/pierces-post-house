"""Bin and master-clip builders for the FCP7 XML export pipeline.

Drop 4.45 introduced a structural rewrite: PreCut now emits the bin
hierarchy that Premiere's FCP7 importer actually honors, instead of the
single flat outer bin that Drop 4.44 produced (which Premiere flattened
into "Recovered Clips" on import). Verified against Premiere's own export
(Pierce's Example_Bins.xml reference); test alpha + beta confirmed the
structure imports with bins populated and forward file refs resolving.

Module responsibilities:
  * make_bin / bin_children — empty <bin> scaffolding with <labels>
  * build_aroll_master_clip — video master with audio tracks (the "normal"
    A-roll/B-roll case)
  * build_audio_master_clip — audio-only master (lav/boom WAV)
  * build_image_master_clip — still master (overlay PNG)
  * _build_full_video_file / _build_full_audio_file / _build_full_image_file
    — the full <file> blocks that go inside the FIRST clipitem of each
    master clip (Option B: master clips declare files; sequences ref by ID)

Key structural points (vs older drops):
  * Root element is <project><name/><children/></project>, NOT <bin>...
  * <xmeml version="4">, not "5" (matches Premiere's own export)
  * Top-level bins (Footage, Audio, Files) are SIBLINGS of "Project Name",
    all direct children of <project><children>. Project Name only contains
    sequence sub-bins (Seq/v1, Seq/Final).
  * Master clips contain a full <media> tree with <video>/<audio> tracks,
    each with <clipitem> elements that have <masterclipid> self-refs and
    <link> blocks wiring V↔A clipitems together.
  * Every bin gets <labels><label2>Mango</label2></labels>.
  * Master clips get a <colorinfo> block.
  * The FIRST clipitem inside each master clip embeds the full <file>
    block; sequences elsewhere emit bare <file id="..."/> self-closing
    references that resolve to it via Premiere's importer.

DISPLAY QUIRK WARNING: In some terminals/print contexts the bytes
'<name>' visually render as '<n>'. The bytes are correct — this is a
display issue. Always hex-verify if a tool seems to be producing <n>.
"""
from __future__ import annotations

import uuid as _uuid
from pathlib import Path
from typing import Optional
from xml.dom import minidom


# ---------------------------------------------------------------------------
# Tiny DOM helpers
# ---------------------------------------------------------------------------

def _text_elem(doc: minidom.Document, tag: str, text: str) -> minidom.Element:
    el = doc.createElement(tag)
    el.appendChild(doc.createTextNode(str(text)))
    return el


def _append_text(doc: minidom.Document, parent: minidom.Element, tag: str, text: str) -> None:
    parent.appendChild(_text_elem(doc, tag, text))


def _build_rate(doc: minidom.Document, timebase: int, ntsc: bool) -> minidom.Element:
    rate = doc.createElement("rate")
    _append_text(doc, rate, "timebase", str(timebase))
    _append_text(doc, rate, "ntsc", "TRUE" if ntsc else "FALSE")
    return rate


# ---------------------------------------------------------------------------
# Bin scaffolding
# ---------------------------------------------------------------------------

def make_bin(doc: minidom.Document, name: str, label: str = "Mango") -> minidom.Element:
    """Create an empty <bin> with <name>, <labels>, and <children>.

    Returns the bin element. Caller appends content via bin_children().
    """
    bin_el = doc.createElement("bin")
    _append_text(doc, bin_el, "name", name)

    labels = doc.createElement("labels")
    _append_text(doc, labels, "label2", label)
    bin_el.appendChild(labels)

    children = doc.createElement("children")
    bin_el.appendChild(children)
    return bin_el


def bin_children(bin_el: minidom.Element) -> minidom.Element:
    """Return the <children> element of a bin (created by make_bin)."""
    for child in bin_el.childNodes:
        if child.nodeType == child.ELEMENT_NODE and child.tagName == "children":
            return child
    raise ValueError("bin has no <children> element")


# ---------------------------------------------------------------------------
# Logginginfo / colorinfo (used by all master clips)
# ---------------------------------------------------------------------------

def _build_empty_logginginfo(doc: minidom.Document) -> minidom.Element:
    """Empty logginginfo block matching Premiere's reference shape."""
    li = doc.createElement("logginginfo")
    for tag in ("description", "scene", "shottake", "lognote", "good",
                "originalvideofilename", "originalaudiofilename"):
        _append_text(doc, li, tag, "")
    return li


def _build_logginginfo_with_tags(
    doc: minidom.Document,
    description: str = "",
    lognote: str = "",
) -> minidom.Element:
    """Logginginfo block populated with searchable tag data.

    Used for B-roll library entries where we want Premiere to surface
    the tags in its Description and Log Note columns.
    """
    li = doc.createElement("logginginfo")
    _append_text(doc, li, "description", description)
    _append_text(doc, li, "scene", "")
    _append_text(doc, li, "shottake", "")
    _append_text(doc, li, "lognote", lognote)
    _append_text(doc, li, "good", "")
    _append_text(doc, li, "originalvideofilename", "")
    _append_text(doc, li, "originalaudiofilename", "")
    return li


def _build_empty_colorinfo(doc: minidom.Document) -> minidom.Element:
    ci = doc.createElement("colorinfo")
    for tag in ("lut", "lut1", "asc_sop", "asc_sat", "lut2"):
        _append_text(doc, ci, tag, "")
    return ci


# ---------------------------------------------------------------------------
# File reference
# ---------------------------------------------------------------------------

def _build_file_ref_id_only(doc: minidom.Document, file_id: str) -> minidom.Element:
    """Bare <file id="x"/> self-closing reference."""
    el = doc.createElement("file")
    el.setAttribute("id", file_id)
    return el


def _build_full_video_file(
    doc: minidom.Document,
    file_id: str,
    pathurl: str,
    name: str,
    duration_frames: int,
    timebase: int,
    ntsc: bool,
    width: int,
    height: int,
    has_audio: bool = True,
    audio_samplerate: int = 48000,
    audio_channels: int = 2,
    audio_depth: int = 16,
) -> minidom.Element:
    """Full <file> block with media characteristics for a video file.

    Use ONLY for the FIRST appearance of a file in document order.
    Subsequent references should use _build_file_ref_id_only.
    """
    file_el = doc.createElement("file")
    file_el.setAttribute("id", file_id)
    _append_text(doc, file_el, "name", name)
    _append_text(doc, file_el, "pathurl", pathurl)
    file_el.appendChild(_build_rate(doc, timebase, ntsc))
    _append_text(doc, file_el, "duration", str(duration_frames))

    # Timecode anchor
    tc = doc.createElement("timecode")
    tc.appendChild(_build_rate(doc, timebase, ntsc))
    _append_text(doc, tc, "string", "00:00:00:00")
    _append_text(doc, tc, "frame", "0")
    _append_text(doc, tc, "displayformat", "NDF" if not ntsc else "DF")
    file_el.appendChild(tc)

    media = doc.createElement("media")

    video = doc.createElement("video")
    samp = doc.createElement("samplecharacteristics")
    samp.appendChild(_build_rate(doc, timebase, ntsc))
    _append_text(doc, samp, "width", str(width))
    _append_text(doc, samp, "height", str(height))
    _append_text(doc, samp, "anamorphic", "FALSE")
    _append_text(doc, samp, "pixelaspectratio", "square")
    _append_text(doc, samp, "fielddominance", "none")
    video.appendChild(samp)
    media.appendChild(video)

    if has_audio:
        audio = doc.createElement("audio")
        a_samp = doc.createElement("samplecharacteristics")
        _append_text(doc, a_samp, "depth", str(audio_depth))
        _append_text(doc, a_samp, "samplerate", str(audio_samplerate))
        audio.appendChild(a_samp)
        _append_text(doc, audio, "channelcount", str(audio_channels))
        media.appendChild(audio)

    file_el.appendChild(media)
    return file_el


def _build_full_audio_file(
    doc: minidom.Document,
    file_id: str,
    pathurl: str,
    name: str,
    duration_frames: int,
    timebase: int,
    ntsc: bool,
    samplerate: int = 48000,
    channels: int = 1,
    depth: int = 16,
) -> minidom.Element:
    """Full <file> block for an audio-only file (WAV)."""
    file_el = doc.createElement("file")
    file_el.setAttribute("id", file_id)
    _append_text(doc, file_el, "name", name)
    _append_text(doc, file_el, "pathurl", pathurl)
    file_el.appendChild(_build_rate(doc, timebase, ntsc))
    _append_text(doc, file_el, "duration", str(duration_frames))

    tc = doc.createElement("timecode")
    tc.appendChild(_build_rate(doc, timebase, ntsc))
    _append_text(doc, tc, "string", "00:00:00:00")
    _append_text(doc, tc, "frame", "0")
    _append_text(doc, tc, "displayformat", "NDF" if not ntsc else "DF")
    file_el.appendChild(tc)

    media = doc.createElement("media")
    audio = doc.createElement("audio")
    a_samp = doc.createElement("samplecharacteristics")
    _append_text(doc, a_samp, "depth", str(depth))
    _append_text(doc, a_samp, "samplerate", str(samplerate))
    audio.appendChild(a_samp)
    _append_text(doc, audio, "channelcount", str(channels))
    media.appendChild(audio)
    file_el.appendChild(media)
    return file_el


def _build_full_image_file(
    doc: minidom.Document,
    file_id: str,
    pathurl: str,
    name: str,
    width: int,
    height: int,
    timebase: int = 30,
    ntsc: bool = False,
) -> minidom.Element:
    """Full <file> block for a still image (PNG overlay)."""
    file_el = doc.createElement("file")
    file_el.setAttribute("id", file_id)
    _append_text(doc, file_el, "name", name)
    _append_text(doc, file_el, "pathurl", pathurl)
    file_el.appendChild(_build_rate(doc, timebase, ntsc))
    # 108000 frames @ 30fps = 1hr — sentinel, "stretch as needed"
    _append_text(doc, file_el, "duration", "108000")

    tc = doc.createElement("timecode")
    tc.appendChild(_build_rate(doc, timebase, ntsc))
    _append_text(doc, tc, "string", "00:00:00:00")
    _append_text(doc, tc, "frame", "0")
    _append_text(doc, tc, "displayformat", "NDF")
    file_el.appendChild(tc)

    media = doc.createElement("media")
    video = doc.createElement("video")
    samp = doc.createElement("samplecharacteristics")
    samp.appendChild(_build_rate(doc, timebase, ntsc))
    _append_text(doc, samp, "width", str(width))
    _append_text(doc, samp, "height", str(height))
    _append_text(doc, samp, "anamorphic", "FALSE")
    _append_text(doc, samp, "pixelaspectratio", "square")
    _append_text(doc, samp, "fielddominance", "none")
    video.appendChild(samp)
    media.appendChild(video)
    file_el.appendChild(media)
    return file_el


# ---------------------------------------------------------------------------
# Master clip builders
#
# Pattern from Example_Bins.xml (verified):
#   <clip id="masterclip-N" explodedTracks="true">
#     <uuid>...</uuid>
#     <masterclipid>masterclip-N</masterclipid>      ← self-reference
#     <ismasterclip>TRUE</ismasterclip>
#     <duration>FRAMES</duration>
#     <rate>...</rate>
#     <name>FILENAME</name>
#     <media>
#       <video><track>
#         <clipitem id="clipitem-X">
#           <masterclipid>masterclip-N</masterclipid>
#           <name/><rate/><alphatype>none</alphatype>
#           <file id="file-N"/>                       ← bare reference
#           <link>...</link>                          ← V↔A wiring
#         </clipitem>
#       </track></video>
#       <audio>
#         <track><clipitem id="clipitem-Y">
#           <file id="file-N"/>
#           <sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>
#           <link>...</link>
#         </clipitem></track>
#         (one track per audio channel)
#       </audio>
#     </media>
#     <logginginfo/>
#     <colorinfo/>
#     <labels><label2>Iris</label2></labels>
#   </clip>
# ---------------------------------------------------------------------------

def build_aroll_master_clip(
    doc: minidom.Document,
    master_id: str,                 # e.g. "masterclip-1"
    file_id: str,                   # the file_id this master refs
    name: str,                      # filename, e.g. "A011C0054.MOV"
    duration_frames: int,           # source file duration in frames at given timebase
    timebase: int,
    ntsc: bool,
    audio_track_count: int = 2,     # number of source audio channels (typ. 2)
    color_label: str = "Iris",
    next_clipitem_id_fn=None,       # callable returning next "clipitem-N" string
    file_ref_first_use: Optional[minidom.Element] = None,  # optional full <file> block to embed in first clipitem (only if no sequence has declared this file yet)
    logginginfo: Optional[minidom.Element] = None,
) -> minidom.Element:
    """Build a master clip for a video A-roll file.

    The master contains video + N audio tracks, each with a clipitem that
    references the file by ID and links cross-track via <link> elements.

    next_clipitem_id_fn() is called to mint clipitem IDs for the internal
    track clipitems. Pass an existing counter generator to avoid collisions
    with sequence clipitem IDs.
    """
    if next_clipitem_id_fn is None:
        # Fallback: simple internal counter, only safe if isolated
        _counter = [0]
        def _gen():
            _counter[0] += 1
            return f"masterclipitem-{_counter[0]}"
        next_clipitem_id_fn = _gen

    clip_el = doc.createElement("clip")
    clip_el.setAttribute("id", master_id)
    clip_el.setAttribute("explodedTracks", "true")

    _append_text(doc, clip_el, "uuid", str(_uuid.uuid4()))
    _append_text(doc, clip_el, "masterclipid", master_id)
    _append_text(doc, clip_el, "ismasterclip", "TRUE")
    _append_text(doc, clip_el, "duration", str(duration_frames))
    clip_el.appendChild(_build_rate(doc, timebase, ntsc))
    _append_text(doc, clip_el, "name", name)

    # Allocate clipitem IDs upfront so we can wire <link> elements
    video_clipitem_id = next_clipitem_id_fn()
    audio_clipitem_ids = [next_clipitem_id_fn() for _ in range(audio_track_count)]

    media = doc.createElement("media")

    # ---- Video ----
    video = doc.createElement("video")
    v_track = doc.createElement("track")
    v_ci = doc.createElement("clipitem")
    v_ci.setAttribute("id", video_clipitem_id)
    _append_text(doc, v_ci, "masterclipid", master_id)
    _append_text(doc, v_ci, "name", name)
    v_ci.appendChild(_build_rate(doc, timebase, ntsc))
    _append_text(doc, v_ci, "alphatype", "none")
    if file_ref_first_use is not None:
        v_ci.appendChild(file_ref_first_use)
    else:
        v_ci.appendChild(_build_file_ref_id_only(doc, file_id))
    # Links: video clipitem links to itself + each audio clipitem
    v_ci.appendChild(_build_link(doc, video_clipitem_id, "video", 1, 1))
    for idx, aci_id in enumerate(audio_clipitem_ids, start=1):
        v_ci.appendChild(_build_link(doc, aci_id, "audio", idx, 1))
    v_track.appendChild(v_ci)
    video.appendChild(v_track)
    media.appendChild(video)

    # ---- Audio ----
    audio = doc.createElement("audio")
    for idx, aci_id in enumerate(audio_clipitem_ids, start=1):
        a_track = doc.createElement("track")
        a_ci = doc.createElement("clipitem")
        a_ci.setAttribute("id", aci_id)
        _append_text(doc, a_ci, "masterclipid", master_id)
        _append_text(doc, a_ci, "name", name)
        a_ci.appendChild(_build_rate(doc, timebase, ntsc))
        a_ci.appendChild(_build_file_ref_id_only(doc, file_id))
        # sourcetrack: which channel of the source file this clipitem represents
        st = doc.createElement("sourcetrack")
        _append_text(doc, st, "mediatype", "audio")
        _append_text(doc, st, "trackindex", str(idx))
        a_ci.appendChild(st)
        # Links: same V+A linking pattern as video clipitem
        a_ci.appendChild(_build_link(doc, video_clipitem_id, "video", 1, 1))
        for j, other_aci_id in enumerate(audio_clipitem_ids, start=1):
            a_ci.appendChild(_build_link(doc, other_aci_id, "audio", j, 1))
        a_track.appendChild(a_ci)
        audio.appendChild(a_track)
    media.appendChild(audio)

    clip_el.appendChild(media)

    # ---- Trailing metadata ----
    if logginginfo is not None:
        clip_el.appendChild(logginginfo)
    else:
        clip_el.appendChild(_build_empty_logginginfo(doc))
    clip_el.appendChild(_build_empty_colorinfo(doc))

    labels = doc.createElement("labels")
    _append_text(doc, labels, "label2", color_label)
    clip_el.appendChild(labels)

    return clip_el


def _build_link(
    doc: minidom.Document,
    linkclipref: str,
    mediatype: str,           # "video" or "audio"
    trackindex: int,
    clipindex: int,
) -> minidom.Element:
    link = doc.createElement("link")
    _append_text(doc, link, "linkclipref", linkclipref)
    _append_text(doc, link, "mediatype", mediatype)
    _append_text(doc, link, "trackindex", str(trackindex))
    _append_text(doc, link, "clipindex", str(clipindex))
    return link


def build_audio_master_clip(
    doc: minidom.Document,
    master_id: str,
    file_id: str,
    name: str,
    duration_frames: int,
    timebase: int,
    ntsc: bool,
    channel_count: int = 1,
    color_label: str = "Caribbean",
    next_clipitem_id_fn=None,
    file_ref_first_use: Optional[minidom.Element] = None,
) -> minidom.Element:
    """Master clip for an audio-only file (WAV lav/boom)."""
    if next_clipitem_id_fn is None:
        _counter = [0]
        def _gen():
            _counter[0] += 1
            return f"audiomasterclipitem-{_counter[0]}"
        next_clipitem_id_fn = _gen

    clip_el = doc.createElement("clip")
    clip_el.setAttribute("id", master_id)
    clip_el.setAttribute("explodedTracks", "true")

    _append_text(doc, clip_el, "uuid", str(_uuid.uuid4()))
    _append_text(doc, clip_el, "masterclipid", master_id)
    _append_text(doc, clip_el, "ismasterclip", "TRUE")
    _append_text(doc, clip_el, "duration", str(duration_frames))
    clip_el.appendChild(_build_rate(doc, timebase, ntsc))
    _append_text(doc, clip_el, "name", name)

    audio_clipitem_ids = [next_clipitem_id_fn() for _ in range(channel_count)]

    media = doc.createElement("media")
    audio = doc.createElement("audio")
    for idx, aci_id in enumerate(audio_clipitem_ids, start=1):
        a_track = doc.createElement("track")
        a_ci = doc.createElement("clipitem")
        a_ci.setAttribute("id", aci_id)
        _append_text(doc, a_ci, "masterclipid", master_id)
        _append_text(doc, a_ci, "name", name)
        a_ci.appendChild(_build_rate(doc, timebase, ntsc))
        if idx == 1 and file_ref_first_use is not None:
            a_ci.appendChild(file_ref_first_use)
        else:
            a_ci.appendChild(_build_file_ref_id_only(doc, file_id))
        st = doc.createElement("sourcetrack")
        _append_text(doc, st, "mediatype", "audio")
        _append_text(doc, st, "trackindex", str(idx))
        a_ci.appendChild(st)
        a_track.appendChild(a_ci)
        audio.appendChild(a_track)
    media.appendChild(audio)
    clip_el.appendChild(media)

    clip_el.appendChild(_build_empty_logginginfo(doc))
    clip_el.appendChild(_build_empty_colorinfo(doc))

    labels = doc.createElement("labels")
    _append_text(doc, labels, "label2", color_label)
    clip_el.appendChild(labels)

    return clip_el


def build_image_master_clip(
    doc: minidom.Document,
    master_id: str,
    file_id: str,
    name: str,
    timebase: int,
    ntsc: bool,
    color_label: str = "Lavender",
    next_clipitem_id_fn=None,
    file_ref_first_use: Optional[minidom.Element] = None,
) -> minidom.Element:
    """Master clip for a still image (PNG overlay)."""
    if next_clipitem_id_fn is None:
        _counter = [0]
        def _gen():
            _counter[0] += 1
            return f"imagemasterclipitem-{_counter[0]}"
        next_clipitem_id_fn = _gen

    clip_el = doc.createElement("clip")
    clip_el.setAttribute("id", master_id)
    clip_el.setAttribute("explodedTracks", "true")

    _append_text(doc, clip_el, "uuid", str(_uuid.uuid4()))
    _append_text(doc, clip_el, "masterclipid", master_id)
    _append_text(doc, clip_el, "ismasterclip", "TRUE")
    # Stills use the same 108000-frame sentinel as <file> duration
    _append_text(doc, clip_el, "duration", "108000")
    clip_el.appendChild(_build_rate(doc, timebase, ntsc))
    _append_text(doc, clip_el, "name", name)

    video_clipitem_id = next_clipitem_id_fn()

    media = doc.createElement("media")
    video = doc.createElement("video")
    v_track = doc.createElement("track")
    v_ci = doc.createElement("clipitem")
    v_ci.setAttribute("id", video_clipitem_id)
    _append_text(doc, v_ci, "masterclipid", master_id)
    _append_text(doc, v_ci, "name", name)
    v_ci.appendChild(_build_rate(doc, timebase, ntsc))
    _append_text(doc, v_ci, "alphatype", "straight")
    if file_ref_first_use is not None:
        v_ci.appendChild(file_ref_first_use)
    else:
        v_ci.appendChild(_build_file_ref_id_only(doc, file_id))
    _append_text(doc, v_ci, "stillframe", "TRUE")
    v_track.appendChild(v_ci)
    video.appendChild(v_track)
    media.appendChild(video)
    clip_el.appendChild(media)

    clip_el.appendChild(_build_empty_logginginfo(doc))
    clip_el.appendChild(_build_empty_colorinfo(doc))

    labels = doc.createElement("labels")
    _append_text(doc, labels, "label2", color_label)
    clip_el.appendChild(labels)

    return clip_el


# ---------------------------------------------------------------------------
# Placeholder asset resolution (Drop 4.45.3 / 4.45.4)
#
# Premiere's importer drops bins with no real master clip in them. We can't
# emit "empty" placeholder bins (Final, Music, SFX, Nested Seqs, Colors)
# without putting SOMETHING in them. Each empty placeholder bin gets a
# 1x1 transparent PNG master clip named "(placeholder — delete me)" that
# the editor removes once they have real content.
#
# Drop 4.45.4: SIX distinct PNGs are bundled, one per placeholder slot
# (placeholder_final.png, placeholder_music.png, etc.). All six contain
# identical bytes but live at distinct on-disk paths so Premiere's
# importer doesn't dedupe them by file URL — which is what happened in
# 4.45.3 with a single placeholder.png: only the first bin (Final) kept
# the master, the others got merged out and their bins got culled.
#
# The PNGs ship inside the precut_pipeline package at placeholders/
# alongside overlays_assets/. Resolution mirrors the overlay pattern.
# ---------------------------------------------------------------------------

# Valid slot names for get_placeholder_png_path(slot). Each maps to a
# distinct PNG bundled with the .app. Adding a new placeholder bin?
# Add a new PNG to placeholders/ and a new slot here.
PLACEHOLDER_SLOTS = ("final", "broll", "music", "sfx", "nested_seqs", "colors")


def get_placeholder_png_path(slot: str) -> Optional[Path]:
    """Return the on-disk path to the bundled placeholder PNG for a slot.

    Resolves relative to this module's location so it works whether the
    code is running from source or from inside the .app bundle's
    Resources/_up_/python_backend/precut_pipeline/ directory.

    Slot must be one of PLACEHOLDER_SLOTS. Each slot has its own distinct
    PNG so Premiere's URL-dedup doesn't collapse multiple bins' placeholder
    masters into one.

    Returns None if the asset is missing — caller should treat that as
    "skip this placeholder" rather than crash.
    """
    if slot not in PLACEHOLDER_SLOTS:
        return None
    candidate = Path(__file__).parent / "placeholders" / f"placeholder_{slot}.png"
    return candidate if candidate.exists() else None
