"""The export safety check, with one narrow exemption.

safety_net/verify_export.py guards against PreCut exports that came out as a few coarse slabs (CUT-GRANULARITY: "a 45 s edit came out as 2 slabs"). An XML made from a FINISHED
video file (labs/review_loop/xml_from_media.py) is one clip by design, so that one check does not apply to it. The exemption is explicit: the XML must carry the marker the
converter writes. Every other XML, including a single-clip XML without the marker, is held to every check.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "safety_net"))
import verify_export  # noqa: E402

WHOLE_FILE_MARKER = "posthouse: whole video file"


def is_whole_file(xml: Path) -> bool:
    try:
        return any((m.findtext("name") or "") == WHOLE_FILE_MARKER for m in ET.parse(xml).getroot().iter("marker"))
    except (ET.ParseError, OSError):
        return False


def row(xml_out: Path) -> tuple[str, bool, str]:
    """('verify_export', ok, detail) for the XML, skipping only CUT-GRANULARITY and only for a whole-file XML."""
    rep = verify_export.Report()
    verify_export.check_xml(xml_out, rep)
    skip = {"CUT-GRANULARITY"} if is_whole_file(xml_out) else set()
    bad = [n for n, ok, _d in rep.rows if ok is False and n not in skip]
    note = " (CUT-GRANULARITY does not apply: this XML is one finished video file)" if skip and any(n in skip and ok is False for n, ok, _d in rep.rows) else ""
    return ("verify_export", not bad, ("all applicable checks pass" + note) if not bad else f"FAILED: {', '.join(bad)}")
