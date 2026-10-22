// The command palette reaching content, not just commands: a story by its own title, and a sequence by its name.
// Built on 2026-10-22; this is the check that it actually works, rather than only builds.
//   NODE_PATH=<playwright-core dir> node dev/palette_check.cjs [vitePort]
const { chromium } = require('playwright-core');

const STORIES = [1, 2, 3].map((n) => ({
  idea_id: `a${n}`, kind: "story_angle", created_at: Date.now() / 1000, refinement_count: 0, selected_platform_key: "", selected_aspect_key: "",
  data: {
    angle_id: `ang${n}`,
    brief: { title: ["Never cut corners on a sub's work", "The inspector missed it", "Feeling good versus making money"][n - 1], hook: "A hook.", why_it_works: "Because.", tone: "plain", target_duration_sec: 38 },
    source_ranges: [{ source_path: "/x/a.mp4", source_start_sec: 120, source_end_sec: 140 }],
  },
}));

(async () => {
  const b = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true });
  const p = await (await b.newContext({ viewport: { width: 1280, height: 860 } })).newPage();
  const errs = [];
  p.on('pageerror', (e) => errs.push(e.message));
  const r = {};
  await p.goto(`http://localhost:${process.argv[2] || 5199}/harness.html?app=1`);
  await p.evaluate(() => window.__backend({ type: "ready", version: "1.0", person: "Ryan", settings: { has_env: true, active_source: "env" } }));
  await p.waitForTimeout(400);
  await p.getByRole('button', { name: /^Skip$/ }).click().catch(() => {});
  await p.evaluate(() => window.__backend({ type: "project_loaded", project: { name: "Palette", resolved_dir: "/x", sources: [] } }));
  await p.waitForTimeout(300);

  // the stories have to have been seen once for the palette to know them
  await p.keyboard.press('Meta+2');
  await p.waitForTimeout(300);
  await p.evaluate((s) => window.__backend({ type: "ideas_list", ideas: s }), STORIES);
  await p.waitForTimeout(600);

  // somewhere else, so the jump is a real move
  await p.keyboard.press('Meta+1');
  await p.waitForTimeout(400);
  r.startedOn = await p.evaluate(() => document.querySelector('.page-header h1')?.textContent);

  await p.keyboard.press('Meta+k');
  await p.waitForTimeout(300);
  await p.keyboard.type('inspector');
  await p.waitForTimeout(400);
  r.matches = await p.evaluate(() => [...document.querySelectorAll('.cp-item .cp-label')].map((e) => e.textContent));
  r.topMatch = r.matches[0];
  await p.keyboard.press('Enter');
  await p.waitForTimeout(700);
  r.landedOn = await p.evaluate(() => document.querySelector('.page-header h1')?.textContent);
  r.wentToStories = r.landedOn === 'Stories';
  // and it points at the story it named, not just the page
  r.pointedAtIt = await p.evaluate(() => {
    const card = document.querySelector('[data-idea="a2"]');
    return card ? { found: true, shown: card.classList.contains('ph-found') } : { found: false };
  });

  // and a command still works from the same box
  await p.keyboard.press('Meta+k');
  await p.waitForTimeout(250);
  await p.keyboard.type('footage');
  await p.waitForTimeout(350);
  await p.keyboard.press('Enter');
  await p.waitForTimeout(600);
  r.commandStillWorks = (await p.evaluate(() => document.querySelector('.page-header h1')?.textContent)) === 'Footage';

  // the other half of the same feature: a sequence by its name opens that cut
  await p.keyboard.press('Meta+4');
  await p.waitForTimeout(300);
  await p.evaluate(() => window.__backend({ type: "sequences_extracted", xml: "/x/wk.xml", sequences: [
    { id: "s1", name: "WK 01 bob never cutting a corner", xml: "/x/seqs/WK_01.xml" },
    { id: "s2", name: "WK 02 bob when the sub says done", xml: "/x/seqs/WK_02.xml" },
  ] }));
  await p.waitForTimeout(600);
  await p.evaluate(() => { window.__sent.length = 0; });
  await p.keyboard.press('Meta+k');
  await p.waitForTimeout(250);
  await p.keyboard.type('sub says done');
  await p.waitForTimeout(400);
  r.sequenceMatches = await p.evaluate(() => [...document.querySelectorAll('.cp-item .cp-label')].map((e) => e.textContent));
  await p.keyboard.press('Enter');
  await p.waitForTimeout(900);
  r.askedToOpenThatCut = await p.evaluate(() => window.__sent.some((c) => JSON.stringify(c).includes("WK_02")));
  r.commandsSent = await p.evaluate(() => window.__sent.map((c) => c.type));

  r.pageErrors = errs;
  console.log(JSON.stringify(r, null, 1));
  await b.close();
  process.exit(errs.length || !r.wentToStories || !r.commandStillWorks ? 1 : 0);
})();
