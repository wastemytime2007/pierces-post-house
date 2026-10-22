"""Em and en dashes never reach a story title, hook or tone note.

Ryan's standing rule: no em dashes in published copy, because it is the most-recognised AI-writing tell. A story's title and hook are read on the Stories page and become on-screen
text in the cut, so the model being asked nicely is not enough. `no_dashes` in `posthouse/story_architect.py` is the guarantee; these are its edges.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "posthouse" / "story_architect.py"


def _no_dashes():
    # The module imports PreCut at load time; only this one pure function is wanted, so it is read out and run on its own.
    text = SRC.read_text()
    start = text.index("def no_dashes(")
    end = text.index("TREND_TEXT_SEARCH_PROMPT = ")
    ns: dict = {}
    exec(compile(text[start:end], "story_architect_fragment", "exec"), ns)
    return ns["no_dashes"]


def test_a_dash_between_clauses_becomes_the_comma_it_stood_for():
    f = _no_dashes()
    assert f("Why we never cut corners — a sub's work") == "Why we never cut corners, a sub's work"
    assert f("quiet – unhurried") == "quiet, unhurried"


def test_a_dash_between_numbers_is_a_range_and_reads_as_one():
    f = _no_dashes()
    assert f("A 3–5 minute piece") == "A 3 to 5 minute piece"
    assert f("20—60 seconds") == "20 to 60 seconds"


def test_a_dash_at_either_end_leaves_no_stray_punctuation():
    f = _no_dashes()
    assert f("Ends with a dash —") == "Ends with a dash"
    assert f("— starts with one") == "starts with one"


def test_several_dashes_do_not_pile_up_commas():
    f = _no_dashes()
    assert f("one — two — three") == "one, two, three"
    assert f("a —, b") == "a, b"


def test_ordinary_text_is_returned_untouched():
    f = _no_dashes()
    for t in ["Plain title", "", "A hyphen-joined word stays", "3-5 stays a hyphen"]:
        assert f(t) == t


def test_nothing_is_not_an_error():
    f = _no_dashes()
    assert f("") == ""
