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
