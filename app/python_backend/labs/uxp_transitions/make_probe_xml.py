#!/usr/bin/env python3
"""A one-cut test sequence that asks the Transition Lab to add a Film Impact effect at the cut and write out its settings.

    python3 labs/uxp_transitions/make_probe_xml.py out.xml [effect name part, default "Impact Move"]

Built from Ryan's own test export with its whoosh and transition taken out (make_demo_input.py, cut at frame 116), plus one sequence marker "PH effect probe" at the cut whose comment names the effect.
Edited as text; the sequence gets its own name and uuid so it does not collide with earlier imports.
"""
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
out = Path(sys.argv[1])
want = sys.argv[2] if len(sys.argv) > 2 else "Impact Move"
tmp = Path(tempfile.mkdtemp())
subprocess.run([sys.executable, str(HERE / "make_demo_input.py"), str(tmp)], check=True, capture_output=True)
t = (tmp / "1 - one cut, no transition, no sound.xml").read_text(encoding="utf-8")
s0 = t.index("<sequence")
mm = list(re.finditer(r"</media>(\s*)<timecode>", t[s0:]))
pos = s0 + mm[-1].start() + len("</media>")
marker = f"\n<marker><name>PH effect probe</name><comment>{want}</comment><in>116</in><out>-1</out><color><alpha>255</alpha><red>244</red><green>105</green><blue>11</blue></color></marker>"
t = t[:pos] + marker + t[pos:]
seq_open = t.index(">", s0) + 1
t = t[:seq_open] + re.sub(r"<uuid>[^<]*</uuid>", f"<uuid>{uuid.uuid4()}</uuid>", t[seq_open:], count=1)
t = t[:seq_open] + re.sub(r"<name>[^<]*</name>", f"<name>Move FX test (1 cut)</name>", t[seq_open:], count=1)
out.write_bytes(t.encode("utf-8"))
print("wrote", out)
