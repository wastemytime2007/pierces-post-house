# UI system and Review screen redesign (2026-10-21)

Ryan: "we really need to work on this UI and UX of this app. This is so messy and so confusing. There's a million buttons that could be solved by better solutions. It's all frankensteined together." Done with the `apple-design` skill (Apple's Human Interface Guidelines, read from `~/.claude/skills/apple-design/references/`). Desktop app (Tauri, React), macOS, one person using it for hours next to Premiere.

## 1. What the screens were (measured with `app/harness.html` and `app/dev/shots.js`, 1280 x 800)

| Screen | Controls (app DOM) | Under 28 px | Text under 11 px | Notes |
| --- | --- | --- | --- | --- |
| Review, empty | 9 | 2 | 10 | |
| Review, a cut open | 22 | 2 | 8 | version buttons, sequence chips and actions wrap into a narrow two-column grid in the top-left |
| Review, Get Creative open | 32 | 9 | 9 | |
| Review, a batch running | 29 | 2 | 17 | |
| Review page inside the frame | 10 | 0 | 6 | transport sits below a long graphics panel, far from the picture; a debug line of clip and layer timings sits above the video; the frame scrolls inside a scrolling tab |

Findings, each against the guideline:

- **Critical** `toolbars.md › Actions`: "Only specify one primary action, and put it on the trailing side of the toolbar." The screen had three cyan fills at once (selected version, selected sequence, Open in Premiere) and ten other equal-weight buttons.
- **Critical (accessibility)** `typography.md`/skill Lens 1: desktop minimum text is 10 pt, default 13 pt; 10 px mono uppercase labels were everywhere (17 in a batch view).
- **High** `color.md › Avoid using the same color to mean different things`: cyan meant selected, primary and active tab; amber meant "your turn", which is not a warning.
- **High** `sidebars.md`: a list of 11 sequences is exactly what a sidebar is for ("navigate between ... top-level collections of content"); it was a wrapped strip of truncated chips.
- **High** `modality.md › Aim to keep modal tasks simple`: Get Creative was an inline panel with 8 checkboxes and 3 buttons pushing the picture off screen.
- **High** `progress-indicators.md`: a batch showed an 11-row panel of text; progress belongs in a consistent place, determinate when possible, with a way to stop.
- **Medium** `feedback.md › Consider integrating status feedback into your interface`: a permanent banner restating that nothing is being edited.
- **Medium** `windows.md`/`the-menu-bar.md`: no menu bar commands or shortcuts for Open, Open in Premiere, Get Creative.
- **Medium** `writing.md`: numbered tabs "00 · Project / 01 · Ideas / 02 · Review", "Other cut…", "Start over", and labels in mixed case.
- **Craft**: the look is the near-black-plus-one-acid-accent template (cyan `#00e0e0` with monospace chrome); nothing in it comes from SoldFast or from film.

## 2. Thesis

The Review screen's one job: **look at a cut, fix what is off, send it to Premiere.** Everything else is secondary and lives one step away (a menu, a sheet, the sidebar).

Remembered by: **the timeline ribbon** under the picture, where every graphic, caption line, music bed and cut sits in its own labelled lane and a click on a cut adds a transition. It is the only place with color; the chrome around it is quiet.

## 3. Tokens

Brand: SoldFast Navy `#033459`, Blue `#0391d8`, Light Blue `#00ade1`, Orange `#f4690b` (attention only). Blue is the single accent. Orange is for attention (a fallback was used, something failed). Cyan is retired.

| Role | Dark | Light | Contrast (text on window / sidebar / raised) |
| --- | --- | --- | --- |
| Window | `#14181d` | `#f4f6f8` | |
| Sidebar | `#191f27` | `#eaedf1` | |
| Raised (fields, cards) | `#212934` | `#ffffff` | |
| Hover | `#2a3441` | `#e2e7ed` | |
| Text primary | `#f2f5f8` | `#141b22` | 16.3 / 15.2 / 13.4 dark; 16.0 / 14.8 / 17.4 light |
| Text secondary | `#aab5c1` | `#4a5865` | 8.6 / 8.0 / 7.1 dark; 6.7 / 6.2 / 7.3 light |
| Text tertiary (not on hover) | `#8996a4` | `#5b6774` | 5.9 / 5.5 / 4.9 dark; light recomputed in the test |
| Accent text and focus ring | `#4cb3ee` | `#0369a3` | 7.6 dark on window; 5.5 light |
| Primary button fill (white label) | `#0a72b8` | `#0369a3` | 5.1 and 5.9 (brand blue `#0391d8` itself is 3.5: used for the picture's selection and lines, never behind small text) |
| Attention | `#ff8a3d` | `#a84300` | 7.6 dark |
| Good / Bad | `#4ccb7a` / `#ff6b61` | `#1b6e3a` / `#b82617` | above 4.5 |

A test (`app/tests/design_tokens.test.mjs`) computes every text-on-surface pair above from the stylesheet and fails below 4.5:1 (3:1 for non-text).

Type: the system font (SF Pro on macOS, via `-apple-system`), no network fonts. Scale: 11 caption, 12 footnote, **13 body**, 13 semibold, 15 title, 17 large title; weight and size carry hierarchy, no all-caps tracked labels. Monospace only for file paths and timecodes (tabular figures).

Space and shape: a 4 px grid; control height 28 (desktop default), 24 minimum for dense lists; radii 6 (controls), 10 (cards, sheets), concentric with their containers; separators are 1 px at 10% foreground.

Motion: 120 to 200 ms, only for state changes (a sheet appearing, a row finishing); all of it off under reduced motion.

## 4. Structure

Navigation is by **sequence**, because that is what he works across (11 shorts at a time). Actions live where Apple puts them: one primary on the trailing edge of the toolbar, the rest in menus and a sheet.

```
 App toolbar     ‹  Runnells Weekend                 [ Project | Ideas | Review ]                    ⓘ Activity   ⋯
 ───────────────┬──────────────────────────────────────────────────────────────────────────────────────────────────
 Sequences  8   │  WK_08  If it is someone's home…          V2 ▾        Get Creative…     [ Open in Premiere ]    ⋯
 ───────────────│  ┌───────────────────────────────┬───────────────────────────────┐
 ✓ WK_01 Never… │  │                               │ Graphics   Notes   Details    │
 ✓ WK_02 When…  │  │            picture            │ ○ Overlay                     │
 ◌ WK_04 Fixing │  │          (transport)          │ ● Title: If it is…            │
 · WK_05 Feeli… │  │                               │   words  size  put back       │
 ▌WK_08 If it…  │  ├───────────────────────────────┴───────────────────────────────┤
                │  │  timeline ribbon (the signature)                                │
 Getting creative 4 of 8  ━━━━━━──  Stop        └─────────────────────────────────────────────────────────────────
```

- **Sidebar (Sequences)**: only when more than one sequence is open; full names wrapped to two lines; a status glyph per row (done, working, waiting, needs attention); right-click and the row's menu: Review, Open in Premiere, Show in Finder, Start over. Hidden with one cut. A determinate progress bar and Stop sit at the foot of the list while Get Creative runs.
- **Toolbar**: title; a version pull-down (`V2 ▾`, replaces the V1/V2 buttons); **Open in Premiere** (the only prominent button); Get Creative…; a More menu (Show in Finder, Premiere version…, Start over, Choose another XML…).
- **Get Creative** is a sheet: what it does in one sentence, two small groups of checkboxes (Cut: ums and uhs; Add: captions, graphics, music, sound effects, bleep), a segmented control for scope (This sequence / All 8), a disclosure for "Start over from the original", Cancel and a primary Get Creative (Return).
- **Review page** (the framed page): picture and its transport together, an inspector on the right with tabs Graphics / Notes / Details (the debug line moves to Details), the timeline ribbon under both; the frame fills the window, so there is one scroll, not two.
- **Empty state**: one sentence, one primary button (Choose XML…), recent cuts as a list, drag and drop.
- **Feedback**: no permanent banners. Errors appear inline near the toolbar, dismissable, in words with the next step. Progress is determinate and in one place.
- **Keyboard and menus**: Open XML ⌘O, Open in Premiere ⌘E, Get Creative ⌘G, Show sidebar ⌘⌥S, version up and down ⌘[ ⌘]; all listed in the More menu (a native menu bar is the follow-up in `src-tauri`).

## 5. Critique of this plan (skill, design improvement step 3)

Would I have produced this plan for any video app? The token system alone, yes: dark neutral, one blue accent, system type, is deliberately conventional, because chrome should defer to the picture and to Premiere sitting next to it (`branding.md` in the skill: familiar components carry navigation, identity lives in a few defining moments). What is specific to this product: navigation by sequence and a batch that can be left running; one primary action that is always "send it to Premiere"; the timeline ribbon with click-a-cut transitions and graphics lanes; the brand blue used as the only color and orange reserved for "something fell back". Nothing else is decorative. One thing removed on the second pass: the per-sequence "Review" and "Open in Premiere" buttons in the batch list, which became sidebar rows with a menu.

## 6. Order of work

Accessibility (sizes, contrast, focus) and tokens first; then conventions (toolbar, sidebar, sheet, menus); then craft (the ribbon, empty states, wording); then the other tabs. Each step is checked from screenshots of the real screens (`app/dev/shots.js`) and from numbers.
