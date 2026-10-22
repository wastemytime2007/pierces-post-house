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
    await page.goto(`http://localhost:${port}/harness.html?app=1&bridge=1`);
    await page.waitForFunction(() => document.querySelectorAll(".cover").length > 0, null, { timeout: 40000 });
    await page.waitForTimeout(6000);                                                  // posters arrive one by one
    report.projects = await page.evaluate(() => document.querySelectorAll(".cover").length);
    report.postersShown = await page.evaluate(() => [...document.querySelectorAll(".cover-art")].filter((e) => /url\(["']?data:image/.test(e.style.backgroundImage)).length);
    await page.screenshot({ path: path.join(out, "e1_home_real.png") });

    // a project open (UI only: the backend is not asked to load or change anything), then Finish, then a real XML dropped on the window
    await page.evaluate(() => window.__backend({ type: "project_loaded", project: { name: "E2E", resolved_dir: "/x", sources: [] } }));
    await page.keyboard.press("Meta+4");
    await page.waitForTimeout(500);
    await page.evaluate((x) => window.dispatchEvent(new CustomEvent("posthouse-open-xml", { detail: { path: x } })), XML);
    await page.waitForFunction(() => document.querySelectorAll(".sp-tile").length > 0, null, { timeout: 60000 });
    report.sequences = await page.evaluate(() => document.querySelectorAll(".sp-tile").length);
    await page.waitForTimeout(15000);                                                 // frames are cut in the background, a few at a time
    report.framesShown = await page.evaluate(() => [...document.querySelectorAll(".sp-art")].filter((e) => /url\(["']?data:image/.test(e.style.backgroundImage)).length);
    await page.screenshot({ path: path.join(out, "e2_sheet_real.png") });
  } catch (e) {
    report.failed = String(e.message || e).split("\n")[0];
    await page.screenshot({ path: path.join(out, "e_failed.png") }).catch(() => {});
  }
  report.pageErrors = errors;
  console.log(JSON.stringify(report));
  await browser.close();
  bridge.kill();
  process.exit(report.failed || errors.length ? 1 : 0);
})();
