"""Built-in deliverable presets. Users can pick from these or define custom.

Each preset shapes the LLM prompt — it tells the planner the target duration,
the platform conventions, and the editorial style expected for that format.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DeliverablePreset:
    """A built-in deliverable type with its own editorial DNA."""
    key: str                    # internal identifier, e.g. "reel_15s"
    display_name: str           # human-friendly, shown in UI
    category: str               # "social", "ad", "long_form", "talking_head", "custom"
    target_duration_sec: float  # LLM aims for this, can deviate ±20%
    duration_tolerance: float   # acceptable ± seconds (tighter for ads, looser for long-form)
    style_notes: str            # editorial guidance injected into planner prompt
    aspect_hint: Optional[str]  # "9:16", "16:9", "1:1" — informs B-roll matching later

    # Stage 4 — sequence dimensions for Premiere XML export.
    # These default to 1920x1080@30 but every preset overrides them.
    # The overlay_style drives which safe-zone PNG is placed on V3.
    sequence_width: int = 1920
    sequence_height: int = 1080
    sequence_fps: float = 30.0
    overlay_style: str = "horizontal_1920x1080"  # from overlay.OverlayStyle


# ---------- SOCIAL ----------

REEL_15S = DeliverablePreset(
    key="reel_15s",
    display_name="15s Reel",
    category="social",
    target_duration_sec=15,
    duration_tolerance=2,
    style_notes=(
        "Hyper-condensed vertical social content. Open with a hook in the first "
        "1-2 seconds — the most surprising or emotionally charged line from the "
        "transcript. Every sentence must earn its place. No meandering. End with "
        "a memorable punch line or call-to-action, not a fade-out. Think TikTok/Reels "
        "retention curves: viewers swipe away fast."
    ),
    aspect_hint="9:16",
    sequence_width=1080,
    sequence_height=1920,
    sequence_fps=30.0,
    overlay_style="ig_reels_1080x1920",
)

REEL_30S = DeliverablePreset(
    key="reel_30s",
    display_name="30s Reel",
    category="social",
    target_duration_sec=30,
    duration_tolerance=3,
    style_notes=(
        "Short-form vertical social with room for one micro-arc: hook (3-5s), "
        "payoff content (20s), close (5s). Still needs a strong opener. Can "
        "support one narrative turn or 'reveal' — something the viewer didn't "
        "expect from the opening."
    ),
    aspect_hint="9:16",
    sequence_width=1080,
    sequence_height=1920,
    sequence_fps=30.0,
    overlay_style="ig_reels_1080x1920",
)

TIKTOK_60S = DeliverablePreset(
    key="tiktok_60s",
    display_name="60s TikTok",
    category="social",
    target_duration_sec=60,
    duration_tolerance=5,
    style_notes=(
        "Longer short-form. Allows a full story beat: setup, complication, "
        "resolution. Hook remains critical but you have room to let a moment "
        "breathe. Can support 2-3 distinct segments that build on each other."
    ),
    aspect_hint="9:16",
    sequence_width=1080,
    sequence_height=1920,
    sequence_fps=30.0,
    overlay_style="tiktok_1080x1920",
)

# ---------- ADS ----------

AD_15S = DeliverablePreset(
    key="ad_15s",
    display_name="15s Ad",
    category="ad",
    target_duration_sec=15,
    duration_tolerance=1,  # ads must hit duration exactly
    style_notes=(
        "Broadcast/pre-roll ad length. Must communicate one clear message: "
        "problem → solution, or value prop → proof. Open with the strongest "
        "single line that frames WHO this is for and WHY they should care. "
        "End with the brand message or call-to-action (brief). No wasted frames."
    ),
    aspect_hint="16:9",
    sequence_width=1920,
    sequence_height=1080,
    sequence_fps=30.0,
    overlay_style="horizontal_1920x1080",
)

AD_30S = DeliverablePreset(
    key="ad_30s",
    display_name="30s Ad",
    category="ad",
    target_duration_sec=30,
    duration_tolerance=1,
    style_notes=(
        "Classic TV/pre-roll ad length. Structure: hook (5s) → value prop (15s) "
        "→ proof or testimonial (7s) → CTA (3s). You have room for one piece of "
        "evidence or social proof. Prioritize segments with specific, concrete "
        "claims over abstract generalities."
    ),
    aspect_hint="16:9",
    sequence_width=1920,
    sequence_height=1080,
    sequence_fps=30.0,
    overlay_style="horizontal_1920x1080",
)

AD_60S = DeliverablePreset(
    key="ad_60s",
    display_name="60s Ad",
    category="ad",
    target_duration_sec=60,
    duration_tolerance=2,
    style_notes=(
        "Long-form ad or explainer. Can support a mini-narrative: character, "
        "struggle, resolution. Allows for 2-3 proof points or testimonials. "
        "Hook still in first 5s — viewers will skip. Build to an emotional peak "
        "before the CTA."
    ),
    aspect_hint="16:9",
    sequence_width=1920,
    sequence_height=1080,
    sequence_fps=30.0,
    overlay_style="horizontal_1920x1080",
)

AD_120S = DeliverablePreset(
    key="ad_120s",
    display_name="2min Ad",
    category="ad",
    target_duration_sec=120,
    duration_tolerance=5,
    style_notes=(
        "Brand film / manifesto length. Viewers who stay past 15s are invested — "
        "reward them with substance. Can develop a full argument or story arc. "
        "Multiple characters / perspectives / proof points. Still earn every "
        "sentence but pacing can breathe."
    ),
    aspect_hint="16:9",
    sequence_width=1920,
    sequence_height=1080,
    sequence_fps=30.0,
    overlay_style="horizontal_1920x1080",
)

# ---------- LONG-FORM ----------

YOUTUBE_HIGHLIGHT = DeliverablePreset(
    key="youtube_highlight",
    display_name="3-5min YouTube cut",
    category="long_form",
    target_duration_sec=240,  # 4 min target, tolerate 3-5
    duration_tolerance=60,
    style_notes=(
        "YouTube highlight reel or condensed interview. Structure like a "
        "documentary segment: cold open (30s), chapter structure, satisfying "
        "conclusion. Can develop 3-5 distinct themes or beats. Viewers chose "
        "to click, so front-load the hook but don't panic about retention as "
        "much as social. Prefer segments with clear through-lines over grab-bag "
        "highlights."
    ),
    aspect_hint="16:9",
    sequence_width=1920,
    sequence_height=1080,
    sequence_fps=30.0,
    overlay_style="horizontal_1920x1080",
)

YOUTUBE_EPISODE = DeliverablePreset(
    key="youtube_episode",
    display_name="10min YouTube episode",
    category="long_form",
    target_duration_sec=600,
    duration_tolerance=120,
    style_notes=(
        "Full YouTube episode length. Develops a complete argument or story. "
        "Needs internal chapter structure with clear transitions. Can include "
        "tangents, callbacks, and layered context. Balance is key — don't just "
        "pick the most interesting snippets; pick the ones that build on each "
        "other. Identify 4-7 distinct chapters/beats."
    ),
    aspect_hint="16:9",
    sequence_width=1920,
    sequence_height=1080,
    sequence_fps=30.0,
    overlay_style="horizontal_1920x1080",
)

# ---------- TALKING-HEAD ----------

TALKING_HEAD_FULL = DeliverablePreset(
    key="talking_head_full",
    display_name="Talking-head with B-roll cutaways",
    category="talking_head",
    target_duration_sec=-1,  # -1 = use full A-roll duration, don't trim
    duration_tolerance=0,
    style_notes=(
        "Full talking-head edit. DO NOT TRIM the A-roll — keep the entire "
        "transcript as-is. Your job is only to identify which moments benefit "
        "most from B-roll cutaways, and which should stay on the speaker. "
        "Prefer cutaways when: speaker references something visual ('the data "
        "showed...', 'we went to the warehouse...'), during list/enumeration "
        "moments, during emotional reflection (subtle cutaway to hold attention). "
        "Keep on speaker during direct address, emotional peaks with strong "
        "facial performance, and moments where their expression carries the "
        "meaning. Return segments covering the full duration with cutaway_density "
        "per segment (0=no B-roll, 1=heavy B-roll)."
    ),
    aspect_hint="16:9",
    sequence_width=1920,
    sequence_height=1080,
    sequence_fps=30.0,
    overlay_style="horizontal_1920x1080",
)

# ---------- NEW IN DROP 3: Platform-specific vertical presets ----------

FACEBOOK_REEL_30S = DeliverablePreset(
    key="facebook_reel_30s",
    display_name="30s Facebook Reel",
    category="social",
    target_duration_sec=30,
    duration_tolerance=3,
    style_notes=(
        "Short-form vertical for Facebook Reels. Facebook audiences skew older "
        "than TikTok — slightly slower pacing, less aggressive hook timing, more "
        "narrative payoff. Still needs the first 3s to land, but the close can "
        "breathe more than on TikTok. Emotional resonance over raw virality."
    ),
    aspect_hint="9:16",
    sequence_width=1080,
    sequence_height=1920,
    sequence_fps=30.0,
    overlay_style="facebook_reels_1080x1920",
)

YOUTUBE_SHORTS_60S = DeliverablePreset(
    key="youtube_shorts_60s",
    display_name="60s YouTube Shorts",
    category="social",
    target_duration_sec=60,
    duration_tolerance=4,
    style_notes=(
        "Vertical short-form for YouTube Shorts. YT audiences expect a bit more "
        "substance than TikTok — useful information, clearer structure. Hook in "
        "first 2-3 seconds, clear value prop by 10s, sustained payoff. Less frantic "
        "than TikTok's pacing, but still fast. Works well for educational/how-to "
        "snippets that lead into longer YouTube content."
    ),
    aspect_hint="9:16",
    sequence_width=1080,
    sequence_height=1920,
    sequence_fps=30.0,
    overlay_style="youtube_shorts_1080x1920",
)

X_VERTICAL_15S = DeliverablePreset(
    key="x_vertical_15s",
    display_name="15s X (Twitter) vertical",
    category="social",
    target_duration_sec=15,
    duration_tolerance=2,
    style_notes=(
        "Punchy vertical clip for X/Twitter. Auto-plays muted in the feed, so the "
        "first visual impression has to be striking without relying on audio. "
        "Captions or visual text overlays recommended (plan for them in the cut). "
        "Conversational tone — X users are skimming, so one clean idea per clip."
    ),
    aspect_hint="9:16",
    sequence_width=675,
    sequence_height=1200,
    sequence_fps=30.0,
    overlay_style="x_vertical_675x1200",
)

# ---------- ASPECT PRESETS (Drop 4.4) ----------
# Aspect-only presets define sequence dimensions. Overlay comes from the
# separately-selected PlatformOverlay, not from the aspect preset itself.
# These are user-facing in the Story Angle card's aspect dropdown.

ASPECT_VERTICAL = DeliverablePreset(
    key="aspect_vertical_9_16",
    display_name="Vertical · 9:16",
    category="aspect",
    target_duration_sec=60,
    duration_tolerance=60,
    style_notes="Vertical 9:16 frame (1080x1920).",
    aspect_hint="9:16",
    sequence_width=1080,
    sequence_height=1920,
    sequence_fps=30.0,
    overlay_style="none",  # Platform choice drives overlay, not preset
)

ASPECT_SQUARE = DeliverablePreset(
    key="aspect_square_1_1",
    display_name="Square · 1:1",
    category="aspect",
    target_duration_sec=60,
    duration_tolerance=60,
    style_notes="Square 1:1 frame (1080x1080).",
    aspect_hint="1:1",
    sequence_width=1080,
    sequence_height=1080,
    sequence_fps=30.0,
    overlay_style="none",
)

ASPECT_HORIZONTAL = DeliverablePreset(
    key="aspect_horizontal_16_9",
    display_name="Horizontal · 16:9 (HD)",
    category="aspect",
    target_duration_sec=60,
    duration_tolerance=60,
    style_notes="Horizontal 16:9 at 1080p (1920x1080).",
    aspect_hint="16:9",
    sequence_width=1920,
    sequence_height=1080,
    sequence_fps=30.0,
    overlay_style="none",
)

ASPECT_HORIZONTAL_4K = DeliverablePreset(
    key="aspect_horizontal_16_9_4k",
    display_name="Horizontal · 16:9 (4K)",
    category="aspect",
    target_duration_sec=60,
    duration_tolerance=60,
    style_notes="Horizontal 16:9 at 4K (3840x2160).",
    aspect_hint="16:9",
    sequence_width=3840,
    sequence_height=2160,
    sequence_fps=30.0,
    overlay_style="none",
)

# Narrow list of aspects exposed to the user in the story-angle flow
ASPECT_PRESET_KEYS = [
    "aspect_horizontal_16_9",
    "aspect_horizontal_16_9_4k",
    "aspect_vertical_9_16",
    "aspect_square_1_1",
]


# ---------- PLATFORM OVERLAYS (Drop 4.4) ----------
# Each PlatformOverlay binds a display name to an overlay_style (PNG asset)
# and a list of compatible aspect preset keys. The UI uses allowed_aspects
# to filter/auto-select the aspect dropdown once a platform is chosen.

@dataclass(frozen=True)
class PlatformOverlay:
    key: str                      # stable id, e.g. "platform_ig_reels"
    display_name: str             # dropdown label, e.g. "Instagram Reel"
    overlay_style: str            # PNG asset basename (no .png)
    allowed_aspects: tuple[str, ...]  # aspect preset keys compatible with this platform


PLATFORM_IG_REELS = PlatformOverlay(
    key="platform_ig_reels",
    display_name="Instagram Reel",
    overlay_style="ig_reels_1080x1920",
    allowed_aspects=("aspect_vertical_9_16",),
)

PLATFORM_TIKTOK = PlatformOverlay(
    key="platform_tiktok",
    display_name="TikTok",
    overlay_style="tiktok_1080x1920",
    allowed_aspects=("aspect_vertical_9_16",),
)

PLATFORM_YOUTUBE_SHORTS = PlatformOverlay(
    key="platform_youtube_shorts",
    display_name="YouTube Shorts",
    overlay_style="youtube_shorts_1080x1920",
    allowed_aspects=("aspect_vertical_9_16",),
)

PLATFORM_FACEBOOK_REELS = PlatformOverlay(
    key="platform_facebook_reels",
    display_name="Facebook Reels",
    overlay_style="facebook_reels_1080x1920",
    allowed_aspects=("aspect_vertical_9_16",),
)

PLATFORM_X_VERTICAL = PlatformOverlay(
    key="platform_x_vertical",
    display_name="X (Twitter) Vertical",
    overlay_style="x_vertical_675x1200",
    # X uses 675x1200 specifically, but the UI still offers it under
    # Vertical 9:16 since it's the same aspect family.
    allowed_aspects=("aspect_vertical_9_16",),
)

# YouTube Ad: one platform entry covers all four aspect variants. The
# overlay_style is resolved DYNAMICALLY at export time based on the picked
# aspect (see _resolve_overlay_style_for_platform below).
PLATFORM_YOUTUBE_AD = PlatformOverlay(
    key="platform_youtube_ad",
    display_name="YouTube Ad (safe-zone)",
    overlay_style="youtube_ad_DYNAMIC",  # sentinel; actual PNG picked per aspect
    allowed_aspects=(
        "aspect_horizontal_16_9",
        "aspect_horizontal_16_9_4k",
        "aspect_vertical_9_16",
        "aspect_square_1_1",
    ),
)


ALL_PLATFORMS: list[PlatformOverlay] = [
    PLATFORM_YOUTUBE_AD,
    PLATFORM_IG_REELS,
    PLATFORM_TIKTOK,
    PLATFORM_YOUTUBE_SHORTS,
    PLATFORM_FACEBOOK_REELS,
    PLATFORM_X_VERTICAL,
]

PLATFORMS_BY_KEY: dict[str, PlatformOverlay] = {p.key: p for p in ALL_PLATFORMS}


def resolve_overlay_style_for(platform_key: Optional[str], aspect_key: Optional[str]) -> str:
    """Return the overlay PNG basename for a (platform, aspect) selection.

    Rules:
      - If platform is None/unset → "none" (V3 stays empty)
      - If platform is YouTube Ad → pick the right youtube_ad_* variant by aspect dims
      - Otherwise → platform's static overlay_style
    """
    if not platform_key or platform_key == "none":
        return "none"
    platform = PLATFORMS_BY_KEY.get(platform_key)
    if platform is None:
        return "none"
    # YouTube Ad has per-aspect PNGs
    if platform.overlay_style == "youtube_ad_DYNAMIC":
        if aspect_key in ("aspect_horizontal_16_9", "aspect_horizontal_16_9_4k"):
            return "youtube_ad_1920x1080"
        if aspect_key == "aspect_square_1_1":
            return "youtube_ad_1080x1080"
        if aspect_key == "aspect_vertical_9_16":
            return "youtube_ad_1080x1920"
        # Unknown aspect — default to vertical
        return "youtube_ad_1080x1920"
    return platform.overlay_style


# ---------- REGISTRY ----------

ALL_PRESETS: list[DeliverablePreset] = [
    # Drop 4.3/4.4 aspect-only first — these are the defaults for story angles
    ASPECT_HORIZONTAL, ASPECT_HORIZONTAL_4K,
    ASPECT_VERTICAL, ASPECT_SQUARE,
    # Legacy duration-coupled presets (still used by the Deliverable flow)
    REEL_15S, REEL_30S, TIKTOK_60S,
    FACEBOOK_REEL_30S, YOUTUBE_SHORTS_60S, X_VERTICAL_15S,
    AD_15S, AD_30S, AD_60S, AD_120S,
    YOUTUBE_HIGHLIGHT, YOUTUBE_EPISODE,
    TALKING_HEAD_FULL,
]

PRESETS_BY_KEY: dict[str, DeliverablePreset] = {p.key: p for p in ALL_PRESETS}


def get_preset(key: str) -> DeliverablePreset:
    """Look up a preset by key, raise if not found."""
    if key not in PRESETS_BY_KEY:
        raise KeyError(
            f"Unknown preset '{key}'. Available: {list(PRESETS_BY_KEY.keys())}"
        )
    return PRESETS_BY_KEY[key]


def custom_preset(duration_sec: float, description: str) -> DeliverablePreset:
    """Build a custom preset on the fly (user freeform input)."""
    return DeliverablePreset(
        key=f"custom_{int(duration_sec)}s",
        display_name=f"Custom {duration_sec:.0f}s",
        category="custom",
        target_duration_sec=duration_sec,
        duration_tolerance=max(2, duration_sec * 0.1),
        style_notes=description,
        aspect_hint=None,
    )
