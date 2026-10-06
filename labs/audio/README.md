# audio: a sound effect and a music bed, generated and mixed under the speech

Spike. Standalone, file in and file out, not wired into `app/`. From the creator's "Forget Capcut"
workflow: sound effects for things coming on screen, and a background music track, both generated with
ElevenLabs. Explored alongside, not instead of, the Artlist-local-library decision (ROADMAP Decision
Log, 2026-09-29).

**In:** an export XML, the rendered cut for the window (with its audio), an overlay folder (the
callout the effect goes with), a time window, two prompts.
**Out:** `music_stem.wav` (the music with the ducking already in it), `sfx_clip.wav` (the effect, its own
clip at the callout's time), `audio_preview.mp4` (the picture with the mix), `audio.json`, and an XML with
both on new audio tracks.

```
python3 labs/audio/make_audio.py --xml "<export.xml>" --base "<cut_1080.mp4>" --out "<folder>" \
    --start 0 --end 22 --callout "<overlay folder>" [--preview-video "<preview.mp4>"] \
    [--sfx-prompt "..."] [--music-prompt "..."] [--music-db -5] [--duck-db 12]
python3 labs/audio/verify_audio.py "<folder>"
python3 labs/audio/place_audio.py "<export.xml>" "<folder>" --out "<new.xml>"
python3 -m pytest labs/audio/tests -q
```

Name the placed XML with the cut's version last (`..._layers_v3.xml`, not `..._audio_v2.xml`): the
review loop reads the version from a trailing `_vN`.

## Music like a reference track (`reference_music.py`)
The creator attaches a track he likes and has it generate one "very similar". Made measurable:
```
python3 labs/audio/reference_music.py --reference "<track>" --out "<folder>" --seconds 23 [--tries 3]
python3 labs/audio/reference_music.py --reference "<track>" --out "<folder>" --rank-library "<Artlist Library/Music>"
python3 labs/audio/make_audio.py ... --music-reference "<track>"        # the same, inside the normal build
```
1. **Measure the reference:** tempo (from the onset envelope), where its energy sits in tone (log-frequency
   centre), rhythmic density, dynamics (one-second windows, fades ignored), stereo width, low end, loudness.
2. **Describe it in words, only from those measurements** (a fixed mapping, no model, no names): no song, artist
   or lyric is ever named, so nothing asks for a copy of a particular piece. A tempo above 140 BPM is asked for at
   half (generators hold mid tempos far better; half or double time counts as a match when measured).
3. **Generate a few takes** (each a few cents; `--tries`, default 3) and re-measure each against the reference.
   A take is **close enough** when tempo is within 8% (half/double counts) and at least two of tone (within a
   third), rhythmic density (within 60%) and dynamics (within 4 dB) also hold. The closest take is kept.
4. **`--rank-library`** does the free, licensed version: it measures every track in a folder once (cached) and ranks
   them against the reference. No generation, no spend.
5. In `make_audio`, `--music-reference` picks the take the same way; `--music-file` reuses an exact earlier file
   (reconform does this, so a rebuild never changes the music). `verify_audio` re-measures the finished stem
   against the reference; it is a gating check only when a take was accepted as close, otherwise it is shown as
   information with the measured gap.

**What was actually found** (real reference: Barrell, "Takin' a Walk", from the local Artlist library; six generated
takes over two prompt versions, all measured): a description-only prompt pins down **tempo and rhythm reliably**
(after folding the tempo, two of three takes landed on the reference's tempo exactly) but **not tone or dynamics**:
tone overshot to about half the reference's centre with "dark, bass-heavy" wording, and dynamics missed in every
take. No take met the bar. The measurements themselves had flaws that were fixed after the first round (a frame-averaged
centroid dragged up by quiet bright frames; fades counted as dynamic swings), and the tone wording was then set to an
evidence-based middle that has **not** been tried against the generator. So: use the ranking for a dependable match,
and treat generated takes as approximate, with the gap printed.

## Replacing an effect from a note (`replace_sfx.py`)
A note like "make this sound effect sound like something being highlighted on a piece of paper" goes
through `revise.py`: the interpreter chooses the `replace_sfx` operation and must copy the wanted sound
from the note's own words (an invented sound is refused, like an invented time). `revise.py` changes
nothing on the timeline for it and reports it as "not applied here, made by the audio step". Then:

```
python3 labs/audio/replace_sfx.py --audio "<audio folder>" --ops "<revise's ops.json>" \
    --notes "<review_notes.json>" --out "<new folder>" [--preview-video "<video>"]
```
The note is matched to the effect nearest its moment (from 1 s before it starts to 1 s after it ends, or
it is reported as not applied). That one effect is generated again from the note's description plus
", short and subtle, a sound effect for a graphic appearing on screen" (visible in the ledger). The music,
the speech and the effect's time are left as they were, and the old folder is not touched. It is
re-verified with everything above plus: music and speech byte-identical, the effect at the same time, and
the new effect a measurably different sound (waveform match under 0.60). One change per effect per run.

## How it works
- **Key.** Read from `~/.config/post-house/elevenlabs.env` (mode 600, outside the repo). Never printed,
  logged or written to any output. Needs the Sound Effects and Music permissions.
- **Generation.** `POST /v1/sound-generation` (1.2 s) and `POST /v1/music` (window + 1 s). Results are
  cached by prompt in `<folder>/generated/`, so re-running with the same words does not spend again. A
  new prompt is a new (small) charge.
- **Level.** The music is set relative to the speech level measured while the speaker is talking
  (`--music-db`, default 5 dB below it in the pauses), then ducked `--duck-db` (default 12) while the
  speaker talks. The ducking is an exact smoothed volume curve (0.15 s down, 0.35 s back up), not a
  compressor, so its depth is known. Music fades in over 0.6 s and out over 1.6 s.
- **Effect.** Peak set 6 dB under the speech's peak, placed at the frame the callout enters.
- **The speech track is never touched.** The music and effect are new tracks; nothing else in the XML changes.
- **XML.** Each stereo clip goes on two new audio tracks (left, right), the way the existing tracks carry a
  file's channels. The music is a stem with the ducking baked in: to change it, re-run with other
  numbers, or lower the clip in Premiere.

## What is verified (`verify_audio.py`, measured from the files)
Stems are 48k stereo PCM and the music is exactly the window's length; music present in the longer pauses
and never clipping; music at least 15 dB under the speech while the speaker talks; ducks at least 6 dB
when the speaker starts; the speech level in the mixed preview within 1 dB of the original; effect peak
at least 5 dB under the speech peak; the effect lands within 30 ms of the callout (found by
correlating the mix, not read back from the plan); mix peak under -1 dBFS; fades at both ends. The
placement adds frame-exact checks, that nothing else in the XML changed, and `verify_export`.

## Real limits
- **Nobody has listened to it.** Every check is a measurement. Whether the music suits the piece, the
  effect suits the callout, and the level feels right is Ryan's ear.
- The pause-level check rests on a short stretch (about 0.9 s of qualifying pause in the 22 s window).
- The prompts are defaults chosen by Claude, not by Ryan. They are inputs.
- The source speech is quiet (about -33 dBFS RMS, peaks near -10), so everything sits low in absolute
  terms. Levels here are relative to the speech; overall loudness is a separate job.
- Not done: a reference track ("a song like this one"), replacing an effect by note, several effects
  in one cut, or the whole cut (one 22 s window only, rule 7).
- ElevenLabs commercial-use terms for generated sound effects were not confirmed. Nothing generated ships
  in published work until they are.

## Library first for sound effects; music is generated (Ryan, 2026-10-03)
"Generate music and sfx from eleven labs. Only generate sfx for things that a sound doesn't already exist for in our library."
- **Music** is generated with ElevenLabs (or reuses an exact earlier file); the Artlist music library is only ranked against a reference when asked (`reference_music.py --rank-library`).
- **A sound effect** goes through `get_sfx` (make_audio.py; `replace_sfx.py` uses it too): `sfx_library.py` first asks the local `claude` CLI (free, text only) whether ONE file in the library is the same kind of
  sound; a hit is used as it is (cut to 4 s with a short fade if longer), and only with no hit is one generated. The library is his Artlist sound-effects folder (`POSTHOUSE_SFX_LIBRARY` to change it) plus a store of
  effects generated before (`~/Library/Application Support/Post House/generated_sfx`), so a sound is paid for once: a generated effect is saved there and the next request for it is a library hit.
- **If the library cannot be checked the run refuses** instead of generating ("could not check" is not "nothing there"); a model answer naming a file that is not in the library also refuses.
- `audio.json` records `source` (`library` or `elevenlabs`) and which file or why nothing fit; `verify_audio` shows it on its info line.
- **Dry run, free:** `python3 labs/audio/sfx_library.py "a doorknob opening" "a record scratch"` says, per sound, `LIBRARY <file>` or `GENERATE`.
- **Measured** on the real library (31 sounds): of 12 plausible sounds, 8 resolve to a library file and 4 would be generated (cash register, record scratch, applause, small explosion). One generated live (applause, 2.00 s,
  peak -1.6 dBFS) and the second request for it was a library hit. Not heard by the author. The model's yes/no is a judgement: it first picked crowd cheering for "applause" and the question was tightened; the hammer-on-metal
  and concrete-drill picks are borderline. The library check is cached by (question version, sound, library listing).
- The bleep tool is separate: it lays a 1 kHz tone, not a library sound (his library has "Explainer Video - Censorship Tone Beep" and he has a "Censor Bleep Sound Effect.mp3"; whether the bleep should use one of those instead is his call).

## A steady bed with no sound effect (`--mix-style bed --no-sfx`, 2026-10-06)
Ryan's wallpaper reel has no sound effect and a music bed that holds steady under the voice (measured: about 8 dB under it, level within about 4 dB), where the default build puts the music 5 dB under the speech and then ducks it a further 12 dB while anyone talks, about 17 dB under continuous speech: the "can't hear the music" complaint.
```
python3 labs/audio/make_audio.py --xml ... --base ... --out ... --start 0 --end <cut length> --no-sfx --mix-style bed --music-file "<conformed track>" --music-reference "<the reference's music>"
```
`--mix-style bed` defaults to `--music-db -8 --duck-db 0` and levels the music by the music while it PLAYS (a track with a bare hook and a bare last line was being levelled by its average over the silence). `verify_audio.py` judges a bed as a bed: **BED-LEVEL** (5 to 12 dB under the speech where both play) and **BED-STEADY** (level varies by 6 dB or less), and says `NO-SFX` instead of silently skipping the effect checks. The default (ducked) style and its checks are unchanged.

## Music that matches a reference and puts a beat on every event (`score_music.py`, `conform_music.py`, 2026-10-06)
- `score_music.py --plan score.json --reference-features ref.json --tries N` generates a structured ElevenLabs plan and keeps the first take that matches the reference on ALL four measures (tempo, brightness, rhythmic density, steadiness), each take measured on the part that gets used (from the groove's drop, not its sparse intro).
- `conform_music.py --music track.wav --events events.json --total SEC --out conformed.wav` fits the track's beat grid, puts its drop on the first event, and stretches whole or half beats between events (at most a few percent, reported) so a beat lands on each one; events within 0.12 s of each other are one event (reported); the music stops on the last event. It refuses a track whose beats are not on a steady grid.
