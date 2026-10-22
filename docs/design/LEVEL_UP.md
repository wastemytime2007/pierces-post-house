# Level up: what the overnight research says this app still needs (2026-10-22)

Ryan: "Go through all of the research done all night on app UI and user experience. How the best app developers create mind blowing app experiences and create a list of everything that can/should be done to this app to make it feel like its on that level."

What was read, and how far to trust it, is in `DIRECTION_2.md` (two research passes), `EXPERIENCE.md` (the "why it felt AI-built" list) and `UI_SYSTEM.md` (the HIG review). The primary sources were Apple's HIG, Linear's own redesign post, Emil Kowalski on animation, Apple's Halide piece, Jony Ive as reported, Things 3 and Superhuman as reported, Pixelmator's launch posts. The agency-portfolio research (Instrument, Metalab, Fantasy, Pentagram) came back thin: positioning pages and ranking blogs, no design write-ups, so nothing below is claimed from them. Seventy-nine rounds are already done (`POLISH_LOG.md`); this list is what is left.

Each item carries where it comes from: **[research]** a principle one of the sources states, **[HIG]** an Apple guideline, **[judgment]** my own reading of what the best apps do, not backed by a source I opened. And a status: not done, partly, or done.

The one sentence the sources agree on: **the app should feel like a post house you walk into, where your footage is already on the walls, nothing asks you a question it could answer itself, and the few moments that matter are made with care.** Everything below serves that.

## A. The first five minutes

1. **A sample project on first run** [research: Superhuman teaches in-product with real-looking data] not done. A new person sees a real cover, real stories and a real cut before they have footage. Ship one short interview (SoldFast's own, cleared) inside the app; "Try it with a sample" on the welcome screen; the sample deletes itself when the first real project is made.
2. **Launch into where you were** [research: Linear's local-first "instant"] not done. Reopen the last project on the step it was on, the window at its last size and place. Today the app launches to Projects every time and the window forgets its size.
3. **Say the person's name** [judgment] not done. "Good morning, Ryan." from the macOS account name; no setup step.
4. **The welcome is a person, not a list** [research: Superhuman onboarding] partly. The welcome modal is four steps of text. Replace with the sample project already open and one line over it.
5. **Teach through use, not up front** [HIG `onboarding.md`] partly. The first time each room is entered, one line in the page header says what to do; never a tour. Remove the tour code (`useTour.js`) if it is still reachable.

## B. Speed and trust

6. **A performance budget, measured and kept** [research: Linear's "details are felt"] not done. Home with covers under 1 s; a room change under 100 ms; a frame under 150 ms; no layout jump as frames arrive (reserve the aspect box). Add the numbers to the e2e scripts so a regression fails a run.
7. **The app knows when its backend is stale** [judgment, from today's three failures] not done. The backend reports its build hash on `hello`; when it differs from the UI's, a quiet bar says "Restart to finish updating" instead of letting a button fail with "Unknown command". Nothing a person did today was wrong; the app was.
8. **Every long thing can be left and comes back** [HIG `loading.md`] partly. Get Creative does; the export build and the Premiere open do not. One progress model: a determinate bar in the sidebar foot, the Dock icon shows the same bar, the window title shows "Building 2 of 4".
9. **No raw strings reach the person** [HIG `writing.md`] partly. "Unknown command: open_in_premiere_project", "undefined A-roll phrases" and tracebacks are developer text. Every error is three parts: what happened, why, what to do; the raw text lives in Activity.
10. **Undo where a thing can be undone** [HIG `undo-and-redo.md`] not done. Removing a story from the chosen set, a note, a graphic move; ⌘Z, and "Undo" in the Edit menu that is already there.

## C. Keyboard and reach

11. **Every action has a key, and the key is shown** [research: Linear] partly. The palette and ⌘1 to 4 exist. Missing: shortcuts printed in tooltips and in every menu item; J/K/L and arrows in the review player; ⌘Enter for the primary action of the current room; Space on a contact-sheet tile to preview it large (Quick Look); arrows and Enter through the sidebar and the board; Esc closes anything.
12. **The palette knows your projects' content** [research: Linear's one door] partly. Type a story title or a sequence name and jump straight to it, across projects.
13. **Nothing is mouse-only** [HIG `accessibility.md`] partly, from axe runs. A VoiceOver walk of each room, start to finish, with the issues fixed.

## D. Native macOS

14. **The window chrome is ours** [HIG `windows.md`; Apple's own pro apps] not done. Hidden title bar, traffic lights inside our toolbar, the toolbar as the drag region. Needs one live check that the window still drags.
15. **Window memory** [HIG `windows.md`] not done. Size, position, sidebar width, last room, per project (`tauri-plugin-window-state`).
16. **Dock and notifications carry the work** [HIG `notifications.md`] partly. A badge with the count of finished sequences; the notification has a Review button that opens the board; progress on the Dock icon during a batch.
17. **Settings is a settings window** [HIG `settings.md`] not done. ⌘, opens a fixed-toolbar window (General, Premiere, Audiences, Includes); the Settings menu goes.
18. **Drag out, not just in** [judgment] not done. Drag an XML or a finished cut from the Trim list or the board into Finder or Premiere's project panel.
19. **The menu bar is complete** [HIG `the-menu-bar.md`] partly. Go and Help exist. File: New Project, Open Project, Open XML, Open in Premiere; Edit: Undo, Find (filters the current room); View: Show Sidebar, Show Activity, Board; Window: standard. Every toolbar item reachable from a menu.
20. **Premiere, visibly linked** [judgment] not done. Where the version picker is: "Premiere Pro 2026, panel connected" with a green dot, from the heartbeat that already exists; "panel not loaded, restart Premiere" when it is not. Today the only way to know is to try.

## E. The footage as the interface, further

21. **Covers come alive on hover** [research: Apple TV, Criterion; Frame.io hover scrub] not done. Move across a cover or a tile and it scrubs through five frames of that interview; a two-second loop on the big cover. Frames are already cut; this is the next use of them.
22. **The Trim room shows cuts, not documents** [research: content carries the design] not done. "Sent to Premiere" is a list with a document icon; show each export as a strip of its sequences' frames, and the finished ones with their finished frame.
23. **Stories as storyboards, always** [EXPERIENCE direction] partly. Storyboard strips exist; cards without frames fall back to a film icon tile. Cut a frame for every moment a story uses, in the background, as soon as stories arrive.
24. **One frame means one thing** [research: Things, color only where it carries information] partly. The cover, the title-bar chip and the ambient glow all come from the same frame; keep it that way, and let the person pick the frame (right-click a cover: "Use this frame").
25. **Light appearance is the one that most people will see on a laptop in daylight** [HIG `dark-mode.md`] done, checked; keep both in every screenshot run.

## F. The signature: the ribbon and the Finish room

26. **A waveform lane** [research: Frame.io review lives on the frame] not done. Under the filmstrip, the audio as a quiet waveform; music and SFX beds drawn on it; the bleep marks visible.
27. **A and B versions side by side** [judgment; Frame.io compare] not done. V1 against V2 on one scrubber, locked together; the fastest way to judge what Get Creative changed.
28. **Drag on the picture** [judgment] partly. Graphics move by Basic Motion from the list; let the person drag the title on the frame, with snap lines to the safe zones, since the overlay PNGs already know where those are.
29. **Captions as text in the lane** [judgment] partly. Caption words show in the ribbon; editing a word in place (the one thing noted as not built) is what makes the lane feel real.
30. **Transport that feels like an NLE** [judgment] partly. J/K/L, comma and period for a frame, I and O for a range to note, Space to play; a timecode you can click and type into.

## G. The moments (Ive's "the way a cable unwraps")

The sources agree animation has a job and should be rare. These are the only places it should live, each under 300 ms, each off under reduced motion. Only the first is built.

31. **A batch finishes**: the check draws itself [done, round 22].
32. **The first project**: its cover appears from a neutral tile to its first frame, once the frame is cut; the greeting changes to name it [not done].
33. **Stories arrive**: the storyboards appear in order, one after another, as the backend produces them [not done; today they appear all at once at the end].
34. **The XML goes out**: the Trim tile becomes "Open the XML in Premiere" with a short settle; when Premiere has it, the journey bar's Trim step shows "in Premiere" [partly].
35. **The XML comes back**: the drop veil (built) and then the sequences lay out on the board one at a time [partly].
36. **Sent to Premiere**: the result card appears and the journey bar's Finish step closes [partly].
37. **A sound, off by default** [research: Things rewards completion with a sound] not done. One quiet sound for "batch finished" and "XML came back", a setting, off until turned on (it was not added overnight because a batch can finish at 3 a.m.).

## H. Words: one voice

38. **A copy pass over every string** [HIG `writing.md`; the tells list] partly. The rooms were renamed; the dialogs were not all rewritten. Rules already set: verb plus object on buttons, headings that stand alone, no eyebrow labels, no middle-dot metadata, no "Submit", no "Export" where the person means "Open in Premiere", no jargon ("audalign", "clipitem", "interpretation target") outside Details.
39. **Empty states that invite the one next action** [HIG `feedback.md`] partly. Done for Stories and Finish; check Footage with no sources, Trim with nothing sent, the board with nothing finished, the Activity panel.
40. **Errors that say what to do** [HIG `alerts.md`] partly. See item 9; also no error should default to "OK".

## I. Type and look

41. **The brand face, really** [research: display face only; SoldFast's identity] not done. Install ITC Avant Garde Gothic LT Bold on the machines that run the app (the stack already picks it first); until then Futura stands in.
42. **macOS 26 materials in the chrome** [HIG `materials.md`, `liquid-glass.md` in the skill] partly. The toolbar is translucent in CSS; use Tauri's window effects for a real sidebar vibrancy, so the chrome reads as the system's, not a web page's.
43. **The icon in its variants** [HIG `app-icons.md`] partly. The icon set is generated; the dark and tinted variants macOS now shows have not been made or looked at in the Dock.
44. **Radii and shadows by role, not one value** [EXPERIENCE: the "identical cards" tell] partly. Controls 6, cards 10, sheets 12, window 0; concentric where nested. Audit for cards that still share one radius and one shadow.

## J. What not to do (the tells, so they do not creep back)

45. No icon in a tinted square on every card; no 01/02/03 where nothing is a sequence (the journey is one, so it keeps its numbers); no gradient text, glow for its own sake, emoji, decorative blobs; no hero with three feature cards; no fade-up on everything; no feature that exists to look busy. From `EXPERIENCE.md` § 4 and `DIRECTION_2.md` § 8.

## If five are done first

Chosen by what Ryan could open and judge the same day, and by how much each changes the feel.

1. **Launch into where you were, with window memory and the name** (2, 3, 15): the app stops feeling like a web page that reloads.
2. **Backend version handshake and the three-part error** (7, 9): today's three failures were this; it is the difference between an app and a prototype.
3. **Hidden title bar and the native Settings window** (14, 17): the chrome becomes Apple's, which is what "professionally built" reads as on a Mac.
4. **Covers and tiles scrub on hover, Trim shows cuts as frames** (21, 22): the footage does more of the talking in the two rooms that still show documents.
5. **A and B side by side, plus J/K/L** (27, 30): the Finish room starts to feel like a tool an editor would choose.

The sample project (1) is the right first thing for anyone else who will ever open the app; for Ryan alone it can wait.
