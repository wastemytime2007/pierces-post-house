# Runnells content inventory — what's actually in 53 hours of footage

Owner: Lead. Written 2026-09-13 from Ryan's request: *"Can you go through
all of the runnells footage and find anything that would make a good
How-To Video like the one i did of bob with the wallpaper… And then
another list of any of the longer form How-To Content like the one of
mitch taking over the evicted property."*

**Read this before scanning the Runnells corpus again.** Every number
here was counted from the transcripts, not estimated, and two of the
findings will cost a future agent real time if rediscovered from scratch.

Finished deliverable (the grouped topic lists, with beats and runtimes):
https://claude.ai/code/artifact/0db631eb-6b48-4f3e-a580-3112f09c343d

---

## 1. The transcripts are partly corrupted — filter before you read

Source: `~/Documents/Claude/Agent Studio/projects/runnells-hgtv-story/transcripts/`
(270 clips, WhisperX, pre-existing — **not** produced by this project).

| Measure | Value |
| --- | --- |
| Clips with speech | 270 |
| Total speech | 53.5 h |
| Non-hallucinated | **41.1 h (77%)** |
| Clips >30% loop artifact | **51** |

Whisper's repetition failure on near-silent drone audio produced long
runs of one repeated segment. Real examples: `"We have to go downstairs"`
hundreds of times, `"I said hello"` ×626, `"東京都交通局"` ×543 (Tokyo
Metropolitan Bureau of Transportation — a known Whisper artifact on
silence), `"Four. Four. Four."` ×38.

**This defeats naive keyword and density scanning.** A window of 500
identical sentences scores as the densest "teaching" passage in the
corpus. The first scan run against this data returned 13 of its top 19
hits from a single hallucinated clip.

Working filter, used for every number in this document:

```python
from collections import Counter
c = Counter(s['text'].strip() for s in segs)
loop = {t for t, n in c.items() if n >= 5}     # exact text repeated 5x+ in one clip
segs = [s for s in segs if s['text'].strip() not in loop]
# then drop consecutive duplicates
```

## 2. This footage is observational, not instructional

Scanning 642 sliding windows for explicit tutorial language (`"what you
want to do"`, `"make sure you"`, `"the trick is"`, …) topped out at a
score of **8**, with a median of **1**. There is no dense narration layer
to find with regex — the teachable moments are embedded in working
dialogue and have to be read.

**Consequence for the app:** a fragment-extraction pass tuned on
interview footage will under-select here. What carries these pieces is
Bob narrating while his hands work, which reads as ordinary conversation
in a transcript.

## 3. Speech per shoot day — the pool any piece can draw on

Clean (filtered) speech, per shoot folder. Use this to judge whether a
topic can be extended beyond its core beats.

| Shoot day | Clips | Clean speech |
| --- | --- | --- |
| weekend-interview-bob-mitch | 23 | 94:25 |
| clean-out-carpets-grass-wallpaper | 21 | 80:41 |
| updated-plans-adding-door-to-back-wall | 13 | 72:08 |
| sheeting-walls-removing-windows | 16 | 52:19 |
| termite-damage-window-planning | 9 | 43:00 |
| menards-trip-finishing-up | 32 | 42:44 |
| putting-in-cabinets-painting-windows | 15 | 40:28 |
| cleaning-up | 23 | 30:45 |
| celeste-staging-walkthrough | 6 | 28:15 |
| walkthrough-final-needs-drive-convo | 12 | 38:47 |
| first-walkthrough-after-taking-over | 6 | 19:05 |
| tiling-the-kitchen-bathroom | 7 | 17:04 |
| finishing-deck-flooring-walkthrough | 7 | 15:47 |
| hanging-mirror-installing-shower-lights | 8 | 15:27 |
| angry-bob-last-day | 5 | 14:13 |
| garbage-disposal-stairs-doorknobs (both) | 13 | 26:38 |
| staging | 17 | 13:25 |
| initial-deal-call | 1 | 12:38 |
| ac | 5 | 11:33 |
| cabinets-cont-siding-install | 5 | 10:27 |
| cleaning-up-painting-trim-outside | 4 | 6:49 |
| quick-walkthrough-616 | 2 | 0:00 |

**Richest technique day by far: `tiling-the-kitchen-bathroom`** — only
17 minutes, but it is one continuous Bob demonstration and yields two
complete Reels. Density beats volume.

## 4. Coverage gap

`Bob intv_Recruitment` (20 GB) has **no transcripts** and is outside
every number above. It contains `Bob Recruitment_Final.mp4` (an already
finished cut) plus raw Osmo/DJI audio. It is a sit-down interview on
contractor recruiting — the one format the job-site footage can't
provide, and the missing input for most of the operator-content topics.
Transcribing it is the highest-value next scan.

## 5. Runtime model, calibrated on Ryan's own cut

Measured, not assumed:

| Reference | Source speech | Finished |
| --- | --- | --- |
| Removing Wallpaper Tutorial | 2:46 on topic | **1:06** |
| How To Take Over A Property (eviction) | not measured | **4:26** |

The wallpaper piece trims **2.5:1**. Applying that to measured on-topic
speech is what turned a list of good moments into a list of videos, and
it reclassified most of them:

- **2:30+ of on-topic speech** → a full 60-75s Reel (3 topics qualified)
- **0:45 – 1:35** → a real 25-45s short (5 topics)
- **under 0:45** → a 10-20s insert, not a video (5 topics)
- **3-5 min long-form** needs either ~7-12 min of on-topic speech, or a
  short spine plus a deep day pool to fill with work footage. **Exactly
  one Runnells topic qualified** (termite/windows: 2:48 spine against a
  43:00 day).

Caveat recorded honestly: the ratio comes from one finished piece, and
dense speech trims less than rambling speech. Treat runtimes as a band.

## 6. Method note for the next scan

What worked, in order:

1. Filter loop artifacts (§1) — **before** anything else.
2. Write cleaned per-clip text with timecodes to a scratch dir, ranked by
   clean word count. 71 of 270 clips carry ≥800 clean words; those are
   the meat.
3. Read the hands-on days directly. Regex scoring found the *days* but
   never the *moments* (§2).
4. Group by finished deliverable before reporting. The first pass
   reported nine separate tiling entries; they are two videos. Ryan:
   *"a lot of them are the same how-to topic just a different moment."*
5. Measure speech per beat-window and per day before claiming anything is
   ready. "Enough beats" and "enough footage" are different questions,
   and the second one is the one that changes the plan.
