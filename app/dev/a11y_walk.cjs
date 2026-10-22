// A screen-reader's walk of each room: does everything you can use have a name, do the headings step down one at a time, and does Tab go where the eye goes.
// axe (AXE=1 on the screenshot drivers) checks rules; this checks the thing a person using VoiceOver actually meets.
//   NODE_PATH=<pw> node dev/a11y_walk.cjs <outDir> [vitePort]
const { chromium } = require("playwright-core");
const fs = require("fs");
const path = require("path");

const out = process.argv[2] || "a11y_out";
const port = process.argv[3] || "5199";
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
fs.mkdirSync(out, { recursive: true });

const ROOMS = [
  { key: "home", go: null },
  { key: "footage", go: "Meta+1" },
  { key: "stories", go: "Meta+2" },
  { key: "trim", go: "Meta+3" },
  { key: "finish", go: "Meta+4" },
];

async function walk(page) {
  // everything a person can use, and whether a screen reader would have anything to say about it
  const unnamed = await page.evaluate(() => {
    const sel = 'button, a[href], input, select, textarea, [role="button"], [role="menuitem"], [role="tab"], [role="option"]';
    const name = (el) => (
      el.getAttribute("aria-label")
      || (el.getAttribute("aria-labelledby") && document.getElementById(el.getAttribute("aria-labelledby"))?.textContent)
      || el.title
      || (el.tagName === "INPUT" && el.labels?.[0]?.textContent)
      || el.textContent
      || ""
    ).trim();
    return [...document.querySelectorAll(sel)]
      .filter((el) => el.offsetParent !== null && !el.disabled)
      .filter((el) => !name(el))
      .map((el) => `${el.tagName.toLowerCase()}.${(el.className || "").toString().split(" ")[0]}`);
  });

  const headings = await page.evaluate(() => [...document.querySelectorAll("h1,h2,h3,h4")].filter((h) => h.offsetParent !== null).map((h) => ({ level: +h.tagName[1], text: h.textContent.trim().slice(0, 40) })));
  const jumps = [];
  for (let i = 1; i < headings.length; i += 1) {
    if (headings[i].level - headings[i - 1].level > 1) jumps.push(`${headings[i - 1].level} → ${headings[i].level} at "${headings[i].text}"`);
  }

  // where Tab goes, in order, for the first dozen stops
  const stops = [];
  await page.evaluate(() => document.body.focus());
  for (let i = 0; i < 12; i += 1) {
    await page.keyboard.press("Tab");
    stops.push(await page.evaluate(() => {
      const a = document.activeElement;
      if (!a || a === document.body) return "(nothing)";
      return `${a.tagName.toLowerCase()}:${(a.getAttribute("aria-label") || a.textContent || "").trim().slice(0, 26)}`;
    }));
  }
  return { unnamed, headingJumps: jumps, headings: headings.length, tabStops: stops };
}

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: "dark" })).newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const report = {};
  await page.goto(`http://localhost:${port}/harness.html?app=1`);
  await page.evaluate(() => window.__backend({ type: "ready", version: "1.0", person: "Ryan", settings: { has_env: true, active_source: "env" } }));
  await page.waitForTimeout(500);
  await page.getByRole("button", { name: /^Skip$/ }).click().catch(() => {});
  await page.waitForTimeout(300);
  // real-shaped projects, so the home screen's covers are part of the walk rather than an empty page
  await page.evaluate(() => window.__backend({ type: "projects_list", projects: [
    { name: "Runnells Wall Removal", dir: "/x/a", updated_at: Date.now() / 1000 - 600, sources: [], summary: { transcribed: 2, stories: 3, exports: 1 } },
    { name: "Runnells Weekend Interview Septic", dir: "/x/b", updated_at: Date.now() / 1000 - 90000, sources: [], summary: { transcribed: 2, stories: 0, exports: 0 } },
  ] }));
  await page.waitForTimeout(500);

  for (const room of ROOMS) {
    if (room.key === "footage") {
      await page.evaluate(() => window.__backend({ type: "project_loaded", project: { name: "Walk", resolved_dir: "/x", sources: [] } }));
      await page.waitForTimeout(400);
    }
    if (room.go) { await page.keyboard.press(room.go); await page.waitForTimeout(400); }
    report[room.key] = await walk(page);
  }

  report.pageErrors = errors;
  const bad = Object.entries(report).filter(([k, v]) => v && v.unnamed && (v.unnamed.length || v.headingJumps.length));
  report.clean = bad.length === 0;
  fs.writeFileSync(path.join(out, "a11y_walk.json"), JSON.stringify(report, null, 1));
  console.log(JSON.stringify({ clean: report.clean, rooms: Object.fromEntries(Object.entries(report).filter(([k]) => k !== "pageErrors" && k !== "clean").map(([k, v]) => [k, { unnamed: v.unnamed, headingJumps: v.headingJumps }])), pageErrors: errors }, null, 1));
  await browser.close();
  process.exit(report.clean && !errors.length ? 0 : 1);
})();
