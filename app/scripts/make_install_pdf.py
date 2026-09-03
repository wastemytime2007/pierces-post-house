"""Generate the user-facing install PDF for PreCut.

Two-page Letter PDF with generous spacing, 11pt body text, and
alternating image layout on page 2 (R, L, R) to create visual rhythm.

Inputs
------
- scripts/install_doc_images/1-dock-system-settings.png
- scripts/install_doc_images/2-sidebar-privacy-security.png
- scripts/install_doc_images/3-open-anyway-button.png
- src-tauri/icons/icon.png  (the P-with-cyan-wedge logo)
- The app version (passed on the command line)

Output
------
A 2-page PDF at the path given on the command line, typically:
    dist-release/Read Me First - Install Guide.pdf

Layout (Drop 4.44 revision 3)
-----------------------------
Page 1 — text-heavy, sets up the problem:
  * Header: 44pt logo + 18pt title + subtitle
  * Top callout: yellow "read before you click" warning
  * Steps 1-3: text-only, generous spacing, 11pt body
  * Bottom helper: "now turn the page to finish installing"

Page 2 — visual, walks through the security bypass:
  * Steps 4, 5, 6 with ALTERNATING image sides:
      4: text LEFT,  image RIGHT
      5: image LEFT, text RIGHT
      6: text LEFT,  image RIGHT
    The zig-zag keeps the eye moving and avoids the "three
    identical rows" feel.
  * Bottom callout: green-ish "Done!" reassurance
  * Footer tagline
"""
from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image as RLImage,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


SCRIPT_DIR = Path(__file__).parent
IMAGES_DIR = SCRIPT_DIR / "install_doc_images"
LOGO_PATH = SCRIPT_DIR.parent / "src-tauri" / "icons" / "icon.png"

# Colors
ACCENT = HexColor("#00a0a0")
FG = HexColor("#1a1d22")
MUTED = HexColor("#6b7280")
BORDER = HexColor("#d1d5db")

# Warning callout (top of page 1) — amber/yellow
WARN_BG = HexColor("#fef3c7")
WARN_BORDER = HexColor("#f59e0b")
# Done callout (bottom of page 2) — green
DONE_BG = HexColor("#dcfce7")
DONE_BORDER = HexColor("#22c55e")


def _styles():
    base = getSampleStyleSheet()
    s = {}

    s["Title"] = ParagraphStyle(
        "Title", parent=base["Title"],
        fontName="Helvetica-Bold", fontSize=22, leading=26,
        textColor=FG, spaceAfter=0, alignment=0,
    )
    s["Subtitle"] = ParagraphStyle(
        "Subtitle", parent=base["Normal"],
        fontName="Helvetica", fontSize=10, leading=13,
        textColor=MUTED, spaceAfter=0,
    )
    s["SectionLabel"] = ParagraphStyle(
        "SectionLabel", parent=base["Normal"],
        fontName="Helvetica-Bold", fontSize=9, leading=12,
        textColor=ACCENT, spaceAfter=4,
    )
    s["StepNum"] = ParagraphStyle(
        "StepNum", parent=base["Normal"],
        fontName="Helvetica-Bold", fontSize=14, leading=17,
        textColor=ACCENT,
    )
    s["StepTitle"] = ParagraphStyle(
        "StepTitle", parent=base["Normal"],
        fontName="Helvetica-Bold", fontSize=12, leading=15,
        textColor=FG, spaceAfter=3,
    )
    s["Body"] = ParagraphStyle(
        "Body", parent=base["Normal"],
        fontName="Helvetica", fontSize=11, leading=15,
        textColor=FG, spaceAfter=3,
    )
    s["Callout"] = ParagraphStyle(
        "Callout", parent=base["Normal"],
        fontName="Helvetica", fontSize=10.5, leading=14,
        textColor=FG, spaceAfter=0,
    )
    s["Footer"] = ParagraphStyle(
        "Footer", parent=base["Normal"],
        fontName="Helvetica-Oblique", fontSize=9, leading=11,
        textColor=MUTED, alignment=1,
    )
    s["PageHint"] = ParagraphStyle(
        "PageHint", parent=base["Normal"],
        fontName="Helvetica-Oblique", fontSize=10, leading=13,
        textColor=MUTED, alignment=1, spaceAfter=0,
    )
    s["ImageCaption"] = ParagraphStyle(
        "ImageCaption", parent=base["Normal"],
        fontName="Helvetica-Oblique", fontSize=8.5, leading=11,
        textColor=MUTED, alignment=1, spaceAfter=0,
    )
    return s


def _header(styles, version: str):
    """Logo + title + subtitle row. Used at the top of page 1."""
    if LOGO_PATH.exists():
        logo_cell = RLImage(str(LOGO_PATH), width=52, height=52)
    else:
        logo_cell = Paragraph("", styles["Body"])

    text_cell = [
        Paragraph("Install PreCut", styles["Title"]),
        Paragraph(
            f"Version {version} &middot; Apple Silicon Macs &middot; macOS 12 or later",
            styles["Subtitle"],
        ),
    ]
    text_table = Table([[p] for p in text_cell], colWidths=[5.5 * inch])
    text_table.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 0),
    ]))

    header = Table(
        [[logo_cell, text_table]],
        colWidths=[0.85 * inch, 6.1 * inch],
    )
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 14),
        ("RIGHTPADDING", (1, 0), (1, 0), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return header


def _step_textonly(styles, number: str, title: str, body_html: str):
    """Text-only step: big number on the left, title + body on the right.
    Used on page 1 (steps 1-3)."""
    left = Paragraph(number, styles["StepNum"])
    right = Table(
        [[Paragraph(title, styles["StepTitle"])],
         [Paragraph(body_html, styles["Body"])]],
        colWidths=[6.1 * inch],
    )
    right.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    t = Table([[left, right]], colWidths=[0.42 * inch, 6.2 * inch])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 16),
    ]))
    return t


def _image_cell(image_path: Path, caption: str, styles, width_pt: float):
    """Render an image with caption in a single cell. Returns a Flowable
    you can embed into an outer Table."""
    if not image_path.exists():
        return Paragraph(
            f"<i>[image missing: {image_path.name}]</i>",
            styles["ImageCaption"],
        )

    from PIL import Image as PILImage
    with PILImage.open(image_path) as im:
        orig_w, orig_h = im.size
    ratio = orig_h / orig_w
    rl_img = RLImage(str(image_path), width=width_pt, height=width_pt * ratio)

    # Thin border around the image so it pops off the page.
    bordered = Table([[rl_img]], colWidths=[width_pt])
    bordered.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    stack = Table(
        [[bordered], [Paragraph(caption, styles["ImageCaption"])]],
        colWidths=[width_pt],
    )
    stack.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 0),
    ]))
    return stack


def _text_cell(number: str, title: str, body_html: str, styles,
               text_width_pt: float):
    """Render step number + title + body as a stacked cell."""
    inner = Table(
        [
            [Paragraph(number, styles["StepNum"]),
             Paragraph(title, styles["StepTitle"])],
            ["",
             Paragraph(body_html, styles["Body"])],
        ],
        colWidths=[0.42 * inch, text_width_pt - 0.42 * inch],
    )
    inner.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return inner


def _step_with_image(styles, number: str, title: str, body_html: str,
                     image_path: Path, caption: str,
                     image_side: str):
    """A step with its screenshot beside the text.

    `image_side` is "left" or "right" — controls which column the image
    lives in. Used on page 2 to alternate the layout between steps.
    """
    IMG_W = 235  # screenshot width in points
    TEXT_W = 3.6 * inch

    text = _text_cell(number, title, body_html, styles, TEXT_W)
    img = _image_cell(image_path, caption, styles, IMG_W)

    if image_side == "right":
        left_cell, right_cell = text, img
        left_width, right_width = TEXT_W, IMG_W + 10
    elif image_side == "left":
        left_cell, right_cell = img, text
        left_width, right_width = IMG_W + 10, TEXT_W
    else:
        raise ValueError(f"image_side must be 'left' or 'right', got {image_side!r}")

    row = Table(
        [[left_cell, right_cell]],
        colWidths=[left_width, right_width],
    )
    row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 18),  # gap between columns
        ("RIGHTPADDING", (1, 0), (1, 0), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        # 16pt inter-step spacing (was 24) — gives the footer room
        # to land on page 2 alongside the callout.
        ("BOTTOMPADDING", (0, 0), (-1, -1), 16),
    ]))
    return row


def _callout(styles, body_html: str, bg, border):
    """A colored highlight box. Color passed in so we can theme
    different callouts (warning, success, info)."""
    p = Paragraph(body_html, styles["Callout"])
    t = Table([[p]], colWidths=[6.6 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.75, border),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    return t


def build(output_path: Path, version: str = "0.3.0") -> None:
    styles = _styles()

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=LETTER,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
        title=f"PreCut {version} - Install Guide",
        author="PreCut",
    )

    story = []

    # ============================================================
    # PAGE 1 — text-heavy, set up the problem
    # ============================================================
    story.append(_header(styles, version))
    story.append(Spacer(1, 22))

    # Intro callout: set expectations before they try anything. This is
    # the "heads up" that the next screen may show a scary dialog.
    story.append(_callout(
        styles,
        "<b>Heads up:</b> PreCut isn&rsquo;t notarized with Apple yet, so "
        "macOS will show a security dialog the first time you open it. "
        "This is expected &mdash; the steps below walk you through a "
        "one-time bypass in System Settings. PreCut is safe and runs "
        "entirely on your Mac with no telemetry.",
        WARN_BG, WARN_BORDER,
    ))
    story.append(Spacer(1, 24))

    # Section label
    story.append(Paragraph("FIRST, INSTALL THE APP", styles["SectionLabel"]))
    story.append(Spacer(1, 8))

    story.append(_step_textonly(
        styles, "1.", "Double-click the downloaded zip",
        "Finder will extract a folder containing <b>PreCut.app</b> "
        "and this PDF. You can delete the zip afterward if you want."
    ))
    story.append(_step_textonly(
        styles, "2.", "Drag PreCut.app into your Applications folder",
        "Open a new Finder window, click <b>Applications</b> in the sidebar, "
        "and drag <b>PreCut.app</b> into it. This is where macOS expects "
        "installed apps to live."
    ))
    story.append(_step_textonly(
        styles, "3.", "Double-click PreCut to open it",
        "A dialog will appear saying <b>&ldquo;PreCut&rdquo; Not Opened</b>. "
        "<b>Click Done.</b> Don&rsquo;t click &ldquo;Move to Trash&rdquo; &mdash; "
        "the next three steps will unblock it permanently."
    ))

    # Continuation hint
    story.append(Spacer(1, 40))
    story.append(Paragraph(
        "Turn the page &rarr; to finish unblocking PreCut.",
        styles["PageHint"],
    ))

    # ============================================================
    # PAGE 2 — visual, walk them through the security bypass
    # ============================================================
    story.append(PageBreak())

    # Section label at the top of page 2
    story.append(Paragraph("THEN, UNBLOCK IT IN SYSTEM SETTINGS",
                           styles["SectionLabel"]))
    story.append(Spacer(1, 6))

    # Step 4 — text LEFT, image RIGHT
    story.append(_step_with_image(
        styles, "4.", "Open System Settings",
        "Click the gear icon in your Dock, or press <b>Cmd+Space</b> "
        "and type &ldquo;System Settings&rdquo; to search for it.",
        IMAGES_DIR / "1-dock-system-settings.png",
        "System Settings in the Dock.",
        image_side="right",
    ))

    # Step 5 — image LEFT, text RIGHT  (the switcheroo for rhythm)
    story.append(_step_with_image(
        styles, "5.", "Click Privacy &amp; Security in the sidebar",
        "It&rsquo;s about halfway down the left sidebar, next to a blue "
        "hand icon. You may need to scroll the sidebar to find it.",
        IMAGES_DIR / "2-sidebar-privacy-security.png",
        "Privacy & Security in the sidebar.",
        image_side="left",
    ))

    # Step 6 — text LEFT, image RIGHT (back to the original side)
    story.append(_step_with_image(
        styles, "6.", "Scroll down and click Open Anyway",
        "Scroll the right-hand panel all the way to the bottom. Under "
        "the <b>Security</b> heading you&rsquo;ll see &ldquo;<i>PreCut "
        "was blocked to protect your Mac</i>&rdquo; with an <b>Open "
        "Anyway</b> button. Click it, enter your password or Touch ID, "
        "and click <b>Open</b> on the final confirmation dialog.",
        IMAGES_DIR / "3-open-anyway-button.png",
        "The Open Anyway button.",
        image_side="right",
    ))

    # Done callout
    story.append(_callout(
        styles,
        "<b>That&rsquo;s it.</b> PreCut will launch and show a first-time "
        "setup screen that installs ffmpeg and a few Python tools "
        "(5&ndash;10 minutes). From now on, you can open PreCut by "
        "double-clicking it like any other app &mdash; you won&rsquo;t "
        "need to visit System Settings again.",
        DONE_BG, DONE_BORDER,
    ))

    # Footer
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        f"PreCut {version} &middot; local-first &middot; no telemetry",
        styles["Footer"],
    ))

    doc.build(story)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: make_install_pdf.py <output_path> [version]",
              file=sys.stderr)
        return 2
    out = Path(argv[1])
    version = argv[2] if len(argv) > 2 else "0.3.0"
    out.parent.mkdir(parents=True, exist_ok=True)
    build(out, version)
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
