// Can the window be moved? On macOS the only thing that drags a Tauri window is the `data-tauri-drag-region`
// attribute on the element actually under the pointer. (`-webkit-app-region` is Chromium's and does nothing
// here, which is how the title bar shipped undraggable on 2026-10-22.)
// This walks the title bar and reports, for each point, whether what is under the pointer carries the attribute.
//   NODE_PATH=<playwright-core dir> node dev/drag_check.cjs [vitePort]
const { chromium } = require('playwright-core');

(async () => {
  const b = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true });
  const p = await (await b.newContext({ viewport: { width: 1400, height: 900 } })).newPage();
  await p.goto(`http://localhost:${process.argv[2] || 5199}/harness.html?app=1`);
  await p.evaluate(() => window.__backend({ type: "ready", version: "1.0", person: "Ryan", settings: { has_env: true, active_source: "env" } }));
  await p.waitForTimeout(400);
  await p.getByRole('button', { name: /^Skip$/ }).click().catch(() => {});
  await p.evaluate(() => window.__backend({ type: "project_loaded", project: { name: "Runnells Weekend", resolved_dir: "/x", sources: [] } }));
  await p.waitForTimeout(600);

  const r = await p.evaluate(() => {
    const bar = document.querySelector('.titlebar');
    const box = bar.getBoundingClientRect();
    const y = Math.round(box.top + box.height / 2);
    const out = [];
    // across the whole bar, every 20 px
    for (let x = 4; x < box.width; x += 20) {
      const el = document.elementFromPoint(x, y);
      if (!el) continue;
      const onControl = !!el.closest('button, a, input, select, [role="menu"], [role="menuitem"]');
      const draggable = !!el.closest('[data-tauri-drag-region]') && el.hasAttribute?.('data-tauri-drag-region');
      out.push({ x, cls: (el.className || '').toString().split(' ')[0] || el.tagName.toLowerCase(), onControl, draggable });
    }
    return { width: Math.round(box.width), height: Math.round(box.height), points: out };
  });

  const bad = r.points.filter((q) => !q.onControl && !q.draggable);
  const controls = r.points.filter((q) => q.onControl);
  console.log(JSON.stringify({
    barWidth: r.width,
    pointsTested: r.points.length,
    overControls: controls.length,
    draggable: r.points.filter((q) => q.draggable).length,
    deadSpots: bad.map((q) => `${q.x}px on .${q.cls}`),
    ok: bad.length === 0,
  }, null, 1));
  await b.close();
  process.exit(bad.length ? 1 : 0);
})();
