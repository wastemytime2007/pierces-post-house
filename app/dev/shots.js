// Drives the real Review tab (through app/harness.html) into several states and takes screenshots + counts of controls.
//   node shots.js <outDir> [port]
const { chromium } = require("playwright-core");
const fs = require("fs");
const path = require("path");

const out = process.argv[2] || "shots";
const port = process.argv[3] || "5199";
fs.mkdirSync(out, { recursive: true });
const WK = "/Users/pierce/Desktop/wk xml - seqs";
const base = "http://127.0.0.1:47901/";
const NAMES = ["WK_01_bob_never_cutting_a_corner_over_a_dollar", "WK_02_bob_when_the_sub_says_done_go_check", "WK_03_mitch_why_we_wait_to_replace_a_septic", "WK_04_bob_fixing_a_water_heater_the_inspector_missed",
  "WK_05_mitch_feeling_good_versus_making_money", "WK_06_mitch_why_country_insurance_cost_20_more", "WK_07_mitch_why_you_can_t_just_hire_out_the_quality", "WK_08_mitch_if_it_is_someone_s_home_never_cut_corners"];
const n8 = NAMES[7];
const enc = (s) => encodeURIComponent(s);
const v1 = { label: "V1", xml: `${WK}/${n8}.xml`, folder: `${WK}/${n8} - review`, url: `${base}${enc(n8 + " - review")}/review.html`, qa: null };
const v2 = { label: "V2", xml: `${WK}/${n8}_v2 - revised/${n8}_v2.xml`, folder: `${WK}/${n8}_v2 - revised`, url: `${base}${enc(n8 + "_v2 - revised")}/review.html`, qa: null, applied: 1, notes: 1 };

async function counts(page, label) {
  const c = await page.evaluate(() => {
    const vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none"; };
    const btns = [...document.querySelectorAll("button, [role=button], input[type=checkbox], select, a[href]")].filter(vis);
    const small = btns.filter((b) => { const r = b.getBoundingClientRect(); return r.height < 28 || r.width < 28; });
    const fonts = {};
    document.querySelectorAll("body *").forEach((el) => { if (el.children.length === 0 && el.textContent.trim() && vis(el)) { const px = parseFloat(getComputedStyle(el).fontSize); fonts[px] = (fonts[px] || 0) + 1; } });
    const tiny = Object.entries(fonts).filter(([px]) => parseFloat(px) < 11).reduce((a, [, n]) => a + n, 0);
    const prominent = btns.filter((b) => /btn-primary|primary/.test(b.className)).length;
    return { controls: btns.length, under28: small.length, primary: prominent, textSizes: fonts, textUnder11px: tiny, labels: btns.map((b) => (b.textContent || b.getAttribute("aria-label") || b.title || b.type).trim().slice(0, 34)) };
  });
  console.log(label, JSON.stringify({ controls: c.controls, under28: c.under28, primary: c.primary, textUnder11px: c.textUnder11px, textSizes: c.textSizes }));
  fs.writeFileSync(path.join(out, label + ".controls.json"), JSON.stringify(c, null, 1));
}

(async () => {
  const browser = await chromium.launch({ executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1, colorScheme: process.env.SCHEME || "light" })).newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.addInitScript((items) => { localStorage.setItem("review.seqSet", JSON.stringify({ source: "/x/wk.xml", items })); },
    NAMES.map((n) => ({ id: n, name: n, xml: `${WK}/${n}.xml` })));
  await page.goto(`http://localhost:${port}/harness.html`);
  await page.waitForFunction(() => window.__ready, null, { timeout: 15000 });
  await page.waitForTimeout(500);

  // 1. the project shell on its first tab
  await page.screenshot({ path: path.join(out, "01_project_tab.png") });
  await counts(page, "01_project_tab");

  // 2. the Review tab with nothing open
  await page.getByText("Review", { exact: true }).click();
  await page.waitForTimeout(300);
  await page.evaluate((x) => window.__emit({ type: "exports_listed", exports: x }), [
    { name: "WK_08 mitch if it is someone s home", path: `${WK}/${n8}.xml`, folder: "wk xml - seqs", mtime: Date.now() / 1000 },
    { name: "Runnells Wall Removal 45d0ff4a v7", path: "/x/wall.xml", folder: "Wall Removal", mtime: Date.now() / 1000 - 9000 }]);
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(out, "02_review_empty.png") });
  await counts(page, "02_review_empty");

  // 3. a cut open (V1 and V2), the page loaded
  await page.evaluate((v) => window.__emit({ type: "review_session_loaded", root: v[0].xml, versions: v, ai: {}, auto: null, auto_running: false }), [v1, v2]);
  await page.waitForTimeout(2500);
  await page.screenshot({ path: path.join(out, "03_review_open.png"), fullPage: true });
  await counts(page, "03_review_open");

  // 4. the Get Creative dialog
  const gc = page.getByRole("button", { name: /Get Creative/ }).first();
  if (await gc.count()) { await gc.click(); await page.waitForTimeout(300); await page.screenshot({ path: path.join(out, "04_get_creative_dialog.png"), fullPage: true }); await counts(page, "04_get_creative_dialog"); await page.getByRole("button", { name: "Cancel" }).first().click().catch(() => {}); }

  // 5. a batch running and a batch finished, with the sequences opened from one XML
  const items = NAMES.map((n, i) => ({ xml: `${WK}/${n}.xml`, name: n, status: i < 3 ? "done" : i === 3 ? "running" : "waiting", step: i === 3 ? "adding captions, graphics, music, bleep" : "", message: i < 3 ? "no ums or uhs heard; added captions, graphics, music, bleep; music is an already generated music bed (score_dcce93aefbcb.mp3) (it could not be generated)" : "", final_xml: "" }));
  await page.evaluate((it) => window.__emit({ type: "creative_status", xml: it[0].xml, running: true, state: { state: "running", started: 1, items: it } }), items);
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(out, "05_batch_running.png"), fullPage: true });
  await counts(page, "05_batch_running");

  console.log("page errors:", JSON.stringify(errors));
  await browser.close();
})().catch((e) => { console.error("FAILED:", e); process.exit(1); });
