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

Tests: `PRECUT_ROOT=~/precut-checkout python3 -m pytest labs/recruit -q` (14).
