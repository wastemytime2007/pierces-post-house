"""Review notes -> timeline operations.

The vocabulary is deliberately small and every op is something that can be done
to an XML timeline deterministically and checked afterwards. The interpreter (an
LLM) only chooses among them; it does not get to invent a time.

  tighten_pause {at}             find the real silence near `at` in the audio and
                                 shorten it. Measured, not guessed.
  extend_end {clip}              a word is cut off; the amount is read from the audio's decay
  start_at_words {clip, words}   start the clip at named words, found with word timing
  remove_range {start, end}      only when the note itself states the times
  trim_start / trim_end {clip, seconds}   only when the note states the amount
  extend_start {clip, max_sec}   a word at the START of the clip is cut off (the clip starts in the middle of a sound); the start moves earlier to where the sound begins, measured
  drop_clip {clip}               only when the note says to remove the clip
  reframe_vertical {clip, direction}   a punched-in shot sits too high or too low in the vertical frame (Reel 3: "lower it so Mitch's head isn't cropped off at
                                 the top"). direction is "lower" or "raise" and must be the note's own. The amount is one step (12% of the shot's height)
                                 unless a later version learns to read one; it changes the clip's Basic Motion in the XML, not its timing, so nothing ripples.
  follow_speaker {clip}          the note wants the picture to be on whoever is talking ("the framing should follow the speaker", "it isn't framed on either of the speakers"). The
                                 recorder on each person says who talks when, a face finder says where each stands, and the clip is split at each change of speaker with the
                                 picture centred on the one talking (follow_speaker.py). "clip" is the note's own clip, or "all" when the note is about the whole cut.
  replace_sfx {sound}            the note wants a sound EFFECT at this moment to sound different; the
                                 description is the note's own words. Made by labs/audio/replace_sfx.py,
                                 not on the timeline, so revise.py reports it as not applied here.
  extend_graphic {seconds}       the note wants an on-screen graphic (callout, text bubble, arrow) to stay up
                                 longer. `seconds` only if the note states an amount, else null (the applier
                                 uses a stated default step and says so). Made by labs/overlay/change_callout.py,
                                 not on the timeline, so revise.py reports it as not applied here.
  edit_caption {text}            the note gives the words a caption line should have. `text` is copied from the note (quoted or plainly
                                 stated); the line at the note's moment is replaced, everything else is kept. Made by
                                 labs/captions/fix_caption.py, not on the timeline, so revise.py reports it as not applied here.
  remove_graphic {}              the note wants an on-screen graphic (a callout or an image card) taken out entirely. The graphic is
                                 found from the note's element or its moment. Made by labs/overlay/remove_graphic.py plus reconform
                                 --drop, not on the timeline, so revise.py reports it as not applied here.
  bleep_word {}                  the note wants a word bleeped. Curse words on labs/bleep/profanity.txt are bleeped automatically anyway; this
                                 points the bleep at a stretch (the clip or element the note is on, else about a second around its
                                 moment) for a word the transcript did not show. Made by labs/bleep/bleep.py, not on the timeline, so
                                 revise.py reports it as not applied here.
  end_graphic {}                 the note wants an on-screen graphic (callout) to fade out at the moment the note is about. The time
                                 is the note's own time, never the interpreter's. Made by labs/overlay/change_callout.py, not on the
                                 timeline, so revise.py reports it as not applied here.
  edit_callout {title, subtitle, remove_subtitle}
                                 the note wants the words on a callout changed, or its smaller second line
                                 removed. New words come only from the note; nothing is removed that the note
                                 does not ask to remove. Made by labs/overlay/change_callout.py, not on the
                                 timeline, so revise.py reports it as not applied here.
  unsupported {reason}           everything else, reported and never silently dropped

All times are on the timeline the notes were left on (V1).
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

import numpy as np

import words as words_mod
from timeline import Cut

SR = 8000
KEEP_SEC_DEFAULT = 0.15
MIN_SILENCE_SEC = 0.25
NOTE_WORDS = r"one|two|three|four|five|six|seven|eight|nine|ten|half"
HAS_NUMBER = re.compile(rf"\d|\b({NOTE_WORDS})\b", re.I)
SAYS_SOUND = re.compile(r"\b(sfx|sound effects?|sound|noise|whoosh|swoosh|pop|click|ding|chime|beep|clap|thud)\b", re.I)
SAYS_GRAPHIC = re.compile(r"\b(text|graphic|graphics|arrow|callout|call-out|bubble|label|title|caption box|highlight|overlay|box|cards?|screenshot)\b", re.I)
SAYS_FOOTAGE = re.compile(r"\b(clip|shot|footage|scene|take|b-?roll)\b", re.I)
SAYS_LONGER = re.compile(r"\b(longer|more time|more seconds?|extra (time|seconds?)|linger|stay(s)?|sit|hold|too (short|fast|quick|brief)|only (shows?|appears?|lasts?)|half a second|barely)\b|\bkeep\b[^.]{0,40}\b(up|on)\b", re.I)
SAYS_END = re.compile(r"\b(fade(s|d)? out|fade(s|d)? away|end(s)? here|stop(s)? here|disappear|go(es)? away|come(s)? off|take (it|this|that) off|off the screen|off screen|shorter|less time)\b", re.I)
SAYS_CAPTION = re.compile(r"\b(captions?|subtitles?)\b", re.I)
SAYS_BLEEP = re.compile(r"\b(bleep|beep|censor|curse word|swear|profanity|cuss|expletive|f-?word|mute (that|the|this) word)", re.I)
SAYS_NO = re.compile(r"^\s*(no|nope|nah|wrong|not (it|this|that|a curse word)|ignore|skip|false|that'?s not)\b|\b(not a curse word|isn'?t a curse word|is not a curse word|don'?t bleep|do not bleep|no bleep|leave (it|that|this) (alone|in))\b", re.I)
SAYS_EDIT = re.compile(r"\b(change|rename|reword|replace|instead|say(s)?|read(s)?|wording|words|remove|delete|get rid|drop|without|shorten|simplify|no )\b", re.I)
SAYS_SUBTITLE = re.compile(r"\b(subtitle|sub-title|second line|smaller text|small text|smaller line|description|underneath|line (below|under)|sub text|subtext)\b", re.I)
SAYS_REMOVE = re.compile(r"\b(remove|delete|drop|get rid|lose|kill|take (this|it|that|the [a-z ]{1,30}?) (out|off)|cut (this|it|that)( out| clip| shot)?)\b", re.I)

SAYS_FRAMING = re.compile(r"\b(frame|framing|framed|crop(ped)?|cut off|head|screen|position(ed)?|reposition|shot|angle)\b", re.I)
SAYS_LOWER = re.compile(r"\b(lower|lowered|lowering|down|bring (it|this|them) down|drop (it|this)|move (it|this) down)\b", re.I)
SAYS_RAISE = re.compile(r"\b(raise|raised|raising|higher|up|bring (it|this|them) up|move (it|this) up)\b", re.I)
CUT_OPS = {"tighten_pause", "remove_range", "trim_start", "trim_end", "extend_end", "extend_start", "start_at_words", "end_at_words", "move_clip", "drop_clip", "reframe_vertical", "follow_speaker"}
SAYS_MOVE = re.compile(r"\b(move|moved|moving|put|place|before|after|earlier|first|open(s|ing)? (with|on)|lead (with|in)|order|reorder|swap|switch (the )?order|sets? (it )?up)\b", re.I)
SAYS_FOLLOW = re.compile(r"\b(follow(s|ing)?|track(s|ing)?|on|center(ed|ing)?|centre(d|ing)?)\b.{0,40}\b(speak(er|ers|ing)|talk(er|ing)|whoever|who(\'s| is) (talking|speaking)|person|both)\b|\b(speak(er|ers|ing)|talk(er|ing)|whoever)\b.{0,40}\b(frame|framed|framing|follow|center|centre|on screen)\b|\bframed on (either|both|neither)\b", re.I)
SAYS_SWITCH = re.compile(r"\b(re-?frame|cut(ting)? back and forth|back and forth|switch(ing)? (to|between)|audio source|source audio|mic(rophone)? (source )?(to|for))\b.{0,120}\b(speak(er|ers|ing)|talk(er|ing)|person|people|him|her|them)\b|\b(speak(er|ers))\b.{0,120}\b(re-?frame|cut(ting)? back|switch|audio source|source audio)\b", re.I)
SAYS_SHORT = re.compile(r"\b(yeah|yep|right|uh[- ]?huh|mm+[- ]?hm+|okay|ok|just says?|even if|brief|short|one word|interject\w*|quick)\b", re.I)
# Operations a note may carry as its own measured fix ("suggested_op", written by the AI review): used as they are, the interpreter is not asked, and only structure is checked.
# The amounts are measured later from the audio, never taken from the note; max_sec only bounds how far the measurement may look (4 s lets an end run on to the next pause).
SUGGESTIBLE_OPS = {"extend_start", "extend_end", "drop_clip", "start_at_words", "end_at_words", "move_clip", "follow_speaker"}
SUGGEST_MAX_SEC = 4.0
# What a note left on a timeline element (a box on the review page's map) may turn into. A lane with no entry
# has no note-driven tool yet, so such a note is reported rather than guessed at.
LANE_OPS = {"Suspects": {"bleep_word"}, "Card": {"remove_graphic"}, "Captions": {"edit_caption"}, "Clips": CUT_OPS | {"bleep_word"}, "Cuts": CUT_OPS, "Edits": CUT_OPS, "SFX": {"replace_sfx"}, "Callout": {"extend_graphic", "edit_callout", "end_graphic", "remove_graphic"}}
LANE_WHY = {"Music": "no tool changes the music bed from a note yet"}


def target_of(note: dict) -> dict | None:
    """The timeline element a note was left on (lane, label, start, end, clip), or None for a note left at the playhead."""
    t = note.get("target")
    return t if isinstance(t, dict) and t.get("lane") else None


SYSTEM = """You translate an editor's review notes on a rough cut into edit operations.
Reply with ONLY a JSON array. Each item: {"note": <1-based note number>, "op": <name>, ...params, "why": <one short sentence>}.

Operations (times are seconds on the timeline the notes were left on):
- tighten_pause {"at": t}            The note says a pause/gap/silence/dead air should be tighter or cut. Use the note's own time as "at". The pause is located later by measuring the audio, so never guess a duration.
- remove_range {"start": s, "end": e}  ONLY if the note itself states both times.
- trim_start {"clip": n, "seconds": x}, trim_end {"clip": n, "seconds": x}  ONLY if the note states how many seconds.
- extend_end {"clip": n, "max_sec": 1.0}  The note says a word or sentence at the END of clip n is cut off too soon or needs more time to finish. "clip" is the note's own clip unless the note says otherwise. The amount is measured from how the sound decays, so never give a duration.
- extend_start {"clip": n, "max_sec": 1.0}  The note says a word at the START of clip n is cut off, or the clip starts in the middle of a word or sound, or needs a little more lead-in. "clip" is the note's own clip unless the note says otherwise. The amount is measured from the audio, so never give one.
- start_at_words {"clip": n, "words": "..."}  The note says clip n should START at specific words, dropping words before them (for example "the clean cut should be X to Y": the clip after the seam starts at Y). "words" must be copied from the note. The point is found by listening, so never give a time.
- end_at_words {"clip": n, "words": "..."}  The note says clip n should END after specific words, dropping what comes after them (for example "trim the out-point to end at 'until we're under contract'"). "words" must be copied from the note: the last words to keep. The point is found by listening, so never give a time.
- move_clip {"clip": n, "before": m}  The note says clip n should come earlier, before clip m (a line that sets up another should play first). Only when the note asks for the order to change.
- drop_clip {"clip": n}              ONLY if the note clearly says to remove/delete that clip or shot.
- reframe_vertical {"clip": n, "direction": "lower" or "raise"}  The note says a shot sits too HIGH or too LOW in the (vertical) frame and should be moved down or up on screen, for example a head cropped at the top of the frame ("lower it so his head isn't cut off" is "lower": the picture moves down on screen). "clip" is the note's own clip unless the note says otherwise. "direction" must be the way the note says to move it. The amount is a fixed step, so never give one. Not for zooming in or out, moving sideways or cropping (those are unsupported).
- follow_speaker {"clip": n or "all", "short_turns": true or false}  The note says the picture should be framed on, centred on or follow the person who is TALKING in a shot that shows two people side by side (for example "the framing should follow the speaker", "it isn't framed on either of the speakers"). "clip" is the note's own clip, or "all" when the note is about the whole cut. Use it too when the note asks to cut or switch back and forth between the speakers, to reframe to whoever is talking, or to use the talking person's own microphone: the picture and the sound both follow the speaker. Set "short_turns" true when the note says even a short reply ("yeah", "right") should get the cut. Never give a position or a time: who talks when and where each person stands are measured.
- replace_sfx {"sound": "..."}       The note says a sound EFFECT (a whoosh, pop, click, swoosh, "sound effect") at this moment should sound different, and says what it should sound like. "sound" must be copied from the note's own words describing the wanted sound. The effect and its time are found from the audio project, so never give a time.
- extend_graphic {"seconds": x or null}  The note says an on-screen GRAPHIC (a callout, text bubble, arrow, label) is on screen too briefly and should stay longer. Give "seconds" ONLY if the note states an amount (for example "two more seconds"); otherwise use null. Never guess an amount. The graphic and its time are found from the graphics project, so never give a time.
- edit_caption {"text": "..."}  The note says what a CAPTION (subtitle) line should read ("fix the caption to say ..."). "text" must be copied from the note's own words (usually quoted); never write your own. The line and its time are found from the captions project, so never give a time. Not for callouts or other graphics (that is edit_callout).
- remove_graphic {}  The note says an on-screen GRAPHIC (a callout, text bubble or image card) should be removed, deleted or taken out ("why is this here? remove it ... cards"). The graphic is found from the note's element or moment, so never give a time. NEVER use drop_clip for a graphic: drop_clip removes footage.
- bleep_word {}  The note says to bleep, beep, censor or mute a curse word or swear word ("lets bleep the curse word here"). No parameters: the stretch is the note's clip or element, else its moment. Never give a time.
- A note that says NO to something ("no", "that's not a curse word", "don't bleep it") is never a bleep_word: use unsupported with the reason "rejected".
- end_graphic {}  The note says an on-screen GRAPHIC (callout, text bubble, label) should fade out, end or go away at the moment of the note ("have the graphic fade out here"). No parameters: the moment is the note's own time. Use it only when the note is about WHEN the graphic ends, not its words or how long it lasts in general.
- edit_callout {"title": "..." or null, "subtitle": "..." or null, "remove_subtitle": true or false}  The note asks to change the WORDS of a callout (text bubble, label, on-screen text) or to remove its smaller second line. New wording must be copied from the note (quoted or plainly stated); never write your own. Only remove what the note says to remove. Leave a field null/false if the note does not mention it. The callout is found from the graphics project, so never give a time.
- unsupported {"reason": "..."}      Anything else: swapping to different footage, reframing other than moving a shot up or down in the frame (zooming, moving sideways, cropping), adding graphics or text, music, audio levels, colour, vague taste notes, or drawings that need interpretation. Say plainly what would be needed.

A note may say it was left ON a named timeline element (its lane and label). Then it is about that whole element: "make this longer" on a Callout element means the callout, on an SFX element means that sound. Choose only an operation that acts on that lane (Clips/Cuts/Edits: the cut operations; SFX: replace_sfx; Callout: extend_graphic or edit_callout); otherwise unsupported.

Rules: never invent a time or an amount that the note does not give. A note may produce more than one op. Every note must appear at least once. Prefer unsupported over guessing."""


def describe_shape(s: dict) -> str:
    b = s.get("bbox", {})
    return f"{s.get('type')} in the {s.get('region', '?')} (x {b.get('x0')}-{b.get('x1')}, y {b.get('y0')}-{b.get('y1')} of the frame)"


def build_prompt(cut: Cut, notes: list[dict]) -> str:
    lines = ["CLIPS (timeline seconds, source):"]
    for c in cut.video:
        lines.append(f"clip {c.idx}: {c.tl_start:.2f}-{c.tl_end:.2f}s  {c.name}  source {c.src_in:.2f}-{c.src_out:.2f}s")
    lines.append(f"\nCut length: {cut.zone_end:.2f}s\n\nNOTES:")
    for i, n in enumerate(notes, start=1):
        drawn = "; ".join(describe_shape(s) for s in n.get("shapes", []))
        tg = target_of(n)
        on = f' [left ON the {tg["lane"]} element "{tg.get("label", "")}", {tg.get("start")}-{tg.get("end")}s]' if tg else ""
        lines.append(f'note {i} at {n["timeline_sec"]}s (clip {n.get("clip")}){on}: "{n.get("text", "")}"'
                     + (f"  [drawn on frame: {drawn}]" if drawn else ""))
    return "\n".join(lines)


def _json_array(text: str) -> list:
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M)
    a, b = t.find("["), t.rfind("]")
    if a < 0 or b < a:
        raise ValueError("no JSON array in interpreter reply")
    return json.loads(t[a:b + 1])


def _unsupported(note: int, reason: str, why: str = "") -> dict:
    return {"note": note, "op": "unsupported", "reason": reason, "why": why}


def validate(ops: list, notes: list[dict], cut: Cut) -> list[dict]:
    """Coerce anything invalid into a reported `unsupported`, and make sure every note is accounted for."""
    n_clips, zone = len(cut.video), cut.zone_end
    out: list[dict] = []
    for raw in ops:
        try:
            note = int(raw["note"])
            if not 1 <= note <= len(notes):
                continue
        except (KeyError, TypeError, ValueError):
            continue
        text = notes[note - 1].get("text", "")
        op, why = raw.get("op"), str(raw.get("why", ""))[:200]
        tg = target_of(notes[note - 1])
        lane = tg["lane"] if tg else None
        says_sound = lane == "SFX" or bool(SAYS_SOUND.search(text))                     # a note left on the element needs no word naming it
        says_graphic = lane == "Callout" or bool(SAYS_GRAPHIC.search(text))
        try:
            if tg and op != "unsupported":
                if op not in LANE_OPS.get(lane, set()):
                    raise ValueError(f"the note was left on a {lane} element: " + (LANE_WHY.get(lane) or f"{op} does not act on that"))
                if tg.get("clip") is not None and raw.get("clip") is not None and int(raw["clip"]) != int(tg["clip"]):
                    raise ValueError(f"the note was left on clip {tg['clip']}, not clip {raw['clip']}")
            if raw.get("trusted") and op == "follow_speaker":
                out.append({"note": note, "op": op, "clips": "all", "why": why, "trusted": True})
            elif raw.get("trusted") and op == "move_clip":
                clip, before = int(raw["clip"]), int(raw["before"])
                if not (1 <= clip <= n_clips and 1 <= before <= n_clips) or clip == before:
                    raise ValueError("move_clip needs two different clips that exist")
                out.append({"note": note, "op": op, "clip": clip, "before": before, "why": why, "trusted": True})
            elif raw.get("trusted") and op in SUGGESTIBLE_OPS:
                clip = int(raw["clip"])
                if not 1 <= clip <= n_clips:
                    raise ValueError(f"clip {clip} does not exist")
                d = {"note": note, "op": op, "clip": clip, "why": why, "trusted": True}
                if op in ("extend_start", "extend_end"):
                    d["max_sec"] = min(max(float(raw.get("max_sec", 1.0)), 0.2), SUGGEST_MAX_SEC)
                if op == "end_at_words":
                    d["words"] = str(raw["words"]).strip()
                    if raw.get("reach") is not None:
                        d["reach"] = min(max(float(raw["reach"]), 6.0), 30.0)
                        d["max_trim"] = min(max(float(raw.get("max_trim", 6.0)), 3.0), 30.0)
                    if not words_mod.tokens(d["words"]) or len(words_mod.tokens(d["words"])) > 14:
                        raise ValueError("no usable words")
                if op == "start_at_words":
                    d["words"] = str(raw["words"]).strip()
                    if raw.get("reach") is not None:                              # measured by the check that proposed it (how long the stretch to remove is): bounded, never taken from a note
                        d["reach"] = min(max(float(raw["reach"]), 6.0), 30.0)
                        d["max_trim"] = min(max(float(raw.get("max_trim", 3.0)), 3.0), 30.0)
                    if not words_mod.tokens(d["words"]) or len(words_mod.tokens(d["words"])) > 14:
                        raise ValueError("no usable words")
                out.append(d)
            elif op == "tighten_pause":
                at = float(raw.get("at", notes[note - 1]["timeline_sec"]))
                if not 0 <= at <= zone:
                    raise ValueError(f"'at' {at} outside the cut")
                out.append({"note": note, "op": op, "at": at, "why": why})
            elif op == "remove_range":
                s, e = float(raw["start"]), float(raw["end"])
                if not 0 <= s < e <= zone:
                    raise ValueError(f"range {s}-{e} outside the cut or empty")
                if not HAS_NUMBER.search(text):
                    raise ValueError("note states no explicit time, refusing to invent one")
                out.append({"note": note, "op": op, "start": s, "end": e, "why": why})
            elif op in ("trim_start", "trim_end"):
                clip, sec = int(raw["clip"]), float(raw["seconds"])
                if not 1 <= clip <= n_clips:
                    raise ValueError(f"clip {clip} does not exist")
                length = cut.video[clip - 1].tl_end - cut.video[clip - 1].tl_start
                if not 0 < sec < length:
                    raise ValueError(f"{sec}s does not fit a {length:.2f}s clip")
                if not HAS_NUMBER.search(text):
                    raise ValueError("note states no amount, refusing to invent one")
                out.append({"note": note, "op": op, "clip": clip, "seconds": sec, "why": why})
            elif op == "extend_end":
                clip = int(raw.get("clip", (tg or {}).get("clip") or notes[note - 1].get("clip", 0)))
                if not 1 <= clip <= n_clips:
                    raise ValueError(f"clip {clip} does not exist")
                mx = float(raw.get("max_sec", 1.0))
                out.append({"note": note, "op": op, "clip": clip, "max_sec": min(max(mx, 0.2), 1.5), "why": why})
            elif op == "extend_start":
                clip = int(raw.get("clip", (tg or {}).get("clip") or notes[note - 1].get("clip", 0)))
                if not 1 <= clip <= n_clips:
                    raise ValueError(f"clip {clip} does not exist")
                mx = float(raw.get("max_sec", 1.0))
                out.append({"note": note, "op": op, "clip": clip, "max_sec": min(max(mx, 0.2), 1.5), "why": why})
            elif op == "end_at_words":
                clip = int(raw["clip"])
                if not 1 <= clip <= n_clips:
                    raise ValueError(f"clip {clip} does not exist")
                phrase = str(raw["words"]).strip()
                pt = words_mod.tokens(phrase)
                if not pt or len(pt) > 14:
                    raise ValueError("no usable words")
                if " ".join(pt) not in " ".join(words_mod.tokens(text)):
                    raise ValueError("those words are not in the note, refusing to invent a target")
                out.append({"note": note, "op": op, "clip": clip, "words": phrase, "why": why})
            elif op == "move_clip":
                clip, before = int(raw["clip"]), int(raw["before"])
                if not (1 <= clip <= n_clips and 1 <= before <= n_clips) or clip == before:
                    raise ValueError("move_clip needs two different clips that exist")
                if not SAYS_MOVE.search(text):
                    raise ValueError("the note does not ask for the order to change")
                out.append({"note": note, "op": op, "clip": clip, "before": before, "why": why})
            elif op == "start_at_words":
                clip = int(raw["clip"])
                if not 1 <= clip <= n_clips:
                    raise ValueError(f"clip {clip} does not exist")
                phrase = str(raw["words"]).strip()
                pt = words_mod.tokens(phrase)
                if not pt or len(pt) > 14:
                    raise ValueError("no usable words")
                if " ".join(pt) not in " ".join(words_mod.tokens(text)):
                    raise ValueError("those words are not in the note, refusing to invent a target")
                out.append({"note": note, "op": op, "clip": clip, "words": phrase, "why": why})
            elif op == "drop_clip":
                clip = int(raw["clip"])
                if not 1 <= clip <= n_clips:
                    raise ValueError(f"clip {clip} does not exist")
                if not SAYS_REMOVE.search(text):
                    raise ValueError("note does not say to remove the clip")
                if lane in ("Callout", "Card") or (SAYS_GRAPHIC.search(text) and not SAYS_FOOTAGE.search(text)):
                    raise ValueError("the note is about a graphic, not the footage; dropping the clip would delete footage (remove_graphic takes a graphic out)")
                out.append({"note": note, "op": op, "clip": clip, "why": why})
            elif op == "reframe_vertical":
                clip = int(raw.get("clip", (tg or {}).get("clip") or notes[note - 1].get("clip", 0)))
                if not 1 <= clip <= n_clips:
                    raise ValueError(f"clip {clip} does not exist")
                direction = str(raw.get("direction", "")).strip().lower()
                if direction not in ("lower", "raise"):
                    raise ValueError("a direction ('lower' or 'raise') is needed")
                if not SAYS_FRAMING.search(text):
                    raise ValueError("the note does not talk about the framing of a shot")
                if not (SAYS_LOWER if direction == "lower" else SAYS_RAISE).search(text):
                    raise ValueError(f"the note does not say to {direction} it, refusing to move the shot a way the note did not ask")
                out.append({"note": note, "op": op, "clip": clip, "direction": direction, "why": why})
            elif op == "follow_speaker":
                if not (SAYS_FOLLOW.search(text) or SAYS_SWITCH.search(text)):
                    raise ValueError("the note does not ask for the picture to follow or be framed on the person talking")
                clip = raw.get("clip", "all")
                if clip != "all":
                    clip = int(clip)
                    if not 1 <= clip <= n_clips:
                        raise ValueError(f"clip {clip} does not exist")
                out.append({"note": note, "op": op, "clips": "all" if clip == "all" else [clip], "why": why,
                            "short_turns": bool(raw.get("short_turns")) or bool(SAYS_SHORT.search(text))})
            elif op == "replace_sfx":
                sound = str(raw.get("sound", "")).strip()
                st, nt = words_mod.tokens(sound), set(words_mod.tokens(text))
                if not st or len(sound) > 200:
                    raise ValueError("no usable description of the wanted sound")
                if not says_sound:
                    raise ValueError("the note does not talk about a sound effect")
                if sum(w in nt for w in st) < 0.7 * len(st):
                    raise ValueError("that description is not in the note, refusing to invent a sound")
                out.append({"note": note, "op": op, "sound": sound, "why": why})
            elif op == "extend_graphic":
                if not says_graphic:
                    raise ValueError("the note does not talk about an on-screen graphic")
                if not SAYS_LONGER.search(text):
                    raise ValueError("the note does not ask for it to stay longer")
                sec = raw.get("seconds")
                if sec is not None:
                    sec = float(sec)
                    if not HAS_NUMBER.search(text):
                        raise ValueError("note states no amount, refusing to invent one")
                    if not 0.3 <= sec <= 10:
                        raise ValueError(f"{sec}s is not a believable extra time on screen")
                out.append({"note": note, "op": op, "seconds": sec, "why": why})
            elif op == "edit_caption":
                if lane != "Captions" and not SAYS_CAPTION.search(text):
                    raise ValueError("the note does not talk about a caption")
                new = str(raw.get("text", "")).strip().strip('"\u201c\u201d')
                toks, nt = words_mod.tokens(new), set(words_mod.tokens(text))
                if not toks or len(new) > 90:
                    raise ValueError("no usable caption wording")
                if sum(w in nt for w in toks) < 0.9 * len(toks):
                    raise ValueError("that wording is not in the note, refusing to write words the note did not give")
                out.append({"note": note, "op": op, "text": new, "why": why})
            elif op == "remove_graphic":
                if not says_graphic:
                    raise ValueError("the note does not talk about an on-screen graphic")
                if not SAYS_REMOVE.search(text):
                    raise ValueError("the note does not ask for it to be removed")
                out.append({"note": note, "op": op, "why": why})
            elif op == "bleep_word":
                if SAYS_NO.search(text):
                    raise ValueError("the note says no: this is not a curse word (recorded as a rejected suspect, nothing is bleeped)")
                if lane != "Suspects" and not SAYS_BLEEP.search(text):
                    raise ValueError("the note does not ask for a word to be bleeped")
                out.append({"note": note, "op": op, "why": why})
            elif op == "end_graphic":
                if not says_graphic:
                    raise ValueError("the note does not talk about an on-screen graphic")
                if not SAYS_END.search(text):
                    raise ValueError("the note does not ask for the graphic to end or fade out")
                out.append({"note": note, "op": op, "why": why})
            elif op == "edit_callout":
                if not says_graphic:
                    raise ValueError("the note does not talk about an on-screen graphic")
                if not SAYS_EDIT.search(text):
                    raise ValueError("the note does not ask to change or remove any words")
                fields = {}
                nt = set(words_mod.tokens(text))
                for label, cap in (("title", 60), ("subtitle", 120)):
                    val = raw.get(label)
                    if val is None:
                        continue
                    val = str(val).strip()
                    toks = words_mod.tokens(val)
                    if not toks or len(val) > cap:
                        raise ValueError(f"no usable {label}")
                    if sum(w in nt for w in toks) < 0.9 * len(toks):
                        raise ValueError(f"that {label} is not in the note, refusing to write wording the note did not give")
                    fields[label] = val
                rm = bool(raw.get("remove_subtitle"))
                if rm:
                    if not SAYS_SUBTITLE.search(text):
                        raise ValueError("the note does not mention the smaller second line, refusing to remove it")
                    if "subtitle" in fields:
                        raise ValueError("a note cannot both remove the subtitle and set it")
                if not fields and not rm:
                    raise ValueError("no change to the callout's words was asked for")
                out.append({"note": note, "op": op, "title": fields.get("title"), "subtitle": fields.get("subtitle"), "remove_subtitle": rm, "why": why})
            elif op == "unsupported":
                out.append(_unsupported(note, str(raw.get("reason", "not supported"))[:300], why))
            else:
                raise ValueError(f"unknown op {op!r}")
        except (KeyError, TypeError, ValueError) as e:
            out.append(_unsupported(note, f"interpreter proposed {op!r} but it was refused: {e}", why))
    covered = {o["note"] for o in out}
    for i in range(1, len(notes) + 1):
        if i not in covered:
            out.append(_unsupported(i, "no operation was produced for this note"))
    return sorted(out, key=lambda o: o["note"])


def interpret(cut: Cut, notes: list[dict], client=None) -> list[dict]:
    if client is None:
        from posthouse.cli_llm_client import CLIBackedClient
        client = CLIBackedClient()
    resp = client.messages.create(system=SYSTEM, max_tokens=2000, temperature=0,
                                  messages=[{"role": "user", "content": build_prompt(cut, notes)}])
    return validate(_json_array(resp.content[0].text), notes, cut)


@dataclass
class Pause:
    start: float   # timeline seconds
    end: float
    floor_db: float


def _pcm(path: str, start: float, dur: float) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.4f}", "-t", f"{dur:.4f}", "-i", path,
                        "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True)
    return np.frombuffer(p.stdout, dtype=np.float32)


def detect_pause(cut: Cut, at: float, window: float = 3.0, max_distance: float = 1.0) -> Pause | None:
    """The silence run closest to `at` in the audio the cut actually plays, or None.

    The threshold adapts to the clip: 3x the quiet-frame level, so a noisy room and a
    dead-quiet one are both judged against their own floor.
    """
    clip = next((c for c in cut.video if c.tl_start <= at < c.tl_end), cut.video[-1])
    lo, hi = max(clip.tl_start, at - window), min(clip.tl_end, at + window)
    src = next((a for a in cut.audio if a.tl_start <= at < a.tl_end), None) or clip
    t0 = src.src_in + (lo - src.tl_start)
    pcm = _pcm(src.src_path, t0, hi - lo)
    frame = int(SR * 0.02)
    n = len(pcm) // frame
    if n < 10:
        return None
    rms = np.sqrt((pcm[: n * frame].reshape(n, frame) ** 2).mean(axis=1))
    floor = float(np.percentile(rms, 10))
    loud = float(np.percentile(rms, 90))
    # 3x the quiet level, but never above 0.2x the loud level: with no real contrast
    # in the window (steady noise, continuous speech) nothing counts as a pause.
    thresh = max(min(floor * 3.0, loud * 0.2), 10 ** (-60 / 20))
    quiet = rms < thresh
    runs, i = [], 0
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            if (j - i) * 0.02 >= MIN_SILENCE_SEC:
                runs.append((lo + i * 0.02, lo + j * 0.02))
            i = j
        else:
            i += 1
    if not runs:
        return None
    def dist(r): return 0.0 if r[0] <= at <= r[1] else min(abs(r[0] - at), abs(r[1] - at))
    best = min(runs, key=dist)
    if dist(best) > max_distance:
        return None
    return Pause(best[0], best[1], 20 * np.log10(max(floor, 1e-9)))


def _audio_for(cut: Cut, clip):
    a = next((a for a in cut.audio if abs(a.tl_start - clip.tl_start) < 0.05), None)
    return (a.src_path, a.src_in, a.src_out) if a else (clip.src_path, clip.src_in, clip.src_out)


def _threshold(rms: np.ndarray) -> float:
    floor, loud = float(np.percentile(rms, 10)), float(np.percentile(rms, 90))
    return max(min(floor * 3.0, loud * 0.2), 10 ** (-60 / 20))


def _rms10(pcm: np.ndarray) -> np.ndarray:
    n = len(pcm) // 80
    return np.sqrt((pcm[: n * 80].reshape(n, 80) ** 2).mean(axis=1)) if n else np.zeros(0)


def level_db(path: str, t: float, dur: float = 0.03) -> float:
    r = _rms10(_pcm(path, max(0.0, t), dur))
    return 20 * float(np.log10(max(float(r.mean()) if len(r) else 1e-6, 1e-6)))


def measure_tail(cut: Cut, clip_idx: int, max_sec: float) -> dict:
    """How far past the cut the last sound keeps going, read from the audio's decay.

    Returns {"ext": seconds, "path", "t_end" (source), "thresh_db", "at_cut_db"} or {"reason": ...}.
    """
    clip = cut.video[clip_idx - 1]
    path, _src_in, src_out = _audio_for(cut, clip)
    lo = max(0.0, src_out - 1.0)
    pcm = _pcm(path, lo, 1.0 + max_sec + 0.3)
    rms = _rms10(pcm)
    if len(rms) < 30:
        return {"reason": "not enough audio after the cut to measure"}
    thresh = _threshold(rms)
    cut_i = int(round((src_out - lo) * 100))
    at_cut = float(rms[max(0, cut_i - 5):cut_i].mean())
    if at_cut < thresh:
        return {"reason": f"the audio is already quiet at the cut ({20 * np.log10(max(at_cut, 1e-6)):.0f} dB, room level), so nothing looks cut off"}
    j = cut_i
    while j + 3 <= len(rms) and not (rms[j:j + 3] < thresh).all():
        j += 1
    if j + 3 > len(rms) or (j - cut_i) * 0.01 > max_sec:
        return {"reason": f"the sound keeps going for more than {max_sec:g}s after the cut, so it is not a single word finishing; give an explicit amount instead"}
    ext = (j - cut_i) * 0.01 + 0.04
    if ext < 0.05:
        return {"reason": "the sound has already decayed at the cut"}
    nxt = cut.video[clip_idx] if clip_idx < len(cut.video) else None
    if nxt and nxt.src_path == clip.src_path and nxt.src_in < clip.src_out + ext + 0.02:
        return {"reason": "extending would run into the next clip's footage"}
    return {"ext": ext, "path": path, "t_end": src_out + ext, "thresh_db": 20 * float(np.log10(thresh)),
            "at_cut_db": 20 * float(np.log10(max(at_cut, 1e-6)))}


def measure_head(cut: Cut, clip_idx: int, max_sec: float) -> dict:
    """How far before the cut the first sound begins, read from the audio's attack: where the clip should start so it does not begin in the middle of a sound.

    The mirror of measure_tail. Returns {"ext": seconds, "path", "t_start" (source), "thresh_db", "at_cut_db"} or {"reason": ...}."""
    clip = cut.video[clip_idx - 1]
    path, src_in, _ = _audio_for(cut, clip)
    lo = max(0.0, src_in - max_sec - 0.3)
    pcm = _pcm(path, lo, (src_in + 1.0) - lo)
    rms = _rms10(pcm)
    if len(rms) < 30:
        return {"reason": "not enough audio before the cut to measure"}
    thresh = _threshold(rms)
    cut_i = int(round((src_in - lo) * 100))
    after = rms[cut_i:cut_i + 5]
    at_cut = float(after.mean()) if len(after) else 0.0
    if at_cut < thresh:
        return {"reason": f"the audio is already quiet at the start ({20 * np.log10(max(at_cut, 1e-6)):.0f} dB, room level), so nothing looks cut off"}
    j = cut_i
    while j - 3 >= 0 and not (rms[j - 3:j] < thresh).all():
        j -= 1
    if j - 3 < 0 and lo > 0:
        return {"reason": f"the sound keeps going for more than {max_sec:g}s before the start, so it is not a single word beginning; give an explicit amount instead"}
    if (cut_i - j) * 0.01 > max_sec:
        return {"reason": f"the sound keeps going for more than {max_sec:g}s before the start, so it is not a single word beginning; give an explicit amount instead"}
    ext = (cut_i - j) * 0.01 + 0.04
    if ext < 0.05:
        return {"reason": "the sound has not begun before the start"}
    if src_in - ext < 0:
        ext = src_in
        if ext < 0.05:
            return {"reason": "the clip already starts at the beginning of its file"}
    prev = cut.video[clip_idx - 2] if clip_idx > 1 else None
    if prev and prev.src_path == clip.src_path and prev.src_out > src_in - ext + 0.02:
        return {"reason": "extending would run into the previous clip's footage"}
    return {"ext": ext, "path": path, "t_start": src_in - ext, "thresh_db": 20 * float(np.log10(thresh)),
            "at_cut_db": 20 * float(np.log10(max(at_cut, 1e-6)))}


JOIN_MAX_SEC = 4.0


def measure_join(cut: Cut, clip_idx: int, side: str, max_gap: float = JOIN_MAX_SEC) -> dict:
    """How much footage lies between this clip and its neighbour in the same recording, to put back so the two run on without a cut. side is "next" or "prev".

    A cut that stops a sentence at the end of one clip and picks it up at the start of the next, with a few seconds of the recording removed between, can be repaired from either side by
    putting that stretch back. Returns {"ext": seconds, "other": the neighbour's clip number} or {"reason": ...}."""
    clip = cut.video[clip_idx - 1]
    k = clip_idx if side == "next" else clip_idx - 2
    if not 0 <= k < len(cut.video):
        return {"reason": "there is no clip next to it to join"}
    other = cut.video[k]
    if other.src_path != clip.src_path:
        return {"reason": "the clip next to it is from different footage, so there is nothing between them to put back"}
    gap = (other.src_in - clip.src_out) if side == "next" else (clip.src_in - other.src_out)
    if gap <= 0.02:
        return {"reason": "the two clips already run on from each other"}
    if gap > max_gap:
        return {"reason": f"the footage between the two clips is {gap:.1f}s, too much to put back as a join"}
    return {"ext": gap, "other": other.idx}


def from_suggestions(notes: list[dict], cut: Cut) -> list[dict]:
    """The operations notes carry as their own measured fix (`suggested_op`), marked trusted so `validate` keeps them as they are. A note without one is not touched here."""
    out: list[dict] = []
    for i, n in enumerate(notes, start=1):
        s = n.get("suggested_op")
        if not isinstance(s, dict):
            continue
        if s.get("op") not in SUGGESTIBLE_OPS:
            out.append(_unsupported(i, f"the suggested fix '{s.get('op')}' is not one the editor makes"))
            continue
        out.append({**s, "note": i, "trusted": True, "why": s.get("why") or "the reviewer's measured fix"})
    return out


def locate_end(cut: Cut, clip_idx: int, phrase: str, reach: float = 8.0, max_trim: float = 6.0) -> dict:
    """Where clip `clip_idx` should end so it stops right after `phrase` (the last words to keep). Returns {"trim": s from the clip's end, ...} or {"reason": ...}. The cut is placed at the quietest point
    in the 0.25 s after the last kept word, the way start_at_words places its cut before the first."""
    clip = cut.video[clip_idx - 1]
    path, src_in, src_out = _audio_for(cut, clip)
    lo = max(src_in, src_out - reach)
    ws = [w for w in words_mod.words_in(path, lo, src_out - lo + 0.3) if w.start < src_out - 0.02]
    hit = words_mod.find_phrase(ws, phrase)
    if hit is None:
        return {"reason": f"could not find \"{phrase}\" near the end of clip {clip_idx}; the audio there reads: \"{words_mod.heard(ws[-10:])}\""}
    want = words_mod.tokens(phrase)
    last = want[-1] if want else ""
    i = hit[0]
    span = [j for j in range(i, min(len(ws), i + len(want) + 3)) if (words_mod.tokens(ws[j].text) or [""])[0] == last]
    j = span[-1] if span else min(len(ws) - 1, i + len(want) - 1)
    t = words_mod.valley(path, ws[j].end, min(ws[j].end + 0.25, src_out))
    trim = src_out - t
    dropped = words_mod.heard(ws[j + 1:])
    if trim < 0.05:
        return {"reason": f"clip {clip_idx} already ends at \"{ws[j].text.strip()}\""}
    if trim > max_trim:
        return {"reason": f"ending at those words would remove {trim:.1f}s, which is too much to do without confirmation"}
    return {"trim": trim, "dropped": dropped, "at_word": ws[j].text.strip()}


def locate_start(cut: Cut, clip_idx: int, phrase: str, reach: float = 6.0, max_trim: float = 3.0) -> dict:
    """Where clip `clip_idx` should start so it begins at `phrase`. Returns {"trim": s, ...} or {"reason": ...}.
    `reach` is how far into the clip the phrase is looked for and `max_trim` the most it may remove without a person confirming; a fix that measured the stretch it removes (the off-camera voice
    check) passes both, sized to that stretch, so the ordinary guards stay as they were for everything else."""
    clip = cut.video[clip_idx - 1]
    path, src_in, _ = _audio_for(cut, clip)
    ws = words_mod.words_in(path, src_in - 0.6, reach + 0.6)
    ws = [w for w in ws if w.end > src_in - 0.05]
    hit = words_mod.find_phrase(ws, phrase)
    if hit is None:
        return {"reason": f"could not find \"{phrase}\" near the start of clip {clip_idx}; the audio there reads: \"{words_mod.heard(ws[:10])}\""}
    idx = hit[0]
    t = words_mod.valley(path, ws[idx].start - 0.12, ws[idx].start)
    trim = t - src_in
    dropped = words_mod.heard(ws[:idx])
    if trim < 0.05:
        return {"reason": f"clip {clip_idx} already starts at \"{ws[idx].text}\""}
    if trim > max_trim:
        return {"reason": f"starting at those words would remove {trim:.1f}s, which is too much to do without confirmation"}
    return {"trim": trim, "dropped": dropped, "at_word": ws[idx].text, "heard": words_mod.heard(ws[:idx + 6])}
