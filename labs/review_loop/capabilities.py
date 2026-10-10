"""What the app can do to a cut, in one place: every skill the finish puts on a cut, and every op a note can become with where it is
really made. The AI reviewer, the notes reader and the notes result all read this, and tests/test_capabilities.py fails when a finish step
or a note op is added without an entry here.

Why (Ryan, 2026-10-09): "we keep fixing the same things and then on the next cut we lose the fixes and the app isnt remembering the skills
its supposed to have access to". The cause was three separate lists that drifted: the reviewer's prompt, the notes reader's prompt, and the
finish steps. A note asking for punch-ins came back "not supported" while the finish was already doing them, and notes about graphics or
sound effects were accepted and then made by nothing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Skill:
    name: str          # the finish step's own name (finish_cut.run's steps)
    adds: str          # what it puts on the cut, in words
    asks: str          # a note asking for this thing to be there (regex)


FINISH_SKILLS: tuple[Skill, ...] = (
    Skill("punch-in", "a closer shot on every other clip where one person talks across a cut (no jump cuts)",
          r"jump ?cuts?|\bcrop(ped)?\b|punch(ed)? in|zoom(ed)? in|every other (cut|clip|shot)"),
    Skill("graphics", "a title card with the topic, a name tag for each speaker, and the idea's call to action on screen over the end",
          r"title card|\btitle\b|name ?tags?|lower thirds?|call to action|\bcta\b|end card"),
    Skill("captions", "captions of every word", r"captions?|subtitles?"),
    Skill("music", "a music bed under the voice and a sound effect on each graphic", r"\bmusic\b|\bsong\b|soundtrack|sound effects?|\bsfx\b|whoosh"),
    Skill("bleep", "every curse word bleeped", r"bleep|curse|swear"),
)
SKILL_BY_NAME = {s.name: s for s in FINISH_SKILLS}

# Where each op a note can become is really made. "timeline": revise.py edits the cut. "rebuild": the cut is revised and then the layers are
# put back with this change (creator_tools._put_layers_back). "finish": every finish makes it anyway. "nowhere": understood, but no step in
# the app makes it yet, and the result says so instead of implying it was handed on.
TIMELINE = "timeline"
REBUILD = "rebuild"
FINISH = "finish"
NOWHERE = "nowhere"
OP_MADE_BY: dict[str, str] = {
    "tighten_pause": TIMELINE, "remove_range": TIMELINE, "trim_start": TIMELINE, "trim_end": TIMELINE, "extend_end": TIMELINE,
    "extend_start": TIMELINE, "start_at_words": TIMELINE, "end_at_words": TIMELINE, "move_clip": TIMELINE, "drop_clip": TIMELINE,
    "reframe_vertical": TIMELINE, "follow_speaker": TIMELINE,
    "edit_caption": REBUILD, "bleep_word": REBUILD,
    "by_finish": FINISH,
    "extend_graphic": NOWHERE, "end_graphic": NOWHERE, "edit_callout": NOWHERE, "remove_graphic": NOWHERE, "replace_sfx": NOWHERE,
    "unsupported": NOWHERE,
}

NOWHERE_SAY = {
    "extend_graphic": "keeping a graphic on screen longer",
    "end_graphic": "ending a graphic at a set moment",
    "edit_callout": "changing a graphic's words",
    "remove_graphic": "taking a graphic out",
    "replace_sfx": "swapping a sound effect for a different one",
}


def finish_adds() -> str:
    """The finish's skills in one sentence, for the reviewer: problems these solve are not raised."""
    return "; ".join(s.adds for s in FINISH_SKILLS)


def reader_lines() -> str:
    """The notes reader's lines for what the finish already does, so a note asking for one of them is reported as made, not unsupported."""
    names = ", ".join(f'"{s.name}" ({s.adds})' for s in FINISH_SKILLS)
    return ('- by_finish {"skill": name}  The note asks for something EVERY finish already puts on the cut, so it is made when the cut is finished, not on the timeline. '
            f"Skills: {names}. Use it only when the note asks for that thing to be there (\"crop every other jump cut\", \"add captions\"), never to change how it is done "
            '("the music is too loud", "the caption says the wrong word": those are other ops or unsupported).')


def skill_asked(skill: str, text: str) -> bool:
    s = SKILL_BY_NAME.get(skill)
    return bool(s and re.search(s.asks, text or "", re.I))


def result_text(op: str, o: dict | None = None) -> str:
    """What the notes result says for an op that is not made on the timeline."""
    o = o or {}
    if op == "by_finish":
        s = SKILL_BY_NAME.get(o.get("skill", ""))
        return f"made by the finish, which puts {s.adds if s else 'this'} on every finished cut" + ("" if not s else "; it is on the next finished version")
    if op in NOWHERE_SAY:
        return f"understood, but nothing in the app makes this yet ({NOWHERE_SAY[op]}); it needs you in Premiere for now"
    return ""
