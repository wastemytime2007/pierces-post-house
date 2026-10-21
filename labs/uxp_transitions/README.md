# uxp_transitions: can a script inside Premiere add the transitions an XML cannot carry?

Standalone Premiere Pro UXP panel, not wired into `app/`. Built 2026-10-21 after Ryan's two test exports (`~/Desktop/Film Impact Push Transition (and sfx from my own library).xml`, `~/Desktop/Premiere Composer Transition.xml`) showed:

- A Film Impact transition is written to XML as a plain Cross Dissolve; the plugin's name is not in the file.
- A Mister Horse (Premiere Composer) transition is written as graphic layers on V2 and V3; imported from XML they show the plugin's fallback text instead of the effect.
- Ryan's own SFX (file, in/out, level, speed) does round trip through XML.

Research (2026-10-21): Premiere's UXP API (25.6 and newer) can list video transition match names (`TransitionFactory.getVideoTransitionMatchNames`) and add one at a clip edge with a length and alignment (`VideoClipTrackItem.createAddVideoTransitionAction`, `AddTransitionOptions`). Adobe's docs do not say whether third-party match names are accepted. Film Impact is now part of Premiere (25.5, Adobe acquired it), so its transitions may be on the list. This panel is the test.

## The XML half: your whoosh under every cut (`place_sfx.py`, `make_whoosh.py`)

`python3 labs/uxp_transitions/place_sfx.py in.xml out.xml` takes a Premiere-exported sequence, finds the cuts on V1 (a clip starting exactly where the previous one ends) and writes Ryan's whoosh at each one: a stereo pair on two new audio tracks after the existing ones, from 4 frames before the cut to 7 after (his test export: cut 116, whoosh 112 to 123), built on that export's structure (`templates/film_impact_push_whoosh_template.xml`). It inserts one block of text and changes nothing else (checked).

**The sound is a plain file, not a Time Remap clip.** First proof (2026-10-21): Ryan's template whoosh, a sped-up excerpt of `Whooshes.wav` (source 41.0 to 54.2 frames, 120%, -5 dB), imported with the wrong, nearly silent part (35 to 45 frames): Premiere read the XML's in/out as plain source frames and ignored the Time Remap. `make_whoosh.py` therefore cuts his exact selection into `~/Documents/Transition Lab/sfx/Whoosh Push.wav` with the speed and level baked in, and the XML places that file (in 0 to 11 frames, no Gain, no Time Remap). The test (`test_place_sfx.py`) checks the baked file's length (his selection at 120%), that it is not silent, that its loudness shape matches the selection's and that it is about 5 dB quieter.

The Transition Lab panel then adds `AE.AE_Impact_Push`, 6 frames, centred, at the same cuts ("Add at EVERY cut"). The panel saves its whole log to `last_run_log.txt` in its data folder after every click. Limits: the tool reads cuts where both clips have real start and end frames (a sequence that already has transitions writes -1 at those clips); a cut closer than 4 frames to the start or 7 to the end is skipped. Second proof (baked file) not yet imported into Premiere.

## Automatic mode (v0.4): no click

`place_sfx.py` also writes one sequence marker per cut, named `PH transition`, comment `AE.AE_Impact_Push|6|0.5` (match name, frames, alignment, optionally `|video track`), in the form PreCut writes and Premiere imports. While the Transition Lab panel is loaded and its box is ticked, it looks at every sequence in the project (`project.getSequences()`, markers through `Markers.getMarkers`) when the project opens, a sequence is activated, media is imported or the project changes, plus every 3 seconds as a fallback. A sequence with pending markers gets each transition added at the clip that starts on the marker, and the marker is renamed `PH transition done` in the same undoable step (so nothing is applied twice); a marker it cannot act on becomes `PH transition FAILED` with the reason in the log. The panel logs which events it could listen for. Tested against a stand-in Premiere (`test_panel_logic.js`); not yet run in Premiere. The panel must be loaded: a plugin loaded from the UXP Developer Tools has to be loaded again every time Premiere starts; making it load by itself needs packaging as a .ccx (not done yet).

## The panel: what it does

1. **List transitions**: every video transition match name Premiere offers, filtered by a text box (default "impact"), also saved to `transition_match_names.txt` in the plugin's data folder (the panel prints the path).
2. **Add at the first cut / every cut**: adds the transition whose match name you paste, on the clip after each cut of the chosen video track of the active sequence, in one undoable step (Cmd+Z). Length in frames and alignment (0.5 = centred) are fields.

Errors are printed in the panel with their stack. Nothing else in the sequence is touched.

## Load it (once)

1. Open Premiere Pro 2026 (26.x) with a project and a sequence that has a few cuts on V1.
2. Open **Adobe UXP Developer Tools**, click **Add Plugin**, choose `labs/uxp_transitions/manifest.json`, then **Load** (the Actions menu). If it asks which app, choose Premiere Pro.
3. In Premiere, open **Window > UXP Plugins > Post House Transition Lab** (or Window > Extensions, depending on version).

## Run it

1. Click **List transitions** with "impact" in the filter. Note whether any names appear, and what they look like.
2. Paste one match name into the name box and click **Add at the FIRST cut only**. Look at the timeline and scrub the cut.

## What to report

- The names from step 1 (the file or a screenshot), or "none".
- Whether the transition appeared on the timeline at the cut, and whether it plays as that transition.
- Any text in the red/ERROR lines.

## Checked

`node labs/uxp_transitions/test_panel_logic.js` runs the panel against a stand-in Premiere and passes: it checks the panel's own logic only (lists and saves names, finds cuts between touching clips only, one transition per cut, frames converted to ticks, errors shown). It does not prove Premiere accepts the calls; that needs Premiere. Not yet run inside Premiere.

Note: `VideoTransition` has no readable properties in Adobe's reference, so display names are not available; only match names are listed.
