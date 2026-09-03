# Project Manifest — contract v1

Owner: Lead Architect (`docs/TEAM.md`). Status: **ratified 2026-09-01**
(§6) — fully settled, no open blockers. Governs the artifact only —
nothing here specifies UI.

## 1. Purpose and lifecycle

The Project Manager's hard deliverable: the one file saying *what this
project is, what footage exists, and what it's for.* Every later role
reads it **blind** — the AE never re-asks Ryan which folder is A-roll.

| | |
| --- | --- |
| **Writer** | The PM role. Once at intake, then updatable (late footage, corrected answers). Nobody else writes it. |
| **Readers** | Assistant Editor (cull, sync, grouping), Creative Editor, exporter, benchmark scorer. Read-only for all of them. |
| **Location** | `<project.root_dir>/manifest.json` — always at the project root, always that name. The manifest is *self-locating*: whatever folder you found it in **is** `root_dir` (adopted from PreCut's Drop 4.16 healing behaviour — the folder you opened from is authoritative, a stale stored path is not). |
| **Encoding** | UTF-8 JSON, 2-space indent, keys in the order given below. Written tempfile-then-`os.replace`, exactly as `project.py:Project.save()` does — a crash mid-write never leaves a corrupt manifest. |
| **Versioning** | `contract_version` (int) is the schema shape. Evolution is **additive-only**, mirroring PreCut's DB migration rule: new optional fields may be added at any time under the same version; removing a field, renaming one, or narrowing an enum is a version bump. A reader that finds a `contract_version` it doesn't know must refuse to run, not guess. |
| **Revisions** | `revision` (int) counts edits to *this project's* manifest, independent of `contract_version`. Every write increments it. PreCut conflated these two in `project.version`; we do not. |

## 2. Schema

Types: `str`, `int`, `num`, `bool`, `[T]` list, `{…}` object, `date` =
`YYYY-MM-DD`, `ts` = ISO-8601 UTC (`2026-09-01T14:22:07Z`). "Req" =
required at handoff (§4). Absent optional fields are absent, not `null`,
unless a default is stated.

### 2.1 Top level

*Rationale: identity + provenance, so any artifact traces back to the manifest revision and PreCut pin that produced it.*

| Field | Type | Req | Default | Meaning |
| --- | --- | --- | --- | --- |
| `contract_version` | int | ✓ | `1` | Schema version. |
| `manifest_id` | str | ✓ | uuid4 | Immutable project identity; survives every rename and move. |
| `revision` | int | ✓ | `1` | Bumped on every write. |
| `created_at` / `updated_at` | ts | ✓ | — | ISO strings, not unix floats — the manifest is meant to be read and diffed by humans. |
| `generator` | {…} | ✓ | — | `{name, version, precut_pin}` — `precut_pin` is `posthouse/PRECUT_PIN`, so a harvested inference result is always attributable to a PreCut commit. |
| `project` | {…} | ✓ | — | §2.2 |
| `brand` | {…} | — | — | §2.3. Absent = no brand assets on this job. |
| `sources` | [{…}] | ✓ | — | §2.4. Non-empty. |
| `delivery_targets` | [{…}] | — | `[]` | §2.5. **The PM writes this key never, not even as `[]`** — see §2.5's ruling; the `[]` default here describes what an absent key means to a *reader*, not something the PM emits. |
| `default_includes` | [{…}] | — | `[]` | §2.6 |
| `handoffs` | [{…}] | ✓ | — | §2.7. Append-only. |
| `validation` | {…} | ✓ | — | `{ran_at: ts, mode: "intake"\|"handoff", errors: [], warnings: []}` — the last validation result, recorded so a reader can see what the PM already knew was wrong (§4). |

### 2.2 `project` — identity

*Rationale: who it's for and what kind of shoot it is; `project_type` selects which validation rules apply.*

| Field | Type | Req | Meaning |
| --- | --- | --- | --- |
| `name` | str | ✓ | Human title, spaces allowed (`"Mendez Listing — 128 Alder"`). |
| `slug` | str | ✓ | Slug of `name` per §5 rules; immutable once set. |
| `root_dir` | str | ✓ | Absolute path of the project folder holding this file. |
| `client` | {…} | ✓ | `{name (req), contact?, notes?}` |
| `project_type` | str | ✓ | `interview` \| `property_tour` \| `renovation` \| `event` \| `product` \| `other`. Free-text goes in `notes`, not here. |
| `shoot_dates` | [date] | — | List, not a scalar — multi-day shoots are normal. Read directly from file creation timestamps at intake, no confirmation step (Ryan, Open Q 5). |
| `locations` | [{…}] | — | `[{label (req), address?}]` |
| `people` | [{…}] | — | `[{id, name, role: "subject"\|"agent"\|"host"\|"other"}]` — the roster of who Ryan expects on camera, asked once at intake (`id` = slugified name per §5, unique in the project), referenced by `sources[].subject_ids`. Feeds Phase 4 per-subject grouping. Cross-clip per-voice identity matching is explicitly out of scope (Open Q 3, ratified 2026-09-01) — generic speaker labels are sufficient wherever speaker identity is needed elsewhere in the pipeline. |
| `audience_goal` | str | — | **Added 2026-09-03, additive, same `contract_version`.** Free text describing who this project's footage is FOR — a SoldFast content funnel (Brand/Authority, Franchisee Recruiting, Contractor Recruiting), a personal long-form/documentary intent, or anything else an editor writes in their own words. Deliberately free text, not a fixed picklist — this app is for any editor's projects, not just SoldFast's. Graduated out of `notes` because it has a defined downstream consumer (the Assistant Editor's audience-informed transcript-fragment tagging) rather than being intake trivia with no field yet. |
| `notes` | str | — | Free text from intake that has no field yet. |

### 2.3 `brand` — assets and the Brand Brief

*Rationale: the PM extracts deterministically rather than making Ryan retype (Decision Log, Brand Brief) — every value here is read off a file or confirmed by him.*

**Governing principle (Open Q 4), resolved 2026-09-01:** brand assets
are snapshotted into the project, never referenced from an external
library, because the project folder itself is meant to be the unit of
portability. Ryan clarified the scope directly: this means copying
brand assets (and any other small additional assets) so they live
**alongside** the footage — as a sibling directory under the same
project root — never copying or relocating the footage itself.
`root_dir` is (or contains) wherever the source footage folders
already sit; the PM stages `assets_dir` as a new sibling directory
under that same root and copies brand files into it. **This fully
resolves the earlier flagged tension**: PreCut's "source footage is
never moved" design (README.md) is untouched — `sources[].path`
stays a reference to wherever the footage already lives — while
handing the project root to a human editor still surfaces everything
relevant (footage folders + `assets_dir` + `manifest.json`) as one
browsable structure, at the real cost of copying only the small files.
No longer a blocker on Phase 2.

| Field | Type | Req | Meaning |
| --- | --- | --- | --- |
| `assets_dir` | str | ✓ | Absolute path of the staged brand-assets folder. All `file` values below are **relative to it**. |
| `fonts` | [{…}] | — | `{file, family_name, style_name?, postscript_name?, format: "ttf"\|"otf"\|"woff2", extracted_by: "name_table"\|"filename", install_status: "installed"\|"not_installed"\|"unknown"}`. Fonts can't ride FCP7 XML (ROADMAP §7) — this list is the install checklist. |
| `palette` | [{…}] | — | `{hex: "#RRGGBB", role: "primary"\|"secondary"\|"accent"\|"neutral", source: "logo:<file>"\|"user"}` |
| `logos` | [{…}] | — | `{file, kind: "primary"\|"mark"\|"wordmark"\|"alt", has_alpha: bool}` |
| `documents` | [{…}] | — | `{file, kind: "brand_guidelines"\|"script"\|"contract"\|"other", unsupported_reason?, summarized: bool}` — `unsupported_reason` is the harvested string from `auto_include.unsupported_reason()`, verbatim. |
| `brief` | {…} | — | `{readme_path, card_png_path, bin_path (default "Files/Brand"), marker_written: bool}`. **Co-location rule is a validated invariant:** `card_png_path` must resolve inside `assets_dir`, and the card exists nowhere else. |
| `library_ref` | str | — | Reserved: id of a reusable client brand library. Unused in v1 (Open Q 4). |

### 2.4 `sources` — the folders

*Rationale: the user declares the kind, the machine records what it inferred, the declaration always wins — PreCut's drop-zone model ("the zone chose the kind", `project.py:SourceFolder`).*

| Field | Type | Req | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | str | ✓ | derived | Stable source id per §5. **Immutable** — renaming the folder changes `path`, never `id`. |
| `path` | str | ✓ | — | Absolute path to the folder (or single file). |
| `display_name` | str | ✓ | basename | What a human calls it. |
| `kind` | str | ✓ | — | `aroll` \| `broll` \| `source_audio` \| `assets`. PreCut's `SourceKind` is `aroll\|broll\|audio`; `audio` ⇄ `source_audio` and `assets` is new — bridge code must map, never assume. |
| `is_file` | bool | — | `false` | Single file rather than a folder (PreCut supports both). |
| `dual_use` | bool | — | `false` | A-roll that also yields B-roll — the subject keeps talking while the shooter grabs coverage. Culled twice, under both rulesets (ROADMAP §4). Meaningful only on `kind: "aroll"`. |
| `subject_ids` | [str] | — | `[]` | Ids from `project.people` who appear in this source. |
| `notes` | str | — | `""` | Ryan's words about this folder, carried verbatim to every later role. |
| `added_at` | ts | ✓ | — | When intake registered it. |
| `media` | {…} | — | — | `{video_count, audio_count, image_count, other_count, total_bytes}` — a census taken at intake, explicitly a snapshot, not live state. |
| `unsupported` | [{…}] | — | `[]` | `{ext, count, category, reason}` — §4.3. |
| `inference` | {…} | — | — | `{camera_tags: [str], method: "camera_inference@<precut_pin>", agrees_with_declaration: bool}`. `camera_tags` are `camera_inference.infer_camera_tags()` output verbatim (`drone`, `aerial`, `gopro`, `action_cam`, `sony`, `cinema`, `timelapse`, …). Recorded, never authoritative. |

### 2.5 `delivery_targets` — what gets made

*Rationale: reuse `presets.py`'s vocabulary exactly, so a target maps to a real sequence size and a real overlay with no translation layer.*

**Ruling (Open Q 1 — overrides the draft's recommendation):** the PM
never proposes delivery targets. This list is **absent at PM handoff**
and stays empty until the Creative Editor has actually combed through
and familiarized itself with the organized footage (Phase 6) — only
then does it have grounds to *suggest* deliverables, which it brings to
Ryan for discussion before anything is `confirmed`. Guessing a format
before anyone has seen the footage produces nothing useful; the PM's
job is organizing the material well enough that the Creative Editor's
suggestion is actually informed.

| Field | Type | Req | Meaning |
| --- | --- | --- | --- |
| `id` | str | ✓ | `dt-<slug>-NN` per §5. |
| `label` | str | ✓ | Human name (`"IG Reel — 30s"`). |
| `aspect_key` | str | ✓ | One of `presets.ASPECT_PRESET_KEYS`: `aspect_horizontal_16_9`, `aspect_horizontal_16_9_4k`, `aspect_vertical_9_16`, `aspect_square_1_1`. |
| `platform_key` | str | — | A `presets.PLATFORMS_BY_KEY` key (`platform_ig_reels`, `platform_tiktok`, `platform_youtube_shorts`, `platform_facebook_reels`, `platform_x_vertical`, `platform_youtube_ad`) or `"none"`. Drives the safe-zone overlay. |
| `preset_key` | str | — | A `presets.PRESETS_BY_KEY` key (`reel_30s`, `ad_30s`, `youtube_highlight`, `talking_head_full`, …) when the target matches a built-in. |
| `target_duration_sec` | num | — | Overrides the preset. `-1` = full source length (PreCut's `talking_head_full` sentinel). |
| `duration_tolerance_sec` | num | — | Defaults to the preset's. |
| `status` | str | ✓ | `proposed` (Creative Editor's suggestion, Phase 6) \| `confirmed` (Ryan signed off after discussion). Never written by the PM — see the ruling above. |

### 2.6 `default_includes` — files every project gets

Harvested as-is from PreCut's Auto-Include (SFX, logos, recurring assets):
`auto_include.AutoIncludeRule.to_dict()` verbatim — `{id, type:
"file"|"folder", source_path, bin_path, file_glob}` — plus `origin:
"global_settings" | "project"`. *Rationale: snapshotted at intake so a
later settings change can't silently alter what this project exports.*

### 2.7 `handoffs` — the visible handoff record

`{role, action: "emitted"|"consumed"|"revised"|"returned", at: ts,
revision: int, agent: str, note?: str}` — `role` ∈ `project_manager`,
`assistant_editor`, `creative_editor`, `supervisor`, `exporter`;
`revision` is the manifest revision that role acted on. *Rationale: the
data behind the app's handoff UX (Phase 5) and the audit trail for which
revision each role actually read. Append-only, never edited.*

## 3. Complete example

```json
{
  "contract_version": 1,
  "manifest_id": "9f1c1b4e-3a77-4d6b-9c02-1f0c1a2e55d1",
  "revision": 2,
  "created_at": "2026-09-01T15:04:11Z",
  "updated_at": "2026-09-01T16:20:48Z",
  "generator": {"name": "posthouse.pm", "version": "0.1.0", "precut_pin": "e035fbaf"},
  "project": {
    "name": "Mendez Listing — 128 Alder St",
    "slug": "mendez-listing-128-alder-st",
    "root_dir": "/Volumes/Work/Projects/2026-09-01 Mendez Listing",
    "client": {"name": "Carla Mendez Realty", "contact": "carla@mendezrealty.com"},
    "project_type": "interview",
    "shoot_dates": ["2026-08-27"],
    "locations": [{"label": "128 Alder St", "address": "128 Alder St, Bend OR"}],
    "people": [{"id": "carla", "name": "Carla Mendez", "role": "agent"}],
    "notes": "Agent-led walkthrough. She wants the kitchen island featured."
  },
  "brand": {
    "assets_dir": "/Volumes/Work/Projects/2026-09-01 Mendez Listing/Company Branding",
    "fonts": [
      {"file": "Gilroy-Bold.otf", "family_name": "Gilroy", "style_name": "Bold", "format": "otf", "extracted_by": "name_table", "install_status": "not_installed"},
      {"file": "SourceSerif4-Regular.ttf", "family_name": "Source Serif 4", "style_name": "Regular", "format": "ttf", "extracted_by": "name_table", "install_status": "installed"}
    ],
    "palette": [
      {"hex": "#1B3A57", "role": "primary", "source": "logo:mendez-logo.png"},
      {"hex": "#C9A227", "role": "accent",  "source": "logo:mendez-logo.png"},
      {"hex": "#F5F2EC", "role": "neutral", "source": "user"}
    ],
    "logos": [{"file": "mendez-logo.png", "kind": "primary", "has_alpha": true}],
    "documents": [
      {"file": "Mendez_Brand_Guidelines_2026.pdf", "kind": "brand_guidelines", "summarized": true, "unsupported_reason": "PDFs aren't importable as Premiere project items."}
    ],
    "brief": {"readme_path": "BRAND_README.txt", "card_png_path": "brand-card.png",
              "bin_path": "Files/Brand", "marker_written": true}
  },
  "sources": [
    {
      "id": "aroll-carla-interview-01",
      "path": "/Volumes/Work/Footage/2026-08-27 Mendez/A - Carla Interview",
      "display_name": "A - Carla Interview", "kind": "aroll",
      "dual_use": true, "subject_ids": ["carla"],
      "notes": "She keeps talking while I grab the room — pull B-roll out of this too.",
      "added_at": "2026-09-01T15:06:02Z",
      "media": {"video_count": 14, "audio_count": 0, "image_count": 0, "other_count": 0, "total_bytes": 88213004288},
      "unsupported": [],
      "inference": {"camera_tags": ["sony", "cinema"], "method": "camera_inference@e035fbaf", "agrees_with_declaration": true}
    },
    {
      "id": "broll-interior-rooms-01",
      "path": "/Volumes/Work/Footage/2026-08-27 Mendez/B - Interior Rooms",
      "display_name": "B - Interior Rooms", "kind": "broll",
      "added_at": "2026-09-01T15:06:02Z",
      "media": {"video_count": 63, "audio_count": 0, "image_count": 0, "other_count": 2, "total_bytes": 141238374400},
      "unsupported": [{"ext": ".lrf", "count": 2, "category": "unknown_extension",
                       "reason": "2 .lrf file(s) skipped (unsupported extension; expected audio, video, or image)."}],
      "inference": {"camera_tags": ["osmo", "gimbal", "pocket"], "method": "camera_inference@e035fbaf", "agrees_with_declaration": true}
    },
    {
      "id": "broll-exterior-mavic-01",
      "path": "/Volumes/Work/Footage/2026-08-27 Mendez/B - Exterior Mavic 3",
      "display_name": "B - Exterior Mavic 3", "kind": "broll",
      "notes": "Two batteries, some of it is windy.", "added_at": "2026-09-01T15:06:02Z",
      "media": {"video_count": 27, "audio_count": 0, "image_count": 0, "other_count": 0, "total_bytes": 52961280000},
      "unsupported": [],
      "inference": {"camera_tags": ["drone", "aerial", "mavic"], "method": "camera_inference@e035fbaf", "agrees_with_declaration": true}
    },
    {
      "id": "source-audio-lav-carla-01",
      "path": "/Volumes/Work/Footage/2026-08-27 Mendez/Audio - Lav",
      "display_name": "Audio - Lav", "kind": "source_audio", "subject_ids": ["carla"],
      "added_at": "2026-09-01T15:06:02Z",
      "media": {"video_count": 0, "audio_count": 3, "image_count": 0, "other_count": 0, "total_bytes": 1204224000},
      "unsupported": []
    }
  ],
  "delivery_targets": [
    {"id": "dt-listing-film-01", "label": "Listing film — 2min", "aspect_key": "aspect_horizontal_16_9",
     "platform_key": "none", "preset_key": "ad_120s", "status": "confirmed"},
    {"id": "dt-ig-reel-01", "label": "IG Reel — 30s", "aspect_key": "aspect_vertical_9_16",
     "platform_key": "platform_ig_reels", "preset_key": "reel_30s", "status": "proposed"}
  ],
  "default_includes": [
    {"id": "b7c1b0e2-...", "type": "folder", "source_path": "/Users/ryan/Assets/SFX/Whooshes",
     "bin_path": "Audio/SFX", "file_glob": "*.wav", "origin": "global_settings"}
  ],
  "handoffs": [
    {"role": "project_manager", "action": "emitted", "at": "2026-09-01T15:41:09Z",
     "revision": 1, "agent": "posthouse.pm/0.1.0"},
    {"role": "project_manager", "action": "revised", "at": "2026-09-01T16:20:48Z", "revision": 2,
     "agent": "posthouse.pm/0.1.0", "note": "Drone card arrived late; added broll-exterior-mavic-01."}
  ],
  "validation": {
    "ran_at": "2026-09-01T16:20:48Z", "mode": "handoff", "errors": [],
    "warnings": [
      "brand.fonts[0] 'Gilroy Bold' is not installed on this machine — install before opening the sequence.",
      "broll-interior-rooms-01: 2 .lrf file(s) skipped (unsupported extension; expected audio, video, or image).",
      "delivery_targets[dt-ig-reel-01] is still 'proposed'."
    ]
  }
}
```


## 4. Validation

Two moments, one rule set. **`mode: "intake"`** — the PM is still talking
to Ryan, everything is a warning, the manifest is a legal draft.
**`mode: "handoff"`** — the PM is about to append an `emitted` handoff:
every REJECT below is fatal and no manifest is written. Validation is
**exhaustive, not fail-fast** — every offender reported at once, the same
rule `posthouse.coldfootage` already follows.

### 4.1 REJECT (fatal at handoff)

1. Missing or unrecognized `contract_version`.
2. `project.root_dir` missing, not a directory, or not writable.
3. `sources` empty, or a source `path` that does not exist / is unreadable.
4. A `source.id` that fails the §5 regex, or two sources sharing an `id`.
5. **Kind conflict:** the same resolved path listed twice, or one source
   nested inside another with a *different* `kind`. (Nested with the same
   kind is a warning — it only double-counts.)
6. `kind`, `project_type`, `status`, `role`, or `action` outside its enum.
7. A delivery target whose `aspect_key` is not in the resolved platform's
   `allowed_aspects`, or whose `platform_key`/`aspect_key`/`preset_key` is
   unknown to `presets.py`.
8. `brand.brief.card_png_path` resolving outside `assets_dir` — the
   co-location rule is load-bearing (right-click → Reveal in Finder is
   the whole mechanism).
9. `project_type: "interview"` with no `aroll` source, or whose `aroll`
   sources hold zero video files — letting this through hands the AE a
   spine-less job. *(Warning at intake, since footage often arrives in
   two trips; fatal at handoff.)*

### 4.2 WARN (recorded, never blocking)

`dual_use: true` on a non-`aroll` source (ignored downstream) ·
`inference.agrees_with_declaration: false` · a font with
`install_status != "installed"` · a source
on a different volume than `root_dir` (unmountable later) · `brand`
absent entirely · `source_audio` present with no `aroll` to sync to · a
source with zero media files · nested same-kind sources.

**Amended 2026-09-01 (Lead, after seeing it fire in a real run):** an
empty `delivery_targets` is NOT a warning. Open Q 1's ruling made that
field Creative-Editor-owned and deliberately absent at PM handoff, so
warning about it at the PM stage flags the correct state as a problem
and trains readers to ignore warnings. The check belongs to the Creative
Editor's own validation when Phase 6 builds it: by the time a cut is
being assembled, missing delivery targets are genuinely worth flagging.

### 4.3 Unsupported files → warn, with the harvested reason

Unclassifiable files are counted by extension and reported through
`auto_include.unsupported_reason()` — **verbatim, never paraphrased**, so
PreCut and the Post House give Ryan the same sentence. `category` buckets
those extensions:

| category | extensions | posture |
| --- | --- | --- |
| `lut` | `.cube` `.look` `.3dl` | Warn; stage into `brand.assets_dir` — Lumetri preset, not an import. |
| `document` | `.pdf` `.doc` `.docx` | Warn; if brand-related, stage and list under `brand.documents`. |
| `text` | `.txt` `.rtf` | Warn; this is exactly why the Brand Brief renders a PNG card. |
| `layered_image` | `.ai` `.psd` | Warn; ask for a PNG/SVG export. |
| `unknown_extension` | anything else `kind_for_path()` returns `None` for | Warn with the generic message. |

Warnings never remove a file from disk and never stop a handoff.

## 5. Composition — how everything else hangs off this

**Stable source ids**, because downstream artifacts must survive Ryan
renaming `B - Interior` to `B - Interior Rooms` at 11pm:

```
id  =  <kind>-<slug>-<NN>
       kind  ∈ aroll | broll | source-audio | assets   (hyphenated form)
       slug  = slugify(display_name at the moment of intake)
       NN    = 2-digit ordinal, always present, assigned in intake order
               within that kind, never reused, never renumbered
regex: ^(aroll|broll|source-audio|assets)-[a-z0-9]+(-[a-z0-9]+)*-[0-9]{2}$
```

`slugify()`: NFKD-normalize, drop combining marks, lowercase, keep ASCII
alphanumerics, collapse every other run to a single `-`, strip
leading/trailing `-`, truncate to 40 chars at a `-` boundary, empty →
`folder`. Deliberately stricter than PreCut's `_sanitize_project_name()`,
which preserves spaces because it names display folders, not ids.

**The id is minted once and frozen** — a name, not a derivation.
Re-slugging a renamed folder would orphan every artifact citing it, so
`display_name` and `path` change freely and `id` never does; the
always-present `-NN` means a second `B - Interior` needs no retroactive
rename of the first.

**Addressing a file:** downstream artifacts cite `{source_id, rel_path}`,
`rel_path` POSIX-relative to that source's `path` (`""` when
`is_file: true`). Absolute paths live in exactly one place —
`sources[].path` — so relocating a drive is a one-line manifest edit, not
a rewrite of every artifact.

**`culls.json` (Phase 4).** `posthouse.coldfootage`'s absolute
`source_path` stays valid (additive-only); `culls.json` adds
`manifest_id`, `manifest_revision`, and per-segment `source_id` +
`rel_path`, and its producer resolves those against the manifest into the
`source_path` the ratified builder already consumes. The three ratified
v1 rulings are untouched: validation applies to the pre-handle range,
list order is final editorial order, sequence dims probe the first
segment's source. A dual-use source appears twice under the same
`source_id`, segments tagged `ruleset: "narrative" | "visual"` — only
expressible because `dual_use` lives here. **`groups.json`** clusters
over the same `{source_id, rel_path}` pairs and may cite
`project.people[].id`. **The Brand Brief** is generated from `brand`
(README and card PNG both written inside `assets_dir`, per the
co-location rule; `brief` records where they landed).

**Every consumer** appends a `handoffs` entry naming the `revision` it
read and stamps that `{manifest_id, manifest_revision}` pair into its own
output — that pair is how a stale artifact gets caught instead of
silently trusted.

## 6. Open questions — RATIFIED by Ryan, 2026-09-01

1. **Delivery targets: intake, or later?** **Ruling: later, and not by
   the PM at all.** The draft's "both" recommendation is overridden —
   Ryan wants zero guessing before the footage has been organized and
   the Creative Editor has actually looked at it. See the ruling in
   §2.5 above; `delivery_targets` is absent at PM handoff.
2. **`dual_use` grain: folder or clip?** **Ruling: per folder**, as
   recommended.
3. **Should the PM ask who's on camera?** **Ruling: yes**, as
   recommended — `project.people` stays a simple intake roster. The
   follow-on ask (per-voice attribution across clips, with a name
   correction propagating everywhere that voice appears) was
   **de-scoped by Ryan on reflection (2026-09-01): generic Speaker 1 /
   Speaker 2 style labels are sufficient.** No cross-clip voice
   identity, matching, or propagating rename is needed anywhere in the
   Post House. This removes real complexity from the eventual Phase 4
   design — whatever speaker separation the AE's own transcription
   produces (even literally "Speaker 1", "Speaker 2") is the finished
   feature, not an intermediate step toward named identity.
4. **Brand: per-project or reusable library?** **Ruling: snapshot per
   project**, as recommended — and elevated to a governing principle
   (§2.3): the project folder should be a fully self-contained handoff
   unit. That principle surfaced a real, unresolved tension for raw
   footage (see the flagged tension in §2.3) that needs its own
   decision before Phase 2's file-organization step is built.
5. **Shoot dates: ask, or read?** **Ruling: read only, no confirmation
   step** — stronger than the draft's "read, then confirm." §2.2
   updated accordingly.
6. **Late footage: revision or new project?** **Ruling: new
   revision**, as recommended.

---

*Note for the Lead: adopting this means ARCHITECTURE.md's artifact table
gains "Project Manifest — v1 specified, `docs/contracts/PROJECT_MANIFEST.md`",
and the Decision Log gains the source-id scheme (§5) as a settled call.*
