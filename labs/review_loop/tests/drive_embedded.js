// Drives the review page the way the app's Review tab shows it: framed inside a parent page, served over http by creator_tools.serve_review (byte ranges).
//   NODE_PATH=<dir with playwright-core> node labs/review_loop/tests/drive_embedded.js "<url from creator_tools.serve_review>"
// Manual check (needs Chrome and a built, served review folder), not part of pytest.
const { chromium } = require('playwright-core');

const url = process.argv[2];
if (!url) { console.error('usage: drive_embedded.js <review url>'); process.exit(2); }
const rows = [];
const check = (name, ok, detail = '') => rows.push([name, ok, detail]);

(async () => {
  const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 1300 } })).newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });

  // the parent stands in for the Review tab: it records what the page tells it and can ask for the notes
  await page.setContent(`<body style="margin:0"><iframe id="f" src="${url}" style="width:1300px;height:1200px;border:0"></iframe>
    <script>window.__msgs = []; window.addEventListener('message', e => window.__msgs.push(e.data));
    window.ask = () => new Promise(res => { const id = 'x1'; const h = e => { if (e.data && e.data.type === 'review:notes' && e.data.id === id) { removeEventListener('message', h); res(e.data.payload); } };
      addEventListener('message', h); document.getElementById('f').contentWindow.postMessage({ type: 'review:get-notes', id }, '*'); }); </script></body>`);
  const frame = page.frame({ url: /review\.html/ }) || (await (async () => { await page.waitForTimeout(1500); return page.frames().find(f => /review\.html/.test(f.url())); })());
  await frame.waitForFunction(() => document.getElementById('v').readyState >= 2, null, { timeout: 20000 });
  check('PAGE-LOADS-OVER-HTTP', true, frame.url());

  const dur = await frame.evaluate(() => document.getElementById('v').duration);
  await frame.evaluate(() => { document.getElementById('v').currentTime = 12.5; });
  await frame.waitForFunction(() => Math.abs(document.getElementById('v').currentTime - 12.5) < 0.2 && document.getElementById('v').readyState >= 2, null, { timeout: 10000 });
  check('SEEK-WORKS (byte ranges)', true, `duration ${dur.toFixed(2)}s, sought to 12.5s and the frame is ready`);

  const first = await page.evaluate(() => window.__msgs.filter(m => m && m.type === 'review:count').pop());
  check('PAGE-REPORTS-ITS-NOTE-COUNT', !!first && first.n === 0, JSON.stringify(first));

  await frame.click('#add');
  await frame.fill('#text', 'Lower this shot so his head is not cut off');
  await frame.click('#save');
  await page.waitForTimeout(300);
  const count = await page.evaluate(() => window.__msgs.filter(m => m && m.type === 'review:count').pop());
  check('COUNT-UPDATES-WHEN-A-NOTE-IS-SAVED', !!count && count.n === 1, JSON.stringify(count));

  const payload = await page.evaluate(() => window.ask());
  const ok = payload.schema === 'review_notes.v0-draft' && payload.notes.length === 1 && /Lower this shot/.test(payload.notes[0].text) && typeof payload.notes[0].timeline_sec === 'number';
  check('PARENT-GETS-THE-NOTES-THE-DOWNLOAD-WOULD-HAVE-MADE', ok, `${payload.notes.length} note at ${payload.notes[0] && payload.notes[0].timeline_sec}s clip ${payload.notes[0] && payload.notes[0].clip}: "${payload.notes[0] && payload.notes[0].text}"`);

  check('NO-PAGE-ERRORS', errors.length === 0, errors.slice(0, 3).join(' | '));
  await browser.close();
  let bad = 0;
  for (const [n, ok, d] of rows) { bad += !ok; console.log(`[${ok ? 'PASS' : 'FAIL'}] ${n}  ${d}`); }
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error('DRIVER ERROR', e.message); process.exit(1); });
