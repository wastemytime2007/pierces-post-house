# Settled decisions

Design calls that are already made. Treat these as constraints, not open
questions. If one needs to change, change it deliberately and update this
file in the same commit.

## Delivery path

**Premiere FCP7 XML is the delivery mechanism.** PreCut writes one XML;
the editor imports it. This is settled as of August 2026.

A Premiere panel plugin (CEP/UXP) was explored separately and has been
**abandoned**. Do not add plugin, extension, ExtendScript, or panel code
to this repo. If you find a reference to `premiere_exporter.py`,
`export_to_premiere`, or `premiere_export_ready`, it belongs to that dead
branch and should not be reintroduced. (Note: `export_to_premiere_xml` in
`precut_pipeline/exporter.py` is the *XML* path and is correct.)

## AI and cost

- **API key, not OAuth.** Anthropic banned third-party subscription OAuth
  on 4 April 2026. Per-token API billing is the only sanctioned path.
- **Claude vision for B-roll tagging by default.** LLaVA via Ollama stays
  as a fallback when no API key is set.
- Tagging costs roughly $0.10 per minute of B-roll, charged once at
  ingest.
- **`BANNED_TAGS` in `claude_tagger.py` is aggressive by design** (110
  entries). It strips hallucinated atmosphere words like "cozy
  atmosphere" and "minimalist decor". False drops are cheaper than noise.
  Do not soften it.
- **Motion tags are deterministic**, computed from ffmpeg frame sampling
  plus numpy in `motion_analyzer.py`. They are not model output and never
  hallucinate.

## B-roll suggestions

- **Markers replace B-roll clips.** The XML emits `<marker>` elements as
  sequence children rather than placing clips on V2. The empty V2 track
  is omitted entirely.
- **One marker per (phrase x category) pair.** A phrase mentioning both
  kitchen and bathroom produces two markers, staggered within the phrase.
- **Category-based matching, not word-literal.** Both the spoken phrase
  and the clip tags route through the `THEME_CATEGORIES` synonym
  vocabulary. The interview subject says "couch", Claude tags "sofa", and
  both map to `living_room`. Do not revert to literal word matching.
- A marker only emits for a category if the phrase mentions it **and**
  the library actually has clips in it.
- **Short marker names** carrying 3 to 5 primary tags. The full tag list
  goes in the marker comment.
- **Color-coded by theme category**, not by confidence. Fourteen
  categories: kitchen (orange), bathroom (cyan), bedroom (purple),
  living_room (green), dining_room (yellow), office (gray), laundry
  (slate), exterior (brown), yard (forest), pool (blue), neighborhood
  (rose), detail (olive), people (coral), other (medium gray).
- To find suggested footage, the editor copies the tag phrase from the
  marker comment and pastes it into the Premiere project panel search.

## FCP7 XML details that were expensive to learn

These were each found by shipping a broken build and diffing against
Premiere's own XML. Do not undo them without a Premiere test.

1. **Case-sensitive extension probing.** `_find_original_for_proxy` does
   a case-insensitive directory scan that returns the real on-disk
   filename case. macOS is case-preserving but `Path.exists()` is
   case-insensitive, so probing `.mov` against a `.MOV` file matches but
   hands back the wrong-case path, and Premiere then cannot find it.
2. **Library `<file>` blocks emit both `<video>` and `<audio>`
   samplecharacteristics.** Premiere silently rejects clips whose audio
   streams are undeclared.
3. **Master clip id is `masterclip-N`**, distinct from the library clip
   id `broll_lib_clip_N`. Timeline clipitems reference `masterclipid`
   matching the library's. This is what makes clip linking work.
4. **Library clip `<out>` equals `<duration>`**, not `duration - 1`. An
   earlier off-by-one "fix" was wrong; Premiere's own reference XML
   confirmed it.
5. **`duration_frames` is `duration_sec * fps`, rounded.** Do not use
   ffprobe's `nb_frames`. It can be short by one frame on ProRes, and
   Premiere rejects clips whose duration exceeds available frames.
6. **DB schema migrations are additive only** (`ALTER TABLE ADD COLUMN`)
   and wrapped in try/except for idempotence. Never drop a column.

## Debugging technique that works

When Premiere rejects or mangles something, get Premiere to export its
own version of the same content (`File > Export > Final Cut Pro XML`) and
diff it against ours. That comparison has repeatedly found real bugs in
seconds. Reading the FCP7 spec in the abstract has repeatedly wasted
hours.

## Bin layout

`Seq/`, `Footage/`, `Audio/`, and `Files/` are top-level siblings in the
Premiere project panel. There is no project-name wrapper bin around the
sequences. See ARCHITECTURE.md for the full tree.
