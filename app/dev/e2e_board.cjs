// End to end against the REAL backend: the board for a real finished batch (statuses from its get_creative.json, finished frames from the real previews). Reads only: nothing is sent or written.
//   node dev/e2e_board.cjs <outDir> [vitePort] [seqsFolder]
const { chromium } = require("playwright-core");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const out = process.argv[2] || "e2e_out";
const port = process.argv[3] || "5199";
const FOLDER = process.argv[4] || path.join(process.env.HOME, "Desktop", "wk xml - seqs");
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
fs.mkdirSync(out, { recursive: true });
const xmls = fs.readdirSync(FOLDER).filter((f) => /^WK_\d+.*\.xml$/.test(f)).sort();

(async () => {
  const bridge = spawn("node", [path.join(__dirname, "bridge.cjs"), "47950"], { stdio: ["ignore", "pipe", "inherit"] });
  await new Promise((res) => bridge.stdout.on("data", (d) => String(d).includes("bridge on") && res()));
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1280, height: parseInt(process.env.H || "900", 10) }, colorScheme: process.env.SCHEME || "dark" });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const items = xmls.map((f) => ({ id: f, name: f.replace(/\.xml$/, ""), xml: path.join(FOLDER, f) }));
  await page.addInitScript((it) => { localStorage.setItem("review.seqSet", JSON.stringify({ source: "/x/wk.xml", items: it })); }, items);
  const report = { sequences: items.length };
  try {
    await page.goto(`http://localhost:${port}/harness.html?app=1&bridge=1`);
    await page.waitForFunction(() => document.querySelectorAll(".cover").length > 0, null, { timeout: 40000 });
    await page.evaluate(() => window.__backend({ type: "project_loaded", project: { name: "E2E", resolved_dir: "/x", sources: [] } }));
    await page.waitForTimeout(700);
    await page.keyboard.press("Meta+4");
    // open one cut of the batch the way a dropped XML would (its review already exists, so it loads rather than rebuilds); the sidebar and board need a cut open
    await page.evaluate((x) => window.dispatchEvent(new CustomEvent("posthouse-open-xml", { detail: { path: x } })), items[items.length - 1].xml);
    await page.waitForSelector(".rv-side", { timeout: 120000 });
    await page.waitForTimeout(4000);                                                  // the batch record and the frames arrive
    await page.getByRole("button", { name: /Board/ }).click();
    await page.waitForTimeout(8000);
    report.chips = await page.evaluate(() => [...document.querySelectorAll(".board-chip")].map((e) => e.textContent.trim()).reduce((a, k) => ({ ...a, [k]: (a[k] || 0) + 1 }), {}));
    report.framesShown = await page.evaluate(() => [...document.querySelectorAll(".cut-thumb.board")].filter((e) => /url\(["']?data:image/.test(e.style.backgroundImage)).length);
    report.heading = await page.evaluate(() => document.querySelector(".rv-board h2")?.textContent);
    report.sendButton = await page.evaluate(() => [...document.querySelectorAll(".rv-board button")].map((b) => b.textContent.trim()).find((t) => /^Send/.test(t)));
    await page.screenshot({ path: path.join(out, "b1_board_real.png") });
  } catch (e) {
    report.failed = String(e.message || e).split("\n")[0];
    await page.screenshot({ path: path.join(out, "b_failed.png") }).catch(() => {});
  }
  report.pageErrors = errors;
  console.log(JSON.stringify(report));
  await browser.close();
  bridge.kill();
  process.exit(report.failed || errors.length ? 1 : 0);
})();
