---
name: post-house-review-loop
description: Use when working on Pierce's Post House video review loop: turning an export XML into a review page with callouts, captions, music, sound effects and image cards; applying review notes as revisions; re-placing layers after a revision; QA-checking a new version against its notes; comparing a cut's style to a reference; indexing a project. Covers the labs/ tools, their order, what each verifies, and the rules that keep claims honest.
---

# Post House review loop

A standalone set of tools under `labs/` (nothing here is in `app/`; integrating it is a separate step Ryan has not asked for).
Read `labs/TESTING.md` for how to try each piece and `labs/README.md` for the map from capability to tool. Each tool has its own
README with its checks and real limits.

## Setup
- `PRECUT_ROOT=~/precut-checkout` for anything that touches the cut (PreCut is protected; never commit to it).
- `ffmpeg`/`ffprobe`, Node (`npx`) for HyperFrames (pinned 0.8.93), Google Chrome for looking at the pages headless.
- ElevenLabs key at `~/.config/post-house/elevenlabs.env` (`ELEVENLABS_API_KEY=...`, mode 600, outside the repo, with the Sound Effects
  and Music permissions). It is never printed or written anywhere else. Generation costs cents and is cached by prompt.
- Ryan's licensed Artlist library is local: `~/Downloads/Artlist Library/{Music,Sound Effects,Footage}`.

## The loop, in order
1. **Review page.** `python3 labs/review_loop/build_review.py <export.xml> --out <dir>`. A preview of the cut, and if the XML has layers
   placed by these tools, a second `preview_full.mp4` with them composited and a **Layers on/off** toggle. The page has drawing and
   timecoded notes, and a **beatmap** (lanes for Cuts, Edits, Callout, Card, Captions, Music, SFX). Notes download as JSON.
2. **Notes to a revision.** `python3 labs/review_loop/revise.py <xml> <notes.json> --out <dir>`. An interpreter (the local `claude` CLI, no
   API spend) may only *choose* from a fixed vocabulary and may never invent a time, an amount, or wording the note did not give.
   Timeline operations are applied and verified: `tighten_pause`, `remove_range`, `trim_start/end`, `extend_end`, `start_at_words`,
   `drop_clip`. Three operations are made by other tools and reported as "not applied on the timeline": `replace_sfx`, `extend_graphic`,
   `edit_callout`. `ops.json` and `changes.json` in the output folder feed the next steps.
3. **Graphics and audio from notes.** `labs/overlay/change_callout.py --ops ... --notes ... --xml ... --out ...` (keep a callout longer; new
   words; drop its second line; one render carries all of them, default extra time 1.5 s and it says so when the note gave no amount).
   `labs/audio/replace_sfx.py --audio ... --ops ... --notes ... --out ...` (a new sound effect from the note's own description).
4. **Making layers.** `make_overlay.py` (a callout from a note's drawing), `make_image_card.py` (a screenshot with a border, highlight, caption),
   `make_captions.py` (verbatim captions from the cut's own audio, word highlight, moved clear of callouts), `make_audio.py` (music bed
   ducked under speech + an effect at the callout; `--music-reference <track>` to match a reference; `reference_music.py --rank-library`
   to rank licensed tracks against a reference for free). Put layers on the XML with `place_overlay.py` / `place_audio.py`.
5. **After a revision or a layer change: `python3 labs/reconform/reconform.py --revised <xml> --overlay <folder> [--overlay ...] --captions
   <folder> --audio <folder> --out <dir>`.** `revise.py` ripples every track alike, which cuts through a callout, effect or music bed (it
   warns). Reconform strips the layers and puts them back: callouts by the source frame they were drawn on (dropped and reported if that
   frame was cut out), captions rebuilt on the revised speech, the same music and effect re-mixed (nothing regenerated), then a new page.
6. **QA.** `python3 labs/qa/qa_pass.py --notes ... --revise-dir ... --before-xml ... --after-xml ... [--before-page ... --after-page ...]
   --out <dir>`. Re-measures every note on the new version's files (not the ledger) as VERIFIED / APPLIED-UNMEASURED / NOT DONE / FAILED,
   with before and after frames and any change nobody asked for. Always run a negative control when adding a check.
7. **Style.** `python3 labs/style/style_profile.py --reference <video> --ours <preview> --ours-timeline <timeline.json> --out <dir>`.
   Measures both the same way and suggests notes. It never edits.
8. **Index.** `python3 labs/project/project.py --root "~/Documents/Post House Reviews" --name "<project>"`. Reads the folders in place.

## Rules that kept this honest (keep them)
- **Verify by measuring the output file, then try to break the check.** Every tool ships checks on the rendered files and tests that feed it
  deliberately wrong input (a card with no border, an unchanged cut, a ledger that lies). When a check passes too easily, add the control.
- **Nothing is "done" until Ryan says so.** Passing tests and verified checks go under "built, not yet judged" in `docs/STATUS.md`. Notes
  written by Claude to test a chain are **stand-ins** and are marked `_stand_in` in the file; never present them as Ryan's.
- **No invented specifics.** Amounts come from the note or a stated default; wording comes from the note; quotes come from the transcript.
  Captions are the speech verbatim with no dashes.
- **Name XMLs with the cut's version last** (`..._layers_v3.xml`): `revise.py` reads the version from a trailing `_vN`.
- **Sandbox quirk:** long inline shell with heredocs or computed command names is refused in the isolated worktree; write a script file and
  run it.
- **The Decision Log is law** (`ROADMAP.md`). Settled here: standalone in `labs/` until all skills are done and tested, then integrated
  together; a callout whose frame is cut out is dropped, not moved; generated audio is explored alongside, not instead of, the Artlist decision.
- **Costs:** ElevenLabs only (cents per new prompt; cached). Everything else is local. Do not enable API billing paths in the app.

## Known limits worth knowing before promising anything
- Jump cuts inside one shoot are mostly invisible to picture-based cut detection; pace rows say UNRELIABLE when recall on known cuts is low.
- A description-only music prompt holds tempo and rhythm, not tone or dynamics; the measured gap is printed and the closest take kept.
- Premiere import is confirmed by Ryan for the callout, captions, music and effect XML; later variants (image card, reworded and longer
  callouts) are checked structurally (`verify_export`, placement checks) but not yet confirmed in Premiere.
- Captions only avoid overlays they are told about; they never look at the picture.
