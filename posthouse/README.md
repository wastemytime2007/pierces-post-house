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
