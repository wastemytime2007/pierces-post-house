# uxp_transitions: can a script inside Premiere add the transitions an XML cannot carry?

Standalone Premiere Pro UXP panel, not wired into `app/`. Built 2026-10-21 after Ryan's two test exports (`~/Desktop/Film Impact Push Transition (and sfx from my own library).xml`, `~/Desktop/Premiere Composer Transition.xml`) showed:

- A Film Impact transition is written to XML as a plain Cross Dissolve; the plugin's name is not in the file.
- A Mister Horse (Premiere Composer) transition is written as graphic layers on V2 and V3; imported from XML they show the plugin's fallback text instead of the effect.
- Ryan's own SFX (file, in/out, level, speed) does round trip through XML.

Research (2026-10-21): Premiere's UXP API (25.6 and newer) can list video transition match names (`TransitionFactory.getVideoTransitionMatchNames`) and add one at a clip edge with a length and alignment (`VideoClipTrackItem.createAddVideoTransitionAction`, `AddTransitionOptions`). Adobe's docs do not say whether third-party match names are accepted. Film Impact is now part of Premiere (25.5, Adobe acquired it), so its transitions may be on the list. This panel is the test.

## The XML half: your whoosh under every cut (`place_sfx.py`)

`python3 labs/uxp_transitions/place_sfx.py in.xml out.xml` takes a Premiere-exported sequence, finds the cuts on V1 (a clip starting exactly where the previous one ends) and writes Ryan's whoosh at each one, copied from his own test export (`templates/film_impact_push_whoosh_template.xml`): `Whooshes.wav`, the same excerpt, -5 dB, 120% speed, as a stereo pair on two new audio tracks after the existing ones, from 4 frames before the cut to 7 after (his: cut 116, whoosh 112 to 123). It inserts one block of text and changes nothing else (checked). The Transition Lab panel then adds `AE.AE_Impact_Push`, 6 frames, centred, at the same cuts ("Add at EVERY cut"). Tested (`test_place_sfx.py`): on his own one-cut sequence, with his whoosh and transition taken out, the tool recreates his hand-made whoosh exactly; on a three-cut sequence it places three linked pairs with unique ids. Not yet imported into Premiere. Limits: it reads cuts where both clips have real start and end frames (a sequence that already has transitions writes -1 at those clips); a cut closer than 4 frames to the start or 7 to the end is skipped.

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
