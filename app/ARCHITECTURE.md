# PreCut — Architecture

Orientation doc for anyone (human or agent) picking up this codebase. It
describes what each piece does and where to look. For release notes and
user-facing instructions see [README.md](README.md). For settled design
calls see [DECISIONS.md](DECISIONS.md).

## What the app does

PreCut is a local-first macOS app for video editors. You drop in raw
footage: an A-roll interview, a B-roll library, and lav audio. It
transcribes the A-roll, tags the B-roll with AI vision, syncs the lav
audio to camera audio, generates "story angle" pitches you can refine,
and exports a single Premiere-ready FCP7 XML containing multiple
timelines plus a searchable B-roll library bin.

The editor imports that one XML into Premiere and starts cutting.

## Stack

| Layer | Technology |
| --- | --- |
| Frontend | React 18 + Vite, plain CSS variables (`src/styles.css`) |
| Shell | Tauri v2 (Rust), `src-tauri/` |
| Backend | Python 3.11+, JSON-lines IPC over stdin/stdout |
| Storage | SQLite (clip metadata + project state), LanceDB (CLIP vectors) |
| AI | Whisper (local), Claude API (tagging + story angles), CLIP embeddings |
| Media | ffmpeg / ffprobe, `audio-offset-finder` (BBC) for lav sync |

Roughly 13,000 lines of Python and 11,000 lines of frontend.

## How the three layers talk

The Rust shell spawns the Python backend as a subprocess and bridges
stdin/stdout to the React UI. Every message is one JSON object on one
line.

```
React UI  <--Tauri events-->  Rust shell  <--JSON lines/stdio-->  Python backend
```

The backend announces itself on startup:

```json
{"type": "ready", "version": "0.4.43-precut-logo-cleanup", "settings": {...}}
```

Commands in (`{"type": "create_project", ...}`, `"add_source"`,
`"run_pipeline"`, `"story_generate"`, `"export_timelines"`,
`"shutdown"`). Events out (`"project_created"`, `"stage_started"`,
`"file_done"`, `"stage_complete"`, `"pipeline_complete"`, `"log"`,
`"error"`). The full dispatch table is the `HANDLERS` dict at the bottom
of `python_backend/backend.py`.

## Pipeline stages, in order

1. **Proxy generation** shrinks B-roll to small mp4s for fast processing.
2. **Audio indexing** identifies lav files, groups them by mic and time adjacency.
3. **Frame tagging** samples frames per B-roll clip, tags via Claude vision.
4. **Transcription** runs Whisper over the A-roll.
5. **Audio sync** aligns lavs to camera audio via `audio-offset-finder`.
6. **Story planning** asks Claude for deliverable concepts and story angles.
7. **Matching** turns a chosen plan into a CutList: an A-roll phrase track plus a B-roll marker list.
8. **XML export** writes the FCP7 XML with library bin, sequences, and sync tracks.

## Backend file map

`python_backend/` (top level, orchestration):

| File | Lines | Role |
| --- | --- | --- |
| `backend.py` | 734 | IPC event loop and command dispatcher |
| `pipeline.py` | 964 | Orchestrates ingest, tag, sync per source |
| `project.py` | 653 | Project model, `known_projects.json` registry, portable projects |
| `exporter.py` | 931 | Turns selected Ideas into a multi-timeline XML |
| `producer.py` | 697 | AI Producer wrapper |
| `proxy_manager.py` | 414 | ffmpeg proxy generation |
| `setup_helper.py` | 857 | First-launch dependency setup (Xcode CLT, Homebrew, ffmpeg, Python, pip) |
| `settings.py` | 194 | App-level settings persistence |
| `audio_indexer.py` | 161 | Audio indexing for sync prep |

`python_backend/precut_pipeline/` (core logic):

| File | Lines | Role |
| --- | --- | --- |
| `multi_exporter.py` | 2265 | Library bin plus multi-sequence XML assembly. The big one. |
| `exporter.py` | 1041 | FCP7 xmeml writer, preset-aware dimensions |
| `audio_sync.py` | 775 | Lav sync, rollover groups, cross-validation |
| `planner.py` | 733 | Deliverable planner, Claude call plus JSON repair |
| `bin_builders.py` | 619 | Bin and master-clip builders for the XML export |
| `cli.py` | 604 | Command-line interface |
| `matcher.py` | 518 | Turns a Deliverable into a CutList with markers |
| `story_assembler.py` | 515 | Story assembly |
| `presets.py` | 507 | Built-in deliverable presets |
| `markers.py` | 483 | `LibraryVocabulary` plus marker generation |
| `story_planner.py` | 481 | Story angle planner |
| `auto_include.py` | 294 | "Files in every export" rules (Default Includes) |
| `database.py` | 288 | SQLite schema, LanceDB, idempotent migrations |
| `cutlist.py` | 271 | `ARollPhrase`, `BRollShot`, `BRollMarker`, `CutList` |
| `motion_analyzer.py` | 253 | Deterministic static/pan/tilt/zoom and framing detection |
| `theme_categories.py` | 246 | 14 theme categories with synonym vocabularies |
| `claude_tagger.py` | 238 | Claude vision tagging plus `BANNED_TAGS` filter |
| `transcriber.py` | 236 | Whisper transcription |
| `deliverable.py` | 129 | Deliverable data models |
| `config.py` | 127 | Model names, prompts, thresholds |
| `extractor.py` | 119 | ffprobe / keyframe extraction |
| `camera_inference.py` | 93 | Camera and source-type inference from paths |
| `tagger.py` | 90 | LLaVA/Ollama fallback tagger |
| `overlay.py` | 84 | Safe-zone overlay lookup |
| `embedder.py` | 68 | CLIP ViT-B-32, 512-dim embeddings |

## Frontend file map

`src/`:

| File | Lines | Role |
| --- | --- | --- |
| `styles.css` | 4087 | All styling, CSS variables for theming |
| `screens/tabs/IdeasTab.jsx` | 1087 | Story angle cards, generate, export modal |
| `App.jsx` | 766 | Root component, title bar, backend IPC wiring |
| `screens/tabs/IngestTab.jsx` | 608 | Drop zones, sync matrix, transcript status |
| `components/AutoIncludeModal.jsx` | 535 | Default Includes configuration |
| `components/ExportModal.jsx` | 494 | XML export configuration |
| `screens/StartScreen.jsx` | 397 | Project picker and create |
| `screens/ProjectView.jsx` | 336 | Tab bar, log drawer, project layout |
| `screens/SetupScreen.jsx` | 312 | First-launch dependency setup UI |
| `components/WelcomeModal.jsx` | 258 | First-run welcome |
| `components/ApiKeyHelp.jsx` | 231 | API key guidance |
| `components/SyncMatrix.jsx` | 207 | A-roll x audio sync score grid |
| `components/AutoIncludeHelp.jsx` | 188 | Default Includes explainer |
| `components/SettingsModal.jsx` | 188 | API key management |
| `components/TourTooltip.jsx` | 156 | Guided tour |
| `components/DropZone.jsx` | 166 | Drag and drop handling |

Smaller pieces: `StageProgress.jsx`, `ProgressPanel.jsx`,
`AutoIncludeNudge.jsx`, `LogView.jsx`, `HelpTooltip.jsx`,
`ToastStack.jsx`, `hooks/useTour.js`.

`src-tauri/src/main.rs` (650 lines) bootstraps Tauri, spawns the Python
backend, and bridges IPC.

## Data model

```python
# precut_pipeline/cutlist.py

@dataclass
class ARollPhrase:
    phrase_id: int
    source_file: str
    source_start: float      # source timecode in
    source_end: float
    timeline_start: float    # where it lands in the final cut
    timeline_end: float
    text: str                # spoken text from Whisper

@dataclass
class BRollMarker:
    timeline_time: float
    primary_tags: list[str]  # up to 5, shown in the marker name
    all_tags: list[str]      # full list, in the marker comment
    theme_category: str      # kitchen / bedroom / etc.
    color_rgb: tuple[int, int, int]
    phrase_id: int
    segment_order: int

@dataclass
class CutList:
    aroll_track: list[ARollPhrase]    # V1 clips
    broll_track: list[BRollShot]      # V2, empty (markers replace clips)
    broll_markers: list[BRollMarker]  # sequence markers
    sequence_width, sequence_height, sequence_fps
    overlay_style: str                # e.g. "horizontal_1920x1080"
```

## Premiere bin structure

The XML export creates this layout, chosen so editors land in something
familiar:

```
Seq/Final
Seq/v1/<Sequence Name>
Footage/A-Roll
Footage/B-Roll
Audio/Source Audio
Audio/Music
Audio/SFX
Files/Overlays
Files/Nested Seqs
Files/Colors
```

`Seq/`, `Footage/`, `Audio/`, and `Files/` are top-level siblings. There
is no project-name wrapper bin around the sequences.

## Runtime environment

The backend runs under Python 3.11 with the dependencies in
`python_backend/requirements.txt`. On this machine the working
interpreter is `~/precut-venv-fresh/bin/python` (3.11.15). Note that
`~/precut-venv` (3.12) exists but is missing `ffmpeg-python`, so
`extractor`, `ingest`, and `cli` fail to import under it.

For end users, `scripts/install.sh` installs everything on first launch:
Xcode Command Line Tools, Homebrew, ffmpeg, Python 3.12, and the pip
packages.

## App data locations

| What | Path |
| --- | --- |
| Settings and API key | `~/Library/Application Support/PreCut/settings.json` |
| Projects | `~/Library/Application Support/PreCut/projects/` |
| Legacy index (pre-rename) | `~/.broll_buddy/` (SQLite, frames, LanceDB vectors) |

## Build

```bash
npm install
npm run tauri build
```

`scripts/build_dmg.sh` produces the distributable. `tauri.conf.json`
references `src-tauri/icons/icon.icns`, which is generated by
`scripts/make_icns_from_icon.py` and is not checked in.
