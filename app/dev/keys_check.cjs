// The keys the shortcuts sheet promises, pressed for real in the running page.
// `shortcuts.test.mjs` checks the sheet against the code; this checks the code against a person's fingers.
//   NODE_PATH=<playwright-core dir> node dev/keys_check.cjs [vitePort]
const { chromium } = require('playwright-core');
(async () => {
  const b = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true });
  const p = await (await b.newContext({ viewport: { width: 1280, height: 860 } })).newPage();
  const errs = [];
  p.on('pageerror', (e) => errs.push(e.message));
  await p.goto(`http://localhost:${process.argv[2] || 5199}/harness.html?app=1`);
  await p.evaluate(() => window.__backend({ type: "ready", version: "1.0", person: "Ryan", settings: { has_env: true, active_source: "env" } }));
  await p.waitForTimeout(400);
  await p.getByRole('button', { name: /^Skip$/ }).click().catch(() => {});
  await p.waitForTimeout(250);
  const r = {};

  // ? opens the list, Esc closes it
  await p.keyboard.press('?');
  await p.waitForTimeout(250);
  r.sheetOpens = await p.evaluate(() => !!document.querySelector('.sc-sheet'));
  r.groups = await p.evaluate(() => [...document.querySelectorAll('.sc-cols legend')].map((e) => e.textContent));
  await p.keyboard.press('Escape');
  await p.waitForTimeout(200);
  r.sheetCloses = await p.evaluate(() => !document.querySelector('.sc-sheet'));

  // ⌘K opens the palette, Esc closes it
  await p.keyboard.press('Meta+k');
  await p.waitForTimeout(250);
  r.paletteOpens = await p.evaluate(() => !!document.querySelector('.cp'));
  await p.keyboard.press('Escape');
  await p.waitForTimeout(200);

  // ⌘Z with nothing to undo must be quiet, not an error
  await p.keyboard.press('Meta+z');
  await p.waitForTimeout(300);
  r.undoWithNothingIsQuiet = await p.evaluate(() => document.querySelectorAll('.toast').length === 0);

  // choose a story, then ⌘Z takes it back
  await p.evaluate(() => window.__backend({ type: "project_loaded", project: { name: "K", resolved_dir: "/x", sources: [] } }));
  await p.waitForTimeout(300);
  await p.keyboard.press('Meta+2');
  await p.waitForTimeout(300);
  await p.evaluate(() => window.__backend({ type: "ideas_list", ideas: [1, 2, 3].map((n) => ({
    idea_id: `a${n}`, kind: "story_angle", created_at: Date.now() / 1000, refinement_count: 0, selected_platform_key: "", selected_aspect_key: "",
    data: { angle_id: `ang${n}`, brief: { title: `Story ${n}`, hook: "A hook.", why_it_works: "Because.", tone: "plain", target_duration_sec: 38 },
      source_ranges: [{ source_path: "/x/a.mp4", source_start_sec: 120, source_end_sec: 140 }] },
  })) }));
  await p.waitForTimeout(600);
  const box = await p.$('.storyboard-check input');
  if (box) {
    await box.check({ force: true });
    await p.waitForTimeout(250);
    r.chosenBefore = await p.evaluate(() => document.querySelectorAll('.storyboard-check input:checked').length);
    await p.keyboard.press('Meta+z');
    await p.waitForTimeout(400);
    r.chosenAfterUndo = await p.evaluate(() => document.querySelectorAll('.storyboard-check input:checked').length);
    r.undoSaidSo = await p.evaluate(() => [...document.querySelectorAll('.toast')].some((t) => /undid/i.test(t.textContent)));
  } else {
    r.story = 'no story checkbox in this state';
  }
  r.pageErrors = errs;
  console.log(JSON.stringify(r, null, 1));
  await b.close();
  process.exit(errs.length ? 1 : 0);
})();
