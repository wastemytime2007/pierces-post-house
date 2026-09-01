"""Targeted regression tests for precut/DECISIONS.md
§ "FCP7 XML details that were expensive to learn".

Each test cites the quirk number from that section. Two amendments from
the architecture review (logged for the Lead's Decision Log) are called
out explicitly where they apply — quirk 5 is asserted differently than
DECISIONS.md literally states (the doc is stale re: nb_frames vs.
duration*fps), and quirk 4 is asserted against what the LIVE code path
actually emits today (a related staleness this QA pass found and did not
silently paper over — see that test's docstring).
"""
from __future__ import annotations

import json
from pathlib import Path

FIXTURES_MANIFEST = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "media" / "MANIFEST.json").read_text()
)


def _library_master_clips(exported_dom):
    """Master <clip> elements directly under the B-Roll bin's <children>."""
    for b in exported_dom.getElementsByTagName("bin"):
        name_el = b.getElementsByTagName("name")[0] if b.getElementsByTagName("name") else None
        if name_el and name_el.firstChild and name_el.firstChild.nodeValue == "B-Roll":
            children = [c for c in b.childNodes
                        if c.nodeType == c.ELEMENT_NODE and c.tagName == "children"][0]
            return [c for c in children.childNodes
                    if c.nodeType == c.ELEMENT_NODE and c.tagName == "clip"]
    raise AssertionError("no B-Roll bin found in exported document")


def _direct_child(el, tag):
    for c in el.childNodes:
        if c.nodeType == c.ELEMENT_NODE and c.tagName == tag:
            return c
    return None


def _text(el):
    return el.firstChild.nodeValue if el is not None and el.firstChild else None


# ---------------------------------------------------------------------------
# Quirk 1 — case-sensitive extension probing
# ---------------------------------------------------------------------------

def test_quirk1_find_original_for_proxy_returns_true_ondisk_case(synthetic_project):
    """DECISIONS.md #1: `_find_original_for_proxy` does a case-insensitive
    directory scan and must return the file's REAL on-disk case, even when
    the proxy's own stem/extension casing doesn't match (here: proxy
    `aroll_01.mp4` vs. the original `AROLL_01.MOV`). A case-insensitive
    `Path.exists()` probe that returns the WRONG case is exactly the bug
    this quirk documents; Premiere then fails to find the file."""
    from precut_pipeline.multi_exporter import _find_original_for_proxy

    resolved = _find_original_for_proxy(synthetic_project["proxy_aroll"])

    assert resolved is not None
    assert resolved.name == "AROLL_01.MOV", (
        f"expected the exact on-disk case 'AROLL_01.MOV', got {resolved.name!r}"
    )
    assert resolved == synthetic_project["aroll_original"].resolve() or \
        resolved.resolve() == synthetic_project["aroll_original"].resolve()


# ---------------------------------------------------------------------------
# Quirk 2 — library <file> declares both video and audio samplecharacteristics
# ---------------------------------------------------------------------------

def test_quirk2_library_file_declares_video_and_audio_for_clips_with_audio(exported_dom):
    """DECISIONS.md #2: a library <file> block for a clip that has audio
    must declare BOTH <video> and <audio> samplecharacteristics, or
    Premiere silently rejects the clip. AROLL_01.MOV is our only fixture
    clip with a real audio stream."""
    clips = _library_master_clips(exported_dom)
    aroll_clip = next(
        c for c in clips
        if _text(_direct_child(c, "name")) == "AROLL_01.MOV"
    )
    file_el = aroll_clip.getElementsByTagName("file")[0]
    media = _direct_child(file_el, "media")
    assert _direct_child(media, "video") is not None
    audio = _direct_child(media, "audio")
    assert audio is not None, "AROLL_01.MOV has an audio stream — <audio> must be declared"
    assert _direct_child(audio, "samplecharacteristics") is not None


def test_quirk2_companion_silent_clip_declares_no_audio_block(exported_dom):
    """Companion check (not itself in DECISIONS.md, but the flip side of
    quirk 2): a clip that genuinely has NO audio stream must not get a
    fabricated <audio> block either — multi_exporter._safe_probe's
    `has_audio` flag must gate this correctly in both directions."""
    clips = _library_master_clips(exported_dom)
    blurred = next(
        c for c in clips
        if _text(_direct_child(c, "name")) == "blurred.mp4"
    )
    file_el = blurred.getElementsByTagName("file")[0]
    media = _direct_child(file_el, "media")
    assert _direct_child(media, "audio") is None


# ---------------------------------------------------------------------------
# Quirk 3 — masterclipid links timeline clipitems back to the library
# ---------------------------------------------------------------------------

def test_quirk3_timeline_masterclipids_exist_as_library_master_ids(exported_dom):
    """DECISIONS.md #3: timeline <clipitem> elements reference a
    <masterclipid> that must match a real master clip's id (masterclip-N)
    somewhere in the document's bins — otherwise Premiere can't link the
    timeline usage back to its bin master."""
    all_master_ids = {
        c.getAttribute("id") for c in exported_dom.getElementsByTagName("clip")
        if c.getAttribute("id")
    }
    assert all_master_ids, "expected at least one master clip in the document"

    sequences = exported_dom.getElementsByTagName("sequence")
    assert sequences, "expected at least one sequence"

    checked = 0
    for seq in sequences:
        for ci in seq.getElementsByTagName("clipitem"):
            mcid_el = _direct_child(ci, "masterclipid")
            if mcid_el is None:
                continue
            mcid = _text(mcid_el)
            assert mcid in all_master_ids, (
                f"timeline clipitem {ci.getAttribute('id')} references "
                f"masterclipid {mcid!r} which has no matching master <clip id>"
            )
            checked += 1
    assert checked > 0, "no timeline clipitem carried a masterclipid — fixture is too thin to test this"


# ---------------------------------------------------------------------------
# Quirk 4 — library clip <out> equals <duration>, not duration - 1
# ---------------------------------------------------------------------------

def test_quirk4_library_clip_out_never_off_by_one(exported_dom):
    """DECISIONS.md #4: 'Library clip <out> equals <duration>, not
    duration - 1. An earlier off-by-one "fix" was wrong.'

    DISCOVERY: as of the current code, the LIVE library-master-clip builder
    (bin_builders.build_aroll_master_clip, reached via multi_exporter.
    _build_broll_master_for_entry) doesn't emit an <out> element on the
    master <clip> AT ALL — only <duration>. The function that DOES contain
    the literal duration-1 bug pattern, multi_exporter._build_library_bin,
    is dead code: `grep -rn _build_library_bin` across the whole repo shows
    only its own definition, no caller. So the documented historical bug
    can't literally reproduce via the shipped export path today.

    This test therefore checks the invariant DECISIONS.md actually cares
    about — Premiere never sees an <out> that's one frame short of
    <duration> — in a way that's true of the current code (no <out> at
    all) AND that will fail loudly if the bug is ever reintroduced (e.g. by
    resurrecting _build_library_bin or adding an <out> to
    build_aroll_master_clip with the old off-by-one). The safety_net
    sabotage check (see README.md) verifies this by adding exactly that
    <out> = duration - 1 back in a scratch copy and confirming this
    assertion catches it.
    """
    clips = _library_master_clips(exported_dom)
    assert clips, "expected at least one library master clip"
    for clip in clips:
        duration_el = _direct_child(clip, "duration")
        assert duration_el is not None
        duration = int(_text(duration_el))
        out_el = _direct_child(clip, "out")
        if out_el is None:
            continue  # current shipped behavior — nothing to compare
        out_val = int(_text(out_el))
        assert out_val == duration, (
            f"clip {clip.getAttribute('id')}: <out>{out_val}</out> != "
            f"<duration>{duration}</duration> (off-by-one regression)"
        )


# ---------------------------------------------------------------------------
# Quirk 5 — duration_frames from the live probe (Drop 4.30 supersedes
# DECISIONS.md's literal wording)
# ---------------------------------------------------------------------------

def test_quirk5_duration_frames_reflects_live_probe_not_stale_db_values(exported_dom):
    """DECISIONS.md #5 as literally written says
    'duration_frames is duration_sec * fps, rounded... do not use ffprobe's
    nb_frames'. That is STALE per the architecture review: Drop 4.30
    (multi_exporter.py, see the BrollLibraryEntry.frame_count docstring
    and _build_broll_master_for_entry) deliberately does the OPPOSITE —
    it prefers ffprobe's exact nb_frames when available, specifically
    because independently-rounded duration*fps can disagree with
    Premiere's own probe by a frame and get the clip marked offline.

    So this test asserts what the code actually guarantees: the emitted
    duration is the LIVE-PROBED value (nb_frames when ffprobe reports one),
    not a value derived from the SQLite row's cached (and here,
    deliberately wrong) duration_sec/fps. Our synthetic DB rows carry
    duration_sec=1.0, fps=9.0 for every B-roll entry (see conftest.py) —
    if the exporter ever regressed to trusting those, this clip would
    report round(1.0 * 9.0) = 9 frames instead of the real 120.
    """
    clips = _library_master_clips(exported_dom)
    blurred = next(
        c for c in clips
        if _text(_direct_child(c, "name")) == "blurred.mp4"
    )
    duration = int(_text(_direct_child(blurred, "duration")))

    manifest_entry = FIXTURES_MANIFEST["files"]["blurred.mp4"]
    expected_from_live_probe = manifest_entry["nb_frames"]
    stale_db_would_give = round(1.0 * 9.0)  # the deliberately-wrong DB row

    assert duration == expected_from_live_probe, (
        f"expected the live-probed nb_frames ({expected_from_live_probe}), "
        f"got {duration}"
    )
    assert duration != stale_db_would_give, (
        "duration matches what the STALE DB row would produce — the "
        "exporter has regressed to trusting cached metadata over a live probe"
    )


# ---------------------------------------------------------------------------
# Quirk 6 — additive-only DB migrations (Ryan's Mac only)
# ---------------------------------------------------------------------------

import pytest  # noqa: E402  (kept near its single use, mirroring the other skip)


@pytest.mark.skip(
    reason=(
        "DECISIONS.md #6 (DB schema migrations are additive-only, "
        "ALTER TABLE ADD COLUMN wrapped in try/except for idempotence) "
        "lives entirely in database.py, which requires lancedb + numpy + "
        "pyarrow to import for real (see conftest.py 'The markers.py "
        "surprise' — those are stubbed there only to satisfy markers.py's "
        "import chain, not to provide a working Database). Exercising the "
        "actual migration behavior means opening a real Database against "
        "a pre-migration on-disk DB fixture, which needs the real venv. "
        "Covered on Ryan's Mac only, same as the full import gate."
    )
)
def test_quirk6_db_migrations_are_additive_only_ryans_mac_only():
    pass
