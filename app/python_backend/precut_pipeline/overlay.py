"""Safe-zone overlay lookup for Stage 4 exports.

User-provided PNG assets live at ./overlays_assets/<style>.png. These get
copied into the export directory so the XML can reference them at a stable
path, and they're placed on the top video track of the exported sequence
as a visual guide. Before final export the editor disables the overlay
track so it doesn't render into the delivered video.

Previously this module generated overlays programmatically with PIL. We
replaced that with user-designed PNGs (platform-specific safezones) — much
more accurate and visually appropriate per platform.
"""
from pathlib import Path
from typing import Literal, Optional


OverlayStyle = Literal[
    "ig_reels_1080x1920",
    "tiktok_1080x1920",
    "youtube_shorts_1080x1920",
    "facebook_reels_1080x1920",
    "x_vertical_675x1200",
    "youtube_ad_1080x1920",    # vertical YouTube Ads overlay
    "square_1080x1080",
    "horizontal_1920x1080",
    "none",
]


# ---------------------------------------------------------------------------
# Asset discovery
# ---------------------------------------------------------------------------

def _assets_dir() -> Path:
    """Return the directory holding bundled overlay PNGs.

    The PNGs ship alongside this module in `overlays_assets/`. This path
    resolves correctly whether the module is run from source or from an
    installed .app bundle.
    """
    return Path(__file__).parent / "overlays_assets"


def get_overlay_path(style: OverlayStyle) -> Optional[Path]:
    """Return the absolute path to the overlay PNG for a given style.

    Returns None if style == "none" or if the asset file is missing.
    """
    if style == "none":
        return None
    candidate = _assets_dir() / f"{style}.png"
    return candidate if candidate.exists() else None


def list_available_styles() -> list[str]:
    """Return a list of styles that have a PNG asset on disk."""
    if not _assets_dir().exists():
        return []
    return sorted(p.stem for p in _assets_dir().glob("*.png"))


# ---------------------------------------------------------------------------
# Backwards-compat shims
# ---------------------------------------------------------------------------
# The exporter used to call generate_overlay() — now overlays come pre-baked.
# Keep a stub that raises a clear error if anyone tries to regenerate.

def generate_overlay(style: OverlayStyle, output_path: Path) -> Path:
    """Legacy shim — overlays are now bundled as assets, not generated.

    If an asset is missing, the caller should add it to overlays_assets/
    rather than expecting this module to create it.
    """
    src = get_overlay_path(style)
    if src is None:
        raise FileNotFoundError(
            f"No overlay asset for style '{style}'. "
            f"Add a PNG to {_assets_dir()}/ or pick a different style."
        )
    # If the caller wants a copy at a specific path, oblige.
    import shutil
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, output_path)
    return output_path
