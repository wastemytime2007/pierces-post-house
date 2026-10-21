#!/usr/bin/env python3
"""Put Ryan's whoosh under every cut of a Premiere-exported sequence, exactly as Premiere wrote it in his own test export.

    python3 labs/uxp_transitions/place_sfx.py in.xml out.xml [--sequence NAME] [--track 1] [--template T.xml]

What it does (the XML half of "Film Impact Push + whoosh"; the transition itself is added by the Transition Lab panel, which XML cannot carry):
  - finds the cuts on one video track: a cut is where one clip starts exactly as the previous one ends
  - for each cut, writes two audio clips (the stereo whoosh split across two tracks, like Premiere does) copied from Ryan's template
    export: the same excerpt of `Whooshes.wav`, -5 dB, 120% speed, starting 4 frames before the cut and ending 7 frames after it
    (his template: cut at frame 116, whoosh at 112 to 123)
  - adds the two tracks AFTER the sequence's existing audio tracks and touches nothing else: the output is the input with one block of text inserted (checked)
  - writes cuts.json next to the output: the cut frames, for the panel and for anyone checking
Handles Premiere-exported XML (plain <xmeml><sequence> with files defined inline).
"""
import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = HERE / "templates" / "film_impact_push_whoosh_template.xml"
TEMPLATE_CUT = 116                      # in the template: V1 clip 1 ends and clip 2 starts here
BEFORE, AFTER = 4, 7                    # whoosh starts BEFORE frames ahead of the cut and ends AFTER frames past it (112 and 123 in the template)
TRANSITION = {"match_name": "AE.AE_Impact_Push", "frames": 6, "alignment": 0.5}


def sequence_span(raw: str, name: str | None) -> tuple[int, int, str]:
    spans = [(m.start(), m.end()) for m in re.finditer(r"<sequence\b[^>]*>.*?</sequence>", raw, re.S)]
    assert spans, "no <sequence> found"
    for a, b in spans:
        nm = re.search(r"<name>([^<]*)</name>", raw[a:b])
        if name is None or (nm and nm.group(1).startswith(name)):
            return a, b, nm.group(1) if nm else ""
    raise SystemExit(f"no sequence named like {name!r}")


def cuts_on_track(seq_xml: str, track: int) -> tuple[list[int], int]:
    seq = ET.fromstring(seq_xml)
    tr = seq.findall("media/video/track")[track - 1]
    clips = sorted(((int(c.findtext("start")), int(c.findtext("end"))) for c in tr.findall("clipitem") if int(c.findtext("start")) >= 0 and int(c.findtext("end")) > 0))
    cuts = [b[0] for a, b in zip(clips, clips[1:]) if abs(a[1] - b[0]) <= 1]
    return cuts, int(seq.findtext("duration"))


def template_tracks(template: Path) -> tuple[str, str]:
    raw = template.read_text(encoding="utf-8-sig")
    tracks = re.findall(r"<track [^>]*premiereTrackType=\"Stereo\"[^>]*>.*?</track>", raw, re.S)
    whoosh = [t for t in tracks if "<name>Whooshes.wav</name>" in t]
    assert len(whoosh) == 2, f"expected the two whoosh tracks in the template, found {len(whoosh)}"
    return whoosh[0], whoosh[1]


def build_block(left_t: str, right_t: str, cuts: list[int], first_track_number: int, group_base: int) -> str:
    """The two new <track> elements, with one pair of clips per cut."""
    def clip_of(track_text: str) -> str:
        return re.search(r"<clipitem .*?</clipitem>", track_text, re.S).group(0)

    left_clip, right_clip = clip_of(left_t), clip_of(right_t)
    left_head = left_t[:left_t.index("<clipitem ")]
    left_tail = left_t[left_t.rindex("</clipitem>") + len("</clipitem>"):]
    right_head = right_t[:right_t.index("<clipitem ")]
    right_tail = right_t[right_t.rindex("</clipitem>") + len("</clipitem>"):]
    t_l, t_r = first_track_number, first_track_number + 1
    lefts, rights = [], []
    for k, cut in enumerate(cuts, start=1):
        shift = cut - TEMPLATE_CUT
        ids = (f"clipitem-whoosh-{k}-L", f"clipitem-whoosh-{k}-R")
        for clip, side, out in ((left_clip, 0, lefts), (right_clip, 1, rights)):
            c = clip
            c = re.sub(r'<clipitem id="[^"]*"', f'<clipitem id="{ids[side]}"', c, count=1)
            c = c.replace("masterclip-10", "masterclip-whoosh-1")
            if k > 1 and side == 0:                                       # the file is defined once, in the first clip on the first track; every later clip refers to it
                c = re.sub(r'<file id="file-10">.*?</file>', '<file id="file-whoosh-1" />', c, count=1, flags=re.S)
            else:
                c = c.replace('<file id="file-10"', '<file id="file-whoosh-1"')
                c = c.replace('<file id="file-10" />', '<file id="file-whoosh-1" />')
            c = re.sub(r"<start>\d+</start>", f"<start>{112 + shift}</start>", c, count=1)
            c = re.sub(r"<end>\d+</end>", f"<end>{123 + shift}</end>", c, count=1)
            # links: the pair, on the two new tracks, this clip's position on its track, a group number of its own
            links = "".join(
                f"<link><linkclipref>{ids[i]}</linkclipref><mediatype>audio</mediatype><trackindex>{(t_l, t_r)[i]}</trackindex><clipindex>{k}</clipindex><groupindex>{group_base + k}</groupindex></link>"
                for i in (0, 1))
            c = re.sub(r"(<link>.*?</link>\s*)+", links, c, count=1, flags=re.S)
            out.append(c)
    # the template's second track defined nothing new (a bare file reference); keep that: only the very first left clip carries the definition
    for i in range(len(rights)):
        rights[i] = re.sub(r'<file id="file-whoosh-1">.*?</file>', '<file id="file-whoosh-1" />', rights[i], count=1, flags=re.S)
    return left_head + "\n".join(lefts) + left_tail + right_head + "\n".join(rights) + right_tail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--sequence")
    ap.add_argument("--track", type=int, default=1)
    ap.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    a = ap.parse_args()
    raw = Path(a.src).read_bytes().decode("utf-8")
    s0, s1, name = sequence_span(raw, a.sequence)
    seq_xml = raw[s0:s1]
    cuts, duration = cuts_on_track(seq_xml, a.track)
    kept = [c for c in cuts if c - BEFORE >= 0 and c + AFTER <= duration]
    skipped = [c for c in cuts if c not in kept]
    if not kept:
        print(f"no usable cuts on V{a.track} of {name!r} ({len(cuts)} cuts found, {len(skipped)} too close to the ends)")
        return 1
    left_t, right_t = template_tracks(Path(a.template))
    n_audio = len(ET.fromstring(seq_xml).findall("media/audio/track"))
    groups = [int(g) for g in re.findall(r"<groupindex>(\d+)</groupindex>", seq_xml)]
    block = build_block(left_t, right_t, kept, n_audio + 1, max(groups or [0]))
    m = list(re.finditer(r"</track>(\s*)</audio>", seq_xml))
    assert m, "could not find the end of the sequence's audio tracks"
    ins = m[-1].start() + len("</track>")
    new_seq = seq_xml[:ins] + "\n" + block + seq_xml[ins:]
    out = raw[:s0] + new_seq + raw[s1:]
    # verify: only a block of text was inserted
    inserted_len = len(out) - len(raw)
    assert out[:s0 + ins] == raw[:s0 + ins] and out[s0 + ins + inserted_len:] == raw[s0 + ins:], "output differs from the input in more than the inserted block"
    ET.fromstring(out.replace("<!DOCTYPE xmeml>", "").split("?>", 1)[-1])      # still well-formed
    Path(a.dst).write_bytes(out.encode("utf-8"))
    cuts_json = {"sequence": name, "video_track": a.track, "cuts": [{"frame": c, "whoosh_start": c - BEFORE, "whoosh_end": c + AFTER} for c in kept], "skipped_cuts": skipped, "transition": TRANSITION}
    Path(a.dst).with_suffix(".cuts.json").write_text(json.dumps(cuts_json, indent=1))
    print(f"{name!r}: {len(kept)} whoosh pair(s) placed at frames {kept}" + (f"; {len(skipped)} cut(s) too close to an end skipped" if skipped else ""))
    print(f"wrote {a.dst} (new audio tracks A{n_audio + 1} and A{n_audio + 2}) and {Path(a.dst).with_suffix('.cuts.json').name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
