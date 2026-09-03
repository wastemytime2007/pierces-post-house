"""Configuration constants for B-Roll Buddy."""
from pathlib import Path

# Supported video extensions (proxies typically)
VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".mxf", ".avi", ".mkv",
    ".m4v", ".wmv", ".flv", ".webm", ".prores"
}

# How often to sample frames from each clip, in seconds.
# 2s is a good balance: catches scene changes without exploding storage.
FRAME_SAMPLE_INTERVAL_SEC = 2.0

# Max frames per clip (safety cap for very long clips)
MAX_FRAMES_PER_CLIP = 60

# Frame resolution for analysis (smaller = faster, CLIP wants 224x224 anyway)
FRAME_WIDTH = 384

# Where everything gets stored (inside your project folder, user-picked)
DB_FILENAME = "precut.db"
LANCE_DIR = "vectors.lance"
FRAMES_DIR = "frames"  # extracted keyframes, kept for preview

# CLIP model — ViT-B-32 is the sweet spot for speed/quality/size (~150MB)
# Upgrade to ViT-L-14 later if you want better semantic matching.
CLIP_MODEL_NAME = "ViT-B-32"
CLIP_PRETRAINED = "laion2b_s34b_b79k"

# Ollama settings for descriptive tagging
OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "llava:7b"
OLLAMA_TIMEOUT = 60  # seconds per frame

# Prompt for the VLM — tuned for concise, searchable tags
TAGGING_PROMPT = """Describe this video frame for a video editor searching for B-roll.
Output ONLY a comma-separated list of 5-12 concrete visual keywords.
Include: subjects, actions, setting, lighting, mood, camera angle.
No full sentences. No explanations. Just keywords.

Example: man typing laptop, close-up, office, warm lighting, focused, over-the-shoulder

Keywords:"""

# ---------- Stage 2: Whisper transcription ----------
# Model sizes: tiny (39M), base (74M), small (244M), medium (769M), large (1.5GB)
# "base" is the sweet spot for interview-style A-roll on a modern laptop.
# "medium" if you have a GPU and want better accuracy on accents/noise.
WHISPER_MODEL = "base"
WHISPER_LANGUAGE = None  # None = auto-detect. Set to "en" to skip detection.

# ---------- Stage 2.5: Deliverable planner ----------
# Anthropic API settings. Users provide API key via env var ANTHROPIC_API_KEY.
# Sonnet 4.6 is the right default: top-tier quality for editorial judgment at reasonable cost.
# Swap to "claude-opus-4-7" if you want the absolute best quality (pricier).
ANTHROPIC_MODEL = "claude-sonnet-4-6"
ANTHROPIC_MAX_TOKENS = 4096

# Rough cost estimate with Sonnet 4.6: a 30-minute interview transcript costs
# roughly $0.10-0.20 per Analyze & Recommend call. Effectively free.

# ---------- Stage 3: Matching Engine ----------

# CLIP cosine distance threshold — below this = "feels related."
# LanceDB returns cosine DISTANCE (0 = identical, 2 = opposite).
# Empirically calibrated against real footage: "fireplace" matches score
# distance ~0.685 (0.315 similarity), "bed" matches ~0.711 (0.289 similarity).
# Tune up/down based on your library after running some bb search queries.
MATCH_DISTANCE_THRESHOLD = 0.76

# Absolute floor — below this, nothing is "close enough" and we leave the
# segment on the speaker rather than forcing a bad cutaway.
MATCH_HARD_FLOOR = 0.78

# How many candidates per theme to pull from the vector DB.
# We over-fetch so the assignment optimizer has choices.
CANDIDATES_PER_THEME = 25

# Pacing-driven cut length targets. Words-per-second in the A-roll segment
# maps to target B-roll shot duration.
#   Fast talk (>3.0 wps)  → 1.2s cuts (energetic, punchy)
#   Normal    (2.0-3.0)   → 2.0s cuts
#   Slow/reflective (<2)  → 3.0s cuts (contemplative)
PACING_CUT_LENGTHS = {
    "fast": 1.2,
    "normal": 2.0,
    "slow": 3.0,
}

# Override from the plan's broll_pacing field — if planner said "heavy," we
# bias toward shorter shots regardless of speech rate.
PACING_PLAN_OVERRIDE = {
    "heavy": 0.85,   # multiplier — 15% shorter
    "medium": 1.0,
    "sparse": 1.3,   # multiplier — 30% longer (fewer, longer shots)
}

# Minimum and maximum B-roll clip duration on the timeline.
# Shorter than MIN feels like a glitch; longer than MAX overstays its welcome.
MIN_SHOT_DURATION_SEC = 0.6
MAX_SHOT_DURATION_SEC = 4.5

# Safety: leave this much silence-of-cutaway between the segment start and the
# first B-roll shot. Lets A-roll audio establish before cutting away.
SEGMENT_LEAD_IN_SEC = 0.15

# When cutaway_density is below this, we skip B-roll entirely on that segment.
MIN_DENSITY_FOR_BROLL = 0.15

# ---------- Phrase Padding (corrects Whisper's early-end bias) ----------
#
# Whisper's word-level timestamps consistently cut phrase endings 100-300ms
# too early. To avoid chopping off the last syllable of each line, we extend
# each phrase's source in/out during the match step.
#
# Padding is clamped to whatever silence is actually available — we won't
# pad INTO another spoken phrase. So if the next phrase starts 0.08s after
# this one ends, we can only extend by 0.04s (half the gap), regardless of
# the value below.
#
# Tweak these if your cuts still feel clipped OR if you're getting audible
# overlap between adjacent phrases:
#   - Too early / clipped endings → increase PHRASE_PADDING_END
#   - Audible breath-ins / overlap → decrease both
#   - Missing onset syllables → increase PHRASE_PADDING_START
PHRASE_PADDING_START = 0.08  # ~2 frames at 24fps
PHRASE_PADDING_END   = 0.25  # ~6 frames at 24fps (Whisper errs more on the end)
