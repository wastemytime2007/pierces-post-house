#!/usr/bin/env python3
"""Build icon.icns from an existing icon.png using macOS's iconutil.

This script is CAREFUL about not clobbering any existing per-size PNG
(128x128.png, 128x128@2x.png, 32x32.png) that you may have hand-tuned.
It ONLY writes icon.icns and the intermediate iconset directory.

Why not use the placeholder-icon generator?
   The placeholder generator rebuilds every PNG from scratch with a
   generic crosshair mark. If you already have a designed icon in
   icon.png, we want to keep it and just package it.

Why iconutil vs Pillow's ICNS support?
   Pillow CAN write .icns but its output is not always recognized by
   Finder/Spotlight at all sizes — specifically the @2x variants end
   up flagged incorrectly. iconutil is Apple's tool and produces the
   exact format macOS expects.

Usage:
    python3 make_icns_from_icon.py <icons_dir>

Reads:   <icons_dir>/icon.png  (expected ≥ 512x512)
Writes:  <icons_dir>/icon.icns
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

# iconutil expects a directory named Foo.iconset/ containing files with
# these exact names. Sizes match Apple's spec for modern retina icons.
# https://developer.apple.com/library/archive/documentation/GraphicsAnimation/Conceptual/HighResolutionOSX/Optimizing/Optimizing.html
ICONSET_SPEC = [
    ("icon_16x16.png",      16),
    ("icon_16x16@2x.png",   32),
    ("icon_32x32.png",      32),
    ("icon_32x32@2x.png",   64),
    ("icon_128x128.png",    128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png",    256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png",    512),
    ("icon_512x512@2x.png", 1024),
]


def build_icns(icons_dir: Path) -> Path:
    source = icons_dir / "icon.png"
    if not source.exists():
        raise FileNotFoundError(f"No icon.png at {source}")

    # Load the source — we'll resample from this at each target size.
    src = Image.open(source).convert("RGBA")
    src_w, src_h = src.size

    if src_w < 512 or src_h < 512:
        # Not a hard error — we can still produce smaller sizes — but
        # the 1024 variant will be upscaled and look fuzzy in Finder.
        print(f"warning: icon.png is {src_w}x{src_h}; ≥512x512 recommended",
              file=sys.stderr)

    if src_w != src_h:
        print(f"warning: icon.png is {src_w}x{src_h} (not square); will stretch",
              file=sys.stderr)

    with tempfile.TemporaryDirectory(prefix="precut-iconset-") as tmpdir:
        iconset_dir = Path(tmpdir) / "icon.iconset"
        iconset_dir.mkdir()

        for filename, size in ICONSET_SPEC:
            # LANCZOS is the highest-quality downsampler in Pillow.
            # For upscaling (size > src_w) it still looks OK but we
            # warned above.
            scaled = src.resize((size, size), Image.LANCZOS)
            scaled.save(iconset_dir / filename, "PNG")

        # iconutil is bundled with macOS — no install needed. It reads
        # the iconset directory and emits a single .icns.
        out_icns = icons_dir / "icon.icns"
        result = subprocess.run(
            ["iconutil", "--convert", "icns",
             "--output", str(out_icns),
             str(iconset_dir)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"iconutil failed ({result.returncode}):\n{result.stderr}"
            )

    return out_icns


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: make_icns_from_icon.py <icons_dir>", file=sys.stderr)
        return 2

    icons_dir = Path(argv[1])
    if not icons_dir.is_dir():
        print(f"not a directory: {icons_dir}", file=sys.stderr)
        return 1

    # Sanity check: is iconutil available? If not, we're not on a Mac
    # or Xcode CLT is missing.
    if shutil.which("iconutil") is None:
        print("iconutil not found — this script only runs on macOS with "
              "Xcode Command Line Tools installed.", file=sys.stderr)
        return 1

    out = build_icns(icons_dir)
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
