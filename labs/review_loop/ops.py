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
  use_camera_audio {clip}        the voice under the clip is missing, silent or far quieter than the camera's own sound ("I can't hear him here", "the audio drops out on this clip"): the camera's own
                                 sound is played under the clip instead of the separate recorder (both levels are measured first, and it is refused when the recorder is not clearly worse).
  restore_footage {words}        the note asks to put BACK a line of the recording the cut no longer has ("put the part where Bob says ... back in"). The line is found in the transcript of
                                 the footage the cut is made from by the note's own words, then put on the timeline, with its picture and every recorder, where it was said in the story.
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
  redo_captions {}               the note says the captions do not match what is said, are wrong or out of step, and gives no words ("the captions dont match whats being said. Fix the captions"). The
                                 words are in the audio, so the captions are made again from the cut's own sound. Made by the layer rebuild, not on the timeline.
  cta_reason {}                  the note says the on-screen CALL TO ACTION (the "DM ..." words at the end) has no reason, says what to do but not why, or does not make sense, and gives no wording ("This CTA
                                 doesnt make sense. It says 'DM Septic' but gives no reason"). The idea's own words give the reason, so it goes on the box's second line. Made by the layer rebuild.
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
from pathlib import Path

import numpy as np

import capabilities
import words as words_mod
from timeline import Cut

SR = 8000
KEEP_SEC_DEFAULT = 0.15
MIN_SILENCE_SEC = 0.25
NOTE_WORDS = r"one|two|three|four|five|six|seven|eight|nine|ten|half"
HAS_NUMBER = re.compile(rf"\d|\b({NOTE_WORDS})\b|\b(a|the last|the first|the final) (full )?second\b", re.I)          # "the last second" is a stated amount too (2026-10-12)
SAYS_SOUND = re.compile(r"\b(sfx|sound effects?|sound|noise|whoosh|swoosh|pop|click|ding|chime|beep|clap|thud)\b", re.I)
SAYS_GRAPHIC = re.compile(r"\b(text|graphic|graphics|arrow|callout|call-out|bubble|label|title|title card|caption box|highlight|overlay|box|cards?|end card|screenshot|name ?tags?|name ?plates?|lower[- ]thirds?|call to action|cta)\b", re.I)
SAYS_FOOTAGE = re.compile(r"\b(clip|shot|footage|scene|take|b-?roll)\b", re.I)
SAYS_LONGER = re.compile(r"\b(longer|more time|more seconds?|extra (time|seconds?)|linger|stay(s)?|sit|hold|too (short|fast|quick|brief)|only (shows?|appears?|lasts?)|half a second|barely)\b|\bkeep\b[^.]{0,40}\b(up|on)\b", re.I)
SAYS_END = re.compile(r"\b(fade(s|d)? out|fade(s|d)? away|end(s)? here|stop(s)? here|disappear|go(es)? away|come(s)? off|take (it|this|that) off|off the screen|off screen|shorter|less time)\b", re.I)
SAYS_CAPTION = re.compile(r"\b(captions?|subtitles?)\b", re.I)
SAYS_CAPTION_OFF = re.compile(r"\b(don'?t|do not|doesn'?t|does not|aren'?t|are not|isn'?t|is not|not|never|nothing|none|no)\b[^.]{0,40}\b(match(es|ing)?|line[sd]? up|sync(ed|ing)?|right|correct|accurate|what'?s (being )?said|what (he|she|they|bob|mitch|anyone) (say|said|says))\b|\b(wrong|incorrect|inaccurate|out of sync|out of step|mismatch(ed)?|misaligned|garbled|gibberish|off by|behind|ahead|late|early|different from)\b", re.I)
SAYS_CTA = re.compile(r"\b(call[- ]to[- ]action|cta)\b", re.I)
SAYS_CTA_UNCLEAR = re.compile(r"\b(no reason|any reason|without (a |any )?(reason|context|explanation)|(gives?|says?|has|tells?|explains?)\b[^.]{0,20}\b(no|nothing)\b[^.]{0,20}\b(reason|why|context|explanation)|why|what for|for what|reason|(doesn'?t|does not|don'?t|do not|isn'?t|is not|not)\b[^.]{0,20}\b(make sense|clear|explain|say why|give a reason)|unclear|confusing|vague|missing|lacks?|needs? (a |an |some )?(reason|context|explanation|more))\b", re.I)
SAYS_BLEEP = re.compile(r"\b(bleep|beep|censor|curse word|swear|profanity|cuss|expletive|f-?word|mute (that|the|this) word)", re.I)
SAYS_NO = re.compile(r"^\s*(no|nope|nah|wrong|not (it|this|that|a curse word)|ignore|skip|false|that'?s not)\b|\b(not a curse word|isn'?t a curse word|is not a curse word|don'?t bleep|do not bleep|no bleep|leave (it|that|this) (alone|in))\b", re.I)
SAYS_PUNCH = re.compile(r"\b(jump ?cuts?|crop|cropped|reframe[ds]?|punch(ed)? in|zoom(ed)? in|every other (cut|clip|shot))\b", re.I)
SAYS_EDIT = re.compile(r"\b(change|rename|reword|replace|instead|say(s)?|read(s)?|wording|words|remove|delete|get rid|drop|without|shorten|simplify|no )\b", re.I)
SAYS_SUBTITLE = re.compile(r"\b(subtitle|sub-title|second line|smaller text|small text|smaller line|description|underneath|line (below|under)|sub text|subtext)\b", re.I)
SAYS_REMOVE = re.compile(r"\b(remove|delete|drop|get rid|lose|kill|turn (it |this |that )?off|take (this |it |that |them )?(out|off)|take (this|it|that|the [a-z ]{1,30}?) (out|off)|cut (this|it|that)( out| clip| shot)?)\b", re.I)

SAYS_FRAMING = re.compile(r"\b(frame|framing|framed|crop(ped)?|cut off|head|screen|position(ed)?|reposition|shot|angle)\b", re.I)
SAYS_LOWER = re.compile(r"\b(lower|lowered|lowering|down|bring (it|this|them) down|drop (it|this)|move (it|this) down)\b", re.I)
SAYS_RAISE = re.compile(r"\b(raise|raised|raising|higher|up|bring (it|this|them) up|move (it|this) up)\b", re.I)
CUT_OPS = {"tighten_pause", "remove_range", "trim_start", "trim_end", "extend_end", "extend_start", "start_at_words", "end_at_words", "move_clip", "drop_clip", "reframe_vertical", "follow_speaker", "restore_footage", "use_camera_audio"}
SAYS_MOVE = re.compile(r"\b(move|moved|moving|put|place|before|after|earlier|first|open(s|ing)? (with|on)|lead (with|in)|order|reorder|swap|switch (the )?order|sets? (it )?up)\b", re.I)
SAYS_FOLLOW = re.compile(r"\b(follow(s|ing)?|track(s|ing)?|on|center(ed|ing)?|centre(d|ing)?)\b.{0,40}\b(speak(er|ers|ing)|talk(er|ing)|whoever|who(\'s| is) (talking|speaking)|person|both)\b|\b(speak(er|ers|ing)|talk(er|ing)|whoever)\b.{0,40}\b(frame|framed|framing|follow|center|centre|on screen)\b|\bframed on (either|both|neither)\b", re.I)
SAYS_SWITCH = re.compile(r"\b(re-?frame|cut(ting)? back and forth|back and forth|switch(ing)? (to|between)|audio source|source audio|mic(rophone)? (source )?(to|for))\b.{0,120}\b(speak(er|ers|ing)|talk(er|ing)|person|people|him|her|them)\b|\b(speak(er|ers))\b.{0,120}\b(re-?frame|cut(ting)? back|switch|audio source|source audio)\b", re.I)
SAYS_WRONG_SPEAKER = re.compile(r"\b(jump(ing|s|ed)?|cut(s|ting)?|switch(es|ing|ed)?|go(es|ing)?|skip(s|ping)?)\b[^.]{0,40}\bto\s+(mitch|bob|him|her|them|the other (guy|person|speaker|man|woman))\b|\b(source audio|his (mic|microphone|audio)|her (mic|microphone|audio)|their (mic|microphone|audio)|wrong (mic|microphone|speaker|person|audio))\b|\bnot when (he|she|they|mitch|bob|\w+)(\'?s| is| are)? (actually )?(talking|speaking|responding|answering)\b", re.I)
SAYS_SHORT = re.compile(r"\b(yeah|yep|right|uh[- ]?huh|mm+[- ]?hm+|okay|ok|just says?|even if|brief|short|one word|interject\w*|quick)\b", re.I)
# Operations a note may carry as its own measured fix ("suggested_op", written by the AI review): used as they are, the interpreter is not asked, and only structure is checked.
# The amounts are measured later from the audio, never taken from the note; max_sec only bounds how far the measurement may look (4 s lets an end run on to the next pause).
SUGGESTIBLE_OPS = {"restore_footage", "use_camera_audio", "extend_start", "extend_end", "drop_clip", "start_at_words", "end_at_words", "move_clip", "follow_speaker", "remove_range"}
SUGGEST_MAX_SEC = 4.0
# What a note left on a timeline element (a box on the review page's map) may turn into. A lane with no entry
# has no note-driven tool yet, so such a note is reported rather than guessed at.
LANE_OPS = {"Suspects": {"bleep_word"}, "Card": {"remove_graphic"}, "Captions": {"edit_caption", "redo_captions"}, "Clips": CUT_OPS | {"bleep_word"}, "Cuts": CUT_OPS, "Edits": CUT_OPS, "SFX": {"replace_sfx"}, "Callout": {"extend_graphic", "edit_callout", "end_graphic", "remove_graphic", "cta_reason"}}
LANE_WHY = {"Music": "no tool changes the music bed from a note yet"}


def target_of(note: dict) -> dict | None:
    """The timeline element a note was left on (lane, label, start, end, clip), or None for a note left at the playhead."""
    t = note.get("target")
    return t if isinstance(t, dict) and t.get("lane") else None


SYSTEM = """You translate an editor's review notes on a rough cut into edit operations.
Reply with ONLY a JSON array. Each item: {"note": <1-based note number>, "op": <name>, ...params, "why": <one short sentence>}.

Operations (times are seconds on the timeline the notes were left on):
- tighten_pause {"at": t}            The note says a pause/gap/silence/dead air should be tighter or cut. Use the note's own time as "at". The pause is located later by measuring the audio, so never guess a duration.
- remove_range {"start": s, "end": e}  ONLY if the note itself states both times. A note that asks to remove an "um", "uh", "umm" or any filler word is NEVER remove_range: it is remove_filler.
- trim_start {"clip": n, "seconds": x}, trim_end {"clip": n, "seconds": x}  ONLY if the note states how many seconds.
- extend_end {"clip": n, "max_sec": 1.0}  The note says a word or sentence at the END of clip n is cut off too soon or needs more time to finish. "clip" is the note's own clip unless the note says otherwise. The amount is measured from how the sound decays, so never give a duration.
- extend_start {"clip": n, "max_sec": 1.0}  The note says a word at the START of clip n is cut off, or the clip starts in the middle of a word or sound, or needs a little more lead-in. "clip" is the note's own clip unless the note says otherwise. The amount is measured from the audio, so never give one.
- start_at_words {"clip": n, "words": "..."}  The note says clip n should START at specific words, dropping words before them (for example "the clean cut should be X to Y": the clip after the seam starts at Y). "words" must be copied from the note. The point is found by listening, so never give a time.
- end_at_words {"clip": n, "words": "..."}  The note says clip n should END after specific words, dropping what comes after them (for example "trim the out-point to end at 'until we're under contract'"). "words" must be copied from the note: the last words to keep. The point is found by listening, so never give a time.
- move_clip {"clip": n, "before": m}  The note says clip n should come earlier, before clip m (a line that sets up another should play first). Only when the note asks for the order to change.
- drop_clip {"clip": n}              ONLY if the note clearly says to remove/delete that clip or shot.
- use_camera_audio {"clip": n}       The note says the VOICE on clip n is missing, silent, cut out or too quiet to hear ("I can't hear him on this clip", "the sound drops out here"). "clip" is the note's own clip unless the note says otherwise. The camera's own sound is played under that clip instead of the separate recorder; the levels are measured, so never give any.
- restore_footage {"words": "..."}   The note asks to put BACK, bring back or add back a line or moment of the recording that the cut no longer has ("put the part where Bob says 'if there aren't any curbs you're probably on a septic system' back in", "that line should not have been cut"). "words" is the note's own description or quote of that line, copied from the note (never your own words). The line is found by searching the transcript, so never give a time or a clip. Not for a graphic, and not for a word cut off at the edge of a clip (that is extend_end or extend_start).
- remove_filler {}                   The note asks to remove a filler word ("remove this um", "cut the uh", "take out the er") at the note's moment. No parameters: the word is found in the transcript near the note's time and its measured start and end are cut. Never give a time.
- reframe_vertical {"clip": n, "direction": "lower" or "raise"}  The note says a shot sits too HIGH or too LOW in the (vertical) frame and should be moved down or up on screen, for example a head cropped at the top of the frame ("lower it so his head isn't cut off" is "lower": the picture moves down on screen). "clip" is the note's own clip unless the note says otherwise. "direction" must be the way the note says to move it. The amount is a fixed step, so never give one. Not for zooming (that is zoom_clip) or moving sideways (unsupported).
- zoom_clip {"clip": n, "direction": "in" or "out", "amount": "little" or "lot"}  The note asks to zoom in or out on a shot, punch in, crop in, make it closer or tighter, or wider ("zoom in on this shot", "crop this one in a bit", "too tight, pull back"). "clip" is the note's own clip unless the note says otherwise. "direction" is "in" for closer or tighter, "out" for wider. "amount" is "lot" only when the note says a lot, way in, much or big; otherwise "little". The person stays framed and the top of the picture stays, so never give a position or a size.
- follow_speaker {"clip": n or "all", "short_turns": true or false}  The note says the picture should be framed on, centred on or follow the person who is TALKING in a shot that shows two people side by side (for example "the framing should follow the speaker", "it isn't framed on either of the speakers"). "clip" is the note's own clip, or "all" when the note is about the whole cut. Use it too when the note asks to cut or switch back and forth between the speakers, to reframe to whoever is talking, or to use the talking person's own microphone: the picture and the sound both follow the speaker. Set "short_turns" true when the note says even a short reply ("yeah", "right") should get the cut. Never give a position or a time: who talks when and where each person stands are measured.
- replace_sfx {"sound": "..."}       The note says a sound EFFECT (a whoosh, pop, click, swoosh, "sound effect") should sound different, be swapped, or be something else. "sound" is copied from the note's own words if it describes the wanted sound, and "" if it only asks for a different one. The effect is swapped for another from the library; never give a time.
- extend_graphic {"seconds": x or null}  The note says an on-screen GRAPHIC (a callout, text bubble, arrow, label) is on screen too briefly and should stay longer. Give "seconds" ONLY if the note states an amount (for example "two more seconds"); otherwise use null. Never guess an amount. The graphic and its time are found from the graphics project, so never give a time.
- redo_captions {}  The note says the CAPTIONS (subtitles) do not match what is being said, are wrong, garbled or out of step with the speech, and does NOT give the right words ("the captions dont match whats being said. Fix the captions"). No parameters: the right words are in the cut's own audio, so the captions are made again from it. Use edit_caption only when the note gives the words.
- edit_caption {"text": "...", "line": "this" or "next" or "previous"}  The note says what a CAPTION (subtitle) line should read ("fix the caption to say ...", "move the 23 to the next caption"). "text" is the whole new wording of ONE line: the words the note gives, plus the words of that line or its neighbour that stay (the note lists the captions around it; use only those words and the note's). "line" is which one changes: "this" (the one showing at the note's moment, the default), "next" or "previous". A note that moves words from one caption to its neighbour is TWO edit_caption ops, one for each line, each with that line's complete new wording. Never write words that are in neither the note nor those captions. The line and its time are found from the captions project, so never give a time. Not for callouts or other graphics (that is edit_callout).
- cta_reason {}  The note says the on-screen CALL TO ACTION (the "DM ..." / "comment ..." words near the end) has no reason, says what to do but not why, or does not make sense, and gives NO new wording ("This CTA doesnt make sense. It says 'DM Septic' but gives no reason"). No parameters: the idea's own words give the reason and it is added from them. If the note gives the words it wants, use edit_callout instead.
- remove_graphic {}  The note says an on-screen GRAPHIC (a callout, text bubble or image card) should be removed, deleted or taken out ("why is this here? remove it ... cards"). The graphic is found from the note's element or moment, so never give a time. NEVER use drop_clip for a graphic: drop_clip removes footage.
- bleep_word {}  The note says to bleep, beep, censor or mute a curse word or swear word ("lets bleep the curse word here"). No parameters: the stretch is the note's clip or element, else its moment. Never give a time.
- A note that says NO to something ("no", "that's not a curse word", "don't bleep it") is never a bleep_word: use unsupported with the reason "rejected".
{FINISH_LINES}
- end_graphic {}  The note says an on-screen GRAPHIC (callout, text bubble, label) should fade out, end or go away at the moment of the note ("have the graphic fade out here"). No parameters: the moment is the note's own time. Use it only when the note is about WHEN the graphic ends, not its words or how long it lasts in general.
- edit_callout {"title": "..." or null, "subtitle": "..." or null, "remove_subtitle": true or false}  The note asks to change the WORDS of a callout (text bubble, label, on-screen text) or to remove its smaller second line. New wording must be copied from the note (quoted or plainly stated); never write your own. Only remove what the note says to remove. Leave a field null/false if the note does not mention it. The callout is found from the graphics project, so never give a time.
- music_volume {"direction": "up" or "down"}  The note says the MUSIC (the background track, the song, the score) is too loud or too quiet, or asks to turn it up or down ("the music is too loud", "bring the music down", "I can't hear the music"). "direction" is the way the volume should go: "down" when it is too loud, "up" when it is too quiet. The amount is a fixed step, so never give one. Not for the voice or for sound effects.
- remove_music {}  The note asks for the music to be taken out entirely ("no music", "remove the music", "take out the background track").
- remove_sfx {}  The note asks for the sound effects (the whooshes and hits on the graphics) to be taken out entirely ("no sound effects", "remove the whooshes"). Not for one sound at one moment that should sound different (that is replace_sfx).
- unsupported {"reason": "..."}      Anything else: swapping to different footage, reframing other than moving a shot up or down in the frame or zooming it (moving it sideways), adding graphics or text beyond the ones already on the cut, changing the music's style, the voice's level, colour, vague taste notes, or drawings that need interpretation. Say plainly what would be needed.

A note may say it was left ON a named timeline element (its lane and label). Then it is about that whole element: "make this longer" on a Callout element means the callout, on an SFX element means that sound. Choose only an operation that acts on that lane (Clips/Cuts/Edits: the cut operations; SFX: replace_sfx; Callout: extend_graphic or edit_callout); otherwise unsupported.

Rules: never invent a time or an amount that the note does not give. A note may produce more than one op. Every note must appear at least once. Prefer unsupported over guessing."""
SYSTEM = SYSTEM.replace("{FINISH_LINES}", capabilities.reader_lines())        # what every finish already makes: one list (capabilities.py), read by the reviewer and the result too


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
        cl = n.get("caption_lines")
        if isinstance(cl, dict) and cl.get("this"):
            lines.append("    the captions around this note: " + " | ".join(f'{k} line "{cl[k]}"' for k in ("previous", "this", "next") if cl.get(k)))
    return "\n".join(lines)


def _json_array(text: str) -> list:
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M)
    a, b = t.find("["), t.rfind("]")
    if a < 0 or b < a:
        raise ValueError("no JSON array in interpreter reply")
    return json.loads(t[a:b + 1])


def _unsupported(note: int, reason: str, why: str = "") -> dict:
    return {"note": note, "op": "unsupported", "reason": reason, "why": why}


FILLER_WORDS = re.compile(r"^(u+m+|u+h+|e+r+m*|a+h+|h+m+)$", re.I)     # um, umm, uh, uhh, er, erm, ah, ahh, hmm: spelt as the speaker says them (2026-10-11: "Umm" was not matched)
FILLER_REACH = 1.5                  # a filler word this close to the note's own time is the one the note means
SAYS_ZOOM = re.compile(r"\b(zoom(ed|ing)?|punch(ed|ing)? in|crop(ped|ping)? in|closer|tighter|tight(er)? shot|bigger|scale (it |this )?(up|down)|pull(ed)? back|wider|farther|further out|too (close|tight|zoomed))\b", re.I)
SAYS_SWAP = re.compile(r"\b(swap|swapped|replace|replaced|different|something else|another|change (it|the|that)|instead|new (one|sound|effect)|doesn't fit|does not fit|annoying|hate|don't like)\b", re.I)
SAYS_TAKE_OUT = re.compile(r"\b(remove|delete|drop|get rid|take (it |them |this |that )?(out|off)|turn (it |them )?off|kill|mute|no|without|lose)\b", re.I)
SAYS_MUSIC = re.compile(r"\b(music|song|soundtrack|score|background track|beat)\b", re.I)
SAYS_SFX = re.compile(r"\b(sound effects?|sfx|whoosh(es)?|swoosh(es)?|effects?)\b", re.I)
SAYS_QUIETER = re.compile(r"\b(too loud|louder than|quieter|quiet(er)?|softer|lower(ed)?|down|turn (it )?down|bring (it )?down|drowns?|overpower)", re.I)
SAYS_LOUDER = re.compile(r"\b(too (quiet|soft|low)|louder|turn (it )?up|bring (it )?up|can'?t hear|cannot hear|barely hear)", re.I)
SAYS_NO_VOICE = re.compile(r"\b(can'?t|cannot|couldn'?t|can not) hear\b|\b(no|missing|lost|dead|dropped?|drops? out)\b[^.]{0,20}\b(sound|audio|voice|mic|microphone)\b|\b(sound|audio|voice|mic|microphone)\b[^.]{0,20}\b(is |was |goes |went |drops? |dropped )?(missing|gone|silent|dead|out|cuts? out|too quiet|inaudible|muffled|low)\b|\b(silent|inaudible|muffled|too quiet)\b", re.I)
SAYS_RESTORE = re.compile(r"\b(put|bring|add|stick|slot|get|place|work)\b[^.]{0,70}\bback\b|\b(restore|re-?add|reinstate|re-?insert|re-?include)\b|\b(shouldn'?t|should not|should never|did ?n'?t need to|never should) (have )?(been |be )?(cut|removed|dropped|taken out|left out|deleted)\b|\bshould (still )?(be|have been|stay) (in|kept|left in|included)\b|\b(was|were|got|is|are) (cut|removed|dropped|taken out|left out|deleted|missing)\b|\bwhy (was|were|did)\b[^.]{0,60}\b(cut|removed|dropped|out)\b", re.I)
RESTORE_STOP = set("a an the and or but if is it its it's in on of to for so that this there here are was were be been am i you your youre you're we they he she them his her at as by with from any all just not dont don't aren't arent isnt isn't do does did have has had will would can could should one some then than what when who which how like yeah okay really very kind know say says said mention mentions mentioned part line moment bit video clip footage back put into out about where".split())
RESTORE_MIN_SCORE = 0.5            # the share of the note's distinctive words the line must hold (Whisper mishears: "septic system" was written "out of substances", so a full match cannot be asked for)
RESTORE_COMMON_DF = 4              # a word said in more phrases than this names no line: a line found only by such words is for the model to confirm
RESTORE_SURE_SHARE = 0.8           # a line holding at least this share of the described words is taken as it is; any less is confirmed by a second question to the model
RESTORE_CLEAR_LEAD = 1.4           # the best line must beat the next by this much to be taken on the words alone; otherwise the model chooses
RESTORE_MIN_WORDS = 2              # a line named by two or more distinctive words must hold at least this many of them; a note naming it by one word ("the curbs part") is left to the model to place when more than one line holds it
SAYS_FILLER = re.compile(r"\b(um+|uh+|er+m?|ah+|filler|filler words?|\"?um\"?|\"?uh\"?)\b", re.I)


def note_time(note: dict) -> float:
    return float(note.get("timeline_sec", 0.0))


def _filler_span(cut: Cut, at: float, reach: float) -> tuple[float, float] | None:
    """The timeline span of the filler word nearest `at` (within `reach` seconds), read from the voice's own word timing. None when there is none."""
    best = None
    for a in cut.audio:
        dur = a.tl_end - a.tl_start
        if dur <= 0.05:
            continue
        for w in words_mod.words_in(a.src_path, a.src_in, dur):
            if not FILLER_WORDS.match((w.text or "").strip().strip(".,?!\"'")):
                continue
            t0, t1 = a.tl_start + (w.start - a.src_in), a.tl_start + (w.end - a.src_in)
            if t1 <= a.tl_start or t0 >= a.tl_end:
                continue
            gap = 0.0 if t0 <= at <= t1 else min(abs(t0 - at), abs(t1 - at))
            if gap <= reach and (best is None or gap < best[0]):
                best = (gap, round(max(t0 - 0.02, 0.0), 3), round(t1 + 0.02, 3))
    return None if best is None else (best[1], best[2])


VOICED_MIN_SEC, VOICED_MAX_SEC = 0.12, 0.9     # a filler sound is a short burst of voice: shorter is a click, longer is a word


def _voiced_filler_span(cut: Cut, at: float, reach: float) -> tuple[float, float] | None:
    """A filler sound the transcript did not show (Whisper drops "umm"): the short voiced burst in the audio nearest `at` (within `reach`), as a timeline span.
    Refused when the transcript shows a real word over it, so speech the editor can read is never cut as a sound. Measured from the audio, nothing guessed."""
    a = next((a for a in cut.audio if a.tl_start - 1e-6 <= at < a.tl_end), None)
    if a is None:
        return None
    src_t = a.src_in + (at - a.tl_start)
    lo = max(0.0, src_t - reach - 0.3)
    dur = 2 * reach + 0.6
    rms = _rms10(_pcm(a.src_path, lo, dur))
    if len(rms) < 30:
        return None
    thresh = _threshold(rms)
    voiced = rms > thresh
    runs, start = [], None
    for i, v in enumerate(list(voiced) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i))
            start = None
    best = None
    for s, e in runs:
        t0, t1 = lo + s * 0.01, lo + e * 0.01
        if not (VOICED_MIN_SEC <= t1 - t0 <= VOICED_MAX_SEC):
            continue
        mid = (t0 + t1) / 2
        if abs(mid - src_t) > reach:
            continue
        if best is None or abs(mid - src_t) < best[0]:
            best = (abs(mid - src_t), t0, t1)
    if best is None:
        return None
    _gap, t0, t1 = best
    for w in words_mod.words_in(a.src_path, lo, dur):
        if w.end > t0 and w.start < t1 and not FILLER_WORDS.match((w.text or "").strip().strip(".,?!\"'")):
            return None                                              # a real word the transcript shows is over it: not a filler sound
    tl0 = a.tl_start + (t0 - a.src_in)
    tl1 = a.tl_start + (t1 - a.src_in)
    return round(max(tl0 - 0.02, a.tl_start), 3), round(min(tl1 + 0.02, a.tl_end), 3)


PROMPT_WINDOW_SEC = 8.0
FILLER_PAD_BEFORE, FILLER_PAD_AFTER = 0.10, 0.08     # the small model times a filler tightly (734.06-734.48 s on the Septic cut); the base model needed 0.3 s because it heard onsets late ("u---mmm", 2026-10-11)


def _prompted_filler_span(cut: Cut, at: float, reach: float) -> tuple[float, float] | None:
    """A filler the plain transcript leaves out, found by transcribing the window again with a prompt that lists fillers (words.words_prompted). Kept only when the
    filler is a voiced sound: a prompted pass can also write a filler where there is only silence, so the level under it must be well above the room."""
    v = next((c for c in cut.video if c.tl_start - 1e-6 <= at < c.tl_end), None)     # the picture's own file: its sound is what the cut plays under the picture
    if v is None:
        return None
    src_t = v.src_in + (at - v.tl_start)
    lo = max(0.0, src_t - PROMPT_WINDOW_SEC / 2)                       # a long window: a short one made the prompted pass write nonsense (2026-10-11)
    dur = PROMPT_WINDOW_SEC
    best = None
    prompted = words_mod.words_prompted(v.src_path, lo, dur)
    for w in prompted:
        if not FILLER_WORDS.match((w.text or "").strip().strip(".,?!\"'")):
            continue
        mid = (w.start + w.end) / 2
        if abs(mid - src_t) > reach or not (0.08 <= w.end - w.start <= 1.2):
            continue
        if best is None or abs(mid - src_t) < best[0]:
            best = (abs(mid - src_t), w.start, w.end)
    if best is None:
        return None
    _gap, t0, t1 = best
    level = _rms10(_pcm(v.src_path, t0, t1 - t0))
    room = _threshold(_rms10(_pcm(v.src_path, max(0.0, lo), dur)))
    if len(level) == 0 or float(np.percentile(level, 50)) < room * 3.0:
        return None                                                  # not a voiced sound: the prompt wrote a filler over silence
    # Whisper's word edges move by up to ~0.3 s between passes (2026-10-11: "u---mmm" was left, the start was late): the span is widened to take the
    # whole sound, but never back into the word before it
    before = [w.end for w in prompted if w.end <= t0 - 0.02 and not FILLER_WORDS.match((w.text or "").strip().strip(".,?!\"'"))]
    t0 = max(t0 - FILLER_PAD_BEFORE, max(before) if before else lo)
    t1 = t1 + FILLER_PAD_AFTER
    tl0 = _src_to_tl(cut, v.src_path, t0)
    if tl0 is None:
        return None
    dur = t1 - t0                                                    # the filler's length is kept; it may run over a cut point into the next clip
    return round(max(tl0, 0.0), 3), round(min(tl0 + dur, cut.zone_end), 3)


def _src_to_tl(cut: Cut, path: str, t: float) -> float | None:
    """The timeline time of source time `t` in file `path`, through the clip that shows it (None when no clip shows it)."""
    for c in cut.video:
        if c.src_path == path and c.src_in - 1e-6 <= t < c.src_out:
            return c.tl_start + (t - c.src_in)
    return None


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
            elif raw.get("trusted") and op == "restore_footage":
                phrase = str(raw.get("words", "")).strip()
                if not _distinctive(phrase) or len(words_mod.tokens(phrase)) > 40:
                    raise ValueError("the line to add is not described in enough words to search for")      # the reviewer's own words (not the note's): the search confirms the line before anything is added
                before = raw.get("before")
                out.append({"note": note, "op": op, "words": phrase, "before": before if isinstance(before, int) and 1 <= before <= n_clips else None, "why": why, "trusted": True})
            elif raw.get("trusted") and op == "remove_range":
                s, e = float(raw["start"]), float(raw["end"])               # the whole-cut filler pass measured these (fillers.py); no explicit time needs to be in the note's text
                if not 0 <= s < e <= zone:
                    raise ValueError(f"range {s}-{e} outside the cut or empty")
                out.append({"note": note, "op": op, "start": s, "end": e, "why": why, "trusted": True})
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
                if not HAS_NUMBER.search(text) and SAYS_FILLER.search(text):
                    span = _filler_span(cut, note_time(notes[note - 1]), FILLER_REACH) or _prompted_filler_span(cut, note_time(notes[note - 1]), FILLER_REACH)
                    if span is None:
                        raise ValueError(f"no filler word (um, uh, er) heard within {FILLER_REACH:g} s of the note")
                    out.append({"note": note, "op": "remove_range", "start": span[0], "end": span[1], "why": "the filler the note names, found by listening"})
                    continue                                                  # the reader chose remove_range for "remove this um": it is a filler removal, never an invented time
                if not HAS_NUMBER.search(text):
                    raise ValueError("note states no explicit time, refusing to invent one")
                out.append({"note": note, "op": op, "start": s, "end": e, "why": why})
            elif op == "remove_filler":
                if not SAYS_FILLER.search(text):
                    raise ValueError("the note does not ask to remove a filler word")
                at = note_time(notes[note - 1])
                span = _filler_span(cut, at, FILLER_REACH)
                how = "found in the transcript"
                if span is None:
                    span = _voiced_filler_span(cut, at, FILLER_REACH)           # the transcript dropped it (Whisper leaves out "umm"): the voiced sound itself, measured
                    how = "a voiced sound in the audio, which the transcript does not show"
                if span is None:
                    span = _prompted_filler_span(cut, at, FILLER_REACH)         # the plain transcript leaves it out: transcribed again with fillers in the prompt
                    how = "a filler the transcript leaves out, found by a second pass with fillers in the prompt"
                if span is None:
                    raise ValueError(f"no filler word (um, uh, er) in the transcript or as a voiced sound within {FILLER_REACH:g} s of the note")
                out.append({"note": note, "op": "remove_range", "start": span[0], "end": span[1], "why": f"the filler at {span[0]:.2f}s, {how}: {why}".strip()})
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
            elif op == "use_camera_audio":
                clip = int(raw.get("clip", (tg or {}).get("clip") or notes[note - 1].get("clip", 0)))
                if not 1 <= clip <= n_clips:
                    raise ValueError(f"clip {clip} does not exist")
                if not SAYS_NO_VOICE.search(text):
                    raise ValueError("the note does not say the voice is missing, silent or too quiet")
                out.append({"note": note, "op": op, "clip": clip, "why": why})
            elif op == "restore_footage":
                phrase = str(raw.get("words", "")).strip().strip('"\u201c\u201d')
                pt, nt = [w.strip("'") for w in words_mod.tokens(phrase)], {w.strip("'") for w in words_mod.tokens(text)}          # a quoted line carries its quote marks on its first and last word
                if not SAYS_RESTORE.search(text):
                    raise ValueError("the note does not ask for footage to be put back")
                if lane in ("Callout", "Card") or (SAYS_GRAPHIC.search(text) and not SAYS_FOOTAGE.search(text) and not re.search(r"\b(says?|said|mentions?|talks?)\b", text, re.I)):
                    raise ValueError("the note is about a graphic, not footage")
                if not [w for w in pt if w not in RESTORE_STOP and len(w) > 2] or len(pt) > 40:
                    raise ValueError("the note does not say which line to put back in enough words to find it")
                if sum(w in nt for w in pt) < 0.9 * len(pt):
                    raise ValueError("those words are not in the note, refusing to search for words nobody gave")
                before = raw.get("before")
                if before is not None and not (isinstance(before, int) and 1 <= before <= n_clips):
                    before = None
                out.append({"note": note, "op": op, "words": phrase, "before": before, "why": why})
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
                if not (SAYS_FOLLOW.search(text) or SAYS_SWITCH.search(text) or SAYS_WRONG_SPEAKER.search(text)):
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
                if len(sound) > 200:
                    raise ValueError("no usable description of the wanted sound")
                if not says_sound:
                    raise ValueError("the note does not talk about a sound effect")
                if st and sum(w in nt for w in st) < 0.7 * len(st):
                    raise ValueError("that description is not in the note, refusing to invent a sound")
                if not st and not SAYS_SWAP.search(text):
                    raise ValueError("no usable description of the wanted sound, and the note does not ask for a different one")      # "the sound effect is too loud" is not a swap
                out.append({"note": note, "op": op, "sound": sound, "why": why})         # no description is fine: the effect is swapped for the next best whoosh in the library
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
                which = str(raw.get("line", "this")).lower()
                if which not in ("this", "next", "previous"):
                    raise ValueError("edit_caption line must be this, next or previous")
                cl = notes[note - 1].get("caption_lines") or {}
                for k in ("this", "next", "previous"):                               # the words of the captions around the note are the person's words too: they stay, or move to the neighbour
                    nt |= set(words_mod.tokens(str(cl.get(k, ""))))
                if which != "this" and not cl.get(which):
                    raise ValueError(f"there is no {which} caption line to change")
                if not toks or len(new) > 90:
                    raise ValueError("no usable caption wording")
                if sum(w in nt for w in toks) < 0.9 * len(toks):
                    raise ValueError("that wording is not in the note or the captions around it, refusing to write words nobody gave")
                out.append({"note": note, "op": op, "text": new, "line": which, "why": why})
            elif op == "redo_captions":
                if lane != "Captions" and not SAYS_CAPTION.search(text):
                    raise ValueError("the note does not talk about the captions")
                if not SAYS_CAPTION_OFF.search(text):
                    raise ValueError("the note does not say the captions are wrong or do not match the speech")
                out.append({"note": note, "op": op, "why": why})
            elif op == "cta_reason":
                if not SAYS_CTA.search(text):
                    raise ValueError("the note does not talk about the call to action")
                if not SAYS_CTA_UNCLEAR.search(text):
                    raise ValueError("the note does not say the call to action lacks a reason or makes no sense")
                out.append({"note": note, "op": op, "why": why})
            elif op == "remove_graphic":
                if not says_graphic:
                    raise ValueError("the note does not talk about an on-screen graphic")
                if not SAYS_REMOVE.search(text):
                    raise ValueError("the note does not ask for it to be removed")
                out.append({"note": note, "op": op, "why": why})
            elif op == "zoom_clip":
                clip = int(raw["clip"])
                if not 1 <= clip <= n_clips:
                    raise ValueError(f"clip {clip} does not exist")
                direction = str(raw.get("direction", "in")).lower()
                if direction not in ("in", "out"):
                    raise ValueError("zoom_clip needs direction in or out")
                if not SAYS_ZOOM.search(text):
                    raise ValueError("the note does not ask to zoom, crop in or make the shot closer or wider")
                out.append({"note": note, "op": op, "clip": clip, "direction": direction, "amount": "lot" if str(raw.get("amount", "")).lower() == "lot" else "little", "why": why})
            elif op == "music_volume":
                direction = str(raw.get("direction", "")).lower()
                if direction not in ("up", "down"):
                    raise ValueError("music_volume needs direction up or down")
                if not SAYS_MUSIC.search(text):
                    raise ValueError("the note does not talk about the music")
                if direction == "down" and not SAYS_QUIETER.search(text):
                    raise ValueError("the note does not say the music is too loud or should come down")
                if direction == "up" and not SAYS_LOUDER.search(text):
                    raise ValueError("the note does not say the music is too quiet or should come up")
                out.append({"note": note, "op": op, "direction": direction, "why": why})
            elif op == "remove_music":
                if not SAYS_MUSIC.search(text) or not SAYS_TAKE_OUT.search(text):
                    raise ValueError("the note does not ask for the music to be removed")
                out.append({"note": note, "op": op, "why": why})
            elif op == "remove_sfx":
                if not SAYS_SFX.search(text) or not SAYS_TAKE_OUT.search(text):
                    raise ValueError("the note does not ask for the sound effects to be removed")
                out.append({"note": note, "op": op, "why": why})
            elif op in ("by_finish", "punch_in"):
                skill = "punch-in" if op == "punch_in" else str(raw.get("skill", ""))     # punch_in: the name this op had in runs recorded on 2026-10-09
                if skill not in capabilities.SKILL_BY_NAME:
                    raise ValueError(f"no finish skill called {skill!r}")
                if not capabilities.skill_asked(skill, text):
                    raise ValueError(f"the note does not ask for the {skill}")
                out.append({"note": note, "op": "by_finish", "skill": skill, "why": why})
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


def _footage_user(cut: Cut, clip_idx: int, a: float, b: float):
    """The clip (other than `clip_idx`) whose footage overlaps source seconds a..b of the same file, or None. The old guard compared only with the next clip on the TIMELINE by number: a cut that
    plays a later part of the recording before an earlier one (clip 3 from 750 s, then clip 4 from 579 s) looked like an overlap, and a word cut off at 756 s could not be finished."""
    me = cut.video[clip_idx - 1]
    for c in cut.video:
        if c.idx != clip_idx and c.src_path == me.src_path and c.src_in < b - 0.02 and a + 0.02 < c.src_out:
            return c
    return None


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
        w = _word_across(path, src_out, max_sec)
        if w is None:
            return {"reason": "the sound has already decayed at the cut"}
        ext = words_mod.valley(path, w.end, w.end + 0.25) - src_out                      # the loudness says the sound is over; the word's own timing says the cut is still inside it (a soft ending)
        if ext < 0.05:
            return {"reason": "the sound has already decayed at the cut"}
    user = _footage_user(cut, clip_idx, clip.src_out, clip.src_out + ext)
    if user is not None:
        if user.idx == clip_idx + 1:
            return {"reason": "extending would run into the next clip's footage"}          # the words this phrasing is matched on: the editor then tries a join instead
        return {"reason": f"extending would repeat footage clip {user.idx} already shows"}
    return {"ext": ext, "path": path, "t_end": src_out + ext, "thresh_db": 20 * float(np.log10(thresh)),
            "at_cut_db": 20 * float(np.log10(max(at_cut, 1e-6)))}


def _word_across(path: str, t: float, max_sec: float):
    """The word whose own start and end enclose source second `t` (the cut lands inside it), or None. The loudness measure misses a soft first or last sound; the word timing does not
    (2026-10-15, wall project: "the start lands inside the word 'particularly'" was refused 4 times with "the sound has not begun before the start")."""
    ws = words_mod.words_in(path, max(0.0, t - 1.5), 3.0)
    for w in ws:
        if w.start < t - 0.03 and w.end > t + 0.03 and t - w.start <= max_sec and w.end - t <= max_sec:
            return w
    return None


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
        w = _word_across(path, src_in, max_sec)
        if w is None:
            return {"reason": "the sound has not begun before the start"}
        ext = src_in - words_mod.valley(path, w.start - 0.25, w.start)                   # the loudness says the sound begins at the cut; the word's own timing says it began before it (a soft first sound: "th", "p")
        if ext < 0.05:
            return {"reason": "the sound has not begun before the start"}
    if src_in - ext < 0:
        ext = src_in
        if ext < 0.05:
            return {"reason": "the clip already starts at the beginning of its file"}
    user = _footage_user(cut, clip_idx, src_in - ext, src_in)
    if user is not None:
        if user.idx == clip_idx - 1:
            return {"reason": "extending would run into the previous clip's footage"}
        return {"reason": f"extending would repeat footage clip {user.idx} already shows"}
    return {"ext": ext, "path": path, "t_start": src_in - ext, "thresh_db": 20 * float(np.log10(thresh)),
            "at_cut_db": 20 * float(np.log10(max(at_cut, 1e-6)))}


JOIN_MAX_SEC = 4.0
SENTENCE_END = re.compile(r"[.?!][\"')\]]*$")
SENTENCE_MAX_SEC = 8.0           # how far past a clip's end the rest of its sentence may be looked for, for a note that says the sentence is cut off


def measure_sentence_end(cut: Cut, clip_idx: int, max_sec: float = SENTENCE_MAX_SEC) -> dict:
    """The clip stops before its sentence is finished, though the sound is quiet at the cut (a breath or a pause inside the sentence, which measure_tail reads as "nothing is cut off"): where the
    sentence ends, from the words. Returns {"ext", "path", "t_end", "thresh_db", "at_cut_db", "sentence": the words added} or {"reason": ...}. The end is placed at the quietest moment after the
    last word, so it is checked the same way a decayed word is."""
    clip = cut.video[clip_idx - 1]
    path, src_in, src_out = _audio_for(cut, clip)
    lo = max(src_in, src_out - 6.0)
    ws = words_mod.words_in(path, lo, (src_out - lo) + max_sec + 0.5)
    inside = [w for w in ws if w.start < src_out - 0.02]
    if not inside:
        return {"reason": "no words are heard at the end of the clip"}
    last = inside[-1]
    if SENTENCE_END.search(last.text.strip()):
        return {"reason": f'the clip already ends on a finished sentence ("{last.text.strip()}")'}
    after = [w for w in ws if w.start >= src_out - 0.02]
    end_w = next((w for w in after if SENTENCE_END.search(w.text.strip())), None)
    if end_w is None or end_w.end - src_out > max_sec:
        return {"reason": f'the clip stops on "{words_mod.heard(inside[-6:]).strip()}" and no end of that sentence is heard within {max_sec:g}s after it'}
    t = words_mod.valley(path, end_w.end, end_w.end + 0.25)
    ext = t - src_out
    if ext < 0.15:
        return {"reason": "the sentence already ends at the cut"}
    user = _footage_user(cut, clip_idx, clip.src_out, clip.src_out + ext)          # the camera's clock, as the other footage checks use it (the words are on the recorder's clock)
    if user is not None:
        return {"reason": "extending would run into the next clip's footage" if user.idx == clip_idx + 1 else f"extending would repeat footage clip {user.idx} already shows"}
    rms = _rms10(_pcm(path, max(0.0, t - 2.0), 4.0))
    thresh = _threshold(rms) if len(rms) >= 30 else 10 ** (-50 / 20)
    return {"ext": ext, "path": path, "t_end": t, "thresh_db": 20 * float(np.log10(thresh)), "at_cut_db": level_db(path, src_out - 0.05), "sentence": words_mod.heard(after[:int(max(1, (end_w.end - src_out) * 4))]).strip()}


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


def _edge_variants(phrase: str, end: bool) -> list[str]:
    """The phrase, then the words that matter at this edge: the LAST few for a clip's end, the FIRST few for its start. The reviewer's quote comes from one transcript and the audio is read by another
    (2026-10-15: 'oh i feel it right here too yep' against 'it right here too. Yep.'), so the full quote often misses by a word at the side that does not matter; the words at the edge are what places the cut."""
    toks = words_mod.tokens(phrase)
    out = [phrase]
    for n in (5, 4, 3):
        if len(toks) > n:
            out.append(" ".join(toks[-n:] if end else toks[:n]))
    return out


def locate_end(cut: Cut, clip_idx: int, phrase: str, reach: float = 8.0, max_trim: float = 6.0) -> dict:
    """Where clip `clip_idx` should end so it stops right after `phrase` (the last words to keep). Returns {"trim": s from the clip's end, ...} or {"reason": ...}. The cut is placed at the quietest point
    in the 0.25 s after the last kept word, the way start_at_words places its cut before the first."""
    clip = cut.video[clip_idx - 1]
    path, src_in, src_out = _audio_for(cut, clip)
    lo = max(src_in, src_out - reach)
    ws = [w for w in words_mod.words_in(path, lo, src_out - lo + 0.3) if w.start < src_out - 0.02]
    hit, used = None, phrase
    for used in _edge_variants(phrase, end=True):
        hit = words_mod.find_phrase(ws, used)
        if hit is not None:
            break
    if hit is None:
        return {"reason": f"could not find \"{phrase}\" near the end of clip {clip_idx}; the audio there reads: \"{words_mod.heard(ws[-10:])}\""}
    want = words_mod.tokens(used)
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
    hit = None
    for used in _edge_variants(phrase, end=False):
        hit = words_mod.find_phrase(ws, used)
        if hit is not None:
            break
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


def _transcript_dirs() -> list[Path]:
    """Every folder a project's transcripts may be in: the app's own projects folder, and each project the app knows (known_projects.json), because a project may live anywhere (Ryan's Runnells Wall Removal is
    on an external drive, next to its footage). Newest first within each."""
    import os
    app = Path(os.environ.get("POSTHOUSE_APP_DIR") or Path.home() / "Library" / "Application Support" / "Post House")
    root = Path(os.environ.get("POSTHOUSE_PROJECTS") or app / "projects")
    dirs = sorted(root.glob("*/transcripts"), key=lambda d: d.stat().st_mtime, reverse=True) if root.is_dir() else []
    try:
        known = json.loads((app / "known_projects.json").read_text())
        dirs += [Path(v) / "transcripts" for v in known.values() if isinstance(v, str)]
    except (OSError, ValueError, AttributeError):
        pass
    return [d for d in dirs if d.is_dir()]


def _transcript_phrases(path: str) -> list[dict] | None:
    """PreCut's own transcript of this file (phrases with word timing), from the project folders: the first one whose source is this recording. None when there is none."""
    stem = Path(path).stem
    for d in _transcript_dirs():
        f = d / f"{stem}.json"
        if not f.is_file():
            continue
        try:
            j = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if Path(str(j.get("source_path", ""))).stem.lower() == stem.lower() and isinstance(j.get("phrases"), list):       # the transcript is of the proxy (.mp4, lower case) the cut's original (.MP4) was made from: the same recording
            return j["phrases"]
    return None


def _distinctive(text: str) -> list[str]:
    seen: list[str] = []
    for w in (x.strip("'") for x in words_mod.tokens(text)):
        if w not in RESTORE_STOP and len(w) > 2 and w not in seen:
            seen.append(w)
    return seen


def _line_candidates(phrases: list[dict], want: list[str], keep: int = 5) -> list[dict]:
    """The runs of one to three consecutive transcript phrases that best hold the wanted words, best first, none overlapping another: {"rank", "share", "span": (first, last), "held"}.

    A word counts by how rare it is in this recording (a word said in two phrases of two hundred names a line; "septic" in a recording about a septic system names nothing), so the share held is
    weighted by that. The rank also falls with every phrase the words are spread over (0.7 a phrase): words found in separate sentences are far likelier to be chance than words found in one. Each end
    phrase of a run must hold one of the words. The best run that holds the rarest word is always kept: a transcript mishears the common words around it."""
    import math
    toks = [words_mod.tokens(p.get("text", "")) for p in phrases]
    n_ph = len(phrases)
    df = {w: sum(any(words_mod._sim(w, h) >= 0.8 for h in ts) for ts in toks) for w in want}
    idf = {w: max(math.log((n_ph + 1) / (df[w] + 0.5)), 0.05) for w in want}           # smoothed, and never zero: in a transcript of one phrase every word is "common"
    total = sum(idf[w] for w in want if df[w]) or 1.0                                       # a word the recording never has (the note's own paraphrase, or a word the transcript mishears) neither helps nor hurts
    rows = []
    for i in range(n_ph):
        for n in (1, 2, 3):
            if i + n > n_ph:
                break
            heard = [w for ts in toks[i:i + n] for w in ts]
            held = [w for w in want if any(words_mod._sim(w, h) >= 0.8 for h in heard)]
            ends_ok = n == 1 or all(any(words_mod._sim(w, h) >= 0.8 for h in toks[k] for w in want) for k in (i, i + n - 1))
            if held and ends_ok:
                share = sum(idf[w] for w in held) / total
                rows.append({"rank": share * 0.7 ** (n - 1), "share": share, "span": (i, i + n - 1), "held": held, "known": sum(1 for w in want if df[w]), "min_df": min(df[w] for w in held)})
    rows.sort(key=lambda r: -r["rank"])
    out: list[dict] = []
    for r in rows:
        if all(r["span"][1] < o["span"][0] or r["span"][0] > o["span"][1] for o in out):
            out.append(r)
        if len(out) == keep:
            break
    rarest = min(want, key=lambda w: df[w])
    if df[rarest] and not any(rarest in r["held"] for r in out):
        extra = next((r for r in rows if rarest in r["held"]), None)
        if extra:
            out.append(extra)
    return out


def _pick_line(note_words: str, options: list[str], client=None) -> int | None:
    """Which of the candidate lines the note means, chosen by the model, which can see that a transcript's "out of substances" is "on a septic system". None when it says none of them is."""
    if client is None:
        from posthouse.cli_llm_client import CLIBackedClient
        client = CLIBackedClient()
    listing = "\n".join(f"{k + 1}. {o}" for k, o in enumerate(options))
    prompt = (f'A video editor wrote a note asking to put back a line of an interview. The words of the note: "{note_words}"\n\nCandidate lines from an automatic transcript. It mishears words '
              f'(for example "septic system" can be written "out of substances"), so judge by sound and sense, not spelling:\n{listing}\n\nWhich candidate is the line the note means? Be strict: a candidate '
              'counts only if what the note describes is what is said there, or a misheard version of it by sound. A single shared ordinary word ("water" for "weather") is NOT a match, and a wrong '
              'line put into a video costs the editor more than no line, so when you are not sure say none. Reply with ONLY JSON: {"pick": <candidate number>} or {"pick": null}.')
    resp = client.messages.create(system="You pick the transcript line an editor's note refers to. Reply with only JSON.", max_tokens=100, temperature=0, messages=[{"role": "user", "content": prompt}])
    m = re.search(r"\{.*\}", resp.content[0].text, re.S)
    try:
        pick = json.loads(m.group(0)).get("pick") if m else None
    except ValueError:
        return None
    return int(pick) - 1 if isinstance(pick, int) and 1 <= pick <= len(options) else None


def _confirm_line(wanted: str, line: str, client=None) -> bool:
    """A second, narrower question about the one line chosen: does it, by itself, say what was asked for? The first question picks the best of several, and the best of several can still be wrong (2026-10-15:
    for 'a line saying the old door opening is being measured', "And doors not open." was picked). Only a plain yes keeps the line."""
    if client is None:
        from posthouse.cli_llm_client import CLIBackedClient
        client = CLIBackedClient()
    prompt = (f'A video editor wants to add a line to a video, and describes it: "{wanted}"\n\nA candidate line, from an automatic transcript that mishears words: "{line}"\n\n'
              'Two kinds of description exist. (1) The words someone remembers being SAID, close to a quotation: the candidate matches when it SOUNDS like those words, even where the transcript got words wrong '
              '(for example "if you don\'t have curbs, you\'re probably on a septic system" can be transcribed "if you don\'t have curbs ... you\'re probably out of substances"). '
              '(2) A description of a FACT the line must state ("a line saying the old door was measured"): the candidate matches only if the line itself states it; a line that merely shares a word is no match. '
              'Decide which kind it is, then answer. Reply with ONLY JSON: {"says_it": true} or {"says_it": false}.')
    resp = client.messages.create(system="You check whether a transcript line says what an editor asked for. Reply with only JSON.", max_tokens=60, temperature=0, messages=[{"role": "user", "content": prompt}])
    m = re.search(r"\{.*\}", resp.content[0].text, re.S)
    try:
        return bool(m and json.loads(m.group(0)).get("says_it") is True)
    except ValueError:
        return False


def locate_restore(cut: Cut, phrase: str, before: int | None = None) -> dict:
    """Where in the recording the line the note names is, and where in the cut it goes. Returns {"path", "t0", "t1" (source seconds of the camera file), "heard", "held", "after"/"before" (the clip it
    goes next to), "lav", "lav_offset"} or {"reason": ...}.

    The line is searched in the transcript of each file the cut is made from, by the note's distinctive words (a transcript mishears, so half of them is enough, but at least two). What the cut already
    shows of it is not put back twice. Its ends are placed at the quietest moment next to the first and last word, the way every other cut here is. It goes where it was said: after the clip of the same
    recording that ends nearest before it, else before the one that starts nearest after it, unless the note names the clip it must come before."""
    want = _distinctive(phrase)
    if not want:
        return {"reason": "the note does not name the line in enough distinctive words to find it"}
    pool, missing = [], []
    for path in sorted({c.src_path for c in cut.video}):
        ph = _transcript_phrases(path)
        if ph is None:
            missing.append(Path(path).name)
            continue
        pool += [(c, path, ph) for c in _line_candidates(ph, want)]
    pool.sort(key=lambda x: -x[0]["rank"])
    if not pool:
        why = f" (there is no transcript for {', '.join(missing)})" if missing else ""
        return {"reason": f"could not find that line in the footage the cut is made from{why}; the words searched for were: {', '.join(want)}"}
    top, by_model = pool[0], False
    weak = pool[0][0]["known"] < RESTORE_MIN_WORDS or pool[0][0]["min_df"] > RESTORE_COMMON_DF                    # one known word, or only common words: the words alone cannot say it is the line ("weather" is 0.83 like "water")
    if weak or (len(pool) > 1 and pool[0][0]["rank"] < RESTORE_CLEAR_LEAD * pool[1][0]["rank"]):           # no clear winner by the words alone: the model chooses among what was found, and may say none
        by_model = True
        shown = pool[:6]
        pick = _pick_line(phrase, [" ".join(p["text"] for p in sp[c["span"][0]:c["span"][1] + 1]).strip() + f"  [{sp[c['span'][0]]['start']:.0f}s]" for c, _pa, sp in shown])
        if pick is None:
            return {"reason": "could not tell which line in the footage the note means; the closest were: " + " / ".join('"' + " ".join(p["text"] for p in sp[c["span"][0]:c["span"][1] + 1]).strip()[:90] + '"' for c, _pa, sp in shown[:3])}
        top = shown[pick]
    cand, path, ph = top
    said = " ".join(p["text"] for p in ph[cand["span"][0]:cand["span"][1] + 1]).strip()
    if by_model or len(cand["held"]) < RESTORE_SURE_SHARE * len(want):          # a line that holds only some of the described words (two of seven: "door" and "open") is a guess, whoever made it: it must say what was asked
        if not _confirm_line(phrase, said):
            return {"reason": f'the footage has no line that says it: the nearest, "{said[:90]}", does not'}
    score, (i, j), held = cand["share"], cand["span"], cand["held"]
    if not by_model and (score < RESTORE_MIN_SCORE or len(held) < min(RESTORE_MIN_WORDS, cand["known"])):
        return {"reason": f"the closest line in the footage holds only {', '.join(held)} of the words searched for ({', '.join(want)}), too little to be sure it is the one"}
    w0, w1 = ph[i], ph[j]
    t0, t1 = float(w0["start"]), float(w1["end"])
    heard = " ".join(p["text"] for p in ph[i:j + 1]).strip()
    used = sorted((c.src_in, c.src_out) for c in cut.video if c.src_path == path)          # what the cut already shows of it: not put back twice
    free, cur = [], t0
    for a, b in used:
        if b <= cur:
            continue
        if a >= t1:
            break
        if a > cur:
            free.append((cur, a))
        cur = max(cur, b)
    if cur < t1:
        free.append((cur, t1))
    free_len = sum(b - a for a, b in free)
    if free_len < 0.5:
        at = next((c for c in cut.video if c.src_path == path and c.src_in - 0.3 <= t0 and t1 <= c.src_out + 0.3), None)
        where = f" (it plays in clip {at.idx}, at {at.tl_start:.1f}s)" if at else ""
        return {"reason": f'that line is already in the cut{where}: "{heard}"'}
    if len(free) > 1 or free_len < 0.5 * (t1 - t0):
        return {"reason": f'part of that line is in the cut already and the rest is split around other clips, so it cannot be put back as one piece: "{heard}"'}
    f0, f1 = free[0]
    tmpl = min((c for c in cut.video if c.src_path == path), key=lambda c: 0.0 if c.src_in <= f0 <= c.src_out else min(abs(c.src_in - f1), abs(c.src_out - f0)), default=None)
    if tmpl is None:
        return {"reason": "no clip of that recording is in the cut to take the sound and framing from"}
    lav, a_in, _ = _audio_for(cut, tmpl)
    off = a_in - tmpl.src_in                                                               # the recorder's time minus the camera's at that clip
    if f0 > t0 + 0.05:
        t0 = f0                                                                            # a neighbour already holds the start of the line: the free edge stays as it is
    else:
        t0 = max(0.0, words_mod.valley(lav, t0 + off - 0.15, t0 + off) - off)
    if f1 < t1 - 0.05:
        t1 = f1
    else:
        t1 = words_mod.valley(lav, t1 + off, t1 + off + 0.3) - off
    if t1 - t0 < 0.5:
        return {"reason": "the line is shorter than half a second once placed, so nothing was put back"}
    if before is not None and not 1 <= before <= len(cut.video):
        before = None
    after_c = None
    if before is None:
        prev = [c for c in cut.video if c.src_path == path and c.src_out <= t0 + 0.5]
        nxt = [c for c in cut.video if c.src_path == path and c.src_in >= t1 - 0.5]
        if prev:
            after_c = max(prev, key=lambda c: c.src_out)
        elif nxt:
            before = min(nxt, key=lambda c: c.src_in).idx
    return {"path": path, "t0": t0, "t1": t1, "heard": heard, "held": held, "want": want, "before": before, "after": after_c.idx if after_c else None, "template": tmpl.idx, "lav": lav, "lav_offset": off}
