// Plays scripted editor events into the real ReviewTab (app/dev harness) in Chrome, checks what the status strip says at each stage, and screenshots each state.
//   (cd app && npx vite --port 1431) &   then   NODE_PATH=<dir with playwright-core> SHOTS=<dir> node labs/review_loop/tests/drive_tab.js
// Manual check (needs Chrome and node_modules), not part of pytest.
const { chromium } = require('playwright-core');
const OUT = (process.env.SHOTS || '/tmp/review_tab_shots') + '/';
require('fs').mkdirSync(OUT, { recursive: true });

(async () => {
  const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1350, height: 1000 } })).newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|favicon/.test(m.text())) errors.push(m.text()); });
  await page.goto('http://localhost:1431/dev/review_harness.html');
  await page.waitForFunction(() => typeof window.__emit === 'function', null, { timeout: 20000 });
  const emit = ev => page.evaluate(e => window.__emit(e), ev);
  const shot = async (name) => { await page.waitForTimeout(400); await page.screenshot({ path: OUT + name + '.png' }); };
  const text = sel => page.evaluate(s => (document.querySelector(s) || {}).innerText || '', sel);
  const rows = [];
  const check = (n, ok, d = '') => rows.push([n, ok, d]);

  await shot('0-empty');
  await emit({ type: 'review_cut_started', n: 1, of: 1 });
  await emit({ type: 'export_matching' });
  await shot('1-making-the-cut');
  check('MAKING-SHOWS-THE-EDITOR-WORKING', /Working|working/.test(await text('.ai-strip')) && /Matching the idea/.test(await text('.ai-strip')), (await text('.ai-strip')).slice(0, 90).replace(/\n/g, ' | '));

  await emit({ type: 'export_complete', xml_path: '/p/cuts/Cut.xml' });
  await emit({ type: 'review_started' });
  await emit({ type: 'review_built', xml: '/p/cuts/Cut.xml', folder: '/p/cuts/Cut - review', url: 'about:blank', sequence: 'S', clips: 6, duration: 56.07 });
  await shot('2-opened-editor-starts-at-once');
  const t2 = await text('.ai-strip');
  check('NO-FALSE-YOUR-TURN-WHEN-THE-CUT-OPENS', /AI editor is working/.test(t2) && !/Your turn/.test(t2), t2.slice(0, 80).replace(/\n/g, ' | '));

  await emit({ type: 'auto_edit_started', xml: '/p/cuts/Cut.xml', tag: 'V1', max_rounds: 4 });
  await emit({ type: 'ai_review_started', xml: '/p/cuts/Cut.xml', tag: 'V1', auto: true });
  await emit({ type: 'ai_review_stage', xml: '/p/cuts/Cut.xml', tag: 'V1', stage: 'Checking every cut edge against the voice under it', auto: true });
  await shot('3-reviewing-v1');
  check('REVIEW-STEP-LIT', /Review/.test(await page.evaluate(() => (document.querySelector('.ai-step.on') || {}).innerText || '')), '');

  const notes = [{ kind: 'edge-end', text: 'AI: Clip 1 may cut off the end', suggested_op: { op: 'extend_end', clip: 1 }, timeline_sec: 5, clip: 1, shapes: [] },
                 { kind: 'story', text: 'AI: The ending is abrupt', timeline_sec: 50, clip: 6, shapes: [] }];
  await emit({ type: 'ai_review_done', xml: '/p/cuts/Cut.xml', tag: 'V1', auto: true, notes, checks: [{ name: 'CUT-EDGES', ok: false, detail: '10 of 12 edges' }, { name: 'HOOK', ok: false, detail: 'weak' }], summary: 'A septic tip.', unverified_quotes_dropped: 0 });
  await emit({ type: 'auto_edit_round', round: 1, of: 4, label: 'V1', fixing: 1 });
  await emit({ type: 'notes_started', xml: '/p/cuts/Cut.xml', auto: true });
  await emit({ type: 'notes_stage', stage: 'Making the changes the notes ask for', auto: true });
  await shot('4-submitting-fixes');
  check('SUBMIT-STEP-LIT-AND-BADGE-OVER-THE-VIDEO', /Submit fixes/.test(await page.evaluate(() => (document.querySelector('.ai-step.on') || {}).innerText || '')) && (await page.locator('.ai-badge').count()) === 1, '');
  check('NOTHING-NEEDED-FROM-YOU-IS-SAID', /Nothing is needed from you/.test(await text('.ai-strip')), '');

  await emit({ type: 'notes_stage', stage: 'Checking every note against the new version', auto: true });
  await shot('5-checking');
  check('CHECK-STEP-LIT', /Check/.test(await page.evaluate(() => (document.querySelector('.ai-step.on') || {}).innerText || '')), '');

  await emit({ type: 'notes_applied', auto: true, xml: '/p/cuts/Cut_v2.xml', folder: '/p/cuts/Cut_v2 - revised', url: 'about:blank', applied: 1, notes: 1, qa: { notes: [{ note: 1, time: 5, status: 'VERIFIED', text: 'AI: Clip 1', rows: [{ op: 'extend_end', status: 'VERIFIED', detail: 'clip 1 now runs 0.53s longer' }] }], whole_cut: [], unrequested: [] } });
  await emit({ type: 'ai_review_started', xml: '/p/cuts/Cut_v2.xml', tag: 'V2', auto: true });
  await shot('6-reviewing-v2');
  check('NO-YOUR-NOTES-WARNING-WHEN-ONLY-AI-NOTES-REMAIN', (await page.locator('.pm-tab-warnings:has-text("your notes")').count()) === 0, '');
  const tabs = await page.evaluate(() => [...document.querySelectorAll('.btn')].map(b => b.innerText).filter(t => /^V\d/.test(t)));
  check('V2-APPEARED-WHILE-THE-EDITOR-STILL-WORKS', tabs.join(',') === 'V1,V2', tabs.join(','));

  await emit({ type: 'ai_review_done', xml: '/p/cuts/Cut_v2.xml', tag: 'V2', auto: true, notes: [notes[1]], checks: [{ name: 'HOOK', ok: false, detail: 'weak' }], summary: '', unverified_quotes_dropped: 0 });
  await emit({ type: 'auto_edit_done', status: 'left', best: 'V2', versions: [{ label: 'V1', score: 5 }, { label: 'V2', score: 3 }], rounds: [{ round: 1, submitted: 1, applied: 1 }],
    summary: 'Ready for your review: V2 (1 revision). 1 thing left that the editor could not fix by itself (each says why).',
    left: [{ text: 'AI: The ending is abrupt', kind: 'story', reason: 'no ready-made fix: needs a decision or new words, not an edit' }] });
  await shot('7-your-turn');
  const t7 = await text('.ai-strip');
  check('YOUR-TURN-WITH-THE-REASON', /Your turn/.test(t7) && /Ready for your review: V2/.test(t7) && (await page.locator('.ai-strip.working').count()) === 0, t7.slice(0, 120).replace(/\n/g, ' | '));
  check('NO-BADGE-WHEN-IDLE', (await page.locator('.ai-badge').count()) === 0, '');
  const feed = await text('.ai-feed');
  check('FEED-HAS-THE-STEPS-WITH-TIMES', /Reviewed V1: 2 notes, 1 the editor can try to fix/.test(feed) && /Round 1 of 4: submitting 1 fix to V1/.test(feed) && /\d\d:\d\d:\d\d/.test(feed), feed.split('\n').slice(0, 4).join(' | ').slice(0, 200));
  const label = await text('#tablabel');
  check('TAB-LABEL-SAYS-V2-OPEN', /V2 open/.test(label), label);

  // a manual submit whose result leaves two of the user's own notes undone
  await emit({ type: 'notes_applied', xml: '/p/cuts/Cut_v3.xml', folder: '/p/cuts/Cut_v3 - revised', url: 'about:blank', applied: 1, notes: 3,
    qa: { notes: [{ note: 1, time: 5, status: 'VERIFIED', text: 'AI: Clip 1 may cut off the end', rows: [{ op: 'extend_end', status: 'VERIFIED', detail: 'clip 1 now runs 0.2s longer' }] },
                  { note: 2, time: 8, status: 'NOT DONE', text: 'the video on screen doesnt line up with the audio being spoken', rows: [{ op: 'unsupported', status: 'NOT DONE', detail: 'not done: needs a resync, not a supported operation' }] },
                  { note: 3, time: 9, status: 'NOT DONE', text: 'the framing should follow the speaker', rows: [{ op: 'unsupported', status: 'NOT DONE', detail: 'not done: only moving a shot up or down is supported' }] }], whole_cut: [], unrequested: [] } });
  await shot('8a-your-notes-not-done');
  const warn = await text('.pm-tab-warnings');
  check('YOUR-UNDONE-NOTES-ARE-CALLED-OUT-WITH-THEIR-REASONS', /2 of your notes were not done/.test(warn) && /line up with the audio/.test(warn) && /follow the speaker/.test(warn) && /only moving a shot up or down/.test(warn) && !/Clip 1 may cut off/.test(warn), warn.slice(0, 150).replace(/\n/g, ' | '));
  await emit({ type: 'auto_edit_done', status: 'failed', best: 'V2', versions: [], rounds: [], message: 'x', summary: 'The editor could not continue: REFUSING: cannot extend the end of a clip. V2 is the latest good version.', left: [] });
  await shot('8-your-turn-failed');
  check('FAILED-IS-RED', (await page.locator('.ai-strip.yours.bad').count()) === 1, '');

  // ---- reopening: the project is opened again in a new window. The last review comes back as it was left; nothing is built, reviewed or submitted.
  const fresh = async () => {
    const pg = await (await browser.newContext({ viewport: { width: 1350, height: 1000 } })).newPage();
    pg.on('pageerror', e => errors.push(e.message));
    pg.on('dialog', d => d.accept());
    await pg.goto('http://localhost:1431/dev/review_harness.html');
    await pg.waitForFunction(() => typeof window.__emit === 'function', null, { timeout: 20000 });
    return pg;
  };
  const sent = pg => pg.evaluate(() => window.__sent.map(c => c.type + (c.fresh ? ':fresh' : '')));
  const session = { summary: 'x', versions: 3, latest: 'V3', best: 'V3', left: 8, status: 'left', updated: 2000 };
  const exportsList = [{ path: '/p/cuts/New.xml', name: 'New.xml', mtime: 1, session: null }, { path: '/p/cuts/Cut.xml', name: 'Cut.xml', mtime: 0, session }];
  const loaded = { type: 'review_session_loaded', root: '/p/cuts/Cut.xml', auto_running: false, auto_tag: null,
    versions: [{ label: 'V1', xml: '/p/cuts/Cut.xml', folder: '/p/cuts/Cut - review', url: 'about:blank', qa: null, applied: null, notes: null },
               { label: 'V2', xml: '/p/cuts/Cut_v2.xml', folder: '/p/cuts/Cut_v2 - revised', url: 'about:blank', qa: { notes: [{ note: 1, time: 5, status: 'VERIFIED', text: 'AI: Clip 1', rows: [{ op: 'extend_end', status: 'VERIFIED', detail: 'clip 1 now runs 0.5s longer' }] }], whole_cut: [], unrequested: [] }, applied: 1, notes: 1 },
               { label: 'V3', xml: '/p/cuts/Cut_v3.xml', folder: '/p/cuts/Cut_v3 - revised', url: 'about:blank', qa: null, applied: 2, notes: 3 }],
    ai: { V3: { notes: [{ kind: 'story', text: 'AI: The ending is abrupt', timeline_sec: 50, clip: 6, shapes: [] }], checks: [{ name: 'HOOK', ok: false, detail: 'weak' }], summary: 'A septic tip.', unverified_quotes_dropped: 0 } },
    auto: { status: 'left', summary: 'Ready for your review: V3 (2 revisions). 8 things left that the editor could not fix by itself (each says why).', best: 'V3', left: [{ text: 'AI: The ending is abrupt', kind: 'story', reason: 'needs new words' }], versions: [], rounds: [] } };

  const p2 = await fresh();
  await p2.evaluate(e => window.__emit(e), { type: 'exports_listed', exports: exportsList });
  await p2.waitForTimeout(300);
  const s2 = await sent(p2);
  check('REOPEN-ASKS-FOR-THE-LAST-REVIEWED-CUT-NOT-A-BUILD', s2.includes('open_review') && !s2.includes('build_review') && !s2.includes('auto_edit'), s2.join(','));
  const opened = (await p2.evaluate(() => window.__sent.find(c => c.type === 'open_review') || {})).xml;
  check('IT-OPENS-THE-CUT-WITH-A-SESSION-NOT-THE-NEWER-UNREVIEWED-ONE', opened === '/p/cuts/Cut.xml', String(opened));
  await p2.evaluate(e => window.__emit(e), loaded);
  await p2.waitForTimeout(400);
  await p2.screenshot({ path: OUT + '9-reopened.png' });
  const tabs2 = await p2.evaluate(() => [...document.querySelectorAll('.btn')].map(b => b.innerText).filter(t => /^V\d/.test(t)));
  check('ALL-THREE-VERSIONS-ARE-BACK', tabs2.join(',') === 'V1,V2,V3', tabs2.join(','));
  const strip2 = await p2.evaluate(() => (document.querySelector('.ai-strip') || {}).innerText || '');
  check('IT-IS-THE-USERS-TURN-WITH-THE-EDITORS-OWN-SUMMARY', /Your turn/.test(strip2) && /Ready for your review: V3/.test(strip2) && (await p2.locator('.ai-strip.working').count()) === 0, strip2.slice(0, 110).replace(/\n/g, ' | '));
  const s3 = await sent(p2);
  check('REOPENING-STARTS-NOTHING-NEW', !s3.includes('auto_edit') && !s3.includes('ai_review') && !s3.includes('build_review') && !s3.includes('apply_notes'), s3.join(','));
  check('THE-BEST-VERSION-IS-OPEN', /V3 open/.test(await p2.evaluate(() => document.getElementById('tablabel').innerText)), '');
  check('THE-FEED-SAYS-IT-WAS-REOPENED', /Reopened your earlier review of this cut \(V1, V2, V3\)/.test(await p2.evaluate(() => (document.querySelector('.ai-feed') || {}).innerText || '')), '');
  check('WHAT-IS-LEFT-IS-LISTED', /needs new words/.test(await p2.evaluate(() => document.body.innerText)), '');

  // a project with only an unreviewed cut: nothing is built behind the user's back; the list says so
  const p3 = await fresh();
  await p3.evaluate(e => window.__emit(e), { type: 'exports_listed', exports: [exportsList[0]] });
  await p3.waitForTimeout(300);
  check('AN-UNREVIEWED-CUT-IS-NOT-AUTO-BUILT', (await sent(p3)).filter(x => x !== 'list_exports').length === 0 && /not reviewed yet/.test(await p3.evaluate(() => document.body.innerText)), (await sent(p3)).join(','));

  // the list of a reviewed cut: Open review (as it was) and Start over (on purpose, asks first)
  const p4 = await fresh();
  await p4.evaluate(e => window.__emit(e), { type: 'exports_listed', exports: [exportsList[1]] });
  await p4.waitForTimeout(300);
  await p4.evaluate(e => window.__emit(e), loaded);
  await p4.waitForTimeout(300);
  await p4.getByText('Other cut…').click();
  await p4.waitForTimeout(300);
  const body4 = await p4.evaluate(() => document.body.innerText);
  check('THE-LIST-SHOWS-THE-SESSION-AND-BOTH-CHOICES', /reviewed: 3 versions, V3 is the one to review, 8 left/.test(body4) && /Open review/.test(body4) && /Start over/.test(body4), body4.slice(body4.indexOf('Cut.xml'), body4.indexOf('Cut.xml') + 110).replace(/\n/g, ' | '));
  await p4.evaluate(() => { window.__sent.length = 0; });
  await p4.getByText('Open review').click();
  await p4.waitForTimeout(300);
  const s4 = await sent(p4);
  check('OPEN-REVIEW-REOPENS-WITHOUT-THE-FRESH-FLAG', s4.join(',') === 'open_review', s4.join(','));
  const p5 = await fresh();
  await p5.evaluate(e => window.__emit(e), { type: 'exports_listed', exports: [exportsList[1]] });
  await p5.waitForTimeout(300);
  await p5.evaluate(e => window.__emit(e), loaded);
  await p5.waitForTimeout(300);
  await p5.getByText('Other cut…').click();
  await p5.waitForTimeout(300);
  await p5.evaluate(() => { window.__sent.length = 0; });
  await p5.getByText('Start over').first().click();                                                   // the dialog asking first is accepted by the handler above
  await p5.waitForTimeout(300);
  const s5 = await sent(p5);
  check('START-OVER-IS-EXPLICIT-AND-SENDS-THE-FRESH-FLAG', s5.join(',') === 'open_review:fresh', s5.join(','));

  check('NO-PAGE-ERRORS', errors.length === 0, errors.slice(0, 3).join(' | '));
  await browser.close();
  let bad = 0;
  for (const [n, ok, d] of rows) { bad += !ok; console.log(`[${ok ? 'PASS' : 'FAIL'}] ${n}  ${d}`); }
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error('DRIVER ERROR', e.message); process.exit(1); });
