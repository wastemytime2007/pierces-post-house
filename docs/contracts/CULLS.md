# `culls.json` — contract v1

Owner: Lead Architect (`docs/TEAM.md`). Status: **draft, awaiting Ryan's
ratification** (§8). Governs the artifact only — the detection design
that produces it is `docs/design/PHASE4_CULL_DESIGN.md`.

## 1. Purpose and lifecycle

The Assistant Editor's technical-cull deliverable: the one file saying
*which ranges of which clips are usable, why each one opened and closed,
and what was thrown away and for what reason.* It is the input to the
Cold Footage sequence build and to the benchmark scorer, and it is the
artifact a human opens when the cull did something surprising.

| | |
| --- | --- |
| **Writer** | The Assistant Editor's cull skill (`posthouse.cull`). Nobody else writes it. Regenerated whole on every run — never patched in place. |
| **Readers** | `posthouse.coldfootage` (builds the timeline), `posthouse.benchmark.load_culls` (scores it), the grouping skill (Phase 4), the Creative Editor (Phase 6), a human. |
| **Location** | `<project.root_dir>/analysis/culls.json`, with per-source signal sidecars under `<project.root_dir>/analysis/signals/` (§6). `analysis/` is a new sibling of the footage folders and `Brand Assets`, consistent with the manifest's co-location ruling (PROJECT_MANIFEST §2.3). |
| **Encoding** | UTF-8 JSON, 2-space indent, keys in the order given below. Written tempfile-then-`os.replace`, exactly as the manifest and `project.py:Project.save()` do. |
| **Versioning** | `contract_version: 1` — **the same version as `posthouse.coldfootage`'s segments contract, deliberately.** See §3. |
| **Determinism** | Same inputs + same parameter set ⇒ byte-identical file, except `created_at`. No RNG anywhere in the cull. |

## 2. The relationship to the segments contract (the load-bearing rule)

`posthouse.coldfootage`'s module docstring says its segments schema
"should be read as a proposal for [`culls.json`'s] shape, not a parallel
one," and `posthouse.benchmark.load_culls` reads a culls file **through
`coldfootage.validate_segments_shape`** — one shared function, added
specifically so "Phase 4's contract bump cannot silently fork them"
(Decision Log, 2026-09-01, harness review).

So the rule for this contract is:

> **A `culls.json` IS a segments file.** Everything this document adds is
> a new *optional* key that both existing consumers already ignore. A
> file valid under this contract is, without translation, a file
> `coldfootage.build_coldfootage_xml()` and `benchmark.load_culls()` both
> accept today, unmodified.

### 2.1 `contract_version` stays `1`, and here is the proof

`validate_segments_shape` (coldfootage.py) checks exactly four things:

1. `data["contract_version"] == 1`,
2. `data["sequence_name"]` is a non-empty `str`,
3. `data["segments"]` is a non-empty `list`,
4. per segment: `source_path` (non-empty str, exists at build time),
   `in_sec` / `out_sec` (real numbers, `in_sec < out_sec`), and — in
   `coldfootage._validate_and_resolve` only — `[in_sec, out_sec]` inside
   the ffprobe'd duration. `label` and `handle_sec` are read with
   defaults. **Unknown keys are ignored at every level.**

`benchmark.load_culls` adds one check on top: an optional per-segment
`ruleset` must be `"narrative"` or `"visual"`.

Every addition in §4 is a new key with a documented default, at the top
level or inside a segment. Nothing required is removed, nothing is
renamed, no enum is narrowed. That is precisely the manifest's
additive-only test (PROJECT_MANIFEST §1, "Versioning"), so **no bump, no
migration.**

The version bumps to `2` only if one of these ever becomes necessary:
making `source_id`/`rel_path` required *of the segments contract itself*
(not just of a culls file), changing the meaning of `handle_sec`, or
allowing `segments` to be empty. If that day comes, the migration is:
readers accept `1` and `2`, `coldfootage.CONTRACT_VERSION` becomes a
set, and this section records the field-by-field delta. Do not bump
speculatively.

### 2.2 One master file, per-ruleset views for the builder

Dual-use A-roll is culled twice (ROADMAP §4), so one project's cull
produces two overlapping select sets. They live in **one master
`culls.json`** because the benchmark scores both from one file and
because a human should not have to diff two files to see what one clip
produced.

But `coldfootage` lays `segments` **in list order, back to back** (its
ratified ruling: "segment order in the file is final — the producer
sorts, the builder lays"), so building the master directly would emit
one timeline containing narrative selects followed by visual selects —
well-defined, but not what anyone wants. Therefore:

- The master's `segments` are sorted by `(ruleset, source_id, in_sec)` —
  ruleset-major, so a direct build is at least coherent.
- The cull additionally writes **per-ruleset views**:
  `analysis/culls.narrative.json` and `analysis/culls.visual.json`,
  byte-identical in shape, each holding one ruleset's segments and its
  own `sequence_name` (`"Cold Footage: Narrative"` /
  `"Cold Footage: Visual"`). These are what gets handed to the builder.
  Sequence names land in Premiere's project panel, so they are published
  copy under Ryan's style rule: a colon, never an em dash.
- A view is a *projection*, never an independent source of truth: it
  carries the same `manifest_id`/`manifest_revision`/`cull_id` stamps,
  and regenerating the master regenerates both views in the same run.

A project with no dual-use sources still gets both files; one of them may
have an empty `segments` list, which makes it **invalid as a segments
file** (rule 3 above) — so in that case the view is **not written at
all**, and `counts.by_ruleset` records the zero. Never write an
unbuildable view.

## 3. Coordinates, and how a segment names its footage

Per PROJECT_MANIFEST §5 ("Addressing a file"), downstream artifacts cite
`{source_id, rel_path}` and absolute paths live in exactly one place —
`sources[].path` in the manifest. But `coldfootage` and `load_culls`
both read an absolute `source_path`. Both are true here, and the
precedence rule is explicit:

- **`source_id` + `rel_path` are the identity.** They are what survives
  Ryan renaming a folder or remounting a drive, and they are what
  `groups.json` and every later artifact join on.
- **`source_path` is the resolution**, computed by the cull at write time
  as `manifest.sources[source_id].path / rel_path` (POSIX join;
  `rel_path` is `""` when the source is `is_file: true`, in which case
  `source_path` is the source's `path` itself).
- **On any disagreement, identity wins and the file is stale.** A reader
  that has the manifest MUST re-resolve `source_id`+`rel_path` and,
  if the result differs from the stored `source_path`, treat the culls
  file as stale (§7 REJECT 6) rather than silently trusting either.
  A reader that does *not* have the manifest (the benchmark scorer,
  which is deliberately manifest-free) uses `source_path` as-is — which
  is exactly why it is carried.

All times are **seconds, float, relative to the source file's own
timeline**, frame-aligned to the source's frame rate (see `frame_in` /
`frame_out`, §4.3) — never timecode, never timeline position. This
matches `Range` in `benchmark.py` and the segments contract.

## 4. Schema

Types as in PROJECT_MANIFEST §2: `str`, `int`, `num`, `bool`, `[T]`,
`{…}`, `ts` = ISO-8601 UTC. "Req" = required for this contract (all of
these are optional to the *segments* contract by construction, §2.1).
Absent optional fields are absent, not `null`.

### 4.1 Top level

| Field | Type | Req | Default | Meaning |
| --- | --- | --- | --- | --- |
| `contract_version` | int | ✓ | `1` | Always `1`. §2.1. |
| `sequence_name` | str | ✓ | — | Non-empty. Master: `"Cold Footage: <project.name>"`. Views: see §2.2. Required by the shared validator, so it is required here. No em dashes: this string is a Premiere sequence name (published copy). |
| `cull_id` | str | ✓ | uuid4 | Identity of *this cull run*. The views carry the master's. Lets a report, a sidecar, and a timeline be traced to one run. |
| `created_at` | ts | ✓ | — | When the run finished. |
| `generator` | {…} | ✓ | — | `{name: "posthouse.cull", version, precut_pin, ffmpeg_version, numpy_version}` — the exact stack, because a signal is only reproducible against a known ffmpeg. `precut_pin` is `posthouse/PRECUT_PIN`. |
| `manifest_id` | str | ✓ | — | Copied from the manifest. |
| `manifest_revision` | int | ✓ | — | The revision the cull read. PROJECT_MANIFEST §5: "that pair is how a stale artifact gets caught instead of silently trusted." |
| `params` | {…} | ✓ | — | §4.2. The full fitted parameter set that produced this file. |
| `sources` | [{…}] | ✓ | — | §4.4. One entry per analysed **file** (not per manifest source folder). Non-empty. |
| `segments` | [{…}] | ✓ | — | §4.3. The accepted selects, sorted per §2.2. Non-empty (a run that accepted nothing writes no culls file and exits non-zero — §7). |
| `rejections` | [{…}] | ✓ | `[]` | §4.5. **Top level, never inside `segments`** — anything inside `segments` becomes a clip on a timeline. ROADMAP §4: "Every rejection carries its reason; nothing is silently dropped." |
| `counts` | {…} | ✓ | — | `{sources_analysed, segments_accepted, rejections, accepted_sec, analysed_sec, by_ruleset: {<ruleset>: {segments, sec}}}` — derivable, stored anyway so a human reads the shape of the result without arithmetic. Validated for consistency (§7 WARN 1). |

### 4.2 `params` — the fitted thresholds, carried with the output

*Rationale: ROADMAP §5 — "Every threshold is a parameter to be FIT
against the benchmark, never hand-set." A culls.json that does not say
which parameter set produced it cannot be reproduced or compared, and a
score against it means nothing.*

| Field | Type | Req | Meaning |
| --- | --- | --- | --- |
| `params_id` | str | ✓ | Id of the parameter set (`fit-<date>-<NN>` or `default-v1`). |
| `fit_provenance` | str | ✓ | `"fitted:<benchmark_id>:<fold>"` \| `"default"` \| `"manual"`. `"manual"` is legal but is a WARN (§7) — it means someone hand-set a threshold. |
| `analysis` | {…} | ✓ | `{plane_width, plane_height, plane_format: "gray", decode: "hwaccel_videotoolbox"\|"software", source_grade: "original"\|"analysis_decode", audio_sr}` — **`source_grade` must be `"original"` or `"analysis_decode"`. A culls file produced from PreCut's CRF-28 proxies is contractually invalid** (§7 REJECT 8), per ROADMAP §4. |
| `visual` | {…} | ✓ | The visual ruleset's fitted parameters, verbatim as fitted. Shape is the design doc's business, not this contract's; it is stored as an opaque object of scalars so a bump in the detector's parameter list is not a contract change. |
| `narrative` | {…} | ✓ | Likewise for the narrative ruleset. |

### 4.3 `segments[]` — one accepted select

The first five fields are the segments contract, unchanged. Everything
below the rule is additive.

| Field | Type | Req | Default | Meaning |
| --- | --- | --- | --- | --- |
| `source_path` | str | ✓ | — | **Segments contract.** Absolute, resolved (§3). |
| `in_sec` | num | ✓ | — | **Segments contract.** Pre-handle in point. |
| `out_sec` | num | ✓ | — | **Segments contract.** Pre-handle out point. `in_sec < out_sec`. The ratified ruling stands: *validation applies to the pre-handle range.* |
| `label` | str | — | `""` | **Segments contract.** Becomes the `ARollPhrase` text on the timeline, so it is written for a human reading a Premiere clip name: `"pan · visual · 3.9s"`. Never load-bearing — every machine-readable fact has its own field. |
| `handle_sec` | num | — | `1.0` | **Segments contract.** The *requested* symmetric handle the builder applies and then clamps to source bounds. |
| — | | | | |
| `segment_id` | str | ✓ | — | `<source_id>-<ruleset[0]><NNNN>`, e.g. `broll-osmo-01-v0007`. Stable within a run; **not** stable across runs (a re-cull is a new opinion, not an edit). |
| `source_id` | str | ✓ | — | Manifest source id (PROJECT_MANIFEST §5 regex). |
| `rel_path` | str | ✓ | — | POSIX-relative to that source's `path`; `""` when `is_file: true`. |
| `ruleset` | str | ✓ | — | `"narrative"` \| `"visual"`. **Required here** even though `load_culls` treats it as optional, because a culls file with unlabelled segments cannot be scored per-ruleset and a dual-use source's two lives become indistinguishable. |
| `frame_in` / `frame_out` | int | ✓ | — | The same boundary in source frames, `in_sec = frame_in / fps`. Carried because seconds at 29.97 do not round-trip and the sidecar (§6) is indexed by frame. `frame_out` is exclusive. |
| `handle_in_sec` / `handle_out_sec` | num | ✓ | — | The handle actually *available* on each side after clamping to `[0, duration]` — asymmetric at clip edges. Informational: the builder still computes its own clamp from `handle_sec`. A `0.0` here tells the lead editor there is no pre-roll to slip into. |
| `motion_intent` | str | ✓ | — | The single intent held across the whole select (criterion 1): `static` \| `pan_left` \| `pan_right` \| `tilt_up` \| `tilt_down` \| `push_in` \| `pull_out` \| `roll` \| `drift`. `drift` = a consistent slow handheld wander with no dominant axis; it is a legal intent, not a defect. Never a compound. |
| `motion_confidence` | num | — | — | `0..1`. How cleanly the window fits that one intent (design doc §2). |
| `boundary_reason_in` | str | ✓ | — | Why it opened: `clip_start` \| `motion_change` \| `settle` \| `focus_regained` \| `exposure_recovered` \| `audio_start` \| `speech_start` \| `prior_reject_ended`. |
| `boundary_reason_out` | str | ✓ | — | Why it closed: `clip_end` \| `motion_change` \| `shake_onset` \| `focus_lost` \| `focus_hunt` \| `exposure_fault` \| `audio_fault` \| `speech_end` \| `recompose`. |
| `scores` | {…} | ✓ | — | Per-rule, `0..1`, higher is better, each independently interpretable: `{motion_consistency, focus, exposure, audio?, overall}`. `audio` is present iff `ruleset == "narrative"`. `overall` is the min of the present rules, not a mean — a select is only as good as its worst gate, and a mean lets a great motion score hide a focus problem. |
| `focus` | {…} | ✓ | — | `{shape: "steady"\|"rack_in"\|"rack_out", lapvar_median: num, lapvar_normalized: num, motion_adjusted: bool}`. `shape` is the criterion-2 verdict; `lapvar_normalized` is per-clip normalized (design doc §1.4 — an absolute threshold is impossible on this footage: measured medians on the benchmark clip run 100 on a dark interior select to 4890 on a sharp exterior select, both accepted by Ryan). `rack_*` names the direction the focus moved. |
| `exposure` | {…} | ✓ | — | `{mean_luma: num, clip_low_frac: num, clip_high_frac: num}` — worst-frame values over the select, on the 0–255 analysis plane. |
| `audio` | {…} | — | — | Narrative only: `{peak_dbfs, rms_dbfs, speech_frac, clipped_frac, source: "embedded"\|"<source_id>/<rel_path>"}` — `source` names which audio was measured, because a synced lav is not the camera track. |
| `notes` | str | — | `""` | Free text from the cull, for anything the schema has no field for. Never parsed. |

### 4.4 `sources[]` — one analysed file

*Rationale: the per-file facts (duration, fps, where its signals live)
belong once, not repeated on every segment.*

| Field | Type | Req | Meaning |
| --- | --- | --- | --- |
| `source_id` | str | ✓ | Manifest source id. |
| `rel_path` | str | ✓ | File within that source. |
| `source_path` | str | ✓ | Resolved absolute path (§3). |
| `kind` | str | ✓ | Copied from the manifest source: `aroll` \| `broll` \| `source_audio` \| `assets`. |
| `dual_use` | bool | ✓ | Copied from the manifest. `true` ⇒ this file appears under both rulesets. |
| `rulesets_run` | [str] | ✓ | Which rulesets actually ran on it. `["visual"]` for B-roll, `["narrative"]` for plain A-roll, `["narrative","visual"]` for dual-use. Recorded so "no visual selects" is distinguishable from "the visual ruleset never ran." |
| `duration_sec` | num | ✓ | ffprobe'd. |
| `fps` | num | ✓ | Exact (`30000/1001` ⇒ `29.97002997`), stored as the float actually used for `frame_in`/`frame_out`. |
| `width` / `height` | int | ✓ | Native. |
| `analysed_frames` | int | ✓ | Frames the signal pass actually produced. A gap between this and `duration_sec * fps` is a decode problem, not a rounding one (§7 WARN 3). |
| `signals_path` | str | ✓ | Path to the sidecar, **relative to the culls file's directory** (§6). |
| `signals_sha256` | str | ✓ | Of the sidecar, so a report can prove it is looking at the signals that produced these segments. |
| `analysis_sec` | num | ✓ | Wall seconds the signal pass took on this file. Runtime accounting (ROADMAP §7, "Runtime and cost"). |

### 4.5 `rejections[]` — everything that did not become a select

*Rationale: ROADMAP §4 — nothing is silently dropped. This is the field a
human reads when the cull "missed" something; recall is the metric that
matters most (§5), so the misses must be inspectable, not inferred from
the gaps between selects.*

Every second of every analysed file is accounted for: the union of
`segments` (pre-handle) and `rejections` covers `[0, duration_sec]` for
each `(source_id, rel_path, ruleset)` pair with no gaps and no overlaps.
This is a validated invariant (§7 REJECT 7) and it is the whole point —
an unaccounted second means the cull has an opinion it did not write
down.

| Field | Type | Req | Meaning |
| --- | --- | --- | --- |
| `source_id` / `rel_path` / `source_path` | str | ✓ | As §4.3. |
| `ruleset` | str | ✓ | Which ruleset rejected it. The same seconds can be rejected by one ruleset and accepted by the other — that is dual-use working correctly. |
| `in_sec` / `out_sec` | num | ✓ | The rejected range. |
| `frame_in` / `frame_out` | int | ✓ | As §4.3. |
| `reason` | str | ✓ | `shake` \| `motion_inconsistent` \| `focus_hunt` \| `soft` \| `underexposed` \| `overexposed` \| `too_short` \| `audio_clipped` \| `audio_dead` \| `no_speech` \| `settle` \| `transition` \| `record_tap` \| `undecidable`. One reason, the dominant one. |
| `reason_detail` | str | — | Human sentence naming the number that decided it: `"motion residual 3.8 px/frame exceeds fitted 1.4 for 2.3s"`. Written for Ryan, not for a parser. |
| `secondary_reasons` | [str] | — | Other gates this range also failed, from the same enum. Present so "it was rejected for shake" is not read as "it was otherwise fine." |
| `scores` | {…} | — | Same shape as §4.3's, for the rejected range. Present unless the range was never scored (e.g. `record_tap`). |
| `duration_sec` | num | ✓ | Convenience; the report sorts by it to surface the largest misses. |

`too_short`, `settle` and `transition` are the three high-volume reasons
and they are **merged into runs** rather than emitted per frame — a
rejection list must stay readable. The invariant in the intro is over
merged runs.

## 5. Worked example — the Runnells benchmark clip

Real numbers, from the answer key and from a signal pass over
`DJI_20260430075045_0006_D.MP4` (3840×2160 HEVC Main 10, 30000/1001 fps,
235.30s, 7052 frames, 67.3 Mbit/s — ffprobe'd 2026-09-01).

Ryan's selects **#3 and #4** are the clearest illustration of criterion 1
in his own marking. #3 runs 14.98–18.85 and #4 runs 19.19–21.29 — a
0.34s gap between them, which the harness review already flagged as
real, not noise (7 of 25 gaps in his key are under 2s). The measured
signals say why there are two selects and not one:

- 14.98–18.85: mean horizontal shift **−5.84 px/frame** on the 480-wide
  analysis plane (≈ 47 px/frame at 3840, ≈ 1400 px/s), vertical **+0.07**
  — a horizontal pan through the storage room, held to one axis for
  3.87s. Median Laplacian variance 1944.
- 19.19–21.29: horizontal **−0.09**, vertical **−2.78** — the axis has
  swapped. Frames at 19.3s and 21.2s confirm it visually: the camera
  tilts down off the shelving to the floor.
- The 0.34s between them is the operator changing axis. It is in neither
  select, and it appears in `rejections` as `transition`.

That is a `motion_change` boundary, not a shake onset, and it is exactly
what §4 of the roadmap asks the segmenter to find.

```json
{
  "contract_version": 1,
  "sequence_name": "Cold Footage: Runnells Day 1",
  "cull_id": "3f2a91c0-7b4e-4f2a-9a1d-0c5e8b7d2211",
  "created_at": "2026-09-02T03:14:52Z",
  "generator": {
    "name": "posthouse.cull", "version": "0.1.0", "precut_pin": "e035fbaf",
    "ffmpeg_version": "8.1", "numpy_version": "1.26.4"
  },
  "manifest_id": "b2c7e0a4-55d1-4d0e-9c31-8a7f6e2b1c44",
  "manifest_revision": 3,
  "params": {
    "params_id": "fit-20260902-01",
    "fit_provenance": "fitted:runnells-day-1:blockcv-3",
    "analysis": {
      "plane_width": 960, "plane_height": 540, "plane_format": "gray",
      "decode": "hwaccel_videotoolbox", "source_grade": "original",
      "audio_sr": 48000
    },
    "visual": {
      "static_eps_px_per_frame_4k": 3.9, "motion_residual_max": 1.42,
      "smooth_window_frames": 21, "settle_frames": 8,
      "min_duration_sec": 1.15, "state_change_penalty": 7.5,
      "focus_norm_quantile": 0.35, "focus_hunt_sign_changes_per_sec": 2.4,
      "rack_min_ramp_frames": 18, "clip_low_frac_max": 0.31,
      "clip_high_frac_max": 0.06
    },
    "narrative": {
      "min_duration_sec": 1.5, "speech_frac_min": 0.35,
      "peak_dbfs_max": -1.0, "rms_dbfs_min": -48.0,
      "shake_residual_max": 4.1, "clip_low_frac_max": 0.55
    }
  },
  "sources": [
    {
      "source_id": "aroll-osmo-01",
      "rel_path": "DJI_20260430075045_0006_D.MP4",
      "source_path": "/Volumes/RDOSS_2025/SoldFast 2026/10050 NE University Ave Runnells/First Walkthrough After Taking Over/Osmo/DJI_20260430075045_0006_D.MP4",
      "kind": "aroll",
      "dual_use": true,
      "rulesets_run": ["narrative", "visual"],
      "duration_sec": 235.301733,
      "fps": 29.97002997,
      "width": 3840, "height": 2160,
      "analysed_frames": 7052,
      "signals_path": "signals/aroll-osmo-01/DJI_20260430075045_0006_D.MP4.signals.npz",
      "signals_sha256": "9a1f…c7d2",
      "analysis_sec": 33.6
    }
  ],
  "segments": [
    {
      "source_path": "/Volumes/RDOSS_2025/SoldFast 2026/10050 NE University Ave Runnells/First Walkthrough After Taking Over/Osmo/DJI_20260430075045_0006_D.MP4",
      "in_sec": 14.98,
      "out_sec": 18.85,
      "label": "pan · visual · 3.9s",
      "handle_sec": 1.0,
      "segment_id": "aroll-osmo-01-v0003",
      "source_id": "aroll-osmo-01",
      "rel_path": "DJI_20260430075045_0006_D.MP4",
      "ruleset": "visual",
      "frame_in": 449, "frame_out": 565,
      "handle_in_sec": 1.0, "handle_out_sec": 1.0,
      "motion_intent": "pan_left",
      "motion_confidence": 0.91,
      "boundary_reason_in": "settle",
      "boundary_reason_out": "motion_change",
      "scores": {
        "motion_consistency": 0.88, "focus": 0.79,
        "exposure": 0.94, "overall": 0.79
      },
      "focus": {
        "shape": "steady", "lapvar_median": 1943.5,
        "lapvar_normalized": 0.71, "motion_adjusted": true
      },
      "exposure": {"mean_luma": 106.1, "clip_low_frac": 0.04, "clip_high_frac": 0.01}
    },
    {
      "source_path": "/Volumes/RDOSS_2025/…/DJI_20260430075045_0006_D.MP4",
      "in_sec": 19.19,
      "out_sec": 21.29,
      "label": "tilt down · visual · 2.1s",
      "handle_sec": 1.0,
      "segment_id": "aroll-osmo-01-v0004",
      "source_id": "aroll-osmo-01",
      "rel_path": "DJI_20260430075045_0006_D.MP4",
      "ruleset": "visual",
      "frame_in": 575, "frame_out": 638,
      "handle_in_sec": 1.0, "handle_out_sec": 1.0,
      "motion_intent": "tilt_down",
      "motion_confidence": 0.86,
      "boundary_reason_in": "motion_change",
      "boundary_reason_out": "motion_change",
      "scores": {
        "motion_consistency": 0.84, "focus": 0.77,
        "exposure": 0.92, "overall": 0.77
      },
      "focus": {
        "shape": "steady", "lapvar_median": 1835.5,
        "lapvar_normalized": 0.68, "motion_adjusted": true
      },
      "exposure": {"mean_luma": 95.7, "clip_low_frac": 0.06, "clip_high_frac": 0.00}
    }
  ],
  "rejections": [
    {
      "source_id": "aroll-osmo-01",
      "rel_path": "DJI_20260430075045_0006_D.MP4",
      "source_path": "/Volumes/RDOSS_2025/…/DJI_20260430075045_0006_D.MP4",
      "ruleset": "visual",
      "in_sec": 18.85, "out_sec": 19.19,
      "frame_in": 565, "frame_out": 575,
      "reason": "transition",
      "reason_detail": "axis change: horizontal −5.84 px/frame to vertical −2.78 px/frame over 10 frames; neither intent held long enough to open a select",
      "duration_sec": 0.34
    },
    {
      "source_id": "aroll-osmo-01",
      "rel_path": "DJI_20260430075045_0006_D.MP4",
      "source_path": "/Volumes/RDOSS_2025/…/DJI_20260430075045_0006_D.MP4",
      "ruleset": "visual",
      "in_sec": 46.45, "out_sec": 66.47,
      "frame_in": 1392, "frame_out": 1992,
      "reason": "motion_inconsistent",
      "reason_detail": "20.0s of walking coverage: smoothed motion residual 2.44 px/frame against fitted max 1.42, no intent held for 1.15s",
      "secondary_reasons": ["soft"],
      "scores": {"motion_consistency": 0.21, "focus": 0.44, "exposure": 0.88, "overall": 0.21},
      "duration_sec": 20.02
    }
  ],
  "counts": {
    "sources_analysed": 1,
    "segments_accepted": 31,
    "rejections": 44,
    "accepted_sec": 118.4,
    "analysed_sec": 235.3,
    "by_ruleset": {"visual": {"segments": 24, "sec": 89.1}, "narrative": {"segments": 7, "sec": 29.3}}
  }
}
```

*(The `counts`, `scores`, `motion_confidence`, `params` and
clipped-fraction values above are illustrative shapes. The
`in_sec`/`out_sec`, `frame_in`/`frame_out`, the motion figures quoted in
`reason_detail`, `lapvar_median`, `mean_luma`, `analysis_sec` and the
clip metadata are real measurements. No claim is being
made here about what the cull will score — that number does not exist
until slice 3 runs.)*

The second rejection is the honest one: 46.45–66.47 is the largest gap in
Ryan's key (20.0s), and the measured signals over it are the
out-of-select population — smoothed motion 2.44 px/frame against 1.15
inside selects. That range *should* be rejected, and the file says why in
a sentence a human can check against the footage.

## 6. The signals sidecar

Every claim in §4.3 and §4.5 traces to a per-frame number, and a human
who disagrees with a boundary needs to see that number. Format, size, and
the reasoning behind both are in `docs/design/PHASE4_CULL_DESIGN.md` §4.
The contract's part is only this:

- One sidecar per analysed **file**, at
  `analysis/signals/<source_id>/<rel_path>.signals.npz`, with a
  human-readable companion `<…>.signals.json` beside it.
- `sources[].signals_path` is relative to the culls file's directory, so
  the whole `analysis/` tree moves with the project.
- `sources[].signals_sha256` pins it. A report that cannot verify the
  hash says so rather than plotting stale signals.
- A missing sidecar is a WARN, not a REJECT (§7): the culls file is still
  buildable and still scoreable; only the *explanation* is gone.

## 7. Validation

Exhaustive, not fail-fast — every offender reported together, the rule
`posthouse.coldfootage` and `posthouse.manifest` already follow. One
mode: a culls file is either fit to hand to the builder or it is not.

### REJECT (fatal; no file is written, non-zero exit)

1. Anything `coldfootage.validate_segments_shape` rejects — this
   contract never diverges from the shared check (§2.1). That covers
   `contract_version != 1`, a missing/empty `sequence_name`, an empty
   `segments`, and every per-segment shape fault.
2. A segment or rejection missing `source_id`, `rel_path`, or `ruleset`,
   or a `ruleset` outside `{"narrative", "visual"}`.
3. A `source_id` that fails the PROJECT_MANIFEST §5 regex, or that is
   absent from `sources[]`.
4. `motion_intent`, `boundary_reason_in`, `boundary_reason_out`, or
   `reason` outside its §4 enum. New motion vocabulary is a contract
   change, not a free-text field.
5. `frame_in`/`frame_out` inconsistent with `in_sec`/`out_sec` by more
   than half a frame at that source's `fps`, or `frame_out <= frame_in`.
6. **Stale manifest stamp:** `manifest_id` not matching the manifest at
   `root_dir`, or a `source_id`+`rel_path` resolving to a path different
   from the stored `source_path`. (`manifest_revision` *older* than the
   manifest's is a WARN, not a REJECT — see below.)
7. **Coverage broken:** for any `(source_id, rel_path, ruleset)`, the
   merged union of pre-handle segments and rejections does not exactly
   tile `[0, duration_sec]`, or two segments of the same ruleset on the
   same file overlap. Segments of *different* rulesets overlapping is
   legal and expected.
8. `params.analysis.source_grade` absent or not in
   `{"original", "analysis_decode"}` — a cull measured on CRF-28 proxies
   is not a cull (ROADMAP §4).
9. `rulesets_run` on a source not containing the `ruleset` of a segment
   or rejection citing it.
10. Zero accepted segments across the whole run. Not a valid segments
    file, and almost certainly a bug rather than a shoot with nothing
    usable; the run reports the rejection reasons and exits non-zero.

### WARN (recorded in the run report, never blocking)

1. `counts` disagreeing with the arrays it summarizes (recompute and
   report both — a stale count is a symptom).
2. `params.fit_provenance == "manual"` — a hand-set threshold, against
   ROADMAP §5. Legal for exploration, loud in the report.
3. `analysed_frames` differing from `round(duration_sec * fps)` by more
   than 2 — a decode dropped frames and every timestamp downstream of
   the drop is suspect.
4. A sidecar missing or failing its `signals_sha256` (§6).
5. `manifest_revision` older than the manifest's current `revision` —
   late footage may have arrived since; the cull is not wrong, it is
   behind.
6. A `dual_use: true` source with `rulesets_run` of length 1 — the
   manifest asked for two lives and got one.
7. A source in the manifest with video files and no entry in `sources[]`
   — a whole clip went unanalysed.
8. Accepted fraction outside 5–80% of a source's duration. On the
   benchmark clip Ryan marked 39%; a cull returning 3% or 95% has
   probably failed in a way no per-segment check catches.
9. Any segment shorter than `params.<ruleset>.min_duration_sec` (should
   be impossible; belt and braces).

## 8. Open questions for Ryan

These are the contract-shaped ones. The detection-shaped questions are in
`docs/design/PHASE4_CULL_DESIGN.md` §6, and several of them will change
numbers in `params`, not fields here.

1. **`analysis/` as the folder name.** It sits beside the footage folders
   and `Brand Assets` under `root_dir`, and it will hold the sidecars
   (~5 MB per hour of footage). *Recommendation: yes* — same co-location
   logic as the Brand Brief, and it keeps a project self-explaining.
2. **Should the master `culls.json` carry both rulesets, or should there
   only ever be two per-ruleset files?** *Recommendation: master + views
   as specified* — the benchmark's per-ruleset breakdown reads one file,
   and a human comparing "what did narrative keep that visual didn't" has
   one place to look.
3. **`segment_id` stability across runs.** Today a re-cull mints new ids.
   *Recommendation: keep it that way* — a re-cull is a new opinion; a
   stable id would imply an identity that survives a boundary moving,
   which is a lie. Revisit if the Creative Editor ever needs to reference
   a select across cull runs.
