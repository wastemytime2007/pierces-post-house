import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_edges as ce

CUTS = [{"role": "The point", "from": "if a sub says", "to": "what you should know"}, {"role": "Bob", "from": "And it can make you look like an ass", "to": "look like an ass"}]
SPANS = [(0.0, 3.0), (3.0, 4.5)]


def W(text, a, b):
    return {"text": text, "start": a, "end": b}


def test_a_cut_that_starts_on_the_tail_of_the_previous_word_is_caught():
    words = [W("is", 0.0, 0.2), W("if", 0.25, 0.4), W("a", 0.45, 0.5), W("sub", 0.5, 0.7), W("says", 0.7, 0.9), W("what", 2.0, 2.2), W("you", 2.2, 2.3), W("should", 2.3, 2.5), W("know.", 2.5, 2.9),
             W("And", 3.05, 3.2), W("it", 3.2, 3.3), W("look", 3.6, 3.8), W("like", 3.8, 4.0), W("an", 4.0, 4.1), W("a**.", 4.1, 4.4)]
    rows = ce.edge_rows(CUTS, SPANS, words)
    assert rows[0][1] is False and "starts on 'is' (wanted 'if')" in rows[0][2]
    assert rows[1][1] is True                                            # a bleeped word ('a**') matches the word it hides


def test_a_clean_cut_passes_and_one_that_ends_early_or_starts_late_does_not():
    ok = [W("if", 0.05, 0.2), W("a", 0.2, 0.3), W("what", 2.0, 2.2), W("you", 2.2, 2.3), W("should", 2.3, 2.5), W("know", 2.55, 2.9)]
    assert ce.edge_rows(CUTS[:1], SPANS[:1], ok)[0][1] is True
    early = [W("if", 0.05, 0.2), W("what", 1.0, 1.2), W("know", 1.3, 1.5)]                # the last word ends 1.5 s before the cut does: the end of the line was cut off, or the words are not there
    assert ce.edge_rows(CUTS[:1], SPANS[:1], early)[0][1] is False
    late = [W("if", 1.2, 1.4), W("know", 2.7, 2.9)]                                      # the first word starts 1.2 s in: something else plays first
    assert ce.edge_rows(CUTS[:1], SPANS[:1], late)[0][1] is False


def test_starred_words_match_letter_for_letter_and_plain_variants_are_fuzzy():
    assert ce.same("a**.", "ass") and ce.same("f***ing", "fucking")
    assert not ce.same("b**", "ass") and not ce.same("a***", "ass")                     # a different first letter or length is not the same word
    assert ce.same("checked", "check") and ce.same("Know.", "know") and not ce.same("is", "if")
