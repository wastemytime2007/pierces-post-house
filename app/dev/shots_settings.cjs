// The Settings window on its own (it is a second Tauri window; here it is the same bundle at ?settings=1 with a stand-in backend).
//   node dev/shots_settings.cjs <outDir> [vitePort]
const { chromium } = require("playwright-core");
const fs = require("fs");
const path = require("path");

const out = process.argv[2] || "shots_out";
const port = process.argv[3] || "5199";
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
fs.mkdirSync(out, { recursive: true });

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const errors = [];
  for (const scheme of ["dark", "light"]) {
    const page = await (await browser.newContext({ viewport: { width: 620, height: 460 }, colorScheme: scheme })).newPage();
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(`http://localhost:${port}/harness.html?settings=1`);
    await page.waitForSelector(".set-win", { timeout: 20000 });
    await page.evaluate(() => window.__backend({ type: "ready", version: "1.0", settings: { has_env: false, active_source: "none", llm_via_cli: true, key_suffix: "" } }));
    await page.evaluate(() => window.__backend({ type: "premiere_apps", apps: [{ path: "/Applications/Adobe Premiere Pro 2026/Adobe Premiere Pro 2026.app", name: "Adobe Premiere Pro 2026", beta: false }, { path: "/Applications/Adobe Premiere Pro (Beta)/Adobe Premiere Pro (Beta).app", name: "Adobe Premiere Pro (Beta)", beta: true }] }));
    await page.waitForTimeout(300);
    for (const [id, pane] of [["general", "General"], ["premiere", "Premiere"], ["ai", "AI"]]) {
      await page.getByRole("button", { name: pane }).click();
      await page.waitForTimeout(150);
      await page.screenshot({ path: path.join(out, `settings_${id}_${scheme}.png`) });
    }
  }
  console.log(JSON.stringify({ pageErrors: errors }));
  await browser.close();
  process.exit(errors.length ? 1 : 0);
})();
