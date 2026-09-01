# posthouse

`posthouse/` is Layer 2 of `docs/ARCHITECTURE.md`: the new app's Python
code, built as a *client* of PreCut rather than a fork of it. Everything
here either reuses a PreCut capability through `posthouse.precut_bridge`
(the third integration door — `precut_pipeline` imported as a library,
per ROADMAP.md's Decision Log) or is genuinely new logic that PreCut has
no equivalent for. Phase 1 shipped the bridge itself, the Cold Footage
sequence builder, and the light-dependency harvest wrappers first; this
slice adds the three heavy-dependency wrappers (transcribe, index, sync)
that need Ryan's Mac and its real ML venv, closing out Phase 1.

## The pin mechanism

`posthouse/precut_bridge.py` is the *only* module in this package allowed
to import `precut_pipeline` or a top-level `python_backend` module — every
other module calls `precut_bridge.import_precut("...")` instead of
reaching into PreCut directly, so there is exactly one place that knows
where PreCut lives and what commit it's pinned to. `posthouse/PRECUT_PIN`
holds that commit hash as a single line; on every first use, the bridge
compares it against `git -C $PRECUT_ROOT rev-parse HEAD` and prints an
unmissable (but non-fatal) warning naming both hashes on a mismatch —
PreCut is Ryan's live production tool, so a stale pin must never crash a
session, only make drift impossible to miss. Bumping the pin to a newer
PreCut commit is a deliberate act logged in ROADMAP.md's Decision Log,
mirroring the safety net's own re-blessing rule.

## Harvested vs. deferred

`posthouse/harvest/` wraps the PreCut capabilities that import cleanly
with nothing beyond the Python 3.11 standard library, verified module by
module in a clean subprocess: `auto_include`, `camera_inference`,
`theme_categories`, and `proxy_manager` (the last one shells out to
ffmpeg rather than binding it, which is exactly why it's light). Each
wrapper is a thin re-export stating its provenance and pin — no logic of
its own.

Transcription, tagging/indexing, and audio sync need PreCut's real ML
venv (torch, whisper, lancedb, open_clip, anthropic) and, for sync
specifically, real(ish) correlated audio even with that venv installed —
none of which exist in a cloud session, so all three are wrapped but
their tests self-skip off Ryan's Mac. `posthouse/harvest/DEFERRED.md`
records what's still genuinely deferred (Claude/LLaVA vision tagging
needs a key/Ollama neither is exercised here; the below-threshold sync
policy is a Phase 4 product decision, not an engineering one).

### `transcribe.py` — Whisper transcription

Wraps `precut_pipeline.transcriber` unchanged: `transcribe(media_path,
language=..., model_name=..., device=...) -> Transcript`,
`transcript_to_json(transcript) -> str`, `save_transcript(transcript,
path) -> Path`. `Transcript.to_dict()`/`.save()` (re-exported, not
reimplemented) already produce exactly the on-disk shape PreCut's own
pipeline writes per A-roll under `transcripts/<source_stem>.json`.
Phrase-boundary chunking (`chunk_into_phrases`) is PreCut's own function,
imported not re-derived — see the module docstring for how this
addresses ROADMAP.md §7's Whisper timing-bias note. Tier-2 tested
(`safety_net/tests/test_transcribe.py`) against real speech generated
with macOS `say -v Samantha` (the default voice garbles "countertops" in
Whisper's output — a real acoustic finding, recorded in `DEFERRED.md`,
not a wrapper bug): keyword recovery, phrase monotonicity/non-overlap,
sequential ids, and an on-disk round trip. Both the Whisper `base` model
and the ffmpeg decode path were already cached
(`~/.cache/whisper/base.pt`) — no download needed for this build.

### `index.py` — CLIP embedding + B-roll SQLite/LanceDB index

Wraps `precut_pipeline.embedder` (CLIP ViT-B-32, 512-dim),
`precut_pipeline.database`, `precut_pipeline.extractor`, and
`precut_pipeline.ingest._process_clip` (PreCut's own real per-clip
worker, reused rather than re-derived) into `index_broll(clip_paths,
project_dir, tagger=None) -> IndexStats`. Writes `<project_dir>/
broll_index/precut.db` + `vectors.lance` through the real `Database`
class — the exact schema `multi_exporter.load_broll_library` reads.
`tagger` is `None` by default (CLIP-only, no network call); `"claude"`
requires `ANTHROPIC_API_KEY`; `"llava"` requires a reachable Ollama
instance — both raise loudly if their dependency is missing rather than
silently degrading, and neither is exercised by this module's own tests.
Idempotent re-indexing (unchanged mtime → skip; changed mtime →
`delete_frames_for_clip` then re-insert, `clips.path` is `UNIQUE`) is
PreCut's own guarantee, inherited unchanged. Tier-2 tested
(`safety_net/tests/test_index.py`) against the safety-net fixture clips:
asserts the schema is real by actually calling `load_broll_library`
(re-exported from `multi_exporter`) on the index this module built and
getting entries back, asserts LanceDB holds one 512-dim vector per
sampled frame, and asserts re-indexing the same clip adds zero duplicate
rows. CLIP weights were already cached
(`~/.cache/huggingface/hub/models--laion--CLIP-ViT-B-32-laion2B-s34B-b79K`)
— no download needed for this build.

### `sync.py` — lav/audio sync (MFCC cross-correlation)

Wraps `precut_pipeline.audio_sync` unchanged: `sync_pairs(aroll_paths,
lav_paths) -> SyncResult`, walking the full cross product the same way
PreCut's own `sync_project` does, calling the same per-pair primitive
(`sync_pair`, re-exported) for each. Every pair comes back carrying
`offset_sec`, `score`, and `passed_threshold` (`score >= SCORE_USE`,
PreCut's own `10.0`, re-exported — never a locally redefined threshold).
**Below-threshold policy is explicitly not this wrapper's call** (see
`DEFERRED.md`) — every pair is returned, flagged, never dropped, never
silently included; Phase 4 (the Assistant Editor) decides.

This closes the one Tier-2 gap `safety_net/README.md` and ROADMAP.md's
Decision Log both named as still open: real(ish) correlated dual-source
audio, not synthetic tones. `safety_net/tests/test_sync.py` generates
real speech with `say -v Samantha`, ffmpeg's it into a "camera" MOV and a
"lav" WAV offset by a known 1.5s with different gain/EQ and added noise
(not a bit-identical copy), and measures the recovered offset and score
fresh on every run — **measured: offset -1.504s vs known -1.5s (4ms
error); score 11.55 vs `SCORE_USE=10.0`.** Real speech clears the
threshold. Per the task brief, if a future run measures below threshold
the test skips with the exact number rather than lowering the threshold
or weakening the assertion — see the test's own docstring.

### Tier-2 marker

All three of the above are `@pytest.mark.tier2` (registered in
`safety_net/conftest.py`'s `pytest_configure`). Run everything:
`pytest safety_net/tests`. Skip the slow, real-model tests (cloud/CI):
`pytest safety_net/tests -m "not tier2"`. Run only them:
`pytest safety_net/tests -m tier2`.

## The segments contract (`posthouse/coldfootage.py`)

The Cold Footage builder is the one piece of genuinely new code in this
slice, because PreCut's `CutList` model has no way to express "arbitrary
in/out ranges from arbitrary source files, laid back to back" (it's
built from a transcript's topic ranges — see `story_assembler.py` — or
matcher/library output, never raw segments). Its input is a JSON file —
`{"contract_version": 1, "sequence_name": str, "segments": [{"source_path",
"in_sec", "out_sec", "label"?, "handle_sec"? (default 1.0)}]}` — meant to
double as a first draft of the culls→timeline contract Phase 4's
`culls.json` will need once its field-level schema is settled
(ARCHITECTURE.md's open questions list). Segments land on V1 in list
order, back to back; handles extend each stored in/out outward but clamp
to the source's real (ffprobe'd) duration; every segment is validated
before anything is built, and a bad file is rejected with every offending
segment listed at once, not just the first. The build reuses PreCut's own
path exactly as `story_assembler.py` does — one `ARollPhrase` per segment,
wrapped in a `CutList` with empty `broll_track`/`broll_markers`, exported
through `multi_exporter.export_multi_timeline` with `broll_library=None`
and `include_overlay=False` — because that's the harvest rule: reuse the
proven exporter chain, don't write a second XML writer.

## CLI usage

```
python -m posthouse.coldfootage segments.json output.xml
```

Exits non-zero with a stderr message on any failure — a malformed JSON
file, an unsupported `contract_version`, or one or more invalid segments
— and never writes a partial output file. The same behavior is available
as a Python API: `build_coldfootage_xml(segments_dict, output_path)`,
returning the output path on success or raising `ColdFootageError` /
`ColdFootageValidationError` (the latter carries every offending segment
in `.problems`) on failure.

## Friction worth recording

Building this surfaced a real gap between what the Phase 0 safety net's
own docs claim and what the exporter code actually does:
`FCPXMLWriter._build_markers()` imports `precut_pipeline.markers`
**unconditionally** at the top of the method, and `_build_sequence()`
calls it on every export — not only when a `CutList` actually carries
`broll_markers` or a `creative_brief`, as `safety_net/conftest.py`'s
docstring states. That means *every* call into the exporter chain today
needs `precut_pipeline.markers`' transitive deps (lancedb + numpy +
pyarrow + torch), including a Cold Footage export with no markers and no
brief at all. `precut_bridge.py` works around this the same way
`conftest.py` does — inert `sys.modules` stubs, installed only if the
real package isn't already present — but a cleaner upstream fix (for
whoever next touches `exporter.py`, on Ryan's Mac, through the normal
protected-repo process) would make that import conditional on there
being an actual marker or brief to render, removing the need for the
workaround entirely. A second, smaller finding: that same stub, once
installed, makes `pytest.approx()` raise `AttributeError` for the rest of
the test process (pytest probes `sys.modules["numpy"]` and calls
`np.isscalar` on whatever it finds) — this is a **pre-existing** landmine
in `conftest.py`'s stub, not something this slice introduced, and it
affects any test in `safety_net/` that imports anything triggering the
stub and then calls `pytest.approx()`. `test_coldfootage.py` routes
around it with a small `math.isclose`-based comparator rather than a
byte-for-byte identical (and therefore equally fragile) numpy stub.

Segments-contract decisions the Lead should ratify: (1) `in_sec`/`out_sec`
are checked against the *un-handled* range for the "exceeds source
duration" rejection (handles only ever clamp, never trigger a rejection);
(2) segment order in the file is taken as final editorial order — unlike
`story_assembler`, nothing here re-sorts by source file or timestamp,
since there's no transcript to justify overriding the caller's order;
(3) sequence dimensions are probed from the first segment's source file,
falling back to 1920x1080@30 — there is no per-segment aspect choice in
this contract, matching the "no overlay" scope of this slice.

## The Project Manifest (`posthouse/manifest.py`)

The Project Manager's hard deliverable (ROADMAP.md §6 Phase 2): builds
and validates `manifest.json`, the one file every later role reads
blind, per the ratified contract at `docs/contracts/PROJECT_MANIFEST.md`.
This module is deliberately scope-limited to the manifest artifact
itself — it takes already-decided intake answers as structured Python
data (`build_manifest(...)`) and produces a manifest dict; it does not
run an interactive intake conversation or organize files on disk (that's
later Phase 2 work, gated behind "roles before shell").

**Two-moment validation.** `validate_manifest(manifest, mode)` runs one
rule set in two postures: `mode="intake"` reports every rule — including
everything in contract §4.1's REJECT list — as a warning and never
raises (`errors` is always `[]`, since the PM is still talking to Ryan
and the manifest is a legal draft); `mode="handoff"` promotes §4.1
violations to fatal `errors` while §4.2 stays advisory in `warnings`.
Both modes are exhaustive, not fail-fast, matching
`posthouse.coldfootage`'s validation pattern — every offender is
collected in one pass.

**Source IDs are minted once and frozen** (contract §5):
`mint_source_id(kind, display_name, existing_ids)` computes a fresh
`<kind>-<slug>-<NN>` id; `add_source(manifest, ...)` uses it to extend an
*existing* manifest without ever touching a prior source's id, so
renaming a folder at 11pm never orphans a downstream artifact that
already cited the old id. `delivery_targets` is deliberately not a
`build_manifest` parameter — the ratified ruling (contract §2.5) is that
the PM never proposes delivery targets, so the key is structurally
absent from a freshly built manifest, not written as `[]`.

```
python -m posthouse.manifest validate path/to/manifest.json --mode handoff
```

## The Brand Brief (`posthouse/brandbrief.py`)

Fonts, PDFs, and plain `.txt` files can't ride FCP7 XML into Premiere
(ROADMAP.md §7 "Fonts"; contract §4.3's "document"/"text" categories).
This module bridges that gap the way the Brand Brief Decision Log
describes: it reads what it can deterministically off the staged brand
files and delivers a contract §2.3-shaped `brand` dict plus two on-disk
artifacts, both written **inside** `assets_dir` — `BRAND_README.txt` and
a 1920x1080 `brand-card.png` (importable, readable in Premiere's source
monitor). The **co-location rule is enforced in code**, not just
documented: `generate_brief` has no parameter that can point either file
outside `assets_dir`, and `validate_brief_colocation` exhaustively
rejects a `brief.card_png_path`/`readme_path` that resolves outside it —
mirroring `posthouse.manifest`'s own rule 8 for the same field.

**Extraction, not guessing.** `build_brand_section(assets_dir)` scans
`assets_dir` recursively and, for every file, does the deterministic
thing the contract asks for: font `name`-table parsing via `fontTools`
(`extract_font_info` — `family_name` prefers nameID 16 over 1,
`style_name` 17 over 2, `postscript_name` is 6; a font whose table can't
be read degrades to `extracted_by: "filename"` with a best-effort family
guess, never dropped); `install_status` via a plain directory scan of the
real macOS font locations (no `fc-list`, matched by family or
postscript name, `"unknown"` only when the check itself can't run);
palette extraction via PIL with **fixed** quantization parameters
(`MEDIANCUT`, no dithering, no k-means) so the same logo always produces
the same palette, sorted by descending pixel count with an ascending-hex
tiebreak — full transparency is ignored, and role assignment
(primary/secondary/accent/neutral, by rank) is documented everywhere as
a starting point Ryan corrects, never an authoritative brand read;
`has_alpha` via a real PIL mode/`transparency` check. `logos[].kind` and
`documents[].kind` are filename-based best-effort guesses, same posture
the contract allows elsewhere. `documents[].unsupported_reason` reuses
`auto_include.unsupported_reason()` **verbatim** — the same discipline
`posthouse.manifest.categorize_unsupported` already follows, so PreCut
and the Post House give Ryan the same sentence. PDF summarization and the
frame-0 creative-brief marker are explicitly **not** in this slice —
`documents[].summarized` is always `False`, `brief.marker_written` is
always `False` (the exporter, not this module, writes that marker).

```
python -m posthouse.brandbrief build path/to/Brand\ Assets --client "Mendez Realty"
```

Writes `BRAND_README.txt` + `brand-card.png` inside the given
`assets_dir`, prints the `brand` section as JSON (optionally also to
`--out-json PATH`), and exits non-zero with every co-location problem
listed on stderr if the invariant is somehow violated. The same behavior
is available as a Python API: `build_brand_section(assets_dir)` ->
`generate_brief(brand, assets_dir, client_name=...)` ->
`validate_brief_colocation(brand, assets_dir)`.

Exits non-zero with every fatal error printed to stderr on a handoff-mode
failure (intake mode always exits 0 — warnings only). The same behavior
is available as a Python API via `validate_manifest`, which never raises
either way; a caller wanting "handoff with teeth" checks
`result.ok`/`result.errors` itself. `load_manifest`/`save_manifest`
round-trip a manifest to disk with the same write-tempfile-then-
`os.replace` atomic pattern as `precut_pipeline`'s own
`project.py:Project.save()`.

## The Project Manager (`posthouse/projectmanager.py`)

This is the module that completes the PM role's headless build (ROADMAP.md
§6 Phase 2 item 2): `organize_project(...)` ties `manifest.py` and
`brandbrief.py` together into the PM's actual entry point — raw footage
folders plus structured intake answers in, an organized, validated,
handoff-ready project out. No conversational intake and no UI (ROADMAP.md
ground rule 7); the intake answers arrive as a plain list of source
declarations (`{path, kind, dual_use?, notes?}`), matching the scope
`manifest.py` already set for this package.

**Footage is never copied or moved — the one load-bearing rule this
module is built around** (contract §2.3). The only `shutil.copytree` call
in the module is `_stage_brand_assets`, which copies the brand-assets
source directory into a NEW sibling folder under `project.root_dir`
(default name `"Brand Assets"`, matching the contract's own worked
example verbatim) — never anything from a declared footage source. Every
other touch of a source path is read-only (`Path.rglob`/`Path.stat` for
the census and shoot-date derivation). `_stage_brand_assets` refuses to
run at all if the brand-assets source equals, contains, or is contained
by any declared footage source, and asserts after copying that nothing
under `assets_dir` overlaps a footage source's path.

**Per-source census** (`media{}`, contract §2.4) is extension
classification via the harvested `auto_include.kind_for_path` — never
per-file ffprobe. A card dump can be thousands of files; this is an
intake snapshot, not analysis (that's Phase 4's job, on material this
role has already organized). Files `kind_for_path` can't classify count
toward `other_count` and get aggregated into `unsupported[]` by
extension through `manifest.categorize_unsupported` — the exact function
`manifest.py` already uses, so there is no second reason-string
implementation anywhere in the package.

**Inference** (`inference{}`, contract §2.4) calls the harvested
`camera_inference.infer_camera_tags` against each source's folder path
(matching camera-model subfolders the way PreCut's own docstring
describes) and tags `method` with the real PreCut pin, read from
`precut_bridge.PIN_FILE` rather than hardcoded. `agrees_with_declaration`
is a judgment call the ratified contract leaves open (it specifies the
field, not the comparison rule): a declared `aroll`/`source_audio` source
whose inferred tags include `drone`/`aerial`/`timelapse` disagrees (a
locked-off interview does not come from a drone); `broll`/`assets` always
agree, since B-roll can legitimately be anything. Recorded, never
authoritative — the declared `kind` always wins downstream.

**Shoot dates** (contract §2.2, ratified: read from files, no
confirmation step) come from every video file's creation timestamp:
`st_birthtime` where the platform provides it (macOS), falling back to
`st_mtime` where it doesn't (no `os.statx` call is made just to chase a
birthtime Linux doesn't expose through `os.stat_result`). Recomputed from
whatever is on disk on every run, so late footage naturally extends
`project.shoot_dates` without special-casing.

**Idempotent re-runs / late footage** (ratified: late footage is a new
revision): a declared source whose resolved path already matches one in
an existing `manifest.json` is left completely untouched — no recompute,
no re-mint. Only genuinely new source paths are appended via
`manifest.add_source`, which mints a fresh frozen id without touching any
prior one. Brand assets are always re-staged (`copytree(...,
dirs_exist_ok=True)` overwrites same-named files in place — refreshed,
not duplicated). `revision` follows `save_manifest`'s existing rule
(bumped on every write except the very first).

**The final gate.** Before anything is written, the assembled manifest
(handoff entry included) is run through
`manifest.validate_manifest(mode="handoff")`. A failure raises
`OrganizeError` carrying every error and writes nothing — an existing
`manifest.json` on disk is left exactly as it was.

```
python -m posthouse.projectmanager organize --root DIR --client NAME \
  --project NAME --type TYPE --source PATH:KIND[:dual_use] [--source ...] \
  [--assets DIR]
```

Exits non-zero listing every problem on stderr on failure. The same
behavior is available as a Python API: `organize_project(...)` returns an
`OrganizeResult` (`manifest`, `manifest_path`, `is_new_project`,
`added_source_ids`, `staged_asset_files`, `warnings`) or raises
`OrganizeError`.

Out of scope for this slice: an interactive intake conversation (ground
rule 7 — every answer arrives as structured input); recomputing census/
inference for a source already on the manifest from a prior run (it's
left untouched by design, per the idempotency rule above); anything
beyond the PM's own handoff (Assistant Editor consumption is Phase 4).
