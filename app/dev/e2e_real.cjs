// End to end against the REAL backend and the real machine's projects (reads only): the home screen with real covers, then a real many-sequence XML as a contact sheet.
//   node dev/e2e_real.cjs <outDir> [vitePort] [xml]
// Needs `npx vite --port <vitePort> --strictPort` and playwright-core. Starts and stops dev/bridge.cjs itself.
const { chromium } = require("playwright-core");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const out = process.argv[2] || "e2e_out";
const port = process.argv[3] || "5199";
const XML = process.argv[4] || path.join(process.env.HOME, "Documents", "SoldFast ALL SHORTS.xml");
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
fs.mkdirSync(out, { recursive: true });

(async () => {
  const bridge = spawn("node", [path.join(__dirname, "bridge.cjs"), "47950"], { stdio: ["ignore", "pipe", "inherit"] });
  await new Promise((res) => bridge.stdout.on("data", (d) => String(d).includes("bridge on") && res()));
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1280, height: parseInt(process.env.H || "900", 10) }, colorScheme: process.env.SCHEME || "dark" })).newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const report = {};
  try {
    // A budget, kept honest by measuring it: the home screen must be usable quickly, and moving between rooms must feel like nothing happened.
    const t_nav = Date.now();
    await page.goto(`http://localhost:${port}/harness.html?app=1&bridge=1`);
    await page.waitForFunction(() => document.querySelectorAll(".cover").length > 0, null, { timeout: 40000 });
    report.homeMs = Date.now() - t_nav;
    await page.waitForTimeout(6000);                                                  // posters arrive one by one
    report.projects = await page.evaluate(() => document.querySelectorAll(".cover").length);
    report.postersShown = await page.evaluate(() => [...document.querySelectorAll(".cover-art")].filter((e) => /url\(["']?data:image/.test(e.style.backgroundImage)).length);
    await page.screenshot({ path: path.join(out, "e1_home_real.png") });

    // a project open (UI only: the backend is not asked to load or change anything), then Finish, then a real XML dropped on the window
    await page.evaluate(() => window.__backend({ type: "project_loaded", project: { name: "E2E", resolved_dir: "/x", sources: [] } }));
    await page.waitForTimeout(300);
    const roomMs = [];
    for (const k of ["Meta+1", "Meta+2", "Meta+3", "Meta+4", "Meta+1"]) {
      const t0 = Date.now();
      await page.keyboard.press(k);
      await page.waitForFunction(() => !!document.querySelector(".project-stage > *"), null, { timeout: 5000 });
      roomMs.push(Date.now() - t0);
    }
    report.roomMs = roomMs;
    report.roomWorst = Math.max(...roomMs);
    await page.keyboard.press("Meta+4");
    await page.waitForTimeout(500);
    await page.evaluate((x) => window.dispatchEvent(new CustomEvent("posthouse-open-xml", { detail: { path: x } })), XML);
    await page.waitForFunction(() => document.querySelectorAll(".sp-tile").length > 0, null, { timeout: 60000 });
    report.sequences = await page.evaluate(() => document.querySelectorAll(".sp-tile").length);
    await page.waitForTimeout(15000);                                                 // frames are cut in the background, a few at a time
    report.framesShown = await page.evaluate(() => [...document.querySelectorAll(".sp-art")].filter((e) => /url\(["']?data:image/.test(e.style.backgroundImage)).length);
    await page.screenshot({ path: path.join(out, "e2_sheet_real.png") });
    // click picks one, shift-click picks the range from the last click, a second shift-click from the same anchor adds the next stretch
    const tiles = page.locator(".sp-tile:not([disabled])");
    await tiles.nth(1).click();
    await tiles.nth(5).click({ modifiers: ["Shift"] });
    report.rangeChosen = await page.evaluate(() => document.querySelectorAll(".sp-tile.on").length);          // 5 expected (tiles 2 to 6)
    report.footer = await page.evaluate(() => document.querySelector(".sp-foot span")?.textContent);
    // arrow keys walk the sheet, Space looks at one frame large, Escape puts it back
    await tiles.nth(0).focus();
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press(" ");
    await page.waitForTimeout(400);
    report.peek = await page.evaluate(() => { const e = document.querySelector(".sp-peek-art"); return e ? /url\(["']?data:image/.test(e.style.backgroundImage) : false; });
    await page.screenshot({ path: path.join(out, "e3_peek.png") });
    await page.keyboard.press("Escape");
    await page.waitForTimeout(200);
    report.peekClosed = await page.evaluate(() => !document.querySelector(".sp-peek"));
  } catch (e) {
    report.failed = String(e.message || e).split("\n")[0];
    await page.screenshot({ path: path.join(out, "e_failed.png") }).catch(() => {});
  }
  // The budget. A run that breaks it fails, so a slow change is noticed the day it lands, not months later.
  report.budget = {
    home: { ms: report.homeMs, limit: 2500, ok: report.homeMs <= 2500 },
    room: { ms: report.roomWorst, limit: 400, ok: (report.roomWorst ?? 0) <= 400 },
  };
  report.budgetKept = Object.values(report.budget).every((b) => b.ok !== false);
  report.pageErrors = errors;
  console.log(JSON.stringify(report));
  await browser.close();
  bridge.kill();
  process.exit(report.failed || errors.length || !report.budgetKept ? 1 : 0);
})();
