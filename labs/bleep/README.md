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


## Result of the detector on the real cut: it failed, three times
Ryan labelled the cut himself (`ground_truth.json`; score with `evaluate.py`). The word he wanted bleeped, "fuck" at about 28.35 s, sat inside
"don't know" (28.4 to 28.74 s): both Whisper models wrote those two words with probability 0.99 and agreed, nothing was stretched, nothing was
loud. It left no trace. The strict detector flagged four stretches; Ryan judged three of them and **all three were wrong** ("by" 26.3 s, "The"
31.5 s, "Yeah." 66.8 s; "Yeah," at 0.0 s was not judged). Precision 0 of 3, recall 0 of 1.
That is three failed approaches to the same word: a burst inside a long word (wrong word), a curse-word prompt for Whisper (changed nothing),
and the four-signal detector (missed it, three false leads). Per the rule "three failures means the approach is wrong", no more signals will be
added. What stands:
- **Words Whisper writes are bleeped automatically** (the standing rule; works).
- **A word it does not write is bleeped where a person points:** click the clip's box at the word and write "bleep this" (the spoken stretch at
  the click, at most 0.3 s either side). This is the route that worked. Covered by tests and verified on the real cut.
- The Suspects lane is **off by default** (`reconform.py --show-suspects` turns it on). It is noisy and was wrong three times out of three.
- What might change the picture, not done: a larger Whisper model (`medium` or `large`, a 1.5 GB or larger download) may write profanity it now
  drops, and can be tried against `ground_truth.json` with `evaluate.py`. That is Ryan's call.
