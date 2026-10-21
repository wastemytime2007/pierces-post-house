#!/usr/bin/env python3
"""Two undecorated sequences made from Ryan's own Film Impact test export, for proving place_sfx.py.

    python3 labs/uxp_transitions/make_demo_input.py <out folder>

  1 - one cut:  his sequence with the whoosh and the transition taken out (the cut at frame 116 kept). place_sfx.py on this must recreate his hand-made whoosh.
  2 - three cuts: the same recording cut into four pieces with jump cuts (the source jumps forward at each cut), picture, camera sound and recorder together, as a trimmed sequence would look.
Edited as text, so everything not named stays as Premiere wrote it.
"""
import re
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent / "templates" / "film_impact_push_whoosh_template.xml"
TICKS = 8475667200
raw = TEMPLATE.read_bytes().decode("utf-8")


def block(text, cid):
    m = re.search(rf'<clipitem id="{cid}"[^>]*>.*?</clipitem>', text, re.S)
    assert m, cid
    return m


def undecorate(text):
    text = re.sub(r"<transitionitem>.*?</transitionitem>\s*", "", text, flags=re.S)
    tracks = list(re.finditer(r'<track [^>]*premiereTrackType="Stereo"[^>]*>.*?</track>', text, re.S))
    for t in reversed([t for t in tracks if "<name>Whooshes.wav</name>" in t.group(0)]):
        text = text[:t.start()] + text[t.end():]
    # with the transition gone, the two V1 clips meet at the cut: Premiere had written -1 for "ends inside a transition"
    b13 = block(text, "clipitem-13")
    text = text[:b13.start()] + b13.group(0).replace("<end>-1</end>", "<end>116</end>", 1) + text[b13.end():]
    b14 = block(text, "clipitem-14")
    text = text[:b14.start()] + b14.group(0).replace("<start>-1</start>", "<start>116</start>", 1) + text[b14.end():]
    return text


def set_vals(c, start, end, vin, vout):
    for tag, val in (("start", start), ("end", end), ("in", vin), ("out", vout), ("pproTicksIn", vin * TICKS), ("pproTicksOut", vout * TICKS)):
        c = re.sub(rf"(<{tag}>)[^<]*(</{tag}>)", rf"\g<1>{val}\g<2>", c, count=1)
    return c


def split_into_pieces(text, pieces):
    """pieces: [(timeline_start, timeline_end, source_in)]. Picture (13,14), camera sound (15,16) and the recorder (17,18) all follow the same pieces."""
    rec_off = 23779 - 22686
    for first, second, off in (("clipitem-13", "clipitem-14", 0), ("clipitem-15", "clipitem-16", 0), ("clipitem-17", "clipitem-18", rec_off)):
        b1, b2 = block(text, first), block(text, second)
        built = []
        for n, (ts, te, sin) in enumerate(pieces):
            base = b1.group(0) if n == 0 else b2.group(0)
            cid = first if n == 0 else f"{second}-p{n}"
            base = re.sub(r'<clipitem id="[^"]*"', f'<clipitem id="{cid}"', base, count=1)
            built.append(set_vals(base, ts, te, sin + off, sin + off + (te - ts)))
        # replace the two original blocks (the second comes after the first in the file) with the built pieces
        text = text[:b1.start()] + "\n".join(built) + text[b1.end():]
        b2 = block(text, second)
        text = text[:b2.start()] + text[b2.end():]
    return text


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    one = undecorate(raw)
    (out / "1 - one cut, no transition, no sound.xml").write_bytes(one.encode("utf-8"))
    pieces = [(0, 100, 22686), (100, 185, 22686 + 400), (185, 290, 22686 + 900), (290, 350, 22686 + 1500)]
    three = split_into_pieces(one, pieces)
    # sequence duration: the first <duration> after the <sequence ...> opening tag
    s = re.search(r"<sequence\b[^>]*>", three).end()
    mm = re.compile(r"(<duration>)\d+(</duration>)").search(three, s)
    three = three[:mm.start()] + f"<duration>350</duration>" + three[mm.end():]
    three = re.sub(r"(<name>)R1_46_[^<]*(</name>)", r"\g<1>Push and whoosh demo (3 cuts)\g<2>", three, count=1)
    (out / "2 - three cuts, no transition, no sound.xml").write_bytes(three.encode("utf-8"))
    print("wrote", out)


if __name__ == "__main__":
    main()
