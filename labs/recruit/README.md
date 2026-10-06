# labs/recruit: find the footage that helps recruit franchisees and hire subcontractors (standalone, not in app/)

**Units done so far: two.** The May 15 recruitment interview (22 moments) and the June 13 weekend interview (23 moments, below). `index.html` in `~/Documents/Post House Reviews/Recruitment footage (proof)/` links both.

Ryan (2026-10-05): "go through the footage to find any footage that will help us to recruit locations (franchisees) and hire sub contractors for Bob." This is the **exhaustive reading** PreCut's story planner skims
(`precut-capabilities`: one call, about 9 ranges per run). Per rule 7 it was proved on ONE unit first, the Bob and Mitch recruitment interview of May 15; nothing else has been scanned until Ryan reacts.

| Tool | What it does |
| --- | --- |
| `find_moments.py --srt <folder> --moments moments.json --out located.json` | Given a moment's opening and closing words (as heard, approximately), finds the clip and the in/out and copies the transcript's own words between them. Refuses (NOT FOUND, with the reason) when an anchor does not match (ratio under 0.62), the end does not follow the start in one clip, or the moment runs over 150 s. Trims the best window at both ends so a moment does not open with the interviewer's question. |
| `transcribe_clean.py --src <file or folder> --out <folder>` | Clean per-clip transcripts: English set, silence hallucinations and third repeats dropped, `.srt` plus `.json` (with per-segment confidence). Exists because the earlier WhisperX transcripts of the weekend shoot were 66 to 97 percent loops. |
| `pick_mic.py --moments m.json --mics Bob=<folder> --mics Mitch=<folder> --out located.json` | When each person wore their own recorder: locates a moment in each person's transcripts and says whose microphone was clearer. A clue, not a fact. |
| `sync_audio.py --located located.json --mics <recordings> --cams <8 kHz camera audio> --out synced.json` | Finds which camera clip, and where, a moment from a person's recorder also happened (GCC-PHAT audio correlation), so it can be cut with PICTURE. Never guesses: a weak or tied match stays audio only. |
| `pitch_page.py --pitches pitches/2026-10-05.json --units "May15=<folder>" --units "Jun13=<folder>" --out reel_pitches.html` | A page of reel pitches built only from verified moments: hook, ordered beats with the exact line and the clip, close, estimated length, and what must be confirmed first. Refuses to build if any quoted line is not in its moment's transcript. |
| `index_page.py --root <folder> --out index.html` | One page linking every unit's selects page, with counts read from each `selects.json`. |
| `build_selects.py ...` | Checks one headline sentence per moment is verbatim in its transcript, runs your `verified-quotes` verifier on a log of them, cuts a preview clip (video, or audio for audio-only moments) for each, and writes `selects.html`: cards grouped by audience with the clip, the verified headline, why it helps, flags, where the transcript may have misheard, who is speaking, and whether the finished video already used it. |

**What was done, with numbers.** The whole sequence transcript of the interview (`8. Bob intv_Recruitment.txt`, 1,372 lines, 12,253 words, 72 minutes) was read start to finish. The five Osmo clips (46.7 min) and the lavalier recording (`DJI_01_20260515_142122.WAV`, 31 min)
were re-transcribed with Whisper small (English set), because the sequence transcript's timecodes do not map to the clips (it runs 72 minutes, the clips 47). 22 moments were chosen: 7 for hiring subcontractors, 10 for recruiting franchisees and partners, 5 that work for both,
about 16 minutes of speech in all. **22 of 22 headlines verify** against their transcripts with the verified-quotes tool; spot checks of three cut clips (F13, B6, F19) re-transcribed independently contain the headline words.

**Findings worth knowing.**
- **Your finished recruitment video (`Bob Recruitment_Final.mp4`, 1 minute) already uses S1, S3** ("never have a rain day ... 40 plus hours ... willing to learn, we're willing to teach" and "looking for people that are looking for a career ... retire here"). The other 20 moments are untouched.
- **4 moments (F12, F13, F14, F21) exist as audio only.** The Osmo clips have a 9.5 minute gap (clip 2 ends 14:30:43, clip 3 starts 14:40:14 on the camera clock) and the sequence has about 10 minutes of conversation there, which is on the lavalier recording only. Not checked: whether another camera caught it.
  They are Mitch on joining without disappearing (a contractor keeps their brand, the two-year non-compete), and how partners are chosen.
- **The hiring-subcontractor material is Bob's, and it is strong:** career not paycheck, no rain days, the same crew sees the house finished, high standards, loyalty to his guys, help with the business side (accountant, payroll), more opportunity than labor, flexible weeks but no overpromising.
- **The franchisee material is Mitch's:** the honest-broker moments (tells a seller to explore higher offers; best number up front; a roof quote that went from $17,000 to $10,000 in twelve minutes), who we serve, "lipstick" houses, the back end a partner gets.
- **Left out on purpose:** the seller story that runs the first eight minutes (a strong success story, but it involves a death, a heart condition and estate details of a real family) and the employee chatter late in the interview (medical appointments, a stroke).

**Limits, plainly.**
- The transcript is Whisper's and some words are wrong ("please just go find them. Do the job" for "go find a job"; "abusive" for "nuisance" properties). Where the sequence transcript disagrees the page says so. **Who says what is not settled:** the sequence transcript labels the whole interview block "Unknown" and Bob and Mitch both speak in it; the three readings of the "asshole" line (B6) differ on whether Bob says it or it is said about him. Listen to the clip.
- Moments start and end at transcript segment edges, so they are rough in and out points, not edit points. Names (a contractor's company, other partners in other states) and legal terms (the non-compete) are flagged on the cards; nothing here has been cleared for publication.
- **The verified-quotes tool's own example does not parse.** Its docs show a claim line with the `.srt` name in backticks, but its code treats such a line as a source declaration and skips it, so the first run "checked" 0 claims and silently verified nothing. `build_selects.py` writes a `Sources:` line before each claim instead (tested). The skill is in `~/.claude/skills`, so it was not edited.
- A moment is my judgement of what helps; the tool finds and checks where it is, not whether it helps.



## Second unit: the weekend interview (Ryan, 2026-10-05: "Go through the weekend interview next")
The June 13 shoot (`Weekend Interview Bob:Mitch`: 5 Canon clips, 9 Osmo clips, and a recorder file per person: `wknd_Bob1-3`, `wknd_Mitch1-4`, `wknd_Lillie`, `wknd_Maggie`). The sequence transcript (105 minutes, 15,126 words) was read start to finish.
About the first 67 minutes are a family outing and a job-site tour with children around. **The interview itself runs from about 1:07 to 1:45 and is aimed at "operators"**, i.e. the franchise audience.

- **The existing per-person transcripts were unusable:** 66 to 97 percent of their cues were repeated-phrase loops (the `language=None` problem in `docs/reference/RUNNELLS_CONTENT_INVENTORY.md`). All nine recorder files were re-transcribed with `transcribe_clean.py`: **0 loops in every file**, and about twice the words (Bob's three files 7,100 to 10,500, Mitch's four 8,000 to 14,900).
- **23 moments** (6 for subcontractors, 10 for franchisees and partners, 7 for both), about 12 minutes of speech, **23 of 23 headlines verified** with the verified-quotes tool. Franchisee material is Mitch's lessons for operators (our name on it, plan the whole project, holding cost of $88 a day, get in the weeds, integrity, what "done" means) plus the
  neighborhood-party idea and the families who walk the house after a loss. Subcontractor material is what Bob and Mitch expect and need (reliable subs, no-shows blowing up the schedule, a good day's work, how subs are chosen, starting when you said you would).
- **20 of 23 moments have picture; 3 are audio only (W7, W19, W20).** Picture comes from `sync_audio.py`. Evidence it is right: matches scored 43 to 272 against a noise floor of about 11 to 35; every moment from one recorder file lands at the SAME offset in the camera clip (Mitch4: 221.6 s for five moments, Bob3: 523.8 s for four);
  and four synced clips (W1, W9, W12, W21) were re-transcribed from the camera's own audio and contain the headline words. The score threshold (25) was set from synthetic audio (unrelated 8 to 10, true match 400 to 480) and then checked against these real spreads.
- **Whose voice:** each person's own microphone gives a clue (`pick_mic.py`): whichever recorder has the higher Whisper confidence. Most moments are "unclear" because the two sit close together and both mics hear both voices. Where the words themselves settle it (W14 Mitch, W16 Bob, W21 Bob) the clue was right 3 of 3. Small evidence; the card says "a clue, not a fact".
- **Children are in this footage** (the recorder files are named for two of them). The moments were chosen so no child speaks, but W4, W11 and W19 mention or may show children and are flagged. Whether any of it can be used is Ryan's call, and nothing was cleared.
- **Left out on purpose:** anything a child says, the family-outing chatter, the HVAC talk, a story about a child being bullied, the seller's estate details, and the first interview's seller story.
- **Limits:** Whisper mishears (it wrote "slip knot" for "stigma"; the camera-side audio wrote "we actually killed him personally" where the recorder has "know them personally"), so the card says where two readings differ and the clip is the final word. Moments start and end at transcript segment edges. Names, a first name in W12, dollar figures and wording
  such as "greedy" are flagged on the cards; nothing is cleared for publication. A moment is my judgement of what helps.

Tests: `PRECUT_ROOT=~/precut-checkout python3 -m pytest labs/recruit -q` (15).


## Reel pitches (Ryan, 2026-10-05: "pitch me a few videos with the results for reels")
`pitches/2026-10-05.json` holds five pitches; `reel_pitches.html` in `~/Documents/Post House Reviews/Recruitment footage (proof)/` plays every beat. They use only the 45 verified moments, no B-roll (his rule), and nothing is cut into a project.
1. **What Bob expects** (subcontractors): the blunt expectation first, then the honest ask, why he understands the sub's side, then the offer. 2. **What a day costs** (both): "$88 a day", what a good day's work means, pride in the finished house. 3. **That's on me** (operators): the backyard a sub called done,
ownership, what he does about it; the hook is a fragment of the ending, as in the wallpaper reel. 4. **Not everyone is the right fit** (franchisees): who should not apply. 5. **Bob is real** (both): humor, reputation, pride, why he is still out here.
Recommended first: 3, then 1. Lengths are estimates from the wallpaper reel's roughly 2.5 to 1 trim, not measurements. Every beat's line is a verbatim part of its moment's transcript (checked by the page generator; the extra lines beyond the headlines were also run through the verified-quotes verifier, 9 of 9 verified). Speakers
are the transcripts' clue and mostly unconfirmed, and each pitch lists what Ryan must confirm (a dollar figure, a speaker, a trim that removes a seller's estate mention). Not built: any cut of a pitch. Hooks and closes are proposals.

## The five reels as XMLs (Ryan, 2026-10-05: "Push all 5 as reels. Create the xmls. Make sure that audio is synced properly")
Built, machine-verified, NOT yet opened in Premiere or judged by Ryan. Output: `~/Documents/Post House Reviews/Recruitment footage (proof)/reels/` (`Reel 1..5 _v1.xml`, `reports/reel_N_verify.json`, `resolved.json`).

- `rough_cut.py` resolves each cut's words to frames: finds the phrase in a fresh transcript (speaker's own recorder for weekend cuts, mapped to camera time by the measured offset), puts the cut-in/out in a pause, or in the deepest energy dip between words when speech runs on, on a whole frame. Segments Whisper doubts (no_speech_prob over 0.6 and avg_logprob under -1.0) are dropped, since it invents text over silence.
- `build_reels.py` writes the XML through PreCut's own exporter (`posthouse/coldfootage.py`, door 3), 16:9 at the source size and 29.97 fps. Vertical is Premiere's Auto Reframe (Decision Log 2026-09-30). Camera audio is the audio.
- `verify_reel.py` gates: rate, cut count, picture/audio pairing, no gaps, frames as resolved, files reachable, audio enabled with sourcetrack, audio not silent, first and last words are the intended ones, `verify_export.py` (rule 10), and **sync measured** (camera audio against the speaker's recorder, worst lag 1.9 ms across all weekend cuts; limit 33 ms).
- Advisory, not gating: **CUT-POINTS-AT-VALLEYS** flags cuts whose edge sits well above the nearest valley (a clipped consonant or a click). It still flags 3 to 4 cuts per reel (some edges +18 to +23 dB). Continuous speech has no true pause, so some of these cannot be fixed by snapping. Nobody has listened to them.
- A hook is a fragment moved to the front, not repeated source: the safety net refuses source overlap.
- Open: Canon 8K clips (W1, W2) sit in a 4K sequence unscaled; lav audio is not on its own track; speakers mostly unconfirmed; clearance (children, names, "$88", legal terms).
- Fixed on the way: the donor exporter leaves camera-audio clips without a `sourcetrack` on this path (`add_audio_sourcetracks` in `posthouse/coldfootage.py`, test in `safety_net/tests/test_coldfootage.py`).

### Reel 3 through the app features (2026-10-05), built, NOT yet judged by Ryan
Folder: `~/Documents/Post House Reviews/Recruitment footage (proof)/Reel 3 paces/`. Open `Reel 3 - That's on me_layers_v1.xml` (cut on V1/A1, captions on V2, music and a whoosh on four new audio tracks) and `audio/audio_preview.mp4`.
- Captions: 128 words, 25 lines, all gating checks pass (base-model transcript agrees on 95% of words). Frames read: the pill sits low and can cover a hand; it never looks at the picture.
- Bleep scan: 128 words heard, none on the list, nothing bleeped (Whisper can hide curse words; not heard by anyone).
- Audio: music generated with ElevenLabs; the whoosh at 1.17 s (hook to story, frame 35) came from his library, nothing generated for it. MUSIC-AUDIBLE was skipped (no 0.8 s pause in a tight cut), so the music level between words is unmeasured. Nobody has listened.
- Layers placed with `place_overlay.py` then `place_audio.py`; `verify_export.py` passes on the final XML. Premiere import unconfirmed.
- Not run: the QA pass (needs review notes and a before/after pair; the reel has had no revision).

## Reel 3 redone as a vertical reel (Ryan, 2026-10-05: "not framed like a reel... no clear story... the sfx was ridiculous... sounds like camera audio... the music can't even be heard. Use one of my reels as a template")
`render_reel.py` renders a pitch as a finished 1080x1920 reel shaped on the wallpaper reel (`docs/reference/WALLPAPER_REEL_ANATOMY.md`). Output: `Recruitment footage (proof)/Reel 3 vertical/reel.mp4` + `report.json`. Built, measured, NOT yet judged by Ryan, not listened to by me.
- **Story fix:** motion in each half of the frame shows W18 is the person on the right and W17 the person on the left, so the old Reel 3 mixed two speakers. It is now W18 only: hook, the setup line, the closing story, "that's on me" (8 cuts, 29.3 s). Speaker identity is a motion clue and the mic data says "unclear".
- **Template parts used:** vertical full-bleed crop on the speaker (wide and tight alternating), title card with a progressive build and a small joke, step labels built a word at a time, no CTA card, no sound effects (the reference has none), hard cuts.
- **Audio:** the speaker's own recorder is the voice (not camera audio), levelled to -14.6 LUFS; the music bed is measured 8.3 LU under the voice after ducking; whole reel -13.2 LUFS (reference -12.5). First attempt measured the bed 19 LU under: that was the "can't be heard" bug, from ducking far too hard.
- **Draft copy for Ryan to change:** title "THE SUB SAID / "DONE" / (THE BACKYARD DISAGREED)", labels in `pitches/2026-10-05_reel3_style.json`. Font is Arial Black (ITC Avant Garde is not installed); no logo (no asset used).
- **Known weak spots:** labels sit over his hands; music is the earlier ElevenLabs stem, unjudged; the Reel 3 XML in `reels/` is from before this change and is stale; the vertical XML for Premiere is not built (waits for Ryan's reaction to this render).
