"""A TRANSPARENT relief overlay: only the light and shade of sculpted ridges, nothing opaque.

Ryan: "it doesnt look like an overlay it just looks like you replace the bg with an image". The first attempt
painted its own ground, so it was a picture. This one has no ground at all: every mark is a white highlight or
a black shadow at partial alpha, so whatever colour the page already is shows through and the relief blends
with it. Bands run diagonally, each crest has a lit edge with a shadow under it, and fine striations follow
the curve inside each band.

K is the one dial: how present the relief is. 1.0 fought the content; this is set where it reads as a surface
the work sits on rather than a picture competing with it.
"""
import math
from pathlib import Path

W, H = 1800, 1100
K = 0.5


def a(v: float) -> str:
    return f"{v * K:.3f}"


def ridge(y0, amp, periods, phase, steps=9):
    pts = []
    for i in range(steps + 1):
        t = i / steps
        x = t * W
        y = (y0
             + math.sin(t * math.pi * 2 * periods + phase) * amp
             + math.sin(t * math.pi * 2 * periods * 2.37 + phase * 1.7) * amp * 0.32)
        pts.append((x, y))
    d = [f"M {pts[0][0]:.1f} {pts[0][1]:.1f}"]
    for i in range(1, len(pts)):
        x0, ya = pts[i - 1]
        x1, yb = pts[i]
        cx = (x0 + x1) / 2
        d.append(f"C {cx:.1f} {ya:.1f} {cx:.1f} {yb:.1f} {x1:.1f} {yb:.1f}")
    return " ".join(d)


def build() -> str:
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" preserveAspectRatio="xMidYMid slice">',
        "<defs>",
        f'<linearGradient id="hollow" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="#000" stop-opacity="{a(0.42)}"/>'
        f'<stop offset="0.55" stop-color="#000" stop-opacity="{a(0.10)}"/>'
        f'<stop offset="1" stop-color="#000" stop-opacity="0"/></linearGradient>',
        f'<linearGradient id="face" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="#fff" stop-opacity="{a(0.30)}"/>'
        f'<stop offset="0.3" stop-color="#fff" stop-opacity="{a(0.09)}"/>'
        f'<stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>',
        '<filter id="b1"><feGaussianBlur stdDeviation="9"/></filter>',
        '<filter id="b2"><feGaussianBlur stdDeviation="2.2"/></filter>',
        "</defs>",
        '<g transform="rotate(-7 900 550) scale(1.18) translate(-150 -95)">',
    ]

    n = 16
    for k in range(n):
        y0 = -40 + k * (H + 120) / (n - 1)
        amp = 30 + (k % 4) * 13
        periods = 1.0 + (k % 5) * 0.19
        phase = k * 1.37
        path = ridge(y0, amp, periods, phase)
        band_h = (H + 120) / (n - 1)

        out.append(f'<path d="{path} L {W} {y0 + band_h + 60} L 0 {y0 + band_h + 60} Z" fill="url(#hollow)" filter="url(#b1)"/>')
        out.append(f'<path d="{path} L {W} {y0 + band_h * 0.95} L 0 {y0 + band_h * 0.95} Z" fill="url(#face)"/>')
        out.append(f'<path d="{path}" fill="none" stroke="#fff" stroke-opacity="{a(0.34)}" stroke-width="1.6" filter="url(#b2)"/>')
        out.append(f'<path d="{path}" fill="none" stroke="#000" stroke-opacity="{a(0.30)}" stroke-width="2.6" transform="translate(0 3.5)" filter="url(#b2)"/>')

        for s in range(1, 7):
            off = s * (band_h / 7.5)
            sp = ridge(y0 + off, amp * (1 - s * 0.018), periods, phase)
            al = (0.055 - s * 0.006) * K
            out.append(f'<path d="{sp}" fill="none" stroke="#fff" stroke-opacity="{al:.3f}" stroke-width="1"/>')
            out.append(f'<path d="{sp}" fill="none" stroke="#000" stroke-opacity="{al * 0.8:.3f}" stroke-width="1" transform="translate(0 1.6)"/>')

    out.append("</g></svg>")
    return "".join(out)


p = Path("/Users/pierce/.claude/jobs/a40e345e/tmp/relief.svg")
p.write_text(build())
print("relief.svg", len(p.read_text()), "bytes, K =", K)
