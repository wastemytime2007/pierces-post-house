# labs/broll: B-roll from the website and from past SoldFast videos (standalone, not in app/)

Ryan (2026-09-30): "it could be valuable for the app to pull content from the website or any of our previously built assets as b-roll when appropriate." Website: soldfast.com. Pools, after he corrected the first choice
("the portfolio videos are unrelated"): the finished exports in `~/Desktop/SoldFast Exports` plus the main SoldFast folder, and the raw project footage (`/Users/ryandossey/Documents/ACTIVE PROJECTS/SOLDFAST`, read only).
**Nothing is placed in any XML**: the thing Ryan can judge is a contact sheet of suggestions (rule 7: one unit, his review, then broaden).

| Tool | What it does |
| --- | --- |
| `capture_site.py --url https://soldfast.com --out <folder>` | Screenshots the home page and every menu page with headless Chrome. `captures.json` records each address, status and time. |
| `pool.py --tree <folder> [--tree ...] [--skip-folder NAME] [--src <captures>] --out <pool>` | One frame every 3 s of each video, embedded with PreCut's cached CLIP model (offline). `--tree` walks a folder all the way down, videos only; `--src` reads one level (videos and images). Leaves out movies and film-release names, same-named copies, project/psd files, editor cache folders, and every `--skip-folder`; all reported in `pool.json`. |
| `lines.py --video <finished video> --out lines.json` | Speech lines from a video's own audio (Whisper small), for a cut that has no `captions.json`. |
| `suggest.py --pool <pool> --captions <lines.json or captions.json> --out <folder> [--exclude NAME]` | Per speech line: frames that look like the words, plus a website page the line names. `--exclude` keeps a cut's own finished export out of its candidates. Writes `suggestions.html` (frames, scores, rejected ones dimmed) and `suggestions.json`. |

**Two kinds of match, labelled.** *Named page*: the speaker says a page's word (reviews, compare, how it works, FAQ, cash offer, about us, contact us, press, blog, or "website"). *Looks like the words*: CLIP score at least 0.30 and 0.06 over the pool's median for that line. Everything else is `none`.

**The pool that was built:** 428 frames, 30 files: 20 videos (the 9 in SoldFast Exports, REELS, House Reel, Renovation 4_11_25, opener-tile) and the 10 site pages. **Left out on purpose** (his call to change): `SOLDFAST TRAININGS 6:25` (41 internal videos, 102 GB), `SoldFast Enhancements`, `Testimony Videos` and the other testimony folder (customers on camera, releases unknown), the Agent Trainings project folders.

**Raw clips and a vision check, both added (Ryan, 2026-09-30: "do both").**
- `pool.py --tree <drive> --proxies --any-length --every 6` indexes the small proxy copies of raw footage (same file names as the raw clips): 8,005 frames from 87 clips on `RDOSS_2025/SoldFast 2026`, 305 s. `--any-length` because raw camera clips run 20-33 minutes (the first try dropped 13 of them as "movies"). Raw frames have no burned-in captions.
- `judge.py` / `suggest.py --vision`: the best few CLIP candidates per line are shown to the local `claude` CLI (free, no API key), which says per frame whether it clearly shows what the line is about (`fits`) and whether text is burned in (`text_overlay`); a frame is kept only if it fits and has no text. About 8 s per line. A reply that is missing or garbled counts as "no". Cached by line and frames.

**What was measured (frames opened and looked at unless noted).**
| Run | Faucet video (23 lines) | Tiling window (16 lines) |
| --- | --- | --- |
| CLIP score only, finished exports | 3 suggestions, about 2 usable | 2, both wrong |
| Vision, finished exports | 6 lines; 4 usable (soap dispenser, tub faucet, a man handling a cabinet; a vanity with two faucets, but the frame is a 3-panel collage from a reel; a motion-blurred worker is weak) | not run |
| Vision, exports + raw, 5 candidates | 6 lines: black pull-down faucet and soap dispenser (both clean and exactly right), a removed piece in a bathroom (plausible), "no blood in my hand" -> a hand on a wall (loose); "kitchen with a black faucet" and "it's all flimsy" -> peeling wallpaper (frames not opened) | 1 line: "so I tore this piece off" -> a torn drywall patch (plausible); 15 none |
| Vision, exports + raw, 8 candidates | 6 lines, but only 2 are the same lines as the 5-candidate run (the chef faucet and soap dispenser dropped out; cabinet, under-sink and shut-off-valve lines came in); frames not opened | not run |

**Findings.** The vision check removes the failures seen with scores alone: no match from a shared word in a caption, "none" for conversation, no frame with burned-in text. Raw frames are clean. But **the model's yes/no is not repeatable**: run to run, and with 5 versus 8 images in one call, it confirms different lines. Union of what it confirmed on the faucet video across runs is about 10 of 23 lines; any one run finds about 6. It is a proposal list for a person to pick from, not something to place automatically. A larger pool also pushes good frames out of the top 5 before the model sees them. Possible next steps (not built): judge each frame on its own and keep one only when two asks agree; a CLIP shortlist of 10-15.
Not checked: whether a raw clip returned for a line is footage the cut already uses as its A-roll (the faucet export's own shoot is probably in the raw pool); the tool cannot tell which clips an export used.

**Limits.** Stills only (no scrolling recordings of the site). A site capture shows today's page, including a chat bubble and a customer's name and quote. A still every 3 s stands in for a clip. No placement, timing or in/out points.

Tests: `PRECUT_ROOT=~/precut-checkout python3 -m pytest labs/broll -q` (14).
