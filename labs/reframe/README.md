# reframe: make an exporter XML vertical with Premiere's own scale and position

Standalone, XML in and XML out, nothing in `app/`. Built 2026-10-06 from Ryan's Premiere export of a repositioned clip (`safety_net/fixtures/premiere_motion/`).

```
python3 labs/reframe/reframe_xml.py <export.xml> <plan.json> --out <vertical.xml>
python3 -m pytest labs/reframe/tests -q
```
It sets the sequence to 1080x1920 and adds one Basic Motion (scale, position) to each picture clip of the cut, in the structure Premiere writes. The clips stay the camera ORIGINALS: handles intact, re-framable in Premiere. With `mics` in the plan only the person talking has their microphone enabled in each piece.

## The rule (confirmed by Ryan's readout: Position 1530.0, 960.0, Scale 118, Anchor 1920, 1080 gave `center.horiz` 0.2578125)
`center.horiz = (Position.x - sequence_width / 2) / source_width`, positive to the right. To put source pixel `subject_x` mid-frame at scale `s`: `horiz = (source_width / 2 - subject_x) * s / source_width`. `center.vert` is always written 0: its unit is unconfirmed, and a scale of at least 1920 / 2160 (88.9%) already fills the frame top to bottom. One caveat a single sample cannot remove: 3840 is both the source width and twice the sequence height; they coincide for the 3840x2160 DJI sources, so it is safe for them and unconfirmed for any other size.

## What it refuses
A plan row with no picture clip, a picture clip with no row, and any piece whose frame would show beyond the edge of its clip. Its checks (all must pass before it reports success): vertical sequence size, every clip carries one Basic Motion that decodes back to the intended source pixel (within 2 px), no frame shows beyond its clip, everything else in the XML unchanged (only the motion added and microphones switched off), only the speaker's microphone live, and `safety_net/verify_export.py`.

## Where it sits
`labs/recruit/build_vertical_reel.py` runs the pipeline for a reel: resolved cuts, split at speaker changes (`labs/review_loop/speakers.py`), PreCut's exporter through `posthouse.coldfootage` with both recorders handed over as an audio-sync state (PreCut writes the lav tracks and mutes the camera audio itself), then this tool. `labs/review_loop` previews the result as Premiere frames it (`timeline.motion_of`, `render_preview.geometry_filter`) and `verify_preview.py` checks the framed frames against the sources.

## Not done
Vertical (`center.vert`) moves; keyframed motion; sources other than 3840x2160; graphics and music layers on this XML (the overlay and audio tools).
