# Premiere's own motion export (ground truth for scale and position)

`carpet_sub_01_scale118_center_0.257812.xml`: given by Ryan on 2026-10-06, exported from Premiere. One clip (a 3840x2160 DJI file) in a 1080x1920 sequence (timebase 30, not NTSC), no keyframes.
Basic Motion: `scale` = 118 (percent of the clip's native size), `center` = `<horiz>0.257812</horiz><vert>0</vert>`, `centerOffset` (anchor) 0, rotation 0, crops 0.

## What Premiere showed for this clip (Ryan's screenshot, 2026-10-06, Effect Controls > Motion)
Position **1530.0, 960.0**, Scale 118.0, Anchor Point **1920.0, 1080.0** (the source's own centre, in source pixels), Rotation 0, crops 0.

## The rule this settles
`center.horiz = (Position.x - sequence_width / 2) / source_width`: (1530 - 540) / 3840 = 990 / 3840 = 0.2578125 exactly. Positive moves the clip RIGHT. `center.vert` was 0 with Position.y 960 = sequence_height / 2; its normaliser (source height 2160?) is NOT confirmed because no vertical move has been exported, so build vertical moves only with vert 0 until one is.
Read as the clip placed so that source pixel x_s is at the sequence's centre at scale s (fraction): Position.x = 540 + (1920 - x_s) * s, so `horiz = (1920 - x_s) * s / 3840`. For this clip that is x_s = 1081, i.e. it was moved so the left half of the frame is what shows.
One caveat the single sample cannot remove: 3840 is both the source width and twice the sequence height (1920). For the 3840x2160 DJI sources in a 1080x1920 sequence the two coincide, so the rule is safe for them; for any other source size (the Canon 8K clips) it is not confirmed which one it is.

## Tried and failed (kept so nobody repeats it)
Matching the finished carpet reel (`SoldFast Exports/How To Pull Up Old Carpet.mp4`) against this shot: its frame at about 34 s matches source second 126 at scale about 1.0 near the centre (normalized cross-correlation 0.91), so the reel does not contain this 118% / 0.257812 transform and cannot calibrate the unit.
