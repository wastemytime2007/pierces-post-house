"""capabilities.py is the one list of what the app can do. These fail when the reviewer, the notes reader, the finish or the rebuild drift from it
(2026-10-09: a punch-in note came back "not supported" while the finish was making punch-ins, and graphics notes were accepted and made by nothing)."""
import inspect
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))

import capabilities as caps  # noqa: E402
import ops as opsmod  # noqa: E402
from timeline import Cut, VideoClip  # noqa: E402


def _reader_ops() -> set[str]:
    return set(re.findall(r"(?:^- |\}, )(\w+) \{", opsmod.SYSTEM, re.M))             # "- trim_start {...}, trim_end {...}" names two on one line


def test_every_op_the_notes_reader_can_choose_says_where_it_is_made():
    missing = _reader_ops() - set(caps.OP_MADE_BY)
    assert not missing, f"ops the reader can choose with no entry in capabilities.OP_MADE_BY: {missing}"


def test_every_op_in_the_registry_is_one_the_reader_can_choose():
    assert set(caps.OP_MADE_BY) - _reader_ops() == set()


def test_every_finish_step_is_a_registered_skill():
    src = (HERE.parent / "finish_cut.py").read_text()
    steps = set(re.findall(r'steps\.append\(\{"name": "([\w-]+)"', src))
    extra = steps - set(caps.SKILL_BY_NAME) - {"sfx"}                                  # "sfx" is the music skill's own note when the music is off
    assert not extra, f"finish steps with no entry in capabilities.FINISH_SKILLS (the reviewer and the notes reader would not know them): {extra}"
    assert set(caps.SKILL_BY_NAME) <= steps


def test_ops_the_registry_says_the_rebuild_makes_are_really_used_by_the_rebuild():
    sys.path.insert(0, str(REPO / "app" / "python_backend"))
    import creator_tools as ct
    src = inspect.getsource(ct._put_layers_back)
    assert '"edit_caption"' in src and "requests_from_notes" in src
    assert {op for op, by in caps.OP_MADE_BY.items() if by == caps.REBUILD} == {"edit_caption", "bleep_word"}


def test_ops_the_registry_says_nothing_makes_are_really_made_by_nothing_in_the_app():
    """If a step starts making one of these, this fails until the registry says so (and the result stops saying 'nothing makes this')."""
    src = (REPO / "app" / "python_backend" / "creator_tools.py").read_text()
    for tool in ("change_callout", "remove_graphic", "replace_sfx", "reconform"):
        assert tool not in src, f"creator_tools now uses {tool}: update capabilities.OP_MADE_BY"


def test_the_reviewer_is_told_every_finish_skill():
    for s in caps.FINISH_SKILLS:
        assert s.adds in caps.finish_adds()
    sys.path.insert(0, str(REPO / "app" / "python_backend"))
    import creator_tools as ct
    assert "capabilities.finish_adds()" in inspect.getsource(ct.idea_direction)


def _cut():
    cut = Cut("s", 30.0, 1080, 1920, 10.0)
    cut.video += [VideoClip(1, 0.0, 5.0, "/c.mp4", 100.0, 105.0), VideoClip(2, 5.0, 10.0, "/c.mp4", 105.0, 110.0)]
    return cut


def test_a_note_asking_for_what_the_finish_makes_is_reported_as_made_by_the_finish():
    n = {"note": 1, "text": "Make sure every other cut is cropped/reframed to avoid jump cuts", "timeline_sec": 1.0, "clip": 1, "shapes": []}
    o = opsmod.validate([{"note": 1, "op": "by_finish", "skill": "punch-in"}], [n], _cut())[0]
    assert o["op"] == "by_finish" and o["skill"] == "punch-in"
    assert "made by the finish" in caps.result_text("by_finish", o)
    old = opsmod.validate([{"note": 1, "op": "punch_in"}], [n], _cut())[0]                 # the name recorded runs used
    assert old["op"] == "by_finish" and old["skill"] == "punch-in"


def test_a_finish_skill_the_note_does_not_ask_for_is_refused():
    n = {"note": 1, "text": "the music is too loud here", "timeline_sec": 1.0, "clip": 1, "shapes": []}
    assert opsmod.validate([{"note": 1, "op": "by_finish", "skill": "punch-in"}], [n], _cut())[0]["op"] == "unsupported"


def test_a_note_nothing_makes_says_so_plainly():
    assert "nothing in the app makes this yet" in caps.result_text("remove_graphic")
    assert "made by the graphics step" not in caps.result_text("extend_graphic")
