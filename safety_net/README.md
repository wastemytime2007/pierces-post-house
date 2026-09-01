# Phase 0 safety net — PreCut's XML exporter

Owner: whoever holds the QA/Test Engineer hat this session (see
`docs/TEAM.md`). This directory is the only thing that lets CLAUDE.md rule
1 ("no commits to `precut` until the Phase 0 safety net exists and
passes") be checked mechanically instead of by vibes.

## What this guards

The **exporter chain**: `python_backend/precut_pipeline/{multi_exporter,
exporter, bin_builders, cutlist, overlay, presets, theme_categories}.py`
(+ `markers.py`, with a caveat — see "Discoveries" below), the code that
turns a `CutList` into the one FCP7 XML Premiere imports. Specifically:

1. **Import gate** (`test_import_gate.py`) — the exporter chain stays
   importable with nothing but the Python 3.11 standard library, so this
   safety net (and any future cloud agent session) never needs PreCut's
   full ML venv (lancedb, torch, whisper, open_clip, anthropic) just to
   touch the export path.
2. **Golden master** (`test_exporter_golden.py`) — a synthetic two-sequence
   project (A-roll phrases, a 5-clip B-roll library with a real SQLite
   index, markers, one overlay style) exports to XML and the normalized
   output is diffed byte-for-byte against a blessed snapshot. This is the
   thing that catches "someone changed the exporter and Premiere now
   rejects the file" before it ships.
3. **FCP7 quirks** (`test_fcp7_quirks.py`) — one targeted assertion per
   numbered item in `precut/DECISIONS.md` § "FCP7 XML details that were
   expensive to learn." These exist because the golden-master diff alone
   would tell you *something* changed, not *which expensive lesson* got
   un-learned.

## How to run

```bash
PRECUT_ROOT=/path/to/precut ./safety_net/run_safety_net.sh
```

Defaults to `PRECUT_ROOT=/home/user/precut` if unset. Requires Python 3.11,
`pytest` (`pip install pytest`), and `ffmpeg`/`ffprobe` on PATH — nothing
else. `PYTHONHASHSEED=0` is pinned inside the script (belt and suspenders;
see "Normalizations" below for why it mostly doesn't matter here).

## Fixtures

`fixtures/media/` holds ~1.4 MB of tiny (4s, 640x360, 30fps, h264)
deterministic ffmpeg-generated clips, **committed to the repo**:
`stable.mp4`, `shaky.mp4`, `blurred.mp4`, `underexposed.mp4`,
`overexposed.mp4`, `AROLL_01.MOV` (the one clip with an audio stream, saved
with an uppercase extension on purpose), and `lav.wav`. `MANIFEST.json`
records the ffmpeg/ffprobe version used to generate them plus each file's
probed duration/dims/fps/audio specs, so if a test ever behaves differently
on another machine, the first thing to check is whether `MANIFEST.json`
still matches a fresh `ffprobe` of the committed files (encoder drift
across ffmpeg versions is exactly the failure mode this manifest exists to
catch, per the "why commit media" note below).

**Why commit the generated media instead of regenerating it on every run:**
hermetic beats regeneratable here. A different ffmpeg/libx264 build can
encode `testsrc2` to a file with a very slightly different `nb_frames` or
container duration than the one this safety net was blessed against —
which is precisely the kind of drift the exporter's own `_safe_probe` logic
(Drop 4.30, Drop 4.34) exists to be sensitive to. Committing the actual
bytes means the golden master is testing "did the exporter change," not
"did the exporter change or did ffmpeg change."

To regenerate (only do this deliberately, and re-bless afterward):

```bash
python3 safety_net/fixtures/generate_fixtures.py
```

## Re-blessing the golden snapshot

```bash
BLESS=1 PRECUT_ROOT=/path/to/precut python3 -m pytest safety_net/tests/test_exporter_golden.py
```

**Re-blessing requires a Decision Log entry in `ROADMAP.md`** (per CLAUDE.md
rule 3, the Decision Log is append-only law) stating what changed in the
exporter and why the new output is correct — not just "test was failing."
A golden-master snapshot is a claim about what Premiere will accept; changing
it silently is exactly the "silent regression shipped for a month" failure
mode ROADMAP.md §2 cites as the reason this safety net exists at all.

## The sabotage check

Deliverable of this Phase 0 build, run once and reported (not part of the
regular test suite — there's no committed "sabotaged precut" fixture,
because the whole point is that a real regression is caught, not a fake
one that's permanently wired to pass):

1. Copy the real `precut` checkout to a scratch directory.
2. In the copy ONLY, reintroduce DECISIONS.md quirk 4's historical bug:
   append `<out>{duration_frames - 1}</out>` to each B-roll library master
   clip in `multi_exporter._build_broll_master_for_entry`.
3. `PRECUT_ROOT=<scratch copy> ./safety_net/run_safety_net.sh` — confirm
   `test_multi_timeline_export_matches_golden_master` AND
   `test_quirk4_library_clip_out_never_off_by_one` both FAIL.
4. `PRECUT_ROOT=/home/user/precut ./safety_net/run_safety_net.sh` — confirm
   everything PASSES again against the real, unmodified checkout.
5. Delete the scratch copy.

Result when this was last run: both tests failed against the sabotaged
copy (`<out>119</out> != <duration>120</duration>`) and the full suite
passed clean against the real checkout immediately after. See the session
report for the exact transcript.

To re-run this check yourself against a different sabotage, steps 1-2 are
the only manual part — pick any of the six numbered quirks in
`precut/DECISIONS.md` and edit the corresponding code path in the scratch
copy; `test_fcp7_quirks.py` should catch it (quirks 1-5) or the golden
diff will (any of them, since a real regression changes the XML).

## Scoped out (and why)

- **Lav/audio sync.** `ExportRequest.audio_sync_state` stays `None` in the
  golden fixture. `audio_sync.py`'s cross-validation requires an MFCC match
  score above `SCORE_USE=10.0`; synthetic sine-tone audio doesn't clear
  that floor. Exercising the sync path here would either skip it silently
  or bless an XML that *looks* synced but proves nothing about real sync
  behavior. Lav sync is a Tier-2 check: real footage, Ryan's Mac only (see
  `ROADMAP.md` "Where things run").
- **`markers.py` in the stdlib-only import gate.** See "Discoveries" below
  — it's covered by the golden master (with a documented stub) but fails
  the strict stdlib-only claim, so `test_import_gate.py` records that
  fact explicitly instead of quietly dropping it from the chain list.
- **DB migrations (quirk 6).** Lives entirely in `database.py`, which needs
  lancedb/numpy/pyarrow for real. Skipped with reason; Ryan's Mac only.
- **Full 35-module backend import gate.** Needs the real venv (lancedb,
  torch, whisper, open_clip, PIL, rich, anthropic). Skipped with reason;
  Ryan's Mac only, per `precut/ARCHITECTURE.md` "Runtime environment."
- **Quirk 5, as DECISIONS.md literally states it.** The doc says
  `duration_frames = duration_sec * fps, rounded` and "do not use
  ffprobe's nb_frames" — Drop 4.30 in the shipped code does the opposite
  on purpose (prefers `nb_frames`, exactly because the rounded formula can
  disagree with Premiere's own probe by a frame). This safety net asserts
  the CURRENT guarantee (live probe wins over stale cached values), not
  the stale wording, and that resolution is recorded in `ROADMAP.md`'s
  Decision Log per the architecture review that caught it.

## Discoveries worth a second look

1. **`markers.py` isn't actually stdlib-only.** It's listed as part of the
   "stdlib-only exporter chain," but `from .database import Database` and
   `from .transcriber import Phrase` at module scope pull in
   lancedb+numpy+pyarrow and torch respectively — and `exporter.py`'s
   `_build_markers()` / `_build_attached_markers()` both lazily
   `from .markers import format_marker_name, format_marker_comment`
   whenever a `CutList` carries any `BRollMarker`. So the exporter chain
   is stdlib-only only for marker-less cuts. `conftest.py` documents this
   in full ("The markers.py surprise") and works around it with narrowly
   scoped, inert `sys.modules` stubs (real packages are used instead if
   they're actually installed, so this is a no-op on Ryan's Mac).
   `test_import_gate.py` separately proves, in a clean subprocess, that
   `markers.py` really doesn't import without those packages — the
   discovery is recorded, not hidden by the workaround that routes around
   it elsewhere.
2. **Placeholder-bin PNGs leak `PRECUT_ROOT` into the XML.** The five
   empty-bin placeholders (Final/Music/SFX/Nested Seqs/Colors) reference
   `<PRECUT_ROOT>/python_backend/precut_pipeline/placeholders/placeholder_*.png`
   directly — `export_multi_timeline` never copies them next to the output
   XML the way it does for the overlay PNG. That path is
   machine-/checkout-location-dependent, so the golden master normalizes
   it to `{PRECUT_ROOT}` exactly like the temp project root. A snapshot
   blessed without this normalization would only ever match on the machine
   (and directory) it was blessed on.
3. **`_build_library_bin` is dead code.** `multi_exporter.py` has a whole
   second, fully-worked library-bin builder (`_build_library_bin`,
   ~140 lines) that contains the literal quirk-4 bug pattern
   (`<out>{duration_frames - 1}</out>`) — and is never called anywhere in
   the codebase (verified: `grep -rn _build_library_bin` across all of
   `precut` finds only its own definition). The live path
   (`_build_broll_master_for_entry` → `bin_builders.build_aroll_master_clip`)
   doesn't emit an `<out>` on library master clips at all anymore.
   `test_quirk4_library_clip_out_never_off_by_one` is written to hold
   either way (absent `<out>` passes; a reintroduced one must equal
   `<duration>`) — see that test's docstring and the sabotage check above,
   which reintroduces exactly this bug on the LIVE path to prove the test
   isn't vacuous.

## Normalizations applied before comparing XML

All in `conftest.py::normalize_xml_text`, because a golden-master diff is
only as good as the things it doesn't get fooled by:

- **Temp project root**, both as a plain path and as it appears
  percent-encoded inside `file://` URLs (`path_to_url()` runs
  `urllib.parse.quote()` over the *entire* absolute path, so the root
  segment gets percent-escaped too — a plain string replace of the
  unencoded root misses those).
- **`PRECUT_ROOT`**, same treatment, for the placeholder-PNG paths
  (discovery #2 above).
- **UUIDs** — every master `<clip>` gets a fresh `uuid.uuid4()` per run.
- **Re-pretty-printed via minidom** with a fixed indent before comparing,
  so incidental whitespace differences (e.g. across Python patch versions)
  never register as a diff.
- **Exactly one overlay style** in the golden fixture (not a normalization
  so much as an avoidance): `export_multi_timeline` iterates a *set* of
  overlay styles when assigning `masterclip-N`/`file-N` ids, and Python
  set iteration order for a multi-element set isn't guaranteed stable
  across runs/versions. A one-element set has only one possible order.
  `PYTHONHASHSEED=0` is pinned in `run_safety_net.sh` too, belt and
  suspenders, but the real fix is the single-style fixture — hash seed
  pinning alone would not save a fixture that used two or more styles.
- No timestamps appear anywhere in the exporter's output (checked: no
  `datetime`/`time.time()`/`strftime` calls in the chain) — the `<timecode>`
  blocks are always the literal string `00:00:00:00`, so there was nothing
  to neutralize there.
