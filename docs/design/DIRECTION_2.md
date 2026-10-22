# Direction 2: a post house, not a dashboard (2026-10-21, overnight)

Ryan: "I want to be blown away by the product you came up with." Make it feel like a top-end app designer made it: design, ease of use, fun, one of a kind. Respect the real workflow: **prep in the app, export an XML with one or many sequences, edit the storyline and delivery in Premiere, bring that XML back for creative polish.**

## What I read, and how much to trust it

Primary: Apple HIG pages in the `apple-design` skill; Linear's own "How we redesigned the Linear UI" post (linear.app/blog); Emil Kowalski's "You don't need animations" (emilkowal.ski); Apple's "Behind the Design: Halide Mark II" as quoted by several outlets; Jony Ive's 2025 Stripe Sessions talk as reported by 9to5Mac. Secondary and thin: agency-ranking blogs (Instrument, Metalab, Fantasy: price and positioning claims disagree between sources, so I use none of the figures), a Criterion Channel interface commentary, Frame.io reviews, Awwwards tag pages (they list "Big Background Images, Fullscreen, Minimal, Portfolio, Photographic" as styles; no trend analysis was found). Fantasy's own site said little beyond positioning. I could not retrieve Benji Taylor's Family write-up, Things or Craft analyses, or Pentagram's work. Where a rule below comes from a thin source I say so.

## What the best of it agrees on

1. **A camera, not an app** (Halide, via Apple): name the thing for what it is to the user and let the depth sit behind a simple default. "The complexity is there, it's just not going to overwhelm you." For us: Post House is a *post house* with rooms, not a dashboard of tools.
2. **Content carries the design** (Photos, Criterion Channel commentary): black or neutral ground, stills with their titles under them, no autoplay carousels or rotating banners, curation over feeds. For us: every screen leads with the person's own frames (done in the first pass).
3. **Restraint and density** (Linear, first-hand): fewer color inputs, a neutral palette with less blue chrome, aligned sidebar and header elements, a display face for headings, text contrast raised. Details are felt, not seen.
4. **Keyboard-first with one door to everything** (Linear, third-party analyses): a command palette (⌘K) that reaches every action. For us: build it.
5. **Animation has a job** (Emil Kowalski, first-hand): explain a spatial relationship or a state change; under 300 ms; none on things done often; keep delight for rare moments. For us: the finish moment, the first import, the first project get character; everything frequent is instant.
6. **Care shows in small things** (Ive, reported): the way a cable unwraps. For us: define the unwrapping moments (first project, first XML, finishing a batch, opening in Premiere) and make each one deliberate.
7. **Review lives on the frame** (Frame.io reviews): notes on the picture, versions stacked in one place (we have both).
8. **Avoid the tells** (2026 write-ups): same dashboard everywhere, identical cards, decorative icon tiles, eyebrow labels, 01/02/03 where nothing is a sequence, generic copy, undesigned empty and error states.

## The product, restated as rooms

The real workflow is a sequence, so numbering it is honest.

| | Room | What happens | Leaves with |
| --- | --- | --- | --- |
| 1 | **Footage** | Point at the shoot. Interviews are transcribed, B-roll described, recorder audio synced. | A searchable shoot. |
| 2 | **Stories** | The app finds stories in what was said. Choose the ones worth cutting. | An XML with a sequence per story. |
| 3 | **Trim** | You, in Premiere: edit the storyline and the delivery. The app waits and says exactly what to do, and takes the XML back by drag and drop from anywhere. | Your trimmed XML (one or many sequences). |
| 4 | **Finish** | Each sequence opens on its own frame. Ums out, captions, graphics, music, sound, transitions. Check, then back to Premiere. | Finished sequences in Premiere. |

A slim **journey** bar on every project page shows these four with real status (transcribed, N stories chosen, XML out and waiting, N of M finished) and is also the navigation.

## Names, mark, icon

Names say what the person does (Footage, Stories, Trim, Finish). The house-with-play-button mark becomes the app icon. Palette stays SoldFast navy and blue on neutral grays, orange only for attention.

## How I will work tonight

A backlog of small rounds (`docs/design/POLISH_LOG.md`), each: change, check (build, tests, a screenshot when it is visual), log. The log states what was verified and what was not. I will report the true number of rounds.
