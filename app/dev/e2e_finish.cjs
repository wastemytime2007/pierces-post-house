// End to end against the REAL backend: a many-sequence XML (a scratch COPY, so nothing is written beside the original) goes through the contact sheet, two sequences are chosen and opened, and the real
// review page builds. Reads the footage; writes only inside the scratch folder.
//   node dev/e2e_finish.cjs <outDir> [vitePort] [xml]
const { chromium } = require("playwright-core");
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const out = process.argv[2] || "e2e_out";
const port = process.argv[3] || "5199";
const SRC = process.argv[4] || path.join(process.env.HOME, "Desktop", "wk xml.xml");
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
fs.mkdirSync(out, { recursive: true });
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "posthouse-e2e-"));
const XML = path.join(scratch, path.basename(SRC));
fs.copyFileSync(SRC, XML);

(async () => {
  const bridge = spawn("node", [path.join(__dirname, "bridge.cjs"), "47950"], { stdio: ["ignore", "pipe", "inherit"] });
  await new Promise((res) => bridge.stdout.on("data", (d) => String(d).includes("bridge on") && res()));
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1280, height: parseInt(process.env.H || "900", 10) }, colorScheme: process.env.SCHEME || "dark" })).newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const report = { scratch };
  const t0 = Date.now();
  try {
    await page.goto(`http://localhost:${port}/harness.html?app=1&bridge=1`);
    await page.waitForFunction(() => document.querySelectorAll(".cover").length > 0, null, { timeout: 40000 });
    await page.evaluate(() => window.__backend({ type: "project_loaded", project: { name: "E2E", resolved_dir: "/x", sources: [] } }));
    await page.keyboard.press("Meta+4");
    await page.waitForTimeout(400);
    await page.evaluate((x) => window.dispatchEvent(new CustomEvent("posthouse-open-xml", { detail: { path: x } })), XML);
    await page.waitForFunction(() => document.querySelectorAll(".sp-tile").length > 0, null, { timeout: 60000 });
    report.sequences = await page.evaluate(() => document.querySelectorAll(".sp-tile").length);
    await page.waitForTimeout(6000);
    await page.screenshot({ path: path.join(out, "f1_sheet.png") });
    await page.locator(".sp-tile:not([disabled])").nth(0).click();
    await page.locator(".sp-tile:not([disabled])").nth(1).click();
    await page.getByRole("button", { name: /Open 2 sequences/ }).click();
    // the sequences are cut out into their own XMLs, then the first one's review is built
    await page.waitForSelector(".rv-toolbar", { timeout: 300000 });
    report.reviewOpenedAfterSec = Math.round((Date.now() - t0) / 1000);
    await page.waitForFunction(() => { const f = document.querySelector(".rv-frame iframe"); return f && f.src && f.src.includes("review.html"); }, null, { timeout: 60000 });
    await page.waitForTimeout(9000);                                                // the page loads, the filmstrip is read
    report.sidebarRows = await page.evaluate(() => document.querySelectorAll(".rv-seq").length);
    report.title = await page.evaluate(() => document.querySelector(".rv-title h1")?.textContent);
    await page.screenshot({ path: path.join(out, "f2_review_real.png") });
    const frame = page.frames().find((f) => f.url().includes("review.html"));
    report.framePage = frame ? await frame.evaluate(() => ({ duration: window.DATA && DATA.duration, strips: document.querySelectorAll("#filmstrip").length, lanes: document.querySelectorAll(".lane").length })) : "iframe not found";
  } catch (e) {
    report.failed = String(e.message || e).split("\n")[0];
    await page.screenshot({ path: path.join(out, "f_failed.png") }).catch(() => {});
  }
  report.pageErrors = errors;
  report.seconds = Math.round((Date.now() - t0) / 1000);
  console.log(JSON.stringify(report));
  await browser.close();
  bridge.kill();
  process.exit(report.failed || errors.length ? 1 : 0);
})();
