"""reel_60s: a 60-second IG-Reels-branded preset.

2026-09-22, found exporting the Runnells Kitchen/Doors faucet Reel: only
reel_15s and reel_30s carried ig_reels_1080x1920 overlay art. A directed
plan whose real content ran to 82s (on-topic speech drove the length, not a
padded target) had no IG-branded preset to sit under — youtube_shorts_60s
and tiktok_60s are the same dimensions but the wrong overlay brand, and
relabeling the cut as a "30s Reel" would have been its own kind of wrong.
"""
from __future__ import annotations

from conftest import load_fork_module as _load_fork_module


def test_reel_60s_exists_and_is_ig_branded():
    presets = _load_fork_module("presets")
    assert "reel_60s" in presets.PRESETS_BY_KEY
    p = presets.PRESETS_BY_KEY["reel_60s"]
    assert p.overlay_style == "ig_reels_1080x1920", (
        "reel_60s must carry the IG Reels overlay art -- that's the whole "
        "point of it existing alongside reel_15s/reel_30s."
    )
    assert (p.sequence_width, p.sequence_height) == (1080, 1920)
    assert p.target_duration_sec == 60
    assert p.aspect_hint == "9:16"


def test_existing_ig_reels_presets_unaffected():
    """Adding reel_60s must not change the two presets it sits beside."""
    presets = _load_fork_module("presets")
    r15, r30 = presets.PRESETS_BY_KEY["reel_15s"], presets.PRESETS_BY_KEY["reel_30s"]
    assert r15.overlay_style == "ig_reels_1080x1920"
    assert r30.overlay_style == "ig_reels_1080x1920"
    assert r15.target_duration_sec == 15
    assert r30.target_duration_sec == 30


def test_get_preset_resolves_reel_60s():
    presets = _load_fork_module("presets")
    p = presets.get_preset("reel_60s")
    assert p.key == "reel_60s"
