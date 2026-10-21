# Framing rules: crop and reframe for landscape and portrait

Written 2026-10-21 from web research plus what the app already does. Not wired into the app yet. The same numbers are in `labs/reframe/framing_rules.json` (one place for code to read), checked by `labs/reframe/tests/test_framing_rules.py`.

**How to read the tags.** Every rule says where it comes from, because they are not equally solid:

- **[sourced]**: several independent pages say the same thing (links at the bottom). Most are creator and tool-vendor blogs, not platform documents.
- **[Ryan]**: his own ruling, already in the app.
- **[derived]**: arithmetic from his real cameras or a number I worked out from sourced ones. Checkable.
- **[mine]**: my judgment where the research was thin or silent. Unverified. Needs his eye on a real cut before it becomes law.

Where sources disagree (platform safe-zone pixels) I say so and chose a conservative value.

---

## 1. Talking head, landscape (16:9 output)

1. **Eyes on the top third line**, about 33% down from the top (360 px of 1080). Tilt or reposition until they sit on it; do not centre the head. [sourced]
2. **Headroom: a small gap above the head, never cut.** Too much makes the person sink and look less important; too little crops the head. Target: the top of the head 6 to 12% of the frame height below the top edge. [sourced for the principle; the 6 to 12% band is derived from the thirds rule]
3. **Looking room: more space on the side the person faces**, a little on the other side. For a centred tight shot, centre is fine; thirds is a guide, not a law. [sourced]
4. **Two people on one wide camera: frame each on whoever is talking** and cut where the speaker changes; the picture never pans between them. [Ryan, via `follow_speaker.py`]

## 2. Talking head, portrait (9:16, 1080x1920) cut from 16:9

The 9:16 window at full height shows only **31.6% of the width** of a 16:9 frame ([derived](https://toomuchfilmschool.substack.com/p/reframing-aspect-ratios-396): "nearly 70% of the picture" is lost), so a centre crop usually misses the person. Rules:

1. **Centre the window on the speaker's face, not the frame.** [Ryan, `framing.py`]
2. **Eyes at 30 to 38% down the frame (about 580 to 730 px)**, upper third. Re-check after cropping: the person looks bigger in the narrow frame, so headroom shrinks. [sourced for upper-third and the recheck; the band is derived]
3. **Top of the head at least 250 px down** (the platform's name and music label cover roughly the top 108 to 270 px, see section 6). Head-and-shoulders: the face about 22 to 28% of the frame height. [derived from the safe zones; face size is mine]
4. **Mouth and chin stay above where the captions start** (about y 1100). With eyes at 600 px and a 480 px face, the chin lands near y 880. [derived]
5. **Move the window up and down as well as sideways** to hit rule 2. Today only sideways moves exist (see section 8). [Ryan wants the head kept: `punch_in.py` keeps the top of the picture so a closer shot never cuts a head off]

## 3. Speaker changes and camera motion

1. **A frame is still inside a shot and changes only at a cut.** A crop that glides to the next speaker reads as a pan nobody made. Good tools hold still inside a shot and snap at the cut. [sourced: Opus, Choppity, Google AutoFlip's per-scene static mode]
2. **A change of speaker under 1.0 s does not move the picture** ("mm-hm"). It would read as the frame twitching. [Ryan/app: `FRAME_MIN_SEC = 1.0`]
3. **If a subject really moves inside one shot, follow with a smoothed path, never the raw face box.** Raw boxes jitter; the sources fit a smooth path (AutoFlip fits a low-degree polynomial) or ease in and out. No frame-count figure was found in any source. [sourced for smoothing; no numbers, so none are claimed]
4. **Frame on a face only when the evidence is good**; weak evidence is reported and nothing is framed on a guess. [Ryan/app: `framing.py`]

## 4. Punch-ins to hide jump cuts (same person, same camera, cut between)

1. **Alternate the clips: every other clip is closer, the rest stay as shot.** [Ryan, 2026-10-08, `punch_in.py`]
2. **Closer by 15 to 20%** is the common figure; sources range from 10 to 30%. The app uses **15%**, the low end, which the sources say may be too subtle to hide a jump. Try 20% on the next cut and compare. [sourced range; which value is right is unproven]
3. **The same person stays in mid-frame and the top of the picture stays on the same source row**, so the closer shot grows from the top down and never cuts off a head. [Ryan/app]
4. **A real second angle or a cutaway hides a jump better than a punch-in.** Punch-ins fake it and cannot hide a cut that changes the story. [sourced]
5. **The picture must stay sharp** (section 5). Scale changes only work if the image does not go soft. [sourced]

## 5. Resolution limit (how far the crop can go before it softens)

Premiere's Scale is 100% at one source pixel per output pixel. Above 100% the picture is upsampled and softens.

| Source | Output | Scale that fills the frame | Free punch-in before 100% |
| --- | --- | --- | --- |
| Mitch RED 7680x4320 | 1080x1920 | 44% | 2.25x |
| Bob RED 5760x3240 | 1080x1920 | 59% | 1.69x |
| 4K 3840x2160 | 1080x1920 | 89% | 1.12x |
| 4K 3840x2160 | 1920x1080 | 50% | 2.0x |
| Proxy 1920x1080 | 1080x1920 | 178% | none (already soft) |

1. **Warn when a clip's Scale passes 100%, refuse above 120%.** A clip whose fill scale is already above 100% (a proxy) is flagged on its own: the source is too small for the output. [100% is derived; the 120% refusal is mine]
2. **A 4K clip in a portrait reel has only about 12% of free punch-in**, so a 15% punch-in starts to soften it; 20% would. Use the RED originals, never proxies, for any reel with punch-ins. [derived]

## 6. On-screen layers: safe zones (portrait 1080x1920)

Sources disagree on platform pixels and none is an official platform page:

| Edge | Range in sources | Rule here |
| --- | --- | --- |
| Top | 108 to 270 px | **keep text and faces below 250 px; never below 150** |
| Bottom | 250 to 670 px | **keep text above 420 px from the bottom; never closer than 350** |
| Right | 100 to 120 px (the button rail) | **keep 120 px clear** |
| Left | 60 to 90 px | **keep 90 px clear** |

[sourced ranges; the chosen values are the conservative end of each. Preview on a real phone before trusting any of them: the apps change their overlays several times a year.]

1. **Captions** sit lower-centre, above the bottom no-go zone, and never over the mouth. Test the longest two-line caption. [sourced]
2. **A lower third and captions never fight for one band.** The app's lower thirds start 55% down (`TAG_Y`), above the captions, so captions do not jump to the top across the face. [Ryan/app]
3. **Overlay graphics keep out of the speaker's face box.** [sourced: "neither element covers the speaker's mouth"]

## 7. B-roll

Little in the research is specific to B-roll, so these are mostly mine and need his eye.

1. **Landscape output: keep the clip as shot.** Scale only to fill the frame or to remove something. Horizon on the top or bottom third, not the middle. [mine]
2. **Portrait output from 16:9 B-roll: find the subject first.** If it fits inside the 31.6% window, centre the window on it and keep the horizon on a third. Detect it the way AutoFlip does (faces, then objects, then motion), per scene. [sourced method; horizon rule mine]
3. **If the subject is wider than the window (an establishing shot, a whole room):** choose, per clip, between a fit-width picture with a blurred or brand-colour fill, or two held positions across a cut. **Never stretch.** [sourced options; the choice is Ryan's, see below]
4. **Keep each B-roll scene static within itself** unless the camera moved. [sourced: AutoFlip static mode]
5. **No scale above the section 5 limits**; B-roll from a phone or a proxy has less headroom than the RED originals. [derived]

## 8. What the app does today against these rules

| Rule | State |
| --- | --- |
| 2.1, 3.2, 3.4 centre on the speaker, hold short turns, no guesses | done (`framing.py`, `follow_speaker.py`), horizontal only |
| 2.2, 2.3, 2.5 eyes and headroom bands, vertical placement | **not done**: the window never moves vertically except inside punch-ins, and the vertical unit is not confirmed by a Premiere export |
| 4.1 to 4.3 alternate punch-ins, top kept | done (`punch_in.py`, 115%) |
| 4.2 try 120% | not tried |
| 5 resolution limits | **not done**: nothing refuses a scale that upsamples a 4K clip |
| 6 safe zones, caption vs face | **not checked** (captions and lower thirds exist; nothing measures them against the zones or the face box) |
| 7 B-roll portrait reframe | **not inspected for this write-up**; unknown whether B-roll is centred, tracked or letterboxed |

## 9. Checks the app could run mechanically

Measured on the finished XML against `find_faces.swift` face boxes (a face box gives top, bottom and an eye line at about 40% down from its top):

1. eye line inside the band for the output shape; 2. head top below the safe line; 3. no caption or graphic box overlapping the face box or sitting inside a no-go zone; 4. Scale at or below the limit for each clip; 5. window inside the source (exists today in `reframe_xml.py`); 6. no frame movement inside a shot except at a cut.

## 10. Questions for Ryan

1. **Wide B-roll in portrait**: blurred fill, two held positions, or "always crop to the subject and accept the loss"?
2. **Punch-in size**: try 20% on one cut and compare with 15%?
3. **One portrait crop for TikTok, Reels and Shorts**, or does one platform get its own?
4. **Eye line**: do you want the upper third, or lower for your own style? These bands are the research default, not yours.

## Sources

- Talking-head and interview framing: [SSW subject framing](https://www.ssw.com.au/rules/subject-framing), [SCVO framing an interview shot](https://scvo.scot/about/work/campaigns/essentialsector/mobile-video-training/support/framing-an-interview-shot), [Talking-Head Framing Tips](https://gotranscript.com/public/talking-head-framing-tips-headroom-and-eye-level), [Videomaker composition](https://www.videomaker.com/article/7596-getting-started-video-composition)
- Vertical and safe zones (vendor blogs, figures disagree): [Flixier](https://flixier.com/help/safe-zones-for-social-media), [Tella](https://www.tella.com/help/editing/social-safe-zones), [reap.video](https://reap.video/blog/short-form-video-safe-zones), [OpenClip caption placement](https://openclip.app/guides/caption-placement-guide), [Hopper HQ Reels](https://www.hopperhq.com/blog/instagram-reel-size/), [Outfy](https://www.outfy.com/blog/instagram-safe-zone/)
- Reframing method: [Google AutoFlip](https://research.google/blog/autoflip-an-open-source-framework-for-intelligent-video-reframing/), [Opus subject tracking](https://help.opus.pro/docs/article/subject-tracking), [Choppity](https://choppity.com/features/automated-tracking), [Too Much Film School on aspect ratios](https://toomuchfilmschool.substack.com/p/reframing-aspect-ratios-396)
- Punch-ins and jump cuts: [Larry Jordan one-camera editing](https://larryjordan.com/articles/better-one-camera-editing-techniques/), [Vyra talking head](https://www.usevyra.com/formats/talking-head), [DIY Video Editor](https://diyvideoeditor.com/how-to-hide-jump-cuts-in-your-videos/)

Not found: an official TikTok, Meta or YouTube safe-zone page, any frame count for easing a crop, and anything specific to B-roll horizon placement in vertical. Those rules are marked accordingly.
