# labs/broll: B-roll from the website and from past work (standalone, not in app/)

Ryan (2026-09-30): "it could be valuable for the app to pull content from the website or any of our previously built assets as b-roll when appropriate." Website: soldfast.com. Pool: his `Portfolio Videos` folder.
Three small tools, file in / file out. **Nothing is placed in any XML yet**: the first thing Ryan can judge is a contact sheet of suggestions (rule 7: one unit, his review, then broaden).

| Tool | What it does |
| --- | --- |
| `capture_site.py --url https://soldfast.com --out <folder>` | Screenshots the home page and every menu page with headless Chrome (nothing installed). `captures.json` records each address, status and time. |
| `pool.py --src <folder> [--src <captures>] --out <pool>` | One frame every 3 s of each video (an image is one frame), embedded with the CLIP model PreCut already caches (offline). Leaves out full-length movies and film-release names, project/psd files and same-named copies, and says so in `pool.json`. |
| `suggest.py --pool <pool> --captions <captions.json> --out <folder> [--check "words=expected file"]` | For each speech line: the frames that look most like the words, plus any website page the line NAMES ("our reviews" gets the reviews page). Writes `suggestions.html` (frames, scores, what was rejected, dimmed) and `suggestions.json`. |

**Two kinds of match, labelled on the sheet.** *Named page*: the speaker says a page's word (reviews, compare, how it works, FAQ, cash offer, about us, contact us, press, blog, or just "website"), so that capture is offered, most specific page first. *Looks like the words*: CLIP score
at least 0.30 and at least 0.06 above the pool's median for that line. Everything else is `none`, shown dimmed. CLIP matches how a frame looks, not what is printed on it, which is why a web page needs the by-name rule: "Compare all three options" scored 0.27 against the right page and would have been rejected on looks alone.

**What was measured, and what was not.**
- Known-answer checks (the frames were looked at, not trusted by name): "aerial drone footage" -> a real aerial of Rio Cibolo Ranch (0.331); "a website home page" -> home.png (0.346); "a person eating at a restaurant" -> a person at a food table (0.329); "a classic car show" -> the Pismo Beach car show (0.336). All four pass.
- The real Tiling window (16 lines of DIY narration): 0 of 16 get a suggestion. The best scores were 0.22-0.27 and the ones that were looked at were not real matches (before the 0.30 bar, 7 of 16 "matched", all nonsense, e.g. "Alright, so it was what I" -> a kabob restaurant ad). Conversational narration rarely names a picture, so none is the normal answer, and **this pool has nothing for a tiling tutorial**.
- A STAND-IN script (written by Claude, not from any cut of Ryan's): 5 of 7 lines get a suggestion (3 named pages, 2 look-alikes: an aerial for "aerial shot ... drone", the car show for "people at a car show"), "We walk you through the whole process" and the conversational line get none.
- The thresholds (0.30, 0.06) come from this one pool and these few queries. They are a starting point, not a calibration.

**Limits.** Stills only (no scrolling recordings of the site). A site capture shows what the page shows today, including a chat bubble and a customer's name and quote: look before it goes in a video. The pool is Ryan's reel and client work (Deloitte, Disney, Drone Pros and others): whether any of it may be shown in a SoldFast video is his call, not the tool's.
A still every 3 s stands in for a clip. No placement, no timing, no in/out points yet.

Run the tests: `PRECUT_ROOT=~/precut-checkout python3 -m pytest labs/broll -q` (7 tests).
