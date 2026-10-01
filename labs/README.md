# labs: the creator-workflow tools, standalone

Everything here is standalone (file in, file out) and **not in `app/`**: integrating it is its own step, after Ryan has
tested it (ROADMAP Decision Log, 2026-09-29). `TESTING.md` is the guide for trying it; `skill/post-house-review-loop/SKILL.md` is
the workflow as a reusable skill (install: `cp -R labs/skill/post-house-review-loop ~/.claude/skills/`).

| What the creator's workflow does | Tool | README |
| --- | --- | --- |
| A review page with timecoded notes and drawing on the frame | `review_loop/build_review.py` | `review_loop/README.md` |
| See every edit decision as the video plays (beatmap) | the page's lanes | `review_loop/README.md` |
| Apply notes as a revised cut | `review_loop/revise.py` | `review_loop/README.md` |
| A callout (box, arrow, text) from a note's drawing | `overlay/make_overlay.py`, `place_overlay.py` | `overlay/README.md` |
| Keep a graphic longer / change its words | `overlay/change_callout.py` | `overlay/README.md` |
| Border and highlight a screenshot | `overlay/make_image_card.py` | `overlay/README.md` |
| Captions that move clear of other graphics | `captions/make_captions.py` | `captions/README.md` |
| Generated music and sound effects under speech | `audio/make_audio.py`, `place_audio.py` | `audio/README.md` |
| Replace a sound effect from a note | `audio/replace_sfx.py` | `audio/README.md` |
| Music like a reference track (or rank your library) | `audio/reference_music.py` | `audio/README.md` |
| Bleep curse words (automatic, every cut) | `bleep/bleep.py` | `bleep/README.md` |
| Change a caption's words / take a graphic out / fade a callout out | `captions/fix_caption.py`, `overlay/remove_graphic.py`, `overlay/change_callout.py` | `captions/README.md`, `overlay/README.md` |
| Put layers back after a revision | `reconform/reconform.py` | `reconform/README.md` |
| QA every note against the new version | `qa/qa_pass.py` | `qa/README.md` |
| Style from a reference video (measure and suggest) | `style/style_profile.py` | `style/README.md` |
| Emulate a reference video (pick aspects, default all) | `style/emulate.py`, `style/style_brief.html` | `style/README.md` |
| B-roll from the website, SoldFast exports and raw footage (suggestions only, nothing placed; optional vision check) | `broll/capture_site.py`, `pool.py`, `lines.py`, `suggest.py`, `judge.py` | `broll/README.md` |
| One place for every version and asset | `project/project.py` | `project/README.md` |

Run all tests: `PRECUT_ROOT=~/precut-checkout python3 -m pytest labs -q`.

Each README lists what its tool verifies and its real limits. Nothing here counts as done until Ryan has judged it (see
`docs/STATUS.md` § In progress for what has and has not been).
