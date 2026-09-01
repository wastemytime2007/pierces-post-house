# Deferred harvests — Ryan's Mac only

These capabilities are **not** wrapped in this directory. Each needs
PreCut's real ML venv (`~/precut-venv-fresh` per `precut/ARCHITECTURE.md`
"Runtime environment": torch, whisper, lancedb, open_clip, PIL, rich,
anthropic) and, for two of the three, real footage/audio — neither is
available in a cloud session. Implementation happens in a Mac session
with that venv active. Writing stub modules that `raise
NotImplementedError` was deliberately avoided: a stub is importable dead
code that invites an accidental `import posthouse.harvest.transcribe`
somewhere that silently "succeeds" until it's actually called — better to
have nothing here at all than something that pretends to be a wrapper.

## Transcribe

**Source:** `precut_pipeline.transcriber` (`import torch`, then Whisper
via `precut_pipeline.config.WHISPER_MODEL`).
**Contract sketch:** input is an A-roll audio/video file; output is a
`Transcript` (a `source_path`, a `duration`, and a list of `Phrase`
objects — `id`, `start`, `end`, `text`), already the on-disk shape PreCut
persists per A-roll under a project's `transcripts/` dir
(`docs/ARCHITECTURE.md`'s artifact table: "Transcript … JSON per A-roll").
ROADMAP.md §7 flags a **Whisper timing-bias** risk: reuse PreCut's
existing phrase-boundary padding logic rather than re-deriving it from
scratch on the Mac.

## Tag / index

**Source:** `precut_pipeline.tagger` (Ollama/LLaVA vision tags),
`precut_pipeline.claude_tagger` (Claude vision tags, `ANTHROPIC_MODEL`),
`precut_pipeline.embedder` (`numpy` + `torch` + `PIL` + `open_clip` CLIP
embeddings), `precut_pipeline.database` (`lancedb` + `pyarrow` + `numpy`
vector storage), `precut_pipeline.matcher` (consumes all of the above).
**Contract sketch:** input is B-roll frames sampled at ingest; output is,
per clip, a natural-language description + tag list (written to SQLite's
`frames`/`clips` tables — the exact schema `multi_exporter.
load_broll_library` already reads, see `safety_net/conftest.py`'s
`_make_broll_index_db`) plus a CLIP embedding per sampled frame in
LanceDB (`broll_index/precut.db` per `docs/ARCHITECTURE.md`'s "on-disk
project artifacts" door). This is the index Phase 4's subject-grouping
clusters over.

## Sync

**Source:** `precut_pipeline.audio_sync` — the module itself imports
clean (stdlib only at module scope: `hashlib`, `re`, `time`,
`dataclasses`), but its actual cross-correlation work lazily imports
`audio_offset_finder.audio_offset_finder.find_offset_between_files` at
call time, which pulls in real audio-processing deps. **Contract
sketch:** input is a camera A-roll audio track plus one or more
candidate lav/boom WAV files; output is an `AudioSyncState` with, per
`(aroll, lav)` pair, an `offset_sec` and a match `score`, thresholded at
`SCORE_USE=10.0` per `safety_net/README.md` "Scoped out". Per
`safety_net/README.md`, synthetic sine-tone audio cannot clear that
score floor — this is a real-footage-only test, not just a real-venv-only
one, which is why it's Tier 2 even beyond the ML-dependency question.
ROADMAP.md Phase 4 also needs a settled answer for below-threshold pairs
(included-and-flagged / dropped / surfaced) before this is implemented,
not just wrapped.
