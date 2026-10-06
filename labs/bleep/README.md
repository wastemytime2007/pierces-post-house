# Bleep: curse words silenced and bleeped automatically

A standing rule (Ryan, 2026-09-30, Decision Log): every cut is scanned and every word on `profanity.txt` is bleeped without being asked.

    PRECUT_ROOT=~/precut-checkout python3 labs/bleep/bleep.py --xml "<cut .xml>" --out "<new folder>" [--words "a,b"] [--suspects-in START,END]

Reconform runs it as its last step by default (`--no-bleep` turns it off; `--ops/--notes` or `--suspects-in` point it at a stretch).

## What it does
1. Renders the cut's speech from the XML's enabled audio clips and transcribes it with word timing (Whisper `small`, English).
2. Matches whole words against `profanity.txt` (edit that file; `fuck*` also matches fucking; `ass` never matches class).
3. Pads each hit 0.08 s before and 0.12 s after and merges overlaps.
4. **Silences the speech**: each speech clip is split at the span and the inner piece disabled (`enabled=FALSE`, id ending `-bleep`). The picture and the
   cut's length are untouched; the preview and Premiere both honour it.
5. Lays a 1 kHz bleep per span, peak 6 dB under the speech peak, as an audio layer (lane "Bleep" on the review page).
6. Caption text for a listed word is starred (`F******`) by `make_captions.py`.

Running it again on its own result first undoes the previous bleep (pieces enabled, old bleep layers removed), so it never doubles.

## What it checks, from files
Speech silent in every span; speech just outside unchanged (compared under 800 Hz, to within two samples: a piece that starts on a video frame is not on
the audio sample grid, so a fraction of a sample shows in the highs and means nothing); only the spans lost; each bleep a 1 kHz tone at the right
level, time and length; the placed XML's own checks; and a second transcription of the result finds no listed word. Negative controls in the tests:
an unsilenced cut, a wider mute than declared, a wrong tone, a word still heard.

## The real limit, found on Ryan's cut
**Whisper drops or softens profanity.** On clip 8 of the Tiling cut neither Whisper model (`small`, `base`), nor a prompt full of curse words, wrote a curse
word, yet the audio has a burst 15 dB over the speech inside "what" (28.74 to 29.22 s, long for one word). So:
- The automatic rule covers **words the transcript shows**.
- A long word with a loud burst inside it is reported as a **suspect** (11 across the 73 s cut: emphasis trips it too, so it is noisy) and is bleeped
  **only** when a note points at that stretch (`bleep_word`; the strongest suspect in the stretch only).
- For such a word the tool has measured silence, a tone and a level, **not what the word was**. That is for the ear.
Padding can clip a neighbouring word by a few hundredths of a second. The bleep is a plain tone, not a chosen sound.


## The strict detector, and the two ways to point a bleep at a word Whisper did not write
Built after the real cut showed the limit. Each spoken word is scored on four independent signals: **stretched** (a short word spread over
0.4 to 1.0 s), **burst** (6 dB louder than the speech around it for 0.1 s), **unsure** (Whisper's own probability under 0.35) and **models
disagree** (the smaller `base` model heard a different word at that time). A word is flagged when two signals agree ("possible") or three
("likely"), **and at least one is a transcript signal** (unsure or disagree): a hidden word leaves a transcription problem, while a loud
long word both models agree on is just emphasis. Measured on the real 73 s cut: the acoustic-only rule flagged 14 stretches, 9 of them
confident, agreed, merely loud words; with the transcript requirement it flags 4 (`--min-signals 3` flags 1). Whether those 4 are the
curse word is not known: there was no ground truth for the cut. (My first guess, a burst inside "what", was wrong: Whisper was 96% sure of
"what" and the bleep landed on a word that was not the curse word.)

Flagged stretches appear on the review page as a **Suspects** lane. Two ways to bleep one, neither needing the word to be transcribed:
1. **Click a Suspects box** and leave any note ("yes"): that exact span is bleeped.
2. **Click the clip's box at the word** (the click position is kept) and note "bleep this": the spoken stretch at that spot is bleeped, at
   most 0.3 s either side of the click. This needs no detection at all, so it is the reliable route when the detector has nothing.


## What finally worked on the real cut, and what I got wrong on the way
Ryan's curse word ("what the **fuck** just happened", clip 8) was not in either Whisper transcript: both models wrote "what" (28.74 to 29.22 s,
probability 0.96, stretched) and "just", folding the word into them. Four things were tried, honestly scored against his labels
(`ground_truth.json`, `evaluate.py`):
1. **A burst guess** (bleep the loud stretch inside "what"): this was the right place. I called it a guess, Ryan said the sound was not at the
   curse word, and I dropped it. Measured afterwards, the bleep was in the page's audio exactly there; he had since pointed at 28.9 to 29.2 s (he typed 18.9 to 39.2), the same stretch.
2. **A curse-word prompt for Whisper**: changed nothing.
3. **The "strict" four-signal detector**: wrong, and worse than the plain burst rule. It required a transcript doubt (Whisper unsure, or the two models
   disagreeing) and so threw away the one stretch that was right: on the real cut it flagged 4 (Ryan judged 3 of them: all wrong) and found 0 of 1. The
   plain acoustic rule flagged 11 and found the word (recall 1 of 1, precision 1 of 3 judged). Kept as `--min-signals` but it is not the route.
4. **Silence, then listen again** (`reveal`): what works. Silence the first part of each loud stretch on its own, transcribe a short window around it, and
   keep any listed word that appears. With 28.9 to 29.2 s silenced Whisper wrote "what the fuck just happened" (fuck at 29.38 to 29.58 s); the span is the
   word joined to the stretch that hid it, 28.90 to 29.58 s, padded to 28.82 to 29.70 s. Across all 11 loud stretches on the 73 s cut it revealed that one
   word and nothing else (79 s). Two details that matter: each stretch is tried alone (silencing "what" and "just" together left Whisper nothing to hear:
   "don't know if it just happened") and with partial masks (85% and 60% of the front, 60% of the tail).
   It runs by default inside `bleep.py` and reconform (`--no-reveal` skips it); the result is checked by a further transcription of the bleeped audio
   (NO-LISTED-WORD-LEFT), which is also what caught a bleep that stopped short of the word's end.
Limits, plainly: n = 1 real word; it only finds words hiding behind a loud stretch; it costs about 80 s of transcription per cut; the 0.12 s pad after the
word runs into the start of the next word ("just" here); Ryan's first click at 28.35 s was about 0.5 s early, which is how a careful click can still miss.
Clicking the clip at the word, or naming exact times ("bleep 28.9-29.2", used as given, unpadded), remains the route for anything the tool misses.


## Placing the bleep: what Ryan's ear found, and what I got wrong
Ryan corrected the span by typing times (28.35, then 28.9-29.2, then 28.7-29.3; his digits were one key off) and every one of them, and every bleep I placed from them or
from my own analysis, was earlier than the word. When he could set it himself in the editor he put it at **29.44 to 29.70 s**. The earlier "it is at 28.9-29.2" reading of the
sound was mine and it was wrong: the loud vowel at 28.92-29.26 s is a shouted "what the", the **"f" hiss** of the curse word runs 29.32-29.48 s, its vowel 29.50-29.60 s and its
"k" 29.62-29.68 s, then "just". (I had read the hiss as the "k" of a word ending at 29.36 and recommended a bleep to 29.4; it did not cover the vowel and the "k".)
- The tool's own pieces were closer than my analysis: with the loud stretch silenced Whisper timed the word at 29.38-29.58 s, within about 0.1 s of Ryan's. `reveal` now returns the
  **word's own span** (it used to join it to the silenced stretch, which dragged the start to 28.9 s). Padded 0.08 s before and 0.12 s after, the automatic span is 29.30-29.70 s: it
  covers 100% of Ryan's span and starts 0.14 s early. Good enough to start from; he trims it in the editor.
- Times a person gives override the tool's hit where they overlap (removed, not merged back in). With Ryan's exact span the `--listen` check (a second transcription) finds
  no listed word over sound.
- **NO-LISTED-WORD-LEFT counts only a word over sound.** Whisper `small` writes words into digital silence ("what the fuck" into 0.7 s of nothing), which failed a correct bleep. A word
  now counts as left behind only if at least half of its time has sound (20 ms frames above -55 dBFS) in the bleeped audio.
- What this says about trusting my own analysis: two readings of the same spectrogram were confidently wrong, and a person dragging a box against the loudness trace and hearing
  the result got it in one go. That is why the editor exists.

## Editable bleeps (Ryan's idea, 2026-09-30): "make the bleeps editable ... drag longer or shorter ... move them right or left"
Automatic bleeps are only the starting positions. On the review page (any page built from a bleeped XML) the Bleeps lane holds one box per bleep:
drag a box to move it, pull its left or right edge to resize it, **+ Bleep at the playhead** adds one, **Delete** (or the button) removes the selected one, **Back to
automatic** undoes every edit, Alt+arrow nudges by 0.02 s (Shift 0.1 s). Because a 0.7 s box on a 73 s timeline is about 10 pixels wide, a **close-up strip** under it
(2, 4 or 8 seconds wide, about 500 px a second at 2 s) draws the speech's loudness every 10 ms, so the edges can be lined up with the vowel and the "k".
- **What you hear while editing:** the page plays a preview with the speech whole and no bleep baked in (`preview_live.mp4`); while the playhead is inside a box it mutes the
  speech and plays a 1 kHz tone at the automatic bleep's level. That is close to live, about a twentieth of a second, not sample-exact: a browser will not route a local
  video file through its audio engine, so the gate is the video's own mute plus a separate tone. The download is what is exact.
- **Applying:** "Download bleep edits" saves `bleep_edits.json`; `apply_edits.py --xml <cut> --edits <file> [--out <folder>]` silences the speech and lays a bleep at exactly
  those spans (frame-exact, no padding, nothing scanned or guessed, no transcription unless `--listen`), measures the result from files (silent, tone, level, the speech beside
  it unchanged), and rebuilds the page. An empty list takes every bleep out. The edits replace the automatic ones; they are not merged with them.
- **One folder to open:** `0 - CURRENT (open this one)` in Post House Reviews is overwritten each round (page, XML, check clip, QA), and the page header shows when it was built
  and each layer's times. Earlier rounds were separate folders whose names sort confusingly (round 10 and 11 above round 2), which is a likely reason a bleep "in the exact same
  spot" was reported twice.
- **`check_clip.py`** makes a short clip of the cut's own audio with a large timeline clock and a red BLEEP badge on screen exactly while the tone sounds, verified: the tone
  (measured) and the badge start and stop together.
Limits: the live preview mutes everything in a box (music and effects too, which is moot at a bleep); the preview's timing depends on the browser's audio latency (the lead
is 0.05 s while playing); the tone itself could not be auditioned by the tool that built it (the browser test ran without a user click, so it checked the gate's decisions
and the mute, not the sound).


## Learning from how you adjust the bleeps (`learn.py`)
Your idea: have the tool learn from where you moved the bleeps, so the next automatic bleeps start closer. **Applying your edits now teaches it automatically**
(`apply_edits.py` compares your final spans with the automatic run's own record, `bleep.json`, stores what it finds, and re-fits a small model that `bleep.py` reads on every run).
What is recorded, one line per judged bleep in `labs/bleep/learning/feedback.jsonl` (times and words only, never audio):
- **kept / adjusted**: the tool's span, the word's own timing from Whisper, and your final span;
- **deleted**: a bleep you removed (a false alarm); **added**: a bleep you put where the tool had none (a missed word), with what Whisper heard around it and whether it sat inside
  a stretched word or a loud stretch; **yes / no** on the flagged stretches (`learn.py ingest-notes`).
What it changes, all small, bounded and starting from the defaults:
- **Padding**, separately for words Whisper wrote and words found by the silence-and-listen-again step: the median of where your edges sit relative to the word, blended with the defaults
  as (n x median + 3 x default) / (n + 3). One edit moves it a quarter of the way, ten most of the way; clamped to -0.05..0.30 s before and 0..0.40 s after.
- **Reach**: the shortest stretched word examined for a hidden word, lowered (never raised, never below 0.25 s) only after three or more bleeps you added, toward the durations of the
  words the missed curse words hid behind.
What it never does: turn a detector off, bleep more or less because of a count, or edit the word list. Reliability counts are reported for you to decide.
`python3 labs/bleep/learn.py report` says what has been learned and what has not. **Today** it holds your one real edit and three "no" answers: revealed-word padding before the word is
0.045 s (was 0.08; you start 0.06 s after Whisper's start and end 0.12 s after its end), everything else is still at the defaults, and the reach is unchanged until three bleeps have been
added. One data point is thin: this moves the next automatic bleep 0.035 s, which is the honest size of what one edit can teach. The suspect flags are 0 for 3 (precision 0%).
Not learned, on purpose: anything about detecting words it cannot hear yet (there is one example; three missed words are needed before the reach changes).


### Learning which words, not only where (added 2026-09-30, after Ryan confirmed the intent: the tool finds bleeps from the transcript and applies them; placements, additions and removals he makes are applied and taught)
- **A word you remove the bleep from twice, and never keep, is skipped** in future cuts (only words Whisper wrote; never a guessed stretch). It is reported each time ("skipped 'hell' at 12.3 s: you removed
  that bleep before") so nothing vanishes silently, and `learn.py forget WORD` undoes it (it deletes every record that taught it).
- **An ordinary-length word (under 0.45 s) you add a bleep over twice is added** to what is bleeped in future cuts. A long, stretched word under an added bleep is a hiding word, which teaches
  the reach (above), not the word list. `profanity.txt` itself is never written to: learned words live in `learning/model.json`.
- One removal or one addition changes nothing; a word you have kept even once is never skipped.


## Only definitive curse words (Ryan, 2026-09-30: "why is it pulling normal words like so, real, now, because, just, when ... just apply bleeps to definitive curse words and let me add to any that may be missed")
The automatic run no longer flags ordinary words. It bleeps only **definitive** curse words: a word on `profanity.txt` (or one learned from his edits) that Whisper wrote, or one revealed by silencing a
loud stretch and listening again. Anything else that was missed, he adds on the page, and that teaches the tool. The "suspect" stretches (stretched, unsure or disagreed-on words) are off by default
(`--flag-suspects` brings them back for debugging); that also drops the second Whisper pass (the `base` model) that only existed to produce them. A note that points at a stretch still works.
**A word's end is trimmed to where the sound stops** (`trim_to_sound`): on his DeWalt/Milwaukee video Whisper wrote "f**k." at 36.28 s and gave it an end 0.8 s late (37.42 s; the sound was over by
36.6 s), which made a 1.3 s bleep. Now the end is pulled back to the sound plus a 0.06 s release margin, only when that saves at least 0.25 s, and the start is never trimmed: that bleep is 0.5 s.
**Finished videos work too** (`labs/review_loop/xml_from_media.py` makes a one-clip XML from an exported video, size and frame rate unchanged): the export check's coarse-slab rule (CUT-GRANULARITY)
does not apply to a single finished file and is skipped only for an XML carrying the converter's marker (`export_gate.py`); a single-clip XML without the marker still fails it (tested).
**Silence is rounded outward to whole frames**: at 30 fps a frame is 33 ms and rounding to the nearest frame left the start of a bleep audible (-41 dBFS); found by the end-to-end test on a converted video.

## Hidden words under low-confidence words (2026-09-30, from Ryan's DeWalt/Milwaukee edits)
Ryan added a bleep at 18.20-18.43 s that the tool had missed: Whisper heard "you're acting looking fine" and the word was folded into "acting" (0.18 s, probability 0.48). The listen-again pass only
tried loud-and-stretched words, so it never tried that one. Measured on the real audio: silencing the word makes Whisper write "ass" at 18.24-18.44 s (his bleep: 18.20-18.43 s).
Now words Whisper was unsure of (probability under 0.5, at least 0.10 s, not already listed, not inside a loud stretch) are also silenced and listened to again (`unsure_regions`). Nothing is shown or
bleeped unless a listed word appears. Whisper also invents curse words over silenced audio now and then (it wrote "f***ing" and "fuck" over "How do you go off?", which Ryan left alone), so a word found
this way is kept only when it appears under 2 of the 3 ways of silencing the stretch (`votes`); the loud-stretch path is unchanged. Rescanned the three real cuts: Milwaukee gives both of his bleeps and nothing
else, Tiling still gives only its one, smoke detectors still none. Caveat: the 2-vote rule was chosen after seeing those same cuts and rests on ONE real hidden word; it can still miss a hidden word or
invent one. Cost: Milwaukee 11 s to 55 s for a 20 s clip, Tiling about 180 s, smoke detectors 133 s (the extra work is the repeated listening).

## Trailing fricatives (2026-10-06)
Whisper ends a word early, and the 0.12 s pad is not always enough when the word ends in /s/ or /f/: that sound runs 0.05 to 0.1 s after the vowel at a level 18 dB under it, and the bleep stopped before it ("ass" played as a bleep followed by "ss", Ryan's note on Reel 3). `extend_over_fricatives` moves a span's end to the end of a trailing high-frequency run (more than 25% of a 10 ms frame's energy above 3.5 kHz, at least 30 ms), looking up to 0.3 s ahead and stopping at a gap over 60 ms. Tested on synthetic /s/ tails and on the real Reel 3 line; not listened to.
