# STATUS.md — where we are

Owner: Lead. Updated at the end of every working session. § Done records
only completed, verified work with its evidence; § In progress is for
everything in flight.

## Current stage — see § Done for the latest slice

**READ `docs/REQUIREMENTS.md` FIRST.** It carries Ryan's founding words —
why this project exists, the five roles in his own description, the four
PreCut shortcomings he named directly, and the process failure that led to
this re-scope. That file, not this section, is the source of truth for intent.

**This is one application, forked from PreCut — not a separate app, and not
a PreCut rewrite.** PreCut's own repo (`~/precut-checkout`) is the protected
donor: never modified, never committed to. Its ingest, transcription,
tagging, audio sync, and Premiere export are proven code, copied into `app/`
and extended in place, never reimplemented from scratch. This app grows real
code only where PreCut falls short — there is only ever one app to open, per
Ryan's explicit correction (`ROADMAP.md` Decision Log, 2026-09-03). See
`precut-capabilities` skill for exactly what PreCut does and doesn't do,
verified from its source on 2026-09-03 after this project spent three days
operating on a wrong model of it.

**The real, confirmed gap** (Ryan's own words, 2026-09-02): PreCut's story
planner "tends to skim through transcripts and that leaves a lot on the
table." Measured: one Claude call, ~9 ranges / ~13 minutes of material per
run, reading a whole project's transcripts concatenated into one prompt.
Ryan's own hand-pass over comparable material produced 250 selects. That gap
— exhaustive, verified reading vs. one skimmed pass — is the thing worth
building. Nothing else about PreCut is being replaced.

**PHASES 0-3 (safety net, harvest layer, Project Manager code, benchmark
harness) are built; Phase 0/1/3 output was never shown to Ryan directly,
but Phase 2 (Project Manager) has now been verified by him repeatedly on
real footage** — see § Done, 2026-09-03, "That worked perfectly."

**Phase 4's motion cull is PARKED**, not shipped, not being worked on. Five
single-signal detectors were tried; none beat "keep everything" on real
drone footage; grid-fitting was ruled out as the cause. Full history in the
Decision Log. Revisit only with a genuinely new idea, per Ryan's own call.

**`posthouse/moments.py` was built, verified once by Ryan on a real query
("They are about the cabinets" — the first thing in this project confirmed
working by the person who has to trust it), then deleted 2026-09-03** once a
full read of PreCut showed it duplicated the existing story planner with
weaker selection and no audio sync. The verification method it proved
(machine-check every quote before showing it) survives in the
`verified-quotes` skill; the module itself does not.

**Agent Studio and the Content-Engine Obsidian vault were surveyed and
retired as separate systems.** Both were almost entirely doctrine, never
run. Value ported to four global skills (`soldfast-content-funnels`,
`longform-story-craft`, `footage-assembly-method`, `hook-writing`) that the
Creative Editor role loads. `studio.py` and the vault are dead, left alone,
not revived.

**New non-negotiable rules, `CLAUDE.md` §7-8**: prove every new capability
on one clip/transcript/interview before scaling, and one role in flight at a
time with Ryan's tested sign-off as the only valid "done." Both exist
because this session violated them once each.

## In progress

- **2026-09-03 — CORRECTION: the "third falsification" below was wrong —
  interpretation actually worked, just with a visible lag.** Ryan first
  reported "it didn't interpret the footage... just the folder with the
  tagged clips at their original framerate," which led to an incorrect
  conclusion (struck through below, kept for the record of how the
  investigation went) that Premiere was silently collapsing the
  duplicate masterclips. The real, hard evidence that should have
  settled it immediately: `posthouse_interpreter.log` (the companion
  extension's own log) showed all 10 "[INTERPRET TO 29.970fps]" clips
  found and interpreted at 21:20:46, exactly matching the export/import
  timestamp — proving the 20 distinct masterclips genuinely existed as
  separate Project-panel items and the extension processed all of them
  correctly. Ryan then confirmed directly: "It did interpret it just
  had a bit of a lag." The mechanism works; the earlier conclusion was
  reached without checking the extension's own log first, which was a
  mistake — that log is exactly the kind of "cite or don't assert"
  evidence that should be checked before declaring a real Premiere
  behavior discovered. No code change needed; the "three failures" rule
  was invoked incorrectly here since there wasn't a real third failure.
  ~~Third real falsification of B-roll frame-rate interpretation,
  confirmed by Ryan in real Premiere; stopped per the "three failures"
  rule.~~ ~~Verified the actual exported file first, not assumed: it
  genuinely has 20 masterclip entries in the B-Roll bin (10 native + 10
  "[INTERPRET TO 29.970fps]" duplicates), each with a fully distinct
  masterclip id, file id, AND uuid — no XML-level collision anywhere.
  Ryan confirmed Premiere shows only 10 items. Premiere is silently
  collapsing the pair per file despite every XML identifier being
  unique.~~ (Both struck-through claims were wrong — see correction
  above.) Full ROADMAP.md Decision Log entry for this date needs the
  same correction noted against it.
- **First real Assistant Editor tasks: dual-use B-roll tagging confirmed
  on real footage; B-roll frame-rate interpretation built and verified,
  neither yet confirmed by Ryan in the app/Premiere itself.** Ryan chose
  these two specifically after Task 1.1's sign-off, over new AE ground:
  "Go ahead and work on the dual use b-roll tagging and the interpretation
  of the b-roll framerates."
  - **Dual-use B-roll tagging**: confirmed for real, not just the
    scripted check from Task 1.1. Flagged a real Runnells clip dual_use
    in a safe copy of the project, ran PreCut's own real tagging pipeline
    (same CLIP embedder/tagger it already ships, zero new tagging code)
    against it directly — genuine result: 60 frames tagged, `tag_status:
    done`, ~105s. Closes the one item Task 1.1 left unconfirmed.
  - **B-roll frame-rate interpretation**: three real approaches tried,
    each settled by evidence, not reasoning — full account in
    `posthouse/broll_interpret.py`'s module docstring. (1) Declaring a
    mismatched rate in FCP7 XML: tested directly in real Premiere,
    which re-probes the actual media and ignores it — falsified. (2)
    `ffmpeg -itsscale` real-file retiming: verified exact via raw
    frame-level PTS inspection (no re-encode, mathematically uniform
    spacing at the new rate) — worked, but Ryan caught the real cost
    before it shipped: a full-resolution permanent duplicate of every
    interpreted clip on already-tight footage storage ("does this mean
    we're going to be duplicating footage files on the drive"). Right
    concern — that code isn't in this repo (git history only). (3)
    What shipped: Ryan's own real Premiere workflow (duplicate the
    Project-panel item, Interpret Footage on one) doesn't duplicate
    media either — he sent a real FCP7 XML export of exactly that
    workflow. Reading it closely found the real mechanism: Interpret
    Footage isn't expressed on the bin-level clip/file block at all
    (both his duplicates declared identical native rate) — it only
    shows up in the frame math of a clipitem already placed on a
    sequence. `build_broll_reference_sequence()` reproduces that same
    math in a pre-placed "B-Roll (Interpreted)" reference sequence
    (same pattern as "All Synced A-Roll") — zero extra disk, same
    original media referenced directly. Real tradeoff, stated plainly:
    footage has to be pulled from that reference sequence, not the raw
    B-Roll Library bin, to get the corrected speed.
  - **Real bug found and fixed along the way, independent of
    interpretation itself**: `multi_exporter.py`'s master-clip dedup
    keyed by path only, with B-roll registering first — so a dual_use
    source got exactly one Project-panel item (B-Roll only); its A-roll
    usage silently shared it instead of getting its own. Fixed by
    minting a dedicated id for A-roll on collision, without touching
    the shared map other lookups still rely on.
  - Verified end to end against real files (real 59.94fps and 29.97fps
    clips, real `BrollLibraryEntry` objects, real `export_multi_timeline`
    calls) — the 60fps-family clip placed at its exact real frame count
    (genuine 2x real-time slowdown at the 29.97fps target); the
    already-native clip placed at ordinary ~real-time-preserving
    duration. Confirmed zero new media created; the 16.4GB test artifact
    from the abandoned itsscale approach was deleted from the real
    drive. *(a733930.)*
  **Second real failure, tested by Ryan in real Premiere: "That didn't
  work."** "Interpretation doesnt happen at the sequence level it
  happens at the clip/footage level. the sequence being set at a
  different framerate doesnt make the clips on that sequence interpret
  to that framerate." The reference-sequence math (above) didn't survive
  real testing, same as the raw-XML-rate approach before it — two
  failures on this exact problem. Per the standing "three failures means
  the approach is wrong" rule, stopped there instead of guessing a third
  mechanism; asked Ryan directly. **His answer restarts this as two
  separately-provable steps**: "First get the xml to import all
  framerates above the [target] two times... Then we tackle the next
  step which is finding a way to select the secondarily imported footage
  clips and handle the modify-interpret footage function within
  premiere." Step one shipped: `multi_exporter.py`'s B-roll master-clip
  loop now builds a second, clearly-labeled Project-panel entry
  (`"... [INTERPRET TO 29.970fps]"`) for every clip above the target
  rate — same file, zero extra disk, reusing the existing builder
  function verbatim. Verified against a real 59.94fps clip: two distinct
  master-clip entries in the exported XML; a clip already at target rate
  correctly stays a single entry. Both prior (now-removed) approaches'
  code lives only in git history (`b613002`..`d4f5278`), not in this
  repo. *(d4f5278.)* **Step two (triggering Interpret Footage on the
  duplicate) not yet attempted.**
  Ryan: "Where am i setting the target framerate? It doesnt ask so i
  cant tell it." Real gap — target was computed silently with no way to
  see or override it. Added a "B-roll interpretation target" field to
  ExportModal (Auto / 23.976 / 24 / 29.97 / 30 — Ryan's own stated
  realistic set), threaded through as `ExportOptions.broll_target_fps`
  to `export_multi_timeline()`'s new parameter. The auto-compute call
  moved up into `exporter.py` so the resolved target is now ALWAYS
  logged in the export activity log, explicit or automatic. Verified an
  explicit 24.0fps override reaches the duplication logic correctly
  (produces `"[INTERPRET TO 24.000fps]"`, not the auto value). *(d1ca5e2.)*
  **Two real bugs found testing this, independent of the feature itself**:
  Ryan hit `export_multi_timeline() got an unexpected keyword argument
  'broll_target_fps'`. First found and fixed a STALE backend process:
  `pkill -f "target/debug/broll-buddy-app"` (used all session for
  restarts) only ever killed the Rust binary, never the Python
  `backend.py` child it spawns — three orphaned Python processes had
  accumulated (`ps aux` showed one from 9:41PM, one from 10:10PM, one
  from 10:47AM). Killed all three by PID (verified against the real
  PreCut.app's own backend PID first, to avoid touching it). Ryan then
  hit the *identical* error again after a full quit/reopen — the stale
  process was real but not the actual cause. Root cause: `posthouse
  /precut_bridge.py`'s `ensure_precut_on_path()` did
  `sys.path.insert(0, backend_str)` where `backend_str` is
  `<PRECUT_ROOT>/python_backend` (the **protected donor checkout**,
  `~/precut-checkout`). Inserting at position 0 put the donor path
  ahead of the app's own local fork directory, so every bare
  `import precut_pipeline...` in `exporter.py`/`backend.py`/
  `pipeline.py` — done as soon as anything imports `posthouse`, which
  happens at `backend.py`'s top level — silently resolved to the
  donor's unmodified `precut_pipeline`, not the fork's edited copy.
  Confirmed directly: reproducing backend.py's real import order showed
  `precut_pipeline` resolving to `~/precut-checkout/...`, and the
  donor's own `multi_exporter.py` genuinely lacks `broll_target_fps` (as
  it should, since the donor is never touched). Fixed by changing
  `insert(0, ...)` to `append(...)` — the local fork directory (already
  on `sys.path` from the running script's own location) now wins any
  naming collision; the donor path is only a fallback for names
  `import_precut()` needs that the local copy doesn't have. Re-verified
  with the same reproduction: `precut_pipeline` now resolves to the
  local fork copy and `broll_target_fps` is present. *(Fix not yet
  committed as of this writing — see next commit.)*
  **Scope of this bug**: any edit to `precut_pipeline/*.py` made during
  this session, in the live app, before this fix — including the
  dual-use `master_clip_map` collision fix — could have been silently
  running the donor's unmodified code instead. Re-verified against the
  live app after the fix (`405c91d`): resolves to the local fork copy.
  Ryan confirmed both the duplication step and the target-rate
  field/logging work correctly in real Premiere.

## Done

- 2026-09-03 — **Story architect: trend research made to actually watch
  real videos, and made visible in the app — two real gaps Ryan found in
  the entry below, both fixed for real.** He asked, correctly: "Where do
  I see any of that though?" (nothing surfaced the sourced research
  anywhere) and asked for it to actually use video vision to watch real
  reels rather than "just making all of this up" from text search alone.
  **On the second point**: `claude-video-vision`'s MCP tool only exists
  inside an interactive agent session, not a bare `anthropic` SDK call
  from this backend — so `research_trends()` now does the equivalent
  directly in Python: search for a real INDIVIDUAL video permalink (not
  a `/discover/`/category page), `yt-dlp` downloads the actual video,
  `ffmpeg` samples real frames, and those frames go to Claude as real
  image content — genuinely watched. **Proven live before writing final
  code** (per the standing "cite or don't assert" rule): downloaded a
  real, currently-trending `@rooshome` TikTok, extracted 4 frames, and
  got a correct description of its actual before/after text-overlay and
  lighting-driven pacing pattern from the real pixels. Confirmed it
  generalizes on 3 more real runs against different videos afterward (a
  real-estate creator reel, an Instagram farmhouse reveal), not a
  one-off. **Real limitation found and reported honestly, not smoothed
  over**: a dedicated search for "3-5 real individual video URLs"
  returned only ONE genuine permalink out of many category-page results
  — search engines mostly can't find individual TikTok/Instagram
  permalinks, a structural limitation of the platforms, not a bug here.
  Also caught a genuinely irrelevant result once (an unrelated Argentine
  product-shipping video matched the permalink pattern) — the watch step
  now returns a structured `relevant: true/false` verdict instead of
  burying that judgment in prose, so an off-niche match gets excluded
  from the story-sequencing reasoning while staying visible in the audit
  trail. Also fixed a real bug hit while re-testing: the text-search call
  ran out of its 1200-token budget writing a prose preamble before
  reaching the JSON block — bumped the budget and added a "no preamble"
  system prompt (the pattern `audience_relevance.py` already uses
  reliably).
  **On the first point (visibility)**: new `story_architect_generate`/
  `get_story_research` backend commands; a **"Generate from flagged
  fragments" button** in IdeasTab, placed alongside (not replacing)
  PreCut's own "Generate ideas" so the two stay directly comparable; and
  an expandable **"Show research"** section per story-angle card
  rendering actually-watched videos (with links), text-sourced findings
  (with links), what was checked and excluded as off-niche, and what
  real transcript material got left out of the arc and why. *(1be6f65.)*
  **Verified the app itself picks this up**: `tauri.conf.json` bundles
  `python_backend/**/*.py` as a resource copied into
  `src-tauri/target/debug/_up_` on build — confirmed the new files
  landed there identically after a full restart, and the HMR log showed
  every JSX/CSS edit compiling clean with no errors before that restart.
  **Not yet reviewed by Ryan on the real Runnells project** — this is
  the next real judgment call, not a passing test.
- 2026-09-03 — **Creative Editor: story + assembly — scoped and its
  selection/sequencing half built and proven real end-to-end on Runnells,
  the flagship gap this project exists to close.** PreCut's own
  `story_planner.generate_angles()` is one Claude call capped at ~3
  angles / ~9 ranges / ~13 minutes, re-skimming a whole project's
  transcripts every time. Confirmed by reading `story_assembler.py` and
  `exporter.py` directly that PreCut ALREADY has a complete, shipped,
  wired path from a `StoryAngle` to a real assembled `CutList` to real
  XML export (`assemble_cut_from_angle`, plus the existing Ideas UI
  `IdeasTab.jsx`/`ExportModal.jsx` and `project.plans_dir()` idea-JSON
  persistence) — so the only real gap is the SELECTION step, and only
  that was built. New `posthouse/story_architect.py`: given a project's
  already-exhaustive, audience-scored transcript-flagging fragments
  (reuses `transcript_coverage.py`/`audience_relevance.py` output —
  no re-skimming), one Claude call sequences the `strong` fragments
  (widening to `possible` if there aren't enough — never fabricates
  material) into a real hook/build/payoff arc, live web-search-informed
  per Ryan's explicit call ("Live, run it fresh each time"). Output is
  persisted in PreCut's own exact idea-JSON shape so it flows through
  the unmodified export path with zero new UI/export code — a genuine
  "build the missing piece, not the machine" case.
  **Real finding while proving the web_search mechanism**: the newer
  `web_search_20260318` tool variant routes through Claude's
  code-execution sandbox — a real live test burned ~137K input tokens
  across ~15 failed programmatic search attempts, hit a rate limit, and
  gave up with an honest disclosure instead of real results. The older
  `web_search_20250305` variant did a clean direct 2-query search for
  ~17K tokens and returned real sourced links. Used the one that
  actually worked, not the newer one, precisely because it was tested
  before committing to it.
  **Full real proof chain, every step actually run, not assumed**: (1)
  loaded the real "new" Runnells project's 84 real tagged fragments (5
  strong) and real `audience_goal`; (2) generated a real story angle —
  title "10,000 Windows — And Still Learning" / "Installing 10,000
  Windows Wrong" across two live runs, both genuinely coherent
  hook→build→payoff arcs citing real fragments by file/timestamp and
  real trend sources (autoreelapp.com, homeremodelerseo.com, and others,
  all checked as real URLs in the search results, not invented); (3)
  persisted it as a real `plans/*.json` idea; (4) loaded that idea back
  with PreCut's own unmodified `producer._angle_from_dict`; (5) ran it
  through PreCut's own unmodified `assemble_cut_from_angle` with the
  same offset-map helpers `exporter.py` itself uses — produced a real
  `CutList` with correct source files (resolved through proxy → original
  camera path), correct timeline placement, and correct native 4K/59.94
  sequence dims probed from the actual source file. Also fixed a real
  bug found during this test: `why_it_works` was silently truncating
  mid-sentence (and mid-citation) at a 1000-char cap — raised to 3000.
  Also added `save_story_research()` — the sourced trend findings and
  what real material got left out (and why) don't fit PreCut's own
  `CreativeBrief` schema, so they're persisted as a separate audit-trail
  file rather than silently dropped once the function returns.
  **Not yet wired into `backend.py`/the UI** — proven standalone via
  direct calls, same staged pattern as transcript flagging (prove each
  piece for real, wire in as its own explicit later step). A real,
  judgeable idea file now sits in the real "new" project's `plans/`
  directory — Ryan can open Post House's Ideas tab and react to it
  directly if the app's `list_ideas` picks it up without any code
  changes (it just globs the directory), even before wiring a
  dedicated "generate from flags" trigger.
- 2026-09-03 — **Marker colors: root cause found and fixed for real —
  Premiere's FCP7 XML import never honors marker `<color>` at all; the
  two rounds of RGB tuning before this were solving the wrong problem.**
  Ryan reported markers "all still the default green" after the bold
  red/green/gold RGB fix (`eaeb61d`) — a SECOND failure of the same
  mechanism (muted RGB, then bold RGB), which is the project's own
  "three failures means the approach is wrong" signal, so this time the
  investigation went to the spec instead of a third color variant.
  Confirmed via the actual FCP7/xmeml DTD (Apple's interchange-format
  reference) that the `<marker>` element's schema is only
  `name`/`in`/`out`/`comment` — there is no color field at all. Our
  exporter's `<color>` block was never read by Premiere's importer;
  every imported marker silently defaults to Premiere's own color index
  0 (green) regardless of what RGB we wrote. This also means the
  earlier claim (2026-09-03, in the transcript-flagging entries below)
  that `exporter.py`'s "existing marker-writing mechanism (arbitrary
  RGB)" was "already confirmed working" was never actually verified in
  Premiere's UI — only verified as correct XML, which is not the same
  claim. **Real fix**: color has to be set in-app via Premiere's own
  ExtendScript API, confirmed against Adobe's own `Marker` scripting
  docs and a working example in `Adobe-CEP/Samples`'
  `PProPanel/jsx/PPRO/Premiere.jsx` (`marker.setColorByIndex(index)`,
  palette confirmed against Ryan's own marker-color-picker screenshots:
  0=Green 1=Red 2=Purple 3=Orange 4=Yellow 5=White 6=Blue 7=Cyan).
  Extended the already-running, already-proven invisible CEP extension
  (`com.posthouse.interpret`, the same one that fixed the analogous
  frame-rate-interpretation gap) with `scanAndColorMarkers()`: walks
  every sequence and clip marker, parses the fit prefix
  (`strong:`/`possible:`/`off_topic:`) that
  `build_flag_markers_for_phrase` already writes into each marker's
  comment, maps to the real palette index (strong=4/yellow,
  possible=0/green, off_topic=1/red), idempotent (skips a marker
  already at its target index). Wired into `index.js`'s existing poll
  loop alongside `scanAndInterpret()`. *(2d7cbfb.)* **Confirmed by Ryan
  on real footage after restarting Premiere: "Ok that worked."** All
  four transcript-flagging pieces (exhaustive extraction, PM
  audience-goal intake, relevance tagging, XML marker writing) plus this
  marker-color fix are now signed off end-to-end on a real project — not
  just standalone/integration test scripts.
- 2026-09-03 — **Correction to the entry below, found while verifying
  the fix actually worked: the real, primary cause was a genuine
  pre-existing concurrency race, not (mainly) this session's kill -9
  restarts.** After deploying the atomic-write fix, Ryan ran the
  pipeline again and hit the same failures. Caught live, not
  theorized: `ps aux` showed 12 ffmpeg processes running as 6 duplicate
  pairs, each pair independently encoding the SAME 5GB source file at
  the same time. Root cause: `_collect_videos` deliberately puts a
  `dual_use` source in BOTH `aroll_videos` and `broll_videos` (its own
  docstring calls this "a safe, idempotent skip... not a conflicting
  second encode" — true only if the two lists are processed
  sequentially). `run_pipeline` actually processes them via two
  CONCURRENT threads, so both independently see "proxy doesn't exist
  yet" at the same instant and both launch ffmpeg on the same file in
  parallel — a race that would occur any time a dual-use source needs a
  fresh proxy, regardless of any restart. Fixed with a per-proxy-path
  `threading.Lock` shared between the aroll and broll `_run_video_
  pipeline` calls within one `run_pipeline` invocation — whichever
  thread reaches a path first encodes it, the other waits and then
  skips a real, valid result instead of racing it. *(1c3ea98.)*
  Confirmed the still-running old-code backend kept spawning new
  duplicate pairs for queued files even after killing the first 12 —
  had to stop the whole job (kill the backend process), not chase
  individual ffmpeg processes. The atomic-write fix below remains
  correct and worth keeping (it's what prevented this exact race from
  corrupting the final file this second time) — it just wasn't the
  whole story on its own.
- 2026-09-03 — **Real proxy corruption found and fixed, self-inflicted
  by this session's own restart habit.** Ryan: "It took forever and did
  this" — 6/10 B-roll tagging and 9/10 transcriptions failed with
  ffmpeg "moov atom not found" / "Invalid data found when processing
  input". Root-caused against his real project (`new`), not assumed:
  all 10 proxies in the affected folder were genuinely corrupt
  (confirmed via ffprobe), their 5GB original sources probed perfectly
  clean. Real mechanism: `kill -9` on the backend process (used
  repeatedly this session to restart the app for code changes) doesn't
  kill its ffmpeg children — they're orphaned and keep writing. A later
  restart's fresh proxy encode to the SAME output path then writes
  concurrently with the orphan, corrupting the file. The "skip if
  already exists" idempotency check only checked existence + nonzero
  size, so it silently trusted the corrupted result forever after.
  Fixed: `_encode_proxy` now writes to a private uuid-suffixed temp path
  and atomically renames on confirmed success only; the pipeline's skip
  check now calls a new `proxy_manager.proxy_is_valid()` (quick ffprobe)
  before trusting an existing file, so an already-corrupted proxy gets
  re-encoded instead of trusted. Verified for real: the validator
  correctly flags the known-corrupt file; a real re-encode with the fix
  produced a genuinely valid proxy. *(e294370.)* Checked other
  real projects touched this session for the same corruption — none
  found, this appears isolated to the one folder mid-encode during a
  restart. **Going forward**: restarting this app now needs to also
  check for and kill orphaned `ffmpeg` processes, not just the
  backend/Rust ones — noted here so a future session doesn't reintroduce
  this by restarting the old way.
- 2026-09-03 — **Real bug found and fixed testing transcript flagging on
  a real project: dual-use checkbox toggled after the fact didn't
  trigger B-roll tagging.** Ryan: "It didnt pull in any b-roll i dont
  think the dual use checkbox worked this time." Root-caused against his
  actual project files (`Test Project_Post House`), not assumed:
  `project.json` correctly had `dual_use: true` on the A-roll source —
  the checkbox itself worked — but that source's per-file status had no
  tagging fields at all and `broll_index/` was completely empty, meaning
  tagging was never even attempted. Confirmed sequence with Ryan: he
  checked dual-use AFTER the pipeline's initial auto-run (which computes
  `run_tagging` from the dual-use state at that moment), then never
  manually re-ran the pipeline — nothing existed to notice the new state
  and redo tagging for it. `handle_set_source_dual_use` now detects a
  false→true transition and auto-fires a tagging-only pipeline run for
  it, same automatic-over-manual reasoning as the sync-coverage rescue.
  Verified for real against Ryan's actual broken project (not a
  synthetic one): reset the flag, flipped it back, ran the same logic —
  both files tagged (2/2), `broll_index` went from empty to a real index
  (120 frames). This also repaired his real project's missing data, not
  just validated the fix. *(1bca433.)* **Ryan needs to fully quit and
  reopen the app to pick this up** — not done as of this writing.
- 2026-09-03 — **Transcript flagging wired into the real pipeline and
  export flow.** Ryan: "go ahead and wire it in." New
  `PipelineJob.run_transcript_flagging` stage (default on — automatic,
  same reasoning as the sync-coverage rescue's "made automatic" reversal
  — Ryan's standing preference against manual multi-step UI). Runs after
  transcription; a silent no-op when a project has no manifest or no
  `audience_goal` set. Idempotent like transcription — skips a
  `project.dir()/flags/<stem>.json` that already exists rather than
  re-running (and re-billing) on every pipeline click.
  `_build_all_aroll_sequences` loads any matching flags file per phrase
  (by source-file stem) and attaches the resulting markers to the "All
  Synced A-Roll" cutlist. Threaded through `backend.py`'s `run_pipeline`
  command and PMTab's auto-fire-after-organize defaults and manual
  stage checkboxes.
  **Real integration tests, not just the pieces already proven
  standalone**: the pipeline stage against a real manifest + real
  cached transcript — found the audience_goal, matched the transcript
  to its original file by stem (transcripts save under the proxy's own
  path, not the original), ran real extraction+scoring, saved 11 tagged
  fragments, confirmed idempotent on a second run (no new API call);
  the export side against that saved file — correctly loaded by stem
  match and attached 11 real FlagMarkers to the right phrase. Caught a
  real bug via this testing: `Project`'s directory accessor is `.dir()`,
  not `.project_dir()` — fixed before it shipped. *(b6ff479.)*
  **Not yet tested by Ryan in the real running app** — every piece of
  this arc has been proven with real data via test scripts, but no one
  has clicked "Run pipeline" on a real project with an audience goal set
  and confirmed the flags show up correctly in an actual Premiere
  import yet.
- 2026-09-03 — **Transcript flagging: fourth and final underlying piece
  built and proven real — writing tagged fragments as actual color-coded
  FCP7 XML markers.** `FlagMarker` (`cutlist.py`): a genuinely new range
  marker, distinct from `BRollMarker` (a POINT marker, `<out>-1</out>`,
  by design) — a color-coded storyline range needs a real duration, not
  a single tick. `exporter.py` extended with a shared
  `_build_marker_element()` plus sequence-level and clip-attached
  builders emitting a real `<out>` frame for `FlagMarker`.
  `posthouse/transcript_markers.py` translates a fragment's
  source-file-local time into the "All Synced A-Roll" sequence's
  timeline coordinates and attaches the marker to its phrase (rides with
  the clip on rearrangement, same convention as B-roll markers). Real
  end-to-end test: scored the Bob/Mitch interview's fragments, built
  markers on a timeline-offset phrase (proving the translation math, not
  a trivial zero-offset case), exported real XML, inspected the actual
  `<marker>` elements — correct distinct in/out frames, correct RGB per
  fit, correct clip-relative offsets. *(827e041.)*
  **All four pieces of transcript flagging are now built and verified
  real** (exhaustive extraction, PM audience-goal intake, relevance
  tagging, marker writing) — **but nothing is wired into the app
  itself yet.** Every piece so far has been proven via standalone test
  scripts calling the Python modules directly, not a `backend.py`
  command + UI button a user could actually click. That integration is
  the next step before this is something Ryan can test in the real app.
- 2026-09-03 — **Transcript flagging: two more pieces built and proven
  real, on top of exhaustive extraction (separate entry below).** Ryan
  redirected the design twice, both real improvements over the first
  pass:
  1. **Audience/content-goal intake, redesigned from free text to
     app-level profiles.** First built as a per-project free-text field
     on the manifest (`project.audience_goal`, additive schema change,
     `docs/contracts/PROJECT_MANIFEST.md`). Ryan: "This feels like
     something that should probably be built intentionally. Not a text
     box but a dropdown... on the main page of the app... a place for
     the user to add details about the audiences and goals... the
     dropdown in the projects would allow them to select the prebuilt
     audiences goals." Rebuilt as a titlebar-accessible "Audiences &
     content goals" library (`AudienceProfilesModal.jsx`, backed by
     `settings.py`'s `get_audience_profiles`/`set_audience_profiles`),
     seeded once with SoldFast's three real content funnels (ported from
     Agent Studio's `content-engine` team via the existing
     `soldfast-content-funnels` skill) plus an editable placeholder for
     long-form/heart-driven work, which was never formally spec'd
     anywhere. PMTab's intake field is now a dropdown over these
     profiles; the manifest schema is unchanged — the selected profile's
     description is still what lands in `audience_goal`. Ryan reviewed
     the real running UI and confirmed: "Looks good." *(94b1a0b.)*
  2. **`posthouse/audience_relevance.py`**: given a project's single
     stated `audience_goal` and the exhaustive fragment list, judges
     each fragment's fit (`strong`/`possible`/`off_topic`) via one
     Claude call — one call suffices here since scoring a short fragment
     list isn't the same output-budget bottleneck exhaustive reading is.
     Maps fits to real, distinct marker RGB colors for the eventual XML
     write. Real test: the Bob/Mitch recruitment interview's 13
     fragments (task 1's real test data) scored against the seeded
     "Contractor Recruiting" profile — 6 strong / 4 possible / 3
     off_topic, reasoning checked against the actual content and holds
     up (pre-interview chatter and a tangential anecdote correctly
     off_topic; direct pain-point/pitch material correctly strong).
     *(c332767.)*
  **Not yet built**: writing `TaggedFragment` output as actual
  color-coded markers on the exported "All Footage Synced" sequence —
  the piece that makes any of this visible in Premiere rather than
  terminal output. Reuses `exporter.py`'s existing marker-writing
  mechanism (arbitrary RGB, already confirmed working); needs proving
  against a real synced sequence.
- 2026-09-03 — **Exhaustive transcript reading built and proven against
  real material — the project's own named founding gap, closed for the
  first time.** `posthouse/transcript_coverage.py`: windows a transcript
  (10-min windows, 2-min overlap, same shape as `sync_coverage.py`'s
  fix for the analogous audio-sync gap), extracts every storyline-worthy
  fragment per window with no fixed "3 angles" cap, merges across window
  boundaries, computes an explicit coverage percentage instead of
  assuming completeness. Real bug found and fixed along the way: naive
  merging of two windows' independent descriptions of the same
  overlap-zone moment produced a visibly repeated sentence; new
  `_merge_fragments` keeps the more detailed description instead of
  concatenating when overlap covers >50% of the shorter fragment.
  **Real, direct comparison against PreCut's existing
  `story_planner.generate_angles()`, same 23.8-minute real interview
  (Runnells, Bob intv_Recruitment), both run for real through the API**:
  old approach — 3 angles, 6 ranges, covering 417s (29.2%) of the
  interview; new approach — 13 fragments covering 1,405s (98.3%).
  Real token-metered cost: old $0.063, new $0.112 (~1.8x) on this
  transcript — but per second of material actually surfaced, the new
  approach is cheaper (~5¢/min covered vs ~15¢/min), since the old
  approach's output is structurally capped regardless of length. Also
  fixed along the way: PreCut's own `story_planner.py`/`planner.py`
  never sent an `anthropic-workspace-id` header, which broke on Ryan's
  workspace-scoped Console key (a property of the ACCOUNT, not
  universal) — added `precut_pipeline/anthropic_client.py` (shared
  client builder, header sent only when `ANTHROPIC_WORKSPACE_ID` is
  actually configured) and an optional, blank-by-default "workspace ID"
  field in Settings so this never becomes something every user has to
  configure. *(57fdbff, 3968879.)*
  **Not yet built**: the audience-informed tagging layer (multi-label
  scoring of each fragment against a project's captured audience/
  content-goal) and the Project Manager intake addition it depends on —
  see § Next.
- 2026-09-03 — **Dual-use B-roll frame-rate interpretation: fully
  closed out, including step 2 (actually applying Interpret Footage),
  which needed a companion Premiere extension.** Full arc: two XML-only
  mechanisms falsified in real Premiere (rate declaration; reference-
  sequence frame math) → Ryan's incremental plan (duplicate first,
  solve triggering separately) → the `precut_bridge.py` sys.path
  shadowing bug found and fixed (`405c91d`) → Ryan confirmed the
  duplication step works in real Premiere. For step 2: static XML
  genuinely cannot express Interpret Footage state, and Ryan explicitly
  rejected a manual, human-run script ("We need a solution that isnt
  manual at all. It needs to be automatic"), which meant the only path
  was a persistent Premiere extension — the exact thing banned by the
  inherited "no CEP/UXP panel code" decision. Escalated per governance
  rule 2 rather than coding around it; Ryan authorized a narrow
  override (`ROADMAP.md` Decision Log, 2026-09-03): a companion
  extension whose only job is finding clips named
  `"... [INTERPRET TO X.XXXfps]"` and calling Premiere's own Interpret
  Footage API on them — it does not rebuild any of the XML pipeline.
  Built at `app/premiere_extension/`, verified working in real Premiere
  (`782d232`). Then made fully invisible per Ryan ("completely hands
  off... run in the background") using Adobe's own documented
  `AutoVisible=false` + `StartOn(ApplicationActivate)` + `UI Type
  Custom` mechanism — no panel, no menu entry, ever; file-logs to
  `posthouse_interpreter.log` since there's no UI left to watch
  (`c8c6fee`). Polling backs off from 2s to 20s when idle so it isn't
  walking the whole Project panel forever for nothing (`6090582`).
  Finally folded into the app's own install process (`46431bc`): a
  `premiere_extension` component in `setup_helper.py`'s existing
  dependency-installer framework (same pattern as ffmpeg/Xcode CLT),
  with a matching row in `SetupScreen.jsx` and the extension added to
  `tauri.conf.json`'s bundled resources — optional, not required for
  `all_ready`, since a Mac without Premiere installed can still finish
  setup normally.
- 2026-08-31 — Reviewed `precut` and `precut-premiere-extension` end to
  end; PreCut confirmed as foundation-then-donor. *(ROADMAP.md, 6137dfe.)*
- 2026-08-31 — ROADMAP v1. *(6137dfe.)* 2026-09-01 — Governance layer.
  *(fc3cabc.)* Repo renamed to `pierces-post-house`. *(c874dee.)*

- 2026-08-31 — Reviewed `precut` and `precut-premiere-extension` end to
  end; PreCut confirmed as foundation-then-donor. *(ROADMAP.md, 6137dfe.)*
- 2026-08-31 — ROADMAP v1. *(6137dfe.)* 2026-09-01 — Governance layer.
  *(fc3cabc.)* Repo renamed to `pierces-post-house`. *(c874dee.)*
- 2026-09-01 — Adversarial architecture review: 14 findings, 3 blocking,
  all incorporated. *(037694a.)*
- 2026-09-01 — **Phase 0 Tier 1 safety net shipped**: hermetic exporter
  gate, canonicalized golden master, FCP7 quirk tests 1–5, import gate.
  16 passed / 2 skipped, verified independently by the Lead; sabotage
  check caught a planted regression. Tier 2 items (full import gate, DB
  migrations, real-footage sync) deferred to Ryan's Mac. *(5829746.)*
- 2026-09-01 — **Product pivot logged** (Ryan): new app with role-driven
  UX; PreCut = donor, harvested not rebuilt, untouched until superseded;
  build order PM → AE; Project Manifest contract (incl. `dual_use`
  flags) is the PM's hard deliverable. Roadmap restructured to Phases
  0–9. *(7331bf3.)* Brand Brief spec + co-location rule added. *(next
  two commits.)*
- 2026-09-01 — **Reviewer pass on the safety net**: 8 findings (2 would
  have failed on Ryan's Mac: markers import-gate assertion inverted
  under real ML deps; hash-seed-dependent path normalization order on
  macOS), all fixed and re-verified, plus a BLESS=1 refusal in the
  runner and loud-skip on bless. *(This commit.)*
- 2026-09-01 — **Phase 1 slice shipped**: `posthouse/` package — door-3
  bridge pinned to PreCut e035fbaf, cold-footage builder (segments JSON
  → V1 sequence XML through the proven exporter chain, API + CLI,
  non-zero exit on failure), light-dep harvest wrappers (auto_include,
  camera_inference, theme_categories, proxy_manager), 15 new tests +
  cold-footage golden. Suite now 31 passed / 2 skipped, verified by the
  Lead. Heavy-dep wrappers deferred (`posthouse/harvest/DEFERRED.md`).
  *(547a6cd.)*
- 2026-09-01 — Project Manifest contract v1 drafted, pending Ryan's
  ratification. *(8100c85.)*
- 2026-09-01 — **Teleported to Ryan's Mac; Phase 0 Tier 2 shipped for
  real**: full 35-module import gate and additive-only DB-migration
  test implemented (previously documented-only stubs) and run against
  the real `~/precut-venv-fresh` + a freshly pinned checkout
  (`e035fbaf`, exact match to `posthouse/PRECUT_PIN`). 60 passed /
  1 skipped. Sabotage re-verified on the Mac (historical off-by-one bug
  reintroduced in a scratch copy, caught three ways, reverted).
  `run_safety_net.sh` now auto-detects the real venv and refuses to run
  with `PRECUT_ROOT` unset rather than defaulting silently. Real-footage
  audio sync remains the one open Tier-2 item. *(ca50076.)*
- 2026-09-01 — **Project Manifest contract ratified by Ryan.** All 6
  open questions answered; 3 diverge from the draft's recommendation
  (delivery targets not proposed by the PM at all — Creative Editor's
  job after it has actually seen the organized footage; shoot dates
  read with no confirmation step; on-camera naming gets real added
  scope — per-voice attribution with propagating rename, scoped out to
  Phase 4). *(a25e2cc.)*
- 2026-09-01 — **Footage-portability tension resolved.** Ryan clarified
  "self-contained project folder" means brand/other small assets are
  copied to live alongside the footage (a sibling directory under the
  project root) — the footage itself is never copied or relocated,
  fully preserving PreCut's "source footage is never moved" design.
  Contract §2.3 updated; no blocker remains.
- 2026-09-01 — **Cross-clip speaker naming: verified real, then
  dropped.** Confirmed via web search Premiere can't do it (see
  ROADMAP Decision Log). Ryan then de-scoped the feature itself the
  same day: generic "Speaker 1" / "Speaker 2" labels are sufficient
  everywhere — no cross-clip voice matching or propagating rename
  needed. Removes real complexity from the eventual Phase 4 design.
  Manifest's `people` field simplified back to a plain intake roster.
  *(This commit.)*
- 2026-09-01 — **Phase 2 slice 1: manifest builder/validator shipped.**
  `posthouse/manifest.py` — build/load/save/validate, source-ID minting
  per contract §5 (frozen, never renumbered), two-moment validation
  (intake warns / handoff rejects, exhaustive), atomic writes,
  contract_version refusal, CLI. 64 new tests; suite 124 passed /
  1 skipped, verified independently by the Lead. High-effort code
  review found 2 real defects (undetected nested-source kind conflicts;
  person-ID docstring/implementation mismatch) — both fixed with
  regression tests before commit. Four contract-gap judgment calls
  logged in ROADMAP's Decision Log.
- 2026-09-01 — **Phase 2 slice 2: Brand Brief generator shipped.**
  `posthouse/brandbrief.py` — font extraction from name tables, macOS
  install status, deterministic palette, README + brand-card PNG inside
  `assets_dir` with the co-location invariant enforced in code, CLI.
  33 tests; suite 157 passed / 1 skipped, verified by the Lead against a
  realistic SoldFast-branded fixture (it correctly recovered navy
  #033459, blue #0391D8, light blue #00ADE1, orange #F4690B from a logo).
  Three defects found by *looking at* the rendered card rather than by
  tests — em dashes in generated copy, bare counts instead of named
  files with reasons, and a vivid orange labelled "neutral" — all fixed
  with regression tests. Out of scope this slice: PDF summarization and
  the frame-0 marker.
- 2026-09-01 — **Phase 2 slice 3 shipped; PHASE 2 COMPLETE.**
  `posthouse/projectmanager.py` — census, unsupported aggregation,
  harvested camera inference with the real pin, shoot dates from file
  timestamps, brand-asset staging into `Brand Assets`, Brand Brief
  generation, append-only handoff record, and a hard handoff-validation
  gate before anything is written. 21 tests; suite 178 passed /
  1 skipped. Verified by the Lead on a realistic fake shoot including a
  late-footage re-run (revision 1→2, prior source ids frozen, exactly
  one new file on disk, footage never copied). Also amended contract
  §4.2 to drop the now-incorrect "delivery_targets is empty" warning,
  caught by watching the real run.
- 2026-09-01 — **Phase 1 complete; Phase 0's sync gap closed.** Heavy-dep
  harvest wrappers `posthouse/harvest/{transcribe,index,sync}.py` with
  8 Tier-2 tests; suite 186 passed / 1 skipped, verified by the Lead.
  Sync recovered a known 1.5s offset to within 4ms at score 11.55 vs
  SCORE_USE 10.0 on real TTS speech; the index was proven by feeding it
  to PreCut's own `load_broll_library`; transcription reuses PreCut's
  phrase chunking and on-disk shape. Vision tagging opt-in, off by
  default, no network in tests.
- 2026-09-01 — **Benchmark v1 staged (Runnells Day 1) and the PM's
  first real-footage run.** Manifest at `benchmark/runnells-day-1/`
  (paths only; media stays on `RDOSS_2025`). The real run exposed two
  bugs fixtures could not: proxies + `._*` sidecars counted as footage
  (6 videos for a 2-clip shoot, phantom July date) and a stale census
  surviving re-runs. Both fixed, regression-tested, and re-validated on
  the drive: 2 videos, one shoot date, refresh on re-run with frozen
  ids. PM tests 23 passed.
- 2026-09-01 — **Phase 3 scoring harness shipped and reviewed.**
  `posthouse/benchmark.py`: Premiere-export parser, time-overlap
  P/R/IoU with independently-dilated tolerance, per-ruleset and
  truth-scope handling, largest-misses report, CLI. Review found 8
  verified defects (3 load-bearing: gap-merging dilation, wrong-clip
  basename credit, untrimmed nests), all fixed with regression tests;
  truth scope added for the partial answer key. Suite 220 passed /
  1 skipped; arithmetic and real-key parse hand-verified by the Lead.
  Ryan's answer key (clip 0006, 26 selects, 39% usable) staged and
  parsing exactly.
- 2026-09-02 — **Phase 4 slices 1-5 built, reviewed, and honestly
  measured.** Signal extractor (slice 1: memory bug and a sign-inversion
  bug caught by review before shipping, fixed and verified); per-frame
  motion classifier (slice 2: correct per-frame, 18x over-fragmented
  when consolidated — accepted as a labeller, not a boundary-setter);
  segmentation (slice 3: first real culls.json and first honest
  benchmark score, below the crude two-threshold probe); fitting (slice
  4: the pre-committed rule fired — two thresholds beat the full
  classify+consolidate+gate pipeline on a fair held-out comparison, so
  the detector was simplified rather than given more parameters);
  stability detector adopted as production (slice 5: classifier demoted
  to a labeller only, as Ryan approved). Full detail and every number in
  ROADMAP's 2026-09-02 entries — not duplicated here.
- 2026-09-02 — **Benchmark v2 candidate staged: Des Moines Estabs**
  (real 8-day drone project, 41.5 min usable after a parser fix, 59
  true full-clip rejects). Cross-shoot transfer tested both directions:
  fit-on-Des-Moines transfers to Runnells, the reverse does not, and
  per-clip normalization (quantile + robust_scale, both shipped) did
  not close that gap on its own — until Ryan confirmed the real cause:
  Runnells is an exhaustive technical mark, Des Moines is real
  production selects filtered by editorial taste, so the two answer
  keys were never measuring the same thing. The four architecture
  options considered at the time (fit per shoot / per camera / accept
  asymmetry) are withdrawn as premature for that reason.
- 2026-09-02 — **Ryan corrected the detector by hand and gave concrete
  motion criteria** (shape-over-time, not just magnitude; frame-rate-
  aware B-roll interpretation). Video-vision evaluated and scoped as a
  diagnostic aid only — our native-rate signal sampling already exceeds
  what sparse frame extraction could offer for jerk/oscillation
  judgments. Three rounds of feature testing against real ground truth,
  each reported honestly before asking for more of Ryan's time: raw
  axis-purity was backwards (falsified by real data — most of Ryan's
  intentional moves are compound, multi-axis motion); direction-
  stability with a per-clip-normalized floor reached a real AUC of
  0.714 on Runnells, genuine signal, not yet proven to generalize.
- 2026-09-02 — **Two parser bugs found and fixed reading real Premiere
  exports; a real benchmark blind spot found and fixed as a result.**
  Getting Ryan's hand-corrected cuts to parse needed a self-consistency
  rate-resolution fix (retimed clips) plus a rescale correction caught
  by hand-checking the numbers rather than trusting a clean parse.
  Scoring the original detector against those real cuts then exposed
  that precision/recall/IoU cannot see segment count or size: a 4-blob
  detector scored P=0.727/R=0.993/IoU=0.593 — beating select-everything
  — while doing none of the real culling work. Fixed: `granularity_
  ratio` and `under/over_segmentation_events` are now part of every
  score. Suite 363 passed / 1 skipped. *(926a3d6, e36e2c9.)*
- 2026-09-02 — **Slice 4 re-fit with direction-stability, shipped as a
  sixth first-class arm; it wins, narrowly.** The diagnostic sweep that
  found AUC 0.714 was lost to context compaction (never committed as
  code) — reconstructed from scratch against the same cached signals and
  reproduced to 3 decimal places before building on it, not trusted from
  memory. Shipped as `stability_combine="dirstab_only"` in `segment.py`
  (per-clip-normalized circular-statistics signal on motion direction)
  and wired into `fit.py` as a seventh CV/bootstrap/fixture-guarded arm.
  9 new tests; suite 223 passed / 1 skipped (non-tier2) + 2 real-clip
  tier2 tests. Re-fit on Runnells (same sidecar/answer-key/precision-
  floor as the prior resid_only run): `dirstab_only` wins the arm
  ranking, held-out **P 0.629 / R 0.915 / IoU 0.417** — inside noise of
  `resid_only`'s 0.627/0.911/0.417, and still short of the crude
  two-threshold probe overall (beats it on recall, trails on precision
  and IoU by ~0.01). Genuine, confirmed signal; not a breakthrough. Not
  yet scored against real drone footage.
- 2026-09-02 — **Scored against Ryan's real Historic Valley Junction
  cuts: the transfer question is answered, and the answer is no.**
  Both re-fitted arms (dirstab_only, resid_only) score within rounding
  of select-everything on P/R/IoU (0.727/0.99/0.593 vs select-
  everything's 0.726/1.00/0.596). The granularity metrics catch what
  P/R/IoU hides: dirstab_only genuinely predicts more, finer segments
  (9 vs resid_only's 4 vs select-everything's 1) — a real difference —
  but even those 9 are still giant blobs swallowing 7-8 real cuts each.
  Neither arm does the actual culling work Ryan asked for on this
  footage. Likely contributing factor, not yet confirmed: both arms'
  own motion gate is pinned to the edge of its Runnells search grid
  (`stability_resid_max=9.0` of `[0.8,9.0]`, `stability_dirstab_
  max=0.9` of `[0.1,0.9]`) — a gate that already wants to be nearly
  disabled on the shoot it was fitted on is a weak bet to transfer.
- 2026-09-02 — **Widened both grid-edge-pinned grids: neither threshold
  wants to be disabled, ruling out that theory — but the wider grid's
  tie-broken pick is measurably worse on real footage.** Extended
  `stability_resid_max`'s grid to 35.0 (past this clip's own observed
  max of 21.34) and `stability_dirstab_max`'s to its true 1.0 ceiling;
  re-fit on Runnells. Both land on genuine interior optima (18.0, 0.95),
  no longer flagged by the grid-edge alarm, with held-out metrics on
  Runnells essentially unchanged from the narrower grid. Re-scored
  against Historic Valley Junction: `resid_only`'s new value produces
  IDENTICAL output to the old one (both already far above the drone
  footage's own motion scale). `dirstab_only`'s new value (0.95) is
  WORSE — 6 predicted segments (granularity_ratio 0.231) vs the old
  0.9's 9 (0.346) — a real regression from a change that is a dead tie
  on the fitting clip. Concrete proof that fitting on one clip cannot
  see this kind of difference. Not shipped: the original
  `runnells_fit_dirstab/params.json` (0.9) stays the record; the
  widened-grid result is kept alongside at `runnells_fit_dirstab_
  widened/` as a documented alternative, not a silent regression. Suite
  223 passed / 1 skipped, re-verified after the grid change.

- 2026-09-02 — **Agent-proficiency work (outside this repo's code).** Ryan
  supplied four talks/clips as resources; consumed in full. Acted on them:
  global `~/.claude/CLAUDE.md` gained four always-on working rules; the
  existing `agent-guardrails` skill gained the two lessons this session
  taught (slice by outcome not component; documentation is not progress).
  Two new skills built and verified: `verified-quotes` (machine-checks
  transcript quotes against source SRTs — port proven faithful by A/B against
  the original script on identical input) and `footage-analysis` (cut-rhythm
  measurement plus the FCP7/xmeml parsing gotchas and the granularity blind
  spot from this week). In THIS repo: required reading cut from 2,258 lines
  to 179 (progressive disclosure table), and rule 5 replaced — it still said
  "no code yet, we are in planning."

- 2026-09-02 — **`posthouse/moments.py`: transcript query to verified moments to
  Premiere sequence.** The first slice that joins the two halves. Loads 270
  Runnells Whisper transcripts (word-level timings), resolves 268 to real media,
  ranks segments by IDF-weighted term overlap, machine-verifies every quote via
  the `verified-quotes` skill, and emits the coldfootage segments contract plus
  an FCP7 XML. 20 new tests; suite 243 passed / 1 skipped. Measured: retrieval
  recall@1 40.5%, @5 66.7%, @10 73.8% against 42 gold-chunk `why` descriptions;
  XML round-trips through `parse_answer_key_xml` to the exact input ranges;
  0 of 40 returned moments overlapped a Whisper hallucination loop. Two bugs
  found by the verifier on the first real run and fixed with regressions
  (Frankenstein quotes from merged non-adjacent segments; bare timecodes
  reading as TIMECODE_MISMATCH). Known limit: 29% of this corpus is audio-only
  lav/interview material the exporter cannot place; those moments are surfaced
  and labelled, not dropped, and need `harvest/sync.py` to reach a timeline.
  **Confirmed by Ryan** — opened `~/Desktop/Moments_Demo_Runnells/moments.xml`,
  checked the four kitchen-cabinet moments against the footage himself: "They
  are about the cabinets." First slice in this project to be verified working
  end to end by the person who has to trust it, not just by tests.
- 2026-09-02 — **Agent Studio doctrine ported to skills; `studio.py` left dead.**
  Four new global skills (`soldfast-content-funnels`, `longform-story-craft`,
  `footage-assembly-method`, `hook-writing`) consolidated from 15+ scattered
  files, plus `loop_detector.py` added to `verified-quotes` (import fixed, and
  it found one Runnells transcript that is 91% hallucination loops). Agent
  Studio itself untouched and read-only throughout.
- 2026-09-03 — **Task 1.0: PreCut forked into this repo at `app/`, confirmed
  running by Ryan.** Architecture corrected first (see ROADMAP Decision Log)
  — one app, not two: Ryan does not want a separate new app calling PreCut,
  he wants PreCut's own shell absorbed and extended. Copied
  `~/precut-checkout` (source only, no git history) into `app/`; changed
  `productName`/`identifier`/window title to "Post House" /
  `com.pierce.posthouse` so it can't collide with the real installed
  PreCut.app; found and fixed a real data-safety issue along the way — the
  Application Support directory was hardcoded to "PreCut" in three files
  (`project.py`, `setup_helper.py`, `settings.py`), which would have meant
  sharing live settings.json and the project registry with Ryan's
  production app on first run. `npm install` clean; `npm run tauri dev`
  compiled 367 crates with zero errors on this checkout's first-ever build;
  window title confirmed "Post House" via macOS's accessibility API; the
  fork's Python backend and Rust shell run as processes fully separate from
  the real PreCut.app, verified running side by side with no conflict.
  **Ryan's sign-off**: "Ok the app works like regular precut." *(eb6d42c.)*
- 2026-09-03 — **Task 1.1: Project Manager tab built, run end to end on a
  real folder, and taken through three rounds of Ryan's direct feedback.**
  `PMTab.jsx` reuses `DropZone.jsx`'s existing drag-and-drop pattern for
  A-roll/B-roll/source-audio/assets, adds a dual-use checklist for A-roll
  that also serves as B-roll, and a new `organize_project` backend command
  calls the existing, already-tested `projectmanager.organize_project()`
  directly. Three real bugs/mismatches found and fixed from Ryan's actual
  runs: (1) single-folder-plus-dropdown UI didn't match how a real project
  folder holds separately-kinded subfolders — rebuilt around drag-and-drop
  per kind; (2) `brandbrief.py`'s asset scan crashed on a macOS AppleDouble
  sidecar (`._SF-Main-RE-light.png` on external volume RDOSS_2025) because
  it lacked the dotfile filter `projectmanager.py` already used for footage
  census — fixed, regression-tested (confirmed to fail without the fix via
  `git stash`); (3) dragging the same folder into both A-roll and B-roll
  correctly tripped the manifest's kind-conflict rule but with an
  unexplained assertion error — added client-side duplicate-blocking with a
  message pointing at the dual-use checkbox, the contract-correct way to
  express "same footage, both uses." Ryan then asked for a naming change
  (`DEFAULT_ASSETS_SUBDIR` "Brand Assets" → "Company Branding", matched in
  `projectmanager.py`, `posthouse/README.md`, and both contract docs) —
  done, tested (224 passed / 1 skipped, non-tier2), and confirmed live in
  the running app. Suite and commit: *(20682fb.)*
  **Signed off by Ryan, 2026-09-03**: "I'm good with it." Task 1.1 is
  complete.
- 2026-09-03 — **Confirmed (not assumed): PreCut does NOT interpret B-roll
  footage to a different/preselected frame rate than A-roll for dual-use
  sources.** Ryan's belief going in: "the dual use should remember that
  B-roll needs to be interpreted to the preselected framerate and the a
  roll will not (i believe precut does this already but confirm)." Read
  `precut_pipeline/multi_exporter.py` and `story_assembler.py` in full.
  Findings, cited: every clip (A-roll or B-roll) is probed for its own
  native fps via `_safe_probe()`/`detect_frame_rate()`
  (`multi_exporter.py:379-508`), and both `_build_aroll_master_for_path`
  (`:1358`) and `_build_broll_master_for_entry` (`:1433`) declare that
  clip's own native rate as its FCPXML master rate — same code path, no
  kind-based branch. The **sequence** frame rate is set from A-roll's
  native fps (`story_assembler.py:261-263`, "A-roll native dims
  fallback"), not from B-roll, and there is no retime/conform/speed-change
  logic anywhere in either file for either roll kind — `detect_frame_rate`
  (`exporter.py:48-73`) only snaps a measured fps to the nearest standard
  rate, identically for any clip. Separately: in the current pipeline
  B-roll isn't even placed on the timeline as real clips —
  `story_assembler.py`'s `CutList.broll_track` is hardcoded to `[]`
  ("Markers-only; V2 stays omitted per Drop 3.7+"), so even a working
  version of the behavior Ryan described wouldn't currently reach an
  actual B-roll clip. **Conclusion: this is a real gap, not something
  PreCut already does.** Ryan then gave the concrete spec for it in the
  same message as Task 1.1's sign-off (see ROADMAP Decision Log,
  2026-09-03): B-roll conforms to the export frame rate, A-roll never
  does, and a dual-use source needs to exist as **two separate items in
  Premiere's Project panel** (one native, one conformed) — not a shared
  master clip. Captured for Phase 4 (Assistant Editor); not scheduled,
  not started.
- 2026-09-03 — **Task 1.1/2.1 merged into one "Project" tab, and
  automatic sync rescue, both confirmed by Ryan on real footage.**
  Condensed from seven rounds of real use, each driven by Ryan actually
  running the previous round's fix (full detail in git log from
  `454bd05` through `b613002`, and in ROADMAP's Decision Log):
  - PMTab and IngestTab were merged (declaring footage twice → once);
    IngestTab.jsx and AETab.jsx deleted, absorbed into PMTab. Organize
    now auto-fires the pipeline. A real backend bug was found and fixed
    along the way: dual-use A-roll never reached B-roll tagging because
    `add_source` is path-keyed with one kind per path — fixed with
    `SourceFolder.dual_use` + `Project.set_dual_use()`.
  - A real, pre-existing PreCut limitation was found (not a regression —
    verified `audio_sync.py` byte-identical to the protected checkout
    before considering anything else): whole-file cross-correlation
    can't handle a subject leaving the room, since a long dead/
    irrelevant stretch dilutes the one correlation PreCut runs per pair.
    New capability `posthouse/sync_coverage.py` rescues these via
    windowed correlation, verified against the exact real pair before
    the algorithm was even written.
  - Three real bugs surfaced by Ryan actually clicking things (a state-
    clobber bug that erased its own "Analyzing" status, a performance
    bug from never testing the long-file case, a thread-starvation bug
    from 6 concurrent analyses) were each found with real evidence
    (`ps aux`, a direct timing measurement, an actual fired-event
    capture) and fixed, not assumed.
  - Final round: Ryan rejected the entire manual-analyze-then-apply
    workflow as overcomplicated — right call. Rescue is now fully
    automatic, inside the same sync stage, before any result is shown;
    519 lines of manual UI/IPC removed for 54 added. Verified against a
    full reset copy of the real project: all 6 real weak pairs
    processed unattended, 3 rescued, 3 correctly left unsynced (wrong
    mic/clip cross-pairings) — all four real (mic, clip) correspondences
    reliable, every WAV placed somewhere real.
  **Ryan, after running it for real: "That worked perfectly."** Export
  now includes all four WAVs. *(b613002, 6a3a7e2.)*

## Next — skills checklist (role-sequencing gate lifted 2026-09-03)

Per `CLAUDE.md` rule 8 (amended 2026-09-03): Ryan directed building every
role's skills in parallel — "handle all of the skills across the board" —
rather than gating the next role on the current one's full sign-off. Role
and UI assignment is deliberately deferred until skills work. Rule 7
(prove on one real unit before broadening) and the sign-off bar (Ryan on
real material, not passing tests) still apply **per skill** — this list
tracks that per-skill state, not a single "current task."

Status, from `ROADMAP.md` §3's Role → skill map:

| Skill | State |
| --- | --- |
| Project Manager (intake, manifest, dual-use, brand assets) | **Signed off** (Task 1.0/1.1) |
| AE: sync (+ coverage rescue) | **Signed off**, automatic |
| AE: dual-use tagging + B-roll frame-rate interpretation | **Signed off**, incl. Premiere extension |
| AE: technical cull ("Cold Footage") | **PARKED** — 3 detector approaches failed on real footage; also confirmed 2026-09-03 that even a working detector couldn't safely deliver trimmed B-roll segments needing frame-rate interpretation under the current static-XML architecture (interpreting a clip *after* a trim is already placed on a timeline invalidates that trim — confirmed by Ryan directly in real Premiere). Needs its own explicit unpark decision on both fronts, not just the gate lift. |
| AE: subject grouping (per-subject cold-footage sequences) | **Blocked on cull, by Ryan's explicit choice (2026-09-03)** — could have been rescoped to whole-clip bins (same safe untrimmed-master pattern as the shipped B-roll duplication feature) to unblock it now, but Ryan chose to keep it tied to cull output instead. Stays parked alongside cull. |
| AE: transcript flagging (color-coded storyline ranges) | **Signed off by Ryan on real footage, end-to-end** (exhaustive extraction, PM audience-goal intake, relevance tagging, XML marker writing, `run_pipeline`/export wiring, and marker color — the last required moving color-setting from the FCP7 XML into the Premiere ExtendScript extension, since Premiere never honors `<marker><color>`). Ryan: "Ok that worked." See § Done, 2026-09-03. |
| Creative Editor: story + assembly | **Selection step built, wired into the app, and visible** (`posthouse/story_architect.py` + `story_architect_generate`/`get_story_research` backend commands + IdeasTab UI). Live trend research now actually downloads and watches real videos (not just text search), with a visible sourced audit trail per idea card. PreCut's existing assembler/export reused unmodified. **Not yet reviewed by Ryan in the running app.** See § Done, 2026-09-03. |
| Creative Editor: music (Artlist local-library match) | Not started — B− |
| Creative Editor: SFX placement | Not started — B− |
| Creative Editor: B-roll placement (real clips, not markers) | Gated on benchmark precision |
| Audio Designer: loudness → clip gain | Not started — B |
| Colorist: exposure/contrast QC report | Not started — C |
| Supervisor loop (notes → revised cut) | Not started — B |

**Not yet decided: which skill to start on next.** Asked Ryan; awaiting
his pick (or his go-ahead to propose an order).

## Attempts ledger

*(task · tier · attempt # · what was tried · why it failed — written by
the Lead before any re-dispatch; empty so far)*

## Escalations / blockers

*(none open)*

## Standing notes

- Repo: `wastemytime2007/pierces-post-house`, branch
  `claude/ai-video-editing-team-k2a66r`. Working copies: cloud session
  at `/home/user/test`; teleported Mac session at
  `/Users/pierce/pierces-post-house`.
- Real-footage work runs only on Ryan's Mac.
- PreCut pin for harvests: commit `e035fbaf1fe63bfb0647917af142304b4470d00d`
  (`v1.0.0-beta.3`), recorded in `posthouse/PRECUT_PIN`. A read-only
  checkout for `PRECUT_ROOT` lives at `~/precut-checkout` on Ryan's Mac
  (outside this repo, never modified — protected-repo rule).
- Real venv for Tier-2 runs: `~/precut-venv-fresh/bin/python`
  (auto-detected by `safety_net/run_safety_net.sh`).
