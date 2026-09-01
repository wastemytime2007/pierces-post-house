# posthouse

`posthouse/` is Layer 2 of `docs/ARCHITECTURE.md`: the new app's Python
code, built as a *client* of PreCut rather than a fork of it. Everything
here either reuses a PreCut capability through `posthouse.precut_bridge`
(the third integration door — `precut_pipeline` imported as a library,
per ROADMAP.md's Decision Log) or is genuinely new logic that PreCut has
no equivalent for. This slice (Phase 1) ships the bridge itself, the
Cold Footage sequence builder, and the light-dependency harvest wrappers.

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
its own. Transcription, tagging/indexing, and audio sync need PreCut's
real ML venv (torch, whisper, lancedb, open_clip, anthropic) and, for
sync specifically, real footage even with that venv installed — none of
which exist in a cloud session — so they are deliberately *not* wrapped
here. `posthouse/harvest/DEFERRED.md` records each one's contract sketch
for whoever implements it in a Mac session; no stub modules are written,
because an importable stub that raises `NotImplementedError` is dead code
that invites an accidental silent-until-called import.

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
