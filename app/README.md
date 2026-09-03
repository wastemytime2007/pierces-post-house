# PreCut — Beta (1.0.0-beta.3)

Native Mac app. Drop footage in, get AI-generated concept pitches, refine
them, and **export a single Premiere XML with multiple timelines and a
searchable B-roll library**.

## Repo docs

- [ARCHITECTURE.md](ARCHITECTURE.md) — what each piece does and where to look
- [DECISIONS.md](DECISIONS.md) — settled design calls; treat as constraints
- [PROVENANCE.md](PROVENANCE.md) — where this code came from, and known gaps
- [docs/](docs/) — the beta-tester one-pager and the install guide PDF

The rest of this file is the beta release notes and user instructions.

## What is this release?

Beta 1 is the first build offered to outside testers. The core workflow
— footage ingest → indexing → AI producer → multi-timeline Premiere XML
export — is feature-complete and has been used end-to-end against real
projects. Beta is about validating the experience in more hands and on
more diverse footage.

Highlights since the last internal drop:

- **Cross-validated audio sync.** Multi-mic sessions where weak cross-
  correlation scores would previously cause a clip to skip syncing now
  pass via offset-difference agreement with strong matches on other
  clips. The matrix shows `Cross-validated` cells (dashed green) for
  these.
- **Default Includes.** A user-level "files to add to every export"
  feature — point at a stock SFX folder once and it lands in
  `Audio/SFX` on every project from then on. Configurable from the
  titlebar; per-export opt-out lives in the export options.
- **Flatter bin tree.** `Seq/`, `Footage/`, `Audio/`, `Files/` are all
  top-level siblings in the Premiere project panel — no more "Project
  Name" wrapper around the sequences.
- **First-export discovery nudge** for the Default Includes feature,
  shown once at the moment users are most likely to benefit.
- **Footage-only escape hatch** — "Skip ideas — export footage only"
  is available regardless of whether an API key is set.

Foundational properties (unchanged since prior drops):

- **Distributable zipped `.app` with first-launch setup.** PreCut
  builds as a ~40MB zip that strangers can double-click. On first launch
  the app shows a "First-time setup" screen that detects and installs
  missing dependencies (Xcode CLT → Homebrew → ffmpeg → Python 3.12 →
  pip packages) with streamed progress.
- **Ad-hoc codesigning** — no paid Developer ID, no notarization.
  Users get one Gatekeeper dialog on first launch; right-click → Open
  bypasses it. Required on Apple Silicon (unsigned binaries are
  hard-killed on arm64 macOS).
- **Arm64 only.** Intel Mac support dropped from release builds to cut
  size. Source tree still builds on Intel via `install.sh`.

### Why zip instead of DMG?

macOS 15 (Sequoia) removed the right-click → Open bypass for `.dmg`
files specifically — users now have to dig through System Settings →
Privacy & Security to open an unsigned DMG, which is a non-starter for
"strangers double-click" distribution. Zipped `.app` bundles still
support the right-click → Open flow, so they remain the path of least
friction for unsigned distribution.

## Building a distributable release

```bash
cd precut_beta1_package
./scripts/build_dmg.sh
```

On a warm build this takes ~90 seconds. On first run (downloading Rust
crates, compiling Tauri's deps) expect 10-15 minutes. Output lands in
`dist-release/PreCut-1.0.0-beta.3-arm64.zip`.

(The script name still starts with `build_dmg` for backward
compatibility with existing notes and muscle memory. It now builds a
zipped .app rather than a DMG.)

The script:

1. Verifies host (macOS, Xcode CLT, Rust, Node 18+, Python 3 + Pillow)
2. Builds `icon.icns` from `src-tauri/icons/icon.png` via `iconutil`
3. Builds the Tauri `.app` for `aarch64-apple-darwin`
4. Ad-hoc codesigns the `.app` (`codesign -s -`)
5. Archives the `.app` using `ditto` (not `zip` — preserves the
   code signature and macOS metadata)
6. Smoke-tests the archive by extracting and re-verifying the signature

### Prerequisites for building

- **macOS** (any recent version)
- **Xcode Command Line Tools** — `xcode-select --install`
- **Rust** — `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`
- **Node.js 18+** — `brew install node`
- **Pillow for Python 3** — the script installs this automatically if missing

### What end users see

**The release zip now contains both the `.app` AND a PDF install guide.**
When a user downloads and double-clicks `PreCut-0.3.0-arm64.zip`, Finder
extracts a folder containing:

```
PreCut-0.3.0-arm64/
├── PreCut.app
└── Read Me First - Install Guide.pdf
```

The PDF is a 3-page short-form guide with embedded screenshots of the
macOS Sequoia "Open Anyway" flow. Users read it, follow the 6 numbered
steps, and get PreCut running — all without developer hand-holding.

The PDF is generated at build time by `scripts/make_install_pdf.py` from
screenshots in `scripts/install_doc_images/`. If you update the
screenshots (for a newer macOS version, different dialog wording, etc.),
just re-run `./scripts/build_dmg.sh` and the PDF regenerates.

Summary of what users see after opening the PDF:

1. Double-click the downloaded zip → folder extracts
2. Drag `PreCut.app` to `/Applications`
3. Double-click PreCut → "Not Opened" dialog → click **Done**
4. Open **System Settings → Privacy & Security**
5. Click Privacy & Security in the sidebar
6. Scroll to bottom → click **Open Anyway** → enter password →
   click **Open**
7. First-time setup screen inside PreCut installs ffmpeg / Python deps
8. From now on, double-click works normally

### Re-running setup

Users who want to re-verify their environment can trigger a reset
from the app — the Tauri command `setup_reset` clears the flag, and
next launch shows the setup screen again. (Drop 4.44 doesn't surface
this in the UI; ship it in a later drop if users ask.)

## Dev install (unchanged from Drop 4.43)

For local development, use the existing `scripts/install.sh` which
builds and installs to `/Applications` without producing a DMG.

## What's new in Drop 3

- **XML export to Premiere** (FCP7 xmeml v5) with multiple timelines in one file
- **B-Roll Library bin** in the exported XML — every clip with its tags
  (in `Comment` field) and LLaVA descriptions (in `Description` field),
  both searchable via Cmd+F in Premiere's project panel
- **Multi-select on idea cards** — tick any combination of full plans,
  the floating bar shows a live count, one click exports them all
- **Audio sync with audalign** — detects the offset between A-roll's
  camera mic and your clean clip-on mic recording; aligned automatically
  when confidence is high, placed at offset 0 otherwise (you drag to align)
- **Both raw camera audio AND clean mic** land in each sequence as
  redundant audio tracks
- **3 new presets**: 30s Facebook Reel, 60s YouTube Shorts, 15s X vertical
- **All 8 platform-specific safezone overlays** — Instagram Reels, TikTok,
  YouTube Shorts, Facebook Reels, X, YouTube Ad (horizontal / vertical /
  square), horizontal broadcast
- **Reveal in Finder** — after export, one click opens Finder with the
  XML file highlighted, ready to double-click into Premiere

## What Drop 3 does NOT do

- **Native proxy attachment in the XML.** Adobe explicitly does not support
  proxy linkage via FCP7 XML, EDL, AAF, or any interchange format. We
  use a one-click workaround (see "After import" below).
- **Preview the timeline before export.** No preview — you import into
  Premiere and verify there.

## Install

```bash
unzip precut_app_drop4_44.zip
cd precut_beta1_package
chmod +x scripts/install.sh
./scripts/install.sh
```

First install takes 10-15 minutes (PyTorch alone is ~200MB). The installer
prefers `/Applications/` (visible in Finder sidebar), falls back to
`~/Applications/` if no sudo permission.

If you already have Drop 2 installed, the simplest path is to delete the
old app and run the installer — same instructions.

## Using the export (the main new flow)

1. Launch the app, load your project
2. Run the pipeline (if you haven't): proxy + transcribe + tag + audio index
3. Go to **Ideas** tab
4. Generate ideas via **Analyze & recommend** or **Create custom brief**
5. **Refine any concept you want to export** — concepts need at least one
   refinement to become full plans (checkbox stays disabled on concepts)
6. **Tick the checkbox on each idea** you want as a timeline
7. A floating bar appears at the bottom of the screen: *N timelines selected — Export to Premiere XML →*
8. Click Export. The Export modal opens:
   - Summary of selected timelines
   - **Save location** — click Choose, pick a folder, name the XML
   - Options:
     - Include full B-roll library (default on) — puts all tagged clips in Premiere as searchable bin
     - Run audio sync (default on) — audalign pass before export
     - Include clean mic audio as parallel track (default on)
     - Include safe-zone overlay PNG (default on)
9. Click **Export N timelines**. Progress runs in the modal (30-90s depending on sync).
10. On success: **Reveal in Finder →** opens Finder with the XML selected
11. Double-click the XML → Premiere opens → your project appears

## After importing into Premiere

Your XML will import with:
- Each timeline as its own sequence in the project bin
- A **B-Roll Library** bin with every B-roll clip
- Original (full-res) file paths in every clip reference

To use your proxies (instead of editing against the originals):

1. Open your project bin in Premiere
2. Right-click any clip → **Proxy → Attach Proxies**
3. Navigate to your proxy folder. For test footage that's:
   `~/Desktop/B-roll Buddy Test/Celeste Intv/proxies/`
4. Premiere auto-matches every clip by filename — you'll see a "X clips attached" message
5. Click the **Enable Proxies** button in the program monitor (or use Cmd+Opt+P)

After that one-time attach, Premiere remembers the proxy links for the session.

## Searching B-roll in Premiere

Once the B-Roll Library bin is in your project:

1. Select the bin in the Project panel
2. Cmd+F (or tap the search field at the top of the panel)
3. Type a tag: `kitchen`, `steel appliances`, `golden hour`, `slow pan`, etc.
4. Matching clips filter in real-time

The tags come from:
- **CLIP embeddings** (fast visual similarity tags) → `Comment` field
- **LLaVA descriptions** (natural-language descriptions) → `Description` field

Premiere searches both fields by default.

## Audio sync

When you run Export with Audio Sync enabled:
1. We extract audio from your A-roll proxies + clean mic files
2. audalign fingerprints both (Shazam-style — robust to noise, fast)
3. We compute the offset for each A-roll ↔ mic pair
4. The best-confidence pair's offset is applied to the clean mic track in every sequence
5. If confidence is below 60%, clean mic is placed at offset 0 and you drag
   it into place manually in Premiere (the ripples in the waveform make this
   a 3-second job)

## Project data location

```
~/Library/Application Support/PreCut/
├── settings.json                    ← API key (0600 permissions)
└── projects/
    └── <project-name>/
        ├── project.json             ← sources, classifications, per-file status
        ├── transcripts/             ← Whisper output (one JSON per A-roll)
        ├── broll_index/
        │   ├── precut.db       ← SQLite with clips + tags + descriptions
        │   └── vectors.lance/       ← LanceDB embeddings
        ├── audio_index/             ← ffprobe sidecars
        ├── plans/                   ← Claude's ideas (one JSON each)
        └── exports/                 ← (reserved for future)
```

Your source footage is never moved. Proxies live in `<source>/proxies/`
next to the originals. Exported XML files go wherever you pick in the save
dialog.

## Set your API key

Click the key badge in the top-right corner. Paste key, save. Done.

Get a key at console.anthropic.com (Settings → API Keys).

The app stores it at `~/Library/Application Support/PreCut/settings.json`
with 0600 permissions. Environment variable `ANTHROPIC_API_KEY` still wins
if you prefer the terminal way.

## Troubleshooting

### The Export button is greyed out
You must check at least one idea **that's a full plan** (not a concept).
Concepts show a disabled checkbox with a tooltip — refine them first by
clicking Refine and giving notes. Any refinement (even one word) promotes
a concept to a full plan.

### "Idea is a concept, not a full plan"
Same as above — click Refine on that idea first.

### Audio sync says "no pairs synced"
Either:
- No audio-kind source folders in the project (add a folder via the Audio
  drop zone in the Ingest tab)
- No A-roll proxies yet (run the pipeline)
- audalign crashed — check the activity log for the real error

### The XML imports but clips are "offline" / pink
Premiere couldn't find the original files. Usually means you moved the
footage after exporting. In Premiere's Project panel, right-click a clip →
**Link Media** → navigate to the current folder.

### Attach Proxies doesn't find matches
Make sure your proxy filenames match your originals' names (without
extension). Our pipeline names proxies the same as originals by default:
`MyClip.MOV` → `MyClip.mp4`. If you've renamed things, you'll have to link
each manually.

### Tagging is slow or descriptions are empty
Ollama + LLaVA provides the natural-language descriptions. If Ollama isn't
running, you'll still get CLIP tags (search still works, just less
descriptive). To enable full VLM tagging:
```
brew install ollama
ollama pull llava:7b
# Then launch Ollama.app
```

### Transcription is slow
Whisper's `base` model runs on CPU. 10 minutes of A-roll = ~10 minutes of
transcription. Future version may use MPS acceleration.

### Corrupt proxy from an interrupted run
If a proxy is unusually small (a few KB for what should be a 10-min clip):
```bash
rm "/path/to/source/proxies/<broken-file>.mp4"
# Click "Run pipeline" in the app — it'll re-encode
```

### API key works in terminal but not in the app
macOS GUI apps don't inherit shell env vars. Click the key badge in the
app titlebar and paste your key there — it persists regardless of how the
app is launched.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  React UI                                           │
│  ├── StartScreen                                    │
│  ├── ProjectView                                    │
│  │   ├── IngestTab, TranscriptsTab                  │
│  │   └── IdeasTab (multi-select + Export)           │
│  ├── ExportModal    (configure/running/done/error)  │
│  ├── SettingsModal, ToastStack                      │
└──────────────────┬──────────────────────────────────┘
                   │ Tauri IPC (JSON-Lines)
                   │ + tauri_plugin_dialog (save picker)
                   │ + custom show_in_finder command
┌──────────────────┴──────────────────────────────────┐
│  Rust bridge  (message routing + stderr classify)  │
└──────────────────┬──────────────────────────────────┘
                   │ stdin/stdout JSON-Lines
┌──────────────────┴──────────────────────────────────┐
│  Python backend                                     │
│  ├── settings.py, project.py                        │
│  ├── pipeline.py (proxy/transcribe/tag)            │
│  ├── producer.py (Claude analyze/plan/refine)      │
│  ├── exporter.py (DROP 3: orchestrator)            │
│  ├── backend.py (19 IPC handlers incl.             │
│  │               export_timelines)                  │
│  └── precut_pipeline/                         │
│      ├── multi_exporter.py (DROP 3: XML + bin)     │
│      ├── audio_sync.py (DROP 3: audalign)          │
│      ├── matcher.py (B-roll selection)             │
│      ├── overlay.py (bundled safezone PNGs)        │
│      └── overlays_assets/ (8 PNGs)                 │
└─────────────────────────────────────────────────────┘
```

## What's next (possible Drop 4)

- Manual audio-sync nudge UI when audalign confidence is low
- Automatic re-linking of proxies via ExtendScript .jsx on Premiere import
  (sidesteps Adobe's interchange-format limitation)
- Cache tagging/transcription results keyed by file content hash so moving
  a file doesn't trigger re-processing
- Export presets: save "B-roll Library + overlays ON" as a recurring config
