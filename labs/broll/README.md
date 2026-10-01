# labs/broll: B-roll from the website and from past SoldFast videos (standalone, not in app/)

Ryan (2026-09-30): "it could be valuable for the app to pull content from the website or any of our previously built assets as b-roll when appropriate." Website: soldfast.com. Pool, after he corrected the first choice
("the portfolio videos are unrelated"): `~/Desktop/SoldFast Exports` and the main SoldFast folder (`/Users/ryandossey/Documents/ACTIVE PROJECTS/SOLDFAST`, read only).
**Nothing is placed in any XML**: the thing Ryan can judge is a contact sheet of suggestions (rule 7: one unit, his review, then broaden).

| Tool | What it does |
| --- | --- |
| `capture_site.py --url https://soldfast.com --out <folder>` | Screenshots the home page and every menu page with headless Chrome. `captures.json` records each address, status and time. |
| `pool.py --tree <folder> [--tree ...] [--skip-folder NAME] [--src <captures>] --out <pool>` | One frame every 3 s of each video, embedded with PreCut's cached CLIP model (offline). `--tree` walks a folder all the way down, videos only; `--src` reads one level (videos and images). Leaves out movies and film-release names, same-named copies, project/psd files, editor cache folders, and every `--skip-folder`; all reported in `pool.json`. |
| `lines.py --video <finished video> --out lines.json` | Speech lines from a video's own audio (Whisper small), for a cut that has no `captions.json`. |
| `suggest.py --pool <pool> --captions <lines.json or captions.json> --out <folder> [--exclude NAME]` | Per speech line: frames that look like the words, plus a website page the line names. `--exclude` keeps a cut's own finished export out of its candidates. Writes `suggestions.html` (frames, scores, rejected ones dimmed) and `suggestions.json`. |

**Two kinds of match, labelled.** *Named page*: the speaker says a page's word (reviews, compare, how it works, FAQ, cash offer, about us, contact us, press, blog, or "website"). *Looks like the words*: CLIP score at least 0.30 and 0.06 over the pool's median for that line. Everything else is `none`.

**The pool that was built:** 428 frames, 30 files: 20 videos (the 9 in SoldFast Exports, REELS, House Reel, Renovation 4_11_25, opener-tile) and the 10 site pages. **Left out on purpose** (his call to change): `SOLDFAST TRAININGS 6:25` (41 internal videos, 102 GB), `SoldFast Enhancements`, `Testimony Videos` and the other testimony folder (customers on camera, releases unknown), the Agent Trainings project folders.

**What was measured (frames were looked at, not trusted by score).**
- The first pool (the Portfolio Videos folder) was wrong content and has been removed; its checks ("aerial drone", "car show") do not apply to this pool.
- Real narration, the faucet export (`How to install a faucet.mp4`, 23 lines, its own file excluded): 3 lines get a suggestion. Of those, "two rubber tubes goes on it" (a disposal part in a hand) and "shut-off valves" (disposal plumbing) are plausible; "So the water lines hot" got a tile frame because the burned-in caption "MAKE SURE LINES ARE STRAIGHT" shares the word "lines". Tiling window (16 lines, its own exports excluded): 2 suggestions, both wrong (a wallpaper steamer for "stepping the tile up", a door latch for "with the spacer").
- So, on real narration, **about 2 of 5 suggestions are usable.** Not good enough to place automatically.

**Why, and what would fix it.**
1. CLIP matches how a frame looks and also reads text printed in it. Finished exports carry burned-in captions and text overlays, so a frame "matches" a line that shares a word with its caption, and a clip taken from it would carry that caption into the new video. A pool built from the raw clips (not the finished exports) would not have this; the finished exports are also vertical 1080x1920.
2. Conversational DIY narration ("it's always on the left") rarely names anything visible; a score cannot tell "nothing fits" from "a weak fit". A second look by a vision model asking "does this frame illustrate this line, yes or no" would; it costs a model call per candidate.
Neither is built.

**Limits.** Stills only (no scrolling recordings of the site). A site capture shows today's page, including a chat bubble and a customer's name and quote. A still every 3 s stands in for a clip. No placement, timing or in/out points.

Tests: `PRECUT_ROOT=~/precut-checkout python3 -m pytest labs/broll -q` (9).
