// Two things the home screen owes the person, pressed for real: the bar that appears when the app is newer than the piece behind it,
// and choosing which frame of a project stands for it.
//   NODE_PATH=<playwright-core dir> node dev/home_check.cjs [vitePort]
const { chromium } = require('playwright-core');

const PROJECTS = [
  { name: "Runnells Wall Removal", dir: "/x/a", updated_at: Date.now() / 1000 - 600, sources: [], summary: { transcribed: 2, stories: 3, exports: 1 } },
  { name: "Runnells Weekend Septic", dir: "/x/b", updated_at: Date.now() / 1000 - 90000, sources: [], summary: { transcribed: 2, stories: 0, exports: 0 } },
];
// a one-pixel frame, so the picker has something real to show without any footage
const PIX = "data:image/gif;base64,R0lGODlhAQABAIAAAP///wAAACH5BAEAAAAALAAAAAABAAEAAAICRAEAOw==";

(async () => {
  const b = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true });
  const p = await (await b.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
  const errs = [];
  p.on('pageerror', (e) => errs.push(e.message));
  const r = {};
  await p.goto(`http://localhost:${process.argv[2] || 5199}/harness.html?app=1`);
  await p.evaluate(() => window.__backend({ type: "ready", version: "1.0", person: "Ryan", settings: { has_env: true, active_source: "env" } }));
  await p.waitForTimeout(400);
  await p.getByRole('button', { name: /^Skip$/ }).click().catch(() => {});
  await p.evaluate((ps) => window.__backend({ type: "projects_list", projects: ps }), PROJECTS);
  await p.waitForTimeout(500);

  // 1. nothing stale: no bar
  r.noBarWhenFresh = await p.evaluate(() => !document.querySelector('.stale-bar'));

  // the backend says its own code changed since it started
  await p.evaluate(() => window.__backend({ type: "ready", version: "1.0", person: "Ryan", stale: true, settings: { has_env: true, active_source: "env" } }));
  await p.waitForTimeout(400);
  r.bar = await p.evaluate(() => { const e = document.querySelector('.stale-bar'); return e ? e.textContent.trim() : null; });
  r.barSaysRestart = /quit|open it again/i.test(r.bar || "");
  await p.screenshot({ path: '/Users/pierce/.claude/jobs/a40e345e/tmp/p18_stale.png' });

  // 2. choosing the frame that stands for a project
  await p.evaluate((u) => {
    window.__backend({ type: "project_poster", name: "Runnells Wall Removal", poster: u, footage: "/x/a/interview.mp4" });
  }, PIX);
  await p.waitForTimeout(300);
  await p.locator('.cover').first().click({ button: 'right' });
  await p.waitForTimeout(300);
  r.menu = await p.evaluate(() => [...document.querySelectorAll('[role="menuitem"]')].map((e) => e.textContent.trim()));
  await p.getByRole('menuitem', { name: /Choose cover frame/ }).click();
  await p.waitForTimeout(400);
  r.pickerOpens = await p.evaluate(() => !!document.querySelector('.frame-pick'));
  r.tiles = await p.evaluate(() => document.querySelectorAll('.frame-pick-tile').length);
  r.askedForFrames = await p.evaluate(() => window.__sent.filter((c) => c.type === 'frame_thumbs').length > 0);
  // the frames come back, one is chosen, and it becomes the cover
  await p.evaluate((u) => {
    const items = {};
    [0, 1, 2, 3, 4, 5].forEach((i) => { items[`strip:Runnells Wall Removal:${i}`] = u; });
    window.__backend({ type: "frame_thumbs", id: "x", thumbs: items });
  }, PIX);
  await p.waitForTimeout(400);
  r.tilesWithAFrame = await p.evaluate(() => [...document.querySelectorAll('.frame-pick-tile span')].filter((e) => /url\(/.test(e.style.backgroundImage)).length);
  await p.locator('.frame-pick-tile').nth(2).click();
  await p.waitForTimeout(400);
  r.pickerCloses = await p.evaluate(() => !document.querySelector('.frame-pick'));
  r.coverRemembered = await p.evaluate(() => !!localStorage.getItem('ph.cover.Runnells Wall Removal'));

  r.pageErrors = errs;
  console.log(JSON.stringify(r, null, 1));
  await b.close();
  process.exit(errs.length ? 1 : 0);
})();
