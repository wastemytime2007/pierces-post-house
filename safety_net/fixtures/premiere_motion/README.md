# Premiere's own motion export (ground truth for scale and position)

`carpet_sub_01_scale118_center_0.257812.xml`: given by Ryan on 2026-10-06, exported from Premiere. One clip (a 3840x2160 DJI file) in a 1080x1920 sequence (timebase 30, not NTSC), no keyframes.
Basic Motion: `scale` = 118 (percent of the clip's native size), `center` = `<horiz>0.257812</horiz><vert>0</vert>`, `centerOffset` (anchor) 0, rotation 0, crops 0.

What this file settles: the structure Premiere writes (a `filter` / `effect` / `Basic Motion` with `scale`, `rotation`, `center`, `centerOffset`, crops) and that scale is a percent.

What it does NOT settle, so nothing may be built on it yet: the UNIT and DIRECTION of `center`. 0.257812 is exactly 33/128; read as a fraction of the sequence width (1080) it is 278.4 px, of the sequence height (1920) it is 495 px, of the source width (3840) 990 px. The leading guess (FCP7's normalisation, each axis by its own dimension, positive right) makes Position x 818.4. It needs Ryan's Position readout for this clip (Effect Controls > Motion > Position) to confirm.

Tried and failed: matching the finished carpet reel (`SoldFast Exports/How To Pull Up Old Carpet.mp4`) against this shot. The reel's frame at about 34 s matches source second 126 at scale about 1.0 near the centre (normalized cross-correlation 0.91), i.e. the reel does not contain this 118% / 0.257812 transform, so it cannot calibrate the unit.
