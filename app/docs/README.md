# User-facing documents

The two PDFs shipped to beta testers. Both predate this repo, so check
the version stamps below before handing either to anyone.

## PreCut_OnePager.pdf

One-page product overview: what PreCut does, the four-step workflow,
what beta testers should focus on, and install requirements. This is the
"what is this thing" document, given to testers alongside the build.

- **Stamped version:** 1.0.0-beta.2
- **Created:** 28 April 2026
- **Repo version:** 1.0.0-beta.3

**No generator exists for this file.** The PDF is the only copy; there is
no script or source document in the repo that produces it. Treat it as an
original asset. If it needs editing, it has to be rebuilt in whatever
tool made it.

Content worth knowing, since it is the public promise of the product:

- Positions indexing, syncing, and organized export as the core value,
  with the AI producer explicitly optional. Footage-only mode is called
  out as a first-class path.
- Lists eight features: auto-sync audio, searchable B-roll library, local
  transcription, organized Premiere export, Default Includes,
  platform-aware presets, AI producer, footage-only mode.
- Beta feedback address: `info.prep2post@gmail.com`.
- States plainly that footage stays on the user's machine and only
  AI-producer transcripts reach Anthropic, and only when asked. Any change
  to what leaves the machine makes this document wrong.

## Read Me First - Install Guide.pdf

Two-page illustrated Gatekeeper walkthrough: unzip, drag to
Applications, then the System Settings > Privacy & Security > Open Anyway
sequence, with three annotated screenshots. Ships inside the release zip
next to the app.

- **Stamped version:** 0.45.5
- **Created:** 25 April 2026
- **Repo version:** 1.0.0-beta.3

**This one is a build artifact, not a source document.** It is generated
by `scripts/make_install_pdf.py`, which draws on
`scripts/install_doc_images/*.png` and `src-tauri/icons/icon.png`. The
copy committed here is the last known-good build, kept so the shipped
document is not lost.

To regenerate at the current version:

```bash
python3 scripts/make_install_pdf.py "dist-release/Read Me First - Install Guide.pdf" 1.0.0-beta.3
```

Requires `reportlab`. It is not in `requirements.txt`, but it is already
present in `/usr/bin/python3`, which is the interpreter `build_dmg.sh`
picks, so the build works as-is. It is absent from the project venvs, so
run the script with `/usr/bin/python3` if you invoke it by hand.

In practice you rarely need to run it directly: `scripts/build_dmg.sh`
regenerates this PDF at the correct version on every release build and
bundles it into the zip beside the app.

Edit the script, not the PDF. Anything changed in the PDF directly is
lost on the next release build.

## Before the next release

- The install guide regenerates automatically on every `build_dmg.sh`
  run, so the shipped copy always matches the build. The committed copy
  here is only the historical artifact.
- Rebuild or re-stamp the one-pager, which still says beta.2. Note it
  also tells testers the install guide is "inside the zip", so the
  release zip must keep including it.
