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
