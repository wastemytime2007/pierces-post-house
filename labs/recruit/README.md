# labs/recruit: find the footage that helps recruit franchisees and hire subcontractors (standalone, not in app/)

Ryan (2026-10-05): "go through the footage to find any footage that will help us to recruit locations (franchisees) and hire sub contractors for Bob." This is the **exhaustive reading** PreCut's story planner skims
(`precut-capabilities`: one call, about 9 ranges per run). Per rule 7 it was proved on ONE unit first, the Bob and Mitch recruitment interview of May 15; nothing else has been scanned until Ryan reacts.

| Tool | What it does |
| --- | --- |
| `find_moments.py --srt <folder> --moments moments.json --out located.json` | Given a moment's opening and closing words (as heard, approximately), finds the clip and the in/out and copies the transcript's own words between them. Refuses (NOT FOUND, with the reason) when an anchor does not match (ratio under 0.62), the end does not follow the start in one clip, or the moment runs over 150 s. Trims the best window at both ends so a moment does not open with the interviewer's question. |
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

Tests: `PRECUT_ROOT=~/precut-checkout python3 -m pytest labs/recruit -q` (6).
