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
