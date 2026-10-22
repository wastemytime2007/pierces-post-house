# Experience direction: from "buttons and forms" to a creative's app (2026-10-21)

Ryan, after the first two passes: "It still very much feels like an AI built application. The user experience is dry and boring. It's just a bunch of buttons with no clarity. No images or graphical elements attached... I want it to feel really nice and intentional and clear and attractive... an app for creatives. It needs to be attractive and interesting to creative people when using it but also incredibly clear on what each page, button, service does."

## What the research says (and where it is thin)

Sources read: the Apple HIG pages in the `apple-design` skill (`design-principles.md`, `branding.md`, `onboarding.md`), apple.com/final-cut-pro, and a set of 2026 practitioner write-ups on why AI-built interfaces look the same ([mowgli.ai](https://mowgli.ai/blog/why-ai-built-apps-look-the-same), [smoothui.dev](https://smoothui.dev/blog/ai-design-slop), [DEV: Users can tell](https://dev.to/olehvolos/users-can-tell-when-your-ui-was-ai-generated-and-they-dont-like-it), [superdesign.dev](https://superdesign.dev/blog/why-ai-design-looks-generic)). The practitioner posts are vendor blogs, not research; the Apple pages are primary. My searches for design analyses of Frame.io, Descript, Pixelmator and Linear returned only review aggregators, so nothing below is claimed about their interfaces.

1. **Apple apps built for creative work are led by the person's content, not by controls.** Photos shows edge-to-edge images with minimal chrome and uses thumbnails as navigation (Engadget, Take Control, Apple Help results). Apple's own Final Cut Pro page opens each section with a full-bleed image of real creative work, a short noun-phrase headline with a period ("Reel intelligence."), and one or two sentences of benefit. The color comes from the imagery; the layout stays neutral.
2. **Branding defers to content** (`branding.md`): brand color on primary actions and status, not everywhere; no logo repeated through the app; familiar components with a few defining moments.
3. **Design principles that decide this** (`design-principles.md`): Purpose (what is this screen for), Simplicity ("Establish hierarchy... where you are and what comes next"), Craft, and Delight with its warning: "Don't mistake delight for decoration."
4. **The "AI-built" tells** (mowgli.ai, smoothui.dev): the same dashboard layout everywhere; identical cards with the same radius and shadow; an icon in a tinted square on every card; ALL-CAPS eyebrow labels; 01/02/03 markers on things that are not a sequence; middle-dot metadata; generic copy; only the happy path designed (no empty, loading or error state); a fade-up on everything. The fixes they agree on: use real content, make one element primary, assign radii by role, write headings that stand alone, verb-plus-object button labels, design the non-happy paths, add one deliberate non-default element.

## Why this app felt dry (measured against that list)

- It shows no footage anywhere, in an app whose whole job is footage. Every screen was a form, a list of text rows, or a card of text.
- Pages did not say what they were for or what comes next.
- Icons: none, so buttons were the only affordance.
- Empty and first-run states were paragraphs.

## The direction

**The footage is the interface.** Every screen leads with frames from the person's own shoot.

| Where | What changes |
| --- | --- |
| Projects (home) | A poster grid: each project is a frame from its own interview footage with its name over a soft gradient, like Photos or Apple TV. The most recently opened project is large. A project with no footage gets a generated color tile, never a blank. |
| Every project page | A short header that says what the page is for in one plain sentence and names the next step. The three stages (Footage, Ideas, Finish) are a real sequence, so the switcher shows where you are in it and what is done. |
| Ideas | Each idea is a storyboard: a strip of frames taken from the moments it uses, then the title, hook and runtime. |
| Finish | The sequences sidebar shows each cut's own frame. The empty state is the recent cuts as posters, with the three steps beside them. Get Creative is a sheet of rows, each with an icon, a name and one line saying what it does to the video. |
| Icons | One consistent line-icon set (SF Symbols-style, one stroke weight), used only where it labels an action or a kind of thing (cut, captions, graphics, music, sound effects, bleep, transition, Premiere). Not in tinted squares on cards. |
| Mark | A small house-with-a-play-button mark (SoldFast is real estate; Post House is a post-production house), used once in the title bar and on the home screen. |
| Words | Page names are what the person does: Footage, Ideas, Finish. Buttons are verb plus object. Headlines are short and specific. No eyebrow labels, no 01/02/03 where there is no sequence. |
| Motion | One coordinated entrance when a screen loads; otherwise motion only answers an action. All off under reduced motion. |

## What this deliberately does not do

No gradient text, no glow, no emoji, no decorative blobs, no hero with three feature cards. Boldness is spent in one place: the footage frames.

## How it is checked

Screenshots of every main screen with real frames from Ryan's projects, in light and dark, against the tells list above; the contrast test; the page and Python tests; and Ryan using the app.
