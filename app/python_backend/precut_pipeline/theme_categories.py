"""Theme categorization for B-roll marker color-coding.

Takes a set of tags (or transcript text) and maps them to a theme category
like 'kitchen', 'bedroom', 'exterior', etc. Used in Drop 3.7+ to:
  1. Color-code markers on the Premiere timeline by subject matter
  2. Group multi-concept phrases into separate markers
     (e.g. "the kitchen and bathroom renovation" → 1 kitchen marker + 1 bathroom marker)

Design notes:
  - Categories are ordered by specificity so 'kitchen' beats generic 'room'
  - A tag can fall in at most one category (first match wins after tie-break)
  - 'other' is the catch-all for tags that don't fit any category
  - Color values use Premiere's 0-255 RGB convention per channel

The keyword vocabulary is tuned for Ryan's real-estate / home-renovation work
but the structure works for any domain — add more categories as needed.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ThemeCategory:
    """One category in the taxonomy."""
    key: str                           # machine identifier
    display_name: str                  # human-readable label
    keywords: frozenset[str]           # tag/word triggers (lowercase)
    color_rgb: tuple[int, int, int]    # Premiere marker color


# Categories ordered by specificity. Earlier entries take priority when a
# tag matches multiple (so "kitchen" beats the more generic "interior").
THEME_CATEGORIES: tuple[ThemeCategory, ...] = (
    # ---- Interior rooms (most specific first) ----
    ThemeCategory(
        key="kitchen",
        display_name="Kitchen",
        keywords=frozenset({
            "kitchen", "stove", "oven", "range", "refrigerator", "fridge",
            "dishwasher", "microwave", "pantry", "cabinetry", "cabinets",
            "cabinet", "countertop", "countertops", "counter", "counters",
            "backsplash", "island", "sink", "faucet",
            "cookware", "appliance", "appliances", "cooking",
        }),
        color_rgb=(255, 140, 0),     # orange
    ),
    ThemeCategory(
        key="bathroom",
        display_name="Bathroom",
        keywords=frozenset({
            "bathroom", "shower", "tub", "bathtub", "vanity", "toilet",
            "tile", "tiles", "mirror", "towel", "bath", "washroom",
            "restroom", "en-suite", "ensuite", "powder", "shampoo",
        }),
        color_rgb=(0, 200, 255),     # cyan
    ),
    ThemeCategory(
        key="bedroom",
        display_name="Bedroom",
        keywords=frozenset({
            "bedroom", "bed", "beds", "pillow", "pillows", "comforter", "sheets",
            "headboard", "nightstand", "dresser", "wardrobe", "closet",
            "bedding", "duvet", "mattress", "sleep", "sleeping", "bedrooms",
            "primary suite", "master", "master bedroom",
        }),
        color_rgb=(180, 100, 220),   # purple
    ),
    ThemeCategory(
        key="living_room",
        display_name="Living Room",
        keywords=frozenset({
            "living", "living room", "living area", "couch", "couches",
            "sofa", "sofas", "sectional", "loveseat", "recliner",
            "cushion", "cushions",
            "coffee table", "coffee", "tv", "television", "entertainment",
            "fireplace", "mantle", "mantel", "rug", "rugs",
            "family room", "great room", "family area", "den", "lounge",
            "living space", "sitting room", "sitting area",
            "wall art", "artwork", "art",
        }),
        color_rgb=(100, 180, 90),    # green
    ),
    ThemeCategory(
        key="dining_room",
        display_name="Dining",
        keywords=frozenset({
            "dining", "dining room", "dining table", "chandelier",
            "chairs", "place setting", "banquette", "breakfast nook",
            "breakfast", "eat-in",
        }),
        color_rgb=(220, 180, 50),    # yellow
    ),
    ThemeCategory(
        key="office",
        display_name="Office",
        keywords=frozenset({
            "office", "desk", "computer", "monitor", "bookshelf",
            "bookcase", "workspace", "study", "reading",
        }),
        color_rgb=(150, 150, 150),   # gray
    ),
    ThemeCategory(
        key="laundry",
        display_name="Laundry",
        keywords=frozenset({
            "laundry", "washer", "dryer", "mudroom", "utility",
        }),
        color_rgb=(100, 120, 150),   # slate
    ),

    # ---- Exterior / outdoor ----
    ThemeCategory(
        key="exterior",
        display_name="Exterior",
        keywords=frozenset({
            "exterior", "outside", "facade", "front", "entry", "porch",
            "driveway", "roof", "siding", "shingle", "gutter", "window",
            "street", "curb", "curb appeal", "doorstep",
        }),
        color_rgb=(160, 100, 60),    # brown
    ),
    ThemeCategory(
        key="yard",
        display_name="Yard / Garden",
        keywords=frozenset({
            "yard", "backyard", "garden", "lawn", "grass", "landscaping",
            "patio", "deck", "fence", "tree", "trees", "shrubs", "plants",
            "flowers", "outdoor", "pathway",
        }),
        color_rgb=(60, 150, 60),     # forest green
    ),
    ThemeCategory(
        key="pool",
        display_name="Pool / Water",
        keywords=frozenset({
            "pool", "swimming", "hot tub", "jacuzzi", "spa", "fountain",
            "water feature", "pond",
        }),
        color_rgb=(40, 120, 200),    # blue
    ),
    ThemeCategory(
        key="neighborhood",
        display_name="Neighborhood",
        keywords=frozenset({
            "neighborhood", "community", "park", "school", "downtown",
            "shopping", "aerial", "drone", "area", "location",
        }),
        color_rgb=(200, 100, 120),   # rose
    ),

    # ---- Subject / detail shots ----
    ThemeCategory(
        key="detail",
        display_name="Detail Shot",
        keywords=frozenset({
            "detail", "close-up", "closeup", "texture", "material",
            "hardware", "fixture", "trim", "molding", "finish",
            "marble", "granite", "quartz", "stone", "wood", "hardwood",
            "staging", "decor", "accent",
        }),
        color_rgb=(200, 200, 100),   # olive
    ),
    ThemeCategory(
        key="people",
        display_name="People",
        keywords=frozenset({
            "people", "person", "family", "couple", "children", "kids",
            "homeowner", "buyer", "agent", "broker", "walking", "lifestyle",
        }),
        color_rgb=(255, 100, 100),   # coral
    ),
)


# Default for anything unmatched
DEFAULT_CATEGORY = ThemeCategory(
    key="other",
    display_name="Other",
    keywords=frozenset(),
    color_rgb=(120, 120, 120),       # medium gray
)


def categorize_tag(tag: str) -> ThemeCategory:
    """Return the best category for a single tag. Defaults to 'other'."""
    t = tag.strip().lower()
    if not t:
        return DEFAULT_CATEGORY
    for cat in THEME_CATEGORIES:
        # direct hit on any keyword
        if t in cat.keywords:
            return cat
    # Substring fallback: category keyword appears inside the tag
    # (e.g. tag "bright kitchen" contains "kitchen")
    for cat in THEME_CATEGORIES:
        for kw in cat.keywords:
            if " " in kw:
                # multi-word keywords match only as substrings
                if kw in t:
                    return cat
            else:
                # single-word keywords match as whole words
                if _word_in_text(kw, t):
                    return cat
    return DEFAULT_CATEGORY


def categorize_tags(tags: list[str]) -> dict[str, list[str]]:
    """Group tags by theme category.

    Returns {category_key: [tags in that category]}. Tags that fit no
    category go under 'other'. Empty categories are omitted.
    """
    grouped: dict[str, list[str]] = {}
    for tag in tags:
        cat = categorize_tag(tag)
        grouped.setdefault(cat.key, []).append(tag)
    return grouped


def get_category(key: str) -> ThemeCategory:
    """Look up a category by key. Returns DEFAULT_CATEGORY if not found."""
    for cat in THEME_CATEGORIES:
        if cat.key == key:
            return cat
    return DEFAULT_CATEGORY


def _word_in_text(word: str, text: str) -> bool:
    """Case-insensitive whole-word check. Cheap substitute for regex."""
    text_lc = text.lower()
    word_lc = word.lower()
    # Find word as a standalone token in text. Allow hyphens and spaces
    # as boundaries.
    idx = 0
    while idx < len(text_lc):
        pos = text_lc.find(word_lc, idx)
        if pos < 0:
            return False
        before_ok = pos == 0 or not text_lc[pos - 1].isalnum()
        after_end = pos + len(word_lc)
        after_ok = after_end >= len(text_lc) or not text_lc[after_end].isalnum()
        if before_ok and after_ok:
            return True
        idx = pos + 1
    return False
