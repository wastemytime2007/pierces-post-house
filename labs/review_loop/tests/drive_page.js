// Drives review.html in real Chrome and asserts on what a person would see.
//   NODE_PATH=<dir with playwright-core> node labs/review_loop/tests/drive_page.js "<review folder>"
// Manual check (needs Chrome and a built folder), not part of pytest.
const { chromium } = require('playwright-core');
const path = require('path');

const folder = process.argv[2];
if (!folder) { console.error('usage: drive_page.js <review folder>'); process.exit(2); }
const rows = [];
const check = (name, ok, detail = '') => { rows.push([name, ok, detail]); };

(async () => {
  const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1200, height: 1100 }, permissions: ['clipboard-read', 'clipboard-write'] });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto('file://' + path.resolve(folder, 'review.html'));
  await page.waitForFunction(() => document.getElementById('v').readyState >= 2, null, { timeout: 15000 });

  const box = () => page.evaluate(() => { const r = document.getElementById('draw').getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; });
  const drag = async (fx0, fy0, fx1, fy1, steps = 8) => {
    const b = await box();
    await page.mouse.move(b.x + b.w * fx0, b.y + b.h * fy0);
    await page.mouse.down();
    for (let i = 1; i <= steps; i++) await page.mouse.move(b.x + b.w * (fx0 + (fx1 - fx0) * i / steps), b.y + b.h * (fy0 + (fy1 - fy0) * i / steps));
    await page.mouse.up();
  };
  const stored = () => page.evaluate(() => JSON.parse(localStorage.getItem(Object.keys(localStorage).find(k => k.startsWith('review_loop:'))) || '[]'));
  const inkPixels = () => page.evaluate(() => { const c = document.getElementById('draw'); const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++; return n; });
  const seek = async t => { await page.evaluate(t => { document.getElementById('v').currentTime = t; }, t); await page.waitForTimeout(350); };

  // canvas lines up with the video picture
  const geo = await page.evaluate(() => { const v = document.getElementById('v'), c = document.getElementById('draw'); const r = v.getBoundingClientRect(), k = c.getBoundingClientRect(); return { va: v.videoWidth / v.videoHeight, ca: k.width / k.height, inside: k.left >= r.left - 1 && k.right <= r.right + 1 && k.top >= r.top - 1 && k.bottom <= r.bottom + 1 }; });
  check('CANVAS-ALIGNED', Math.abs(geo.va - geo.ca) / geo.va < 0.01 && geo.inside, `video ar ${geo.va.toFixed(3)} canvas ar ${geo.ca.toFixed(3)}`);

  // note 1: circle + arrow + freehand at 25.0s, with text
  await seek(25.0);
  await page.keyboard.press('d');
  check('D-OPENS-DRAW-MODE', await page.evaluate(() => document.getElementById('draw').classList.contains('on')));
  await page.click('[data-tool="ellipse"]'); await drag(0.30, 0.30, 0.50, 0.50);
  await page.click('[data-tool="arrow"]'); await drag(0.80, 0.20, 0.55, 0.40);
  await page.click('[data-tool="pen"]'); await drag(0.10, 0.80, 0.25, 0.70, 12);
  check('INK-WHILE-DRAWING', (await inkPixels()) > 500, `${await inkPixels()} px`);
  await page.screenshot({ path: path.join(require('os').tmpdir(), 'review_drawing_in_progress.png'), fullPage: true });
  await page.click('#text'); await page.keyboard.type('Crop out the cabinet edge on the left, and the circled tile needs regrouting.');
  await page.keyboard.press('Enter');
  let notes = await stored();
  check('NOTE1-SAVED-WITH-3-SHAPES', notes.length === 1 && notes[0].shapes.length === 3, `notes=${notes.length} shapes=${notes[0] && notes[0].shapes.length}`);
  const circle = notes[0] && notes[0].shapes.find(s => s.type === 'ellipse');
  const b = circle && (() => { const w = 1; return { cx: circle.cx, cy: circle.cy, rx: circle.rx, ry: circle.ry }; })();
  check('CIRCLE-COORDS-NORMALIZED', !!b && Math.abs(b.cx - 0.40) < 0.03 && Math.abs(b.cy - 0.40) < 0.03 && Math.abs(b.rx - 0.10) < 0.03, JSON.stringify(b));
  check('EDITOR-CLOSED-AFTER-SAVE', await page.evaluate(() => getComputedStyle(document.getElementById('editor')).display === 'none'));

  // drawing shows again on that frame, and only on that frame
  await seek(40.0); const away = await inkPixels();
  await seek(25.0); const back = await inkPixels();
  check('DRAWING-HIDDEN-ELSEWHERE', away === 0, `${away} px at 40s`);
  check('DRAWING-RETURNS-ON-ITS-FRAME', back > 500, `${back} px at 25s`);

  // undo and empty-save
  await seek(50.0); await page.keyboard.press('d');
  await page.click('[data-tool="rect"]'); await drag(0.2, 0.2, 0.4, 0.4);
  await page.click('#undo');
  await page.click('#save');
  notes = await stored();
  check('UNDO-THEN-EMPTY-SAVE-ADDS-NOTHING', notes.length === 1, `notes=${notes.length}`);

  // drawing-only note (no text)
  await seek(60.0); await page.keyboard.press('d');
  await page.click('[data-tool="rect"]'); await drag(0.6, 0.55, 0.85, 0.85);
  await page.click('#save');
  notes = await stored();
  check('DRAWING-ONLY-NOTE-SAVED', notes.length === 2 && notes[1].text === '' && notes[1].shapes.length === 1, JSON.stringify(notes[1] && { text: notes[1].text, shapes: notes[1].shapes.length }));

  // text-only note still works and has no shapes
  await seek(10.0); await page.keyboard.press('n'); await page.keyboard.type('Tighten this pause.'); await page.keyboard.press('Enter');
  notes = await stored();
  const textOnly = notes.find(n => n.text === 'Tighten this pause.');
  check('TEXT-ONLY-NOTE-STILL-WORKS', !!textOnly && (textOnly.shapes || []).length === 0);

  // what gets handed back
  await page.click('#copy'); await page.waitForTimeout(250);
  const clip = await page.evaluate(() => navigator.clipboard.readText());
  check('FEEDBACK-DESCRIBES-DRAWINGS', /drawn on frame: circle in the .* of the frame\)/.test(clip) && /arrow in the .*pointing at/.test(clip), '');
  check('FEEDBACK-JSON-HAS-SHAPES', clip.includes('"shape_coords"') && clip.includes('"bbox"') && clip.includes('"region"'));
  console.log('\n--- clipboard sample ---\n' + clip.split('```json')[0]);

  await page.waitForTimeout(1200);
  const thumbs = await page.evaluate(() => [...document.querySelectorAll('.thumb video')].map(t => ({ ready: t.readyState, at: t.currentTime })));
  const drawnTimes = (await stored()).filter(n => n.shapes && n.shapes.length).map(n => n.timeline_sec);
  check('THUMBS-SHOW-THE-NOTES-FRAME', thumbs.length === drawnTimes.length && thumbs.every((t, i) => t.ready >= 2 && Math.abs(t.at - drawnTimes[i]) < 0.15), JSON.stringify(thumbs));
  await page.screenshot({ path: path.join(require('os').tmpdir(), 'review_drawing_notes.png'), fullPage: true });
  await page.reload(); await page.waitForTimeout(500);
  notes = await stored();
  check('PERSISTS-WITH-SHAPES-AFTER-RELOAD', notes.length === 3 && notes.some(n => n.shapes && n.shapes.length === 3));

  // informational: can a frame+drawing image be exported from a file:// page?
  const taint = await page.evaluate(() => { try { const c = document.createElement('canvas'); c.width = 8; c.height = 8; c.getContext('2d').drawImage(document.getElementById('v'), 0, 0, 8, 8); c.toDataURL(); return 'exportable'; } catch (e) { return 'blocked: ' + e.name; } });
  console.log('frame image export under file://:', taint);

  check('NO-JS-ERRORS', errors.length === 0, errors.join(' | '));
  await browser.close();

  let bad = 0;
  for (const [n, ok, d] of rows) { if (!ok) bad++; console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${n.padEnd(36)} ${d}`); }
  console.log(bad ? `\n${bad} check(s) FAILED.` : '\nAll checks passed.');
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error('DRIVER FAILED', e); process.exit(1); });
