"""The call to action with no reason (Ryan, 2026-10-15): the editor answered "not done: the note gives no wording". The idea's own words give the reason."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import finish_cut as fc  # noqa: E402
import ops as opsmod  # noqa: E402
import timeline  # noqa: E402


def _cut():
    v = [timeline.VideoClip(1, 0.0, 4.0, "/x/a.mp4", 10.0, 14.0)]
    return timeline.Cut("t", 30.0, 1080, 1920, 4.0, v, [])


FULL = "DM or comment 'septic' if you're selling a house you think might have one, or follow for more of what SoldFast learns on real projects."


def test_the_reason_is_the_ideas_own_clause_and_nothing_is_reworded():
    assert fc.cta_reason(FULL) == "if you're selling a house you think might have one"
    assert fc.cta_reason("DM us") == ""                                                   # the idea gives none: nothing is made up
    assert fc.cta_reason("DM 'septic' if " + "very " * 30 + "long") == ""                 # does not fit a line


def test_the_note_ryan_typed_and_its_plain_variants_are_a_cta_reason():
    cut = _cut()
    for text in ("This CTA doesnt make sense. It says \"DM Septic\" but gives no reason", "the call to action needs a reason", "CTA is unclear, why would I DM?", "the call to action says what to do but not why"):
        got = opsmod.validate([{"note": 1, "op": "cta_reason", "why": "x"}], [{"timeline_sec": 3.0, "clip": 1, "text": text}], cut)[0]
        assert got["op"] == "cta_reason", text


def test_a_note_that_is_not_about_the_cta_or_does_not_complain_is_refused():
    cut = _cut()
    for text in ("the title doesnt make sense", "love the CTA", "make the music quieter"):
        got = opsmod.validate([{"note": 1, "op": "cta_reason", "why": "x"}], [{"timeline_sec": 3.0, "clip": 1, "text": text}], cut)[0]
        assert got["op"] == "unsupported", text


def test_registered_as_made_by_the_rebuild():
    import capabilities
    assert capabilities.OP_MADE_BY["cta_reason"] == capabilities.REBUILD and "cta_reason" in opsmod.SYSTEM
