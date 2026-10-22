# Polish log (overnight, 2026-10-21)

Each round is one change someone could point at. "Checked" says how; screenshots are of the real screens in a browser with a stand-in backend (`app/dev/shots*.cjs`), not the running Tauri window. Direction and sources: `DIRECTION_2.md`.

| # | Round | Checked |
| --- | --- | --- |
| 1 | Research pass and Direction 2 written (rooms, not a dashboard) | sources listed with their weaknesses in the doc |
| 2 | Journey bar: Footage, Stories, Trim, Finish as the navigation, each with live status | screenshot dark |
| 3 | Trim step: what to do in Premiere, the XMLs sent, a drop target to bring the trimmed XML back | screenshot dark |
| 4 | An XML dragged onto the window anywhere opens in Finish, with a veil that says so | build only; Tauri drag events cannot be simulated in the browser |
| 5 | "Ideas" is now "Stories" in every visible string | grep |
| 6 | Welcome text is the four steps | build |
| 7 | Command palette (⌘K): every step, action, project and setting, fuzzy-filtered, keyboard only | screenshot dark, filter typed, build |
| 8 | Keyboard shortcuts sheet (?), grouped | screenshot dark |
| 9 | Sheet focus no longer draws a stray outline | build |
| 10 | "Ready for ideas?" became "Ready to find stories?" | grep |
| 11 | Project covers say where the project stands ("Ready to find stories", "4 stories found", "XML out for trimming"); backend `project_summary` | `test_project_summary.py` (2), run on the real projects |
| 12 | The summary also counts XMLs saved anywhere, from the app's own export record | run on the real projects: Wall Removal 2, Weekend Septic 7 |
| 13 | New project is a sheet over the home screen, not a form that pushes the list down | screenshot dark |
| 14 | New app icon: the house-and-play mark as a proper macOS icon (`app/dev/icon.svg`, full icon set generated) | rendered and viewed; Dock appearance not seen |
| 15 | The sequence listing names the frame that stands for each sequence (first clip on V1, a second in) | `test_split_sequences.py` (9), run on the real 71-sequence XML |
| 16 | Frames are cut from proxies on any connected drive (`...Proxies` folders), so RED R3D long-form works | real XML: frames for 9 of 12 sequences checked (the other 3 have no proxy on the connected drives) |
| 17 | The sequence picker is a contact sheet: each sequence by its own frame, check marks, sticky "Open N sequences" | screenshot dark and light |
| 18 | Duration chips read 0:28, not "0 min" | screenshot |
| 19 | While choosing, the sheet has the whole screen (no intro above it) | screenshot |
| 20 | Board: every open sequence as a tile with its own (finished) frame and a status chip; one click opens it | screenshot dark |
| 21 | The board opens by itself when a Get Creative batch ends | build; needs a real batch to see |
| 22 | When every sequence is finished the heading's check draws itself, once (the one celebratory moment) | build; CSS animation, not seen mid-draw |
| 23 | A macOS notification when a batch ends ("All 8 sequences finished. Ready for review.") | `test_creative_notify.py` (6); a real notification not sent tonight |
| 24 | One tooltip for the whole app (delayed first, instant between neighbors, keeps accessible names), replacing the browser's | screenshot dark; confirmed the title moves to data-tip |
| 25 | Right-click a project on the home screen: Open, Show in Finder, Delete | screenshot dark |
| 26 | Stories empty state with the next action (Plan with AI, Find stories), and a note when interviews are not transcribed yet | screenshot |
| 27 | The no-API-key state in plain words ("Stories need an API key") with two clear actions | screenshot light |
| 28 | The "N stories chosen" bar is a floating translucent pill | build |
| 29 | Export XML is the primary action on Stories; the in-app preview is secondary | build |
| 30 | A finished export hands off: "Next: trim in Premiere" goes to the Trim step | build |
| 31 | Display type: page titles and the greeting in the brand's geometric voice (Futura now, Avant Garde if installed); body stays system | screenshot dark |
| 32 | Title-bar wordmark in the same display face | build |
| 33 | Second research pass (Things, Pixelmator, Superhuman, type), written up with what was applied and what was not | doc |
| 34 | Accessibility checker (axe) wired into both screenshot drivers (`AXE=1`), run on every screen in dark and light | first run: 4 serious kinds found |
| 35 | The faintest text color (`--fg-3`) raised until it passes 4.5:1 on all surfaces in both appearances, and added to the contrast test | `design_tokens.test.mjs` 7 pass |
| 36 | Landmarks (main, nav) on the home, project and setup screens; project cover buttons are named by what they show | axe |
| 37 | Idea card titles are second-level headings (no skipped level) | axe |
| 38 | Names for the command list, the progress bar and the welcome dots; the Get Creative sheet is a dialog containing a form (valid roles) | axe |
| 39 | Result: no serious or critical findings left on any screen in either appearance; remaining are "region" notes on dialogs and the tooltip | axe, dark and light, both drivers |
| 40 | Review ribbon: one calm palette (muted blue, green, gold, violet) instead of saturated neon lanes | screenshot |
| 41 | Review ribbon: a real time ruler (0s, 2s, 4s... or 0:30, 1:00) replaces the empty gray bar | screenshot; both page scripts pass |
| 42 | Notes: drawing tools (pen, circle, arrow, box, colors) appear only after choosing Draw on frame | screenshot; drive_page.js 9/10 (the one failure is a 40 s seek on an 11 s clip, same as before) |
| 43 | Notes: copy and download are quiet text buttons; tool buttons smaller | screenshot |
| 44 | Send all finished sequences back to Premiere as ONE XML: each is export-checked first, only those that pass are merged (the rest named with why), every id namespaced per source, output read back and checked, a new file beside the sequences (never over one) | `test_merge_sequences.py` (7), `test_merge_finished.py` (4); run on the real 11 finished sequences: 11 of 11 pass the export check, merged file has 11 sequences, every clip's timing and footage identical to its own file; not yet opened in Premiere |
| 45 | The board has "Send N to Premiere" and a result card with Open in Premiere and Show in Finder | screenshot dark |
| 46 | Opening a project lands on the step it is waiting at (Footage, Stories, or Finish) instead of always Footage | build; sessionStorage handoff, needs a real project to see |
| 47 | Each cover says what is next ("Bring back your trimmed XML", "Find stories") as a pill (always on the big one, on hover for the rest) | screenshot dark |
| 48 | The title bar shows the open project by its own frame and name | screenshot dark |
| 49 | The wordmark is the way back to all projects | build |
| 50 | Ambient: a soft glow from the open project's own frame behind each page (decoration only, hidden from assistive tech) | screenshot dark and light |
| 51 | Sheets, dialogs and the palette arrive with a short soft scale (190 ms); reduced motion turns it off | build |
| 52 | Reduced transparency: solid surfaces replace the blur, and the ambient glow is hidden | CSS only; the media query was not testable in headless Chrome |
| 53 | Increased contrast: firmer separators and one step stronger text, glow hidden | CSS only; not testable in headless Chrome |
| 54 | Cursors: pointer only where something happens | build |
| 55 | Activity panel: a quiet column of time and message, problems in the attention color, an empty state that explains itself, Clear disabled when empty | screenshot |
| 56 | A crash in one step (Footage, Stories, Trim, Finish) shows a message inside that step with the navigation intact | build; forced crash not tested |
| 57 | The wordmark button's accessible name contains its visible text | axe: no serious or critical findings, both appearances, both drivers |
| 58 | The review page keeps its side-by-side layout down to the smallest window (1000 by 700) and stacks only below 680 px; checked no sideways overflow on any screen at 1000 wide | screenshots at 1000 by 700, overflow measured on 31 states |
| 59 | ⌘1, ⌘2, ⌘3, ⌘4 jump between the four steps | tested in the browser harness (all four) |
| 60 | A Go menu (the four steps and the command palette) and a Help menu (keyboard shortcuts) in the native menu bar | `cargo check`; menu not clicked in the running app |
| 61 | Welcome: the mark with a glow and a display-type title on the first screen; the second screen drops the graphic that repeated the list | screenshots dark |
| 62 | Footage: project details (folder, names, type, audience) fold into one line once the project is set up, so the footage zones lead; they stay open for a new project | screenshot dark |
| 63 | Home: a filter box appears once there are more than six projects; the count shows ("All projects (15)") | screenshot not taken for the filter; build |
| 64 | Footage zones show a frame beside each source, cut from its first clip | screenshot dark (with stand-in frames) |
| 65 | Review ribbon: a filmstrip lane (frames of the picture along the timeline, click to seek, playhead) built inside the page from the preview itself | screenshot; three page scripts pass (drive_page: its one known 40 s check) |
| 66 | A bridge connects the browser test page to the REAL Python backend (`app/dev/bridge.cjs`) and an end-to-end script reads the real projects and a real 71-sequence XML through the real UI (`app/dev/e2e_real.cjs`; reads only) | ran: 15 projects, 13 covers from real footage, 71 sequences, 55 real frames in 15 s, no page errors |
| 67 | Found by that run: a request for 71 frames was cut off at 60. Frames are now requested in chunks of 40 and the backend allows 80 per request | e2e run |
| 68 | End to end on the real backend through the core Finish flow, on a scratch COPY of a real many-sequence XML: contact sheet, choose 2, open, the real review builds (`app/dev/e2e_finish.cjs`) | ran: 11 sequences, review open in 23 s, real filmstrip, no page errors |
| 69 | Found by that run: the tooltip logic showed "Review V1" over the page inside the review frame (frames are now ignored); and tiles for cuts without a preview showed nothing: they now use a frame of the cut's own footage from the listing | e2e screenshot: both sidebar tiles show real frames |
| 70 | Dropdown text was clipped (select height 28 px): selects are now 32 px with room for the arrow | screenshot of the Footage tab |
| 71 | The sequence board checked on the real finished batch of 11 WK cuts (`app/dev/e2e_board.cjs`; reads only, sends nothing): 11 "Finished" chips, 11 real frames, "Send 11 to Premiere" | ran: no page errors; screenshot read |
| 72 | Contact sheet: shift-click picks a range (click tile 2, shift-click tile 6 = 5 chosen), with a hint in the footer; one click per tile was slow for 71 sequences | ran on the real 71-sequence XML: 5 chosen, no page errors (`dev/e2e_real.cjs` now checks it) |
