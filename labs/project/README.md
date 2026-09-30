# project: one index of everything made for a project

Spike. Standalone, file in and file out, not wired into `app/`. From the creator's "a central folder where every video
project gets its own folder and every asset is in one place". Here the folders already exist (one per round), so this
indexes them **in place and moves nothing**: the XMLs point at their layer files by absolute path, and moving a folder
would break them.

```
python3 labs/project/project.py --root "~/Documents/Post House Reviews" --name "Runnells Tiling" [--open]
python3 -m pytest labs/project/tests -q
```
Writes `<root>/<name> - project index.html` and `<name> - project.json` next to the folders. Every folder whose name
starts with the project name is one entry.

## What it shows
At the top: the newest cut that has layers on it (with its XML and review page), the newest QA pass and the newest review
page. Below: every folder, grouped by what it is and newest first, each with the facts its own records give and links to
its XML, review page, QA report, style report or preview. Kinds it recognises (from the records the tools leave):
revisions (`ops.json` + `changes.json`: V2 -> V3, how many notes applied, layer warnings), reconforms (the step ledger),
QA passes (verified / unmeasured / not done / failed counts, unrequested changes), review pages (clips, length, beatmap
lanes, layers), callouts and their changes, image cards, captions, music and sound-effect builds (window, effect time,
whether the music came from a reference), sound-effect replacements, reference music, style profiles, and notes.

## Honest limits
- It says what exists and what it found. It **cannot say whether anyone approved it**; that lives in `docs/STATUS.md`.
- **Notes written by Claude as stand-ins are flagged** (a `_stand_in` marker in the notes file), so a test round is never
  mistaken for Ryan's own notes. A notes file without the marker is shown as "from Ryan".
- Classification comes from file names and records; a folder holding none it knows is listed under "Other files".
- Dates are the newest file's modified time, not when a decision was made.
