"""fix_caption.collect: a note's caption edit can change this line, the next or the previous one (Ryan, 2026-10-12: "move the 23 to the next caption with the 000 so it says $23,000")."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import fix_caption as fc  # noqa: E402

GROUPS = [{"text": "so the plan is", "show_start": 15.0, "show_end": 16.6}, {"text": "this septic, it costs $23", "show_start": 17.0, "show_end": 18.9},
          {"text": "000 is the quote we", "show_start": 19.0, "show_end": 20.4}, {"text": "have for it.", "show_start": 20.5, "show_end": 21.3}]
NOTE = [{"timeline_sec": 18.0, "text": "Make the 23 move over to the next caption with the 000 so it says $23,000"}]


def test_a_note_that_moves_words_to_the_next_caption_changes_both_lines_and_keeps_each_to_its_own_words():
    ops = [{"note": 1, "op": "edit_caption", "text": "this septic, it costs", "line": "this"}, {"note": 1, "op": "edit_caption", "text": "$23,000 is the quote we", "line": "next"}]
    fixes, ledger = fc.collect(ops, NOTE, GROUPS, 0.0, [])
    assert [f["was"] for f in fixes] == ["this septic, it costs $23", "000 is the quote we"]
    assert [f["text"] for f in fixes] == ["this septic, it costs", "$23,000 is the quote we"]
    assert 17.0 <= fixes[0]["at"] <= 18.9 and 19.0 <= fixes[1]["at"] <= 20.4                  # each fix sits at its own line's time
    assert all(e["applied"] for e in ledger)


def test_a_note_for_the_previous_line_and_one_with_no_such_neighbour():
    ops = [{"note": 1, "op": "edit_caption", "text": "so the plan was", "line": "previous"}]
    fixes, _ledger = fc.collect(ops, NOTE, GROUPS, 0.0, [])
    assert fixes[0]["was"] == "so the plan is" and fixes[0]["text"] == "so the plan was"
    first = [{"timeline_sec": 15.5, "text": "x"}]
    fixes, ledger = fc.collect(ops, first, GROUPS, 0.0, [])
    assert fixes == [] and ledger[0]["applied"] is False                                        # nothing before the first line: not applied, nothing changed
