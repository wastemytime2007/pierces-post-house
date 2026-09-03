#!/usr/bin/env python3
"""Generate placeholder app icons for B-Roll Buddy.

Creates a simple but on-brand icon: dark background, cyan geometric mark.
Output: all sizes + .icns bundle Tauri requires.

Usage:
    python3 make_placeholder_icons.py <output_dir>
"""
import sys
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def make_icon(size: int) -> Image.Image:
    """Build one icon at the given size."""
    img = Image.new("RGBA", (size, size), (13, 15, 17, 255))  # --bg-0
    draw = ImageDraw.Draw(img)

    # Rounded square backdrop — matches macOS app icon shape
    radius = int(size * 0.22)
    bg_color = (20, 23, 26, 255)  # --bg-1
    _rounded_rect(draw, 0, 0, size - 1, size - 1, radius, bg_color)

    # The mark: a horizontal line over a vertical line, like a film strip crossed
    # with a waveform. Cyan accent.
    cyan = (0, 224, 224, 255)
    # Horizontal track (B-roll)
    bar_h = max(2, size // 40)
    y1 = size * 0.36
    draw.rectangle([size * 0.2, y1, size * 0.8, y1 + bar_h], fill=cyan)
    # Vertical stem (A-roll) crossing horizontal
    bar_w = max(2, size // 40)
    x2 = size * 0.5 - bar_w / 2
    draw.rectangle([x2, size * 0.22, x2 + bar_w, size * 0.78], fill=cyan)
    # Small square accent (the "cut")
    sq = max(3, size // 18)
    cx = size * 0.66
    cy = size * 0.56
    draw.rectangle([cx - sq/2, cy - sq/2, cx + sq/2, cy + sq/2], fill=cyan)

    return img


def _rounded_rect(draw, x1, y1, x2, y2, radius, fill):
    """PIL doesn't have rounded_rectangle in old versions — polyfill."""
    try:
        draw.rounded_rectangle([x1, y1, x2, y2], radius=radius, fill=fill)
    except AttributeError:
        draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill)
        draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill)
        draw.pieslice([x1, y1, x1 + 2 * radius, y1 + 2 * radius], 180, 270, fill=fill)
        draw.pieslice([x2 - 2 * radius, y1, x2, y1 + 2 * radius], 270, 360, fill=fill)
        draw.pieslice([x1, y2 - 2 * radius, x1 + 2 * radius, y2], 90, 180, fill=fill)
        draw.pieslice([x2 - 2 * radius, y2 - 2 * radius, x2, y2], 0, 90, fill=fill)


def main():
    if len(sys.argv) < 2:
        print("Usage: make_placeholder_icons.py <output_dir>")
        sys.exit(1)
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)

    # Tauri wants these specific filenames
    sizes = {
        "32x32.png": 32,
        "128x128.png": 128,
        "128x128@2x.png": 256,
        "icon.png": 512,
    }
    for fname, px in sizes.items():
        img = make_icon(px)
        img.save(out / fname, "PNG")

    # Build .icns via iconutil (macOS-only). Requires an .iconset directory.
    iconset_dir = out / "icon.iconset"
    iconset_dir.mkdir(exist_ok=True)
    icns_sizes = [(16, "16x16"), (32, "16x16@2x"), (32, "32x32"), (64, "32x32@2x"),
                  (128, "128x128"), (256, "128x128@2x"),
                  (256, "256x256"), (512, "256x256@2x"),
                  (512, "512x512"), (1024, "512x512@2x")]
    for px, name in icns_sizes:
        make_icon(px).save(iconset_dir / f"icon_{name}.png", "PNG")

    try:
        subprocess.run(
            ["iconutil", "-c", "icns", "-o", str(out / "icon.icns"), str(iconset_dir)],
            check=True, capture_output=True,
        )
        # Clean up intermediate iconset
        import shutil
        shutil.rmtree(iconset_dir)
    except (subprocess.CalledProcessError, FileNotFoundError):
        # iconutil missing = not on a Mac. Skip .icns; Tauri will warn but build.
        pass

    print(f"Icons written to {out}")


if __name__ == "__main__":
    main()
