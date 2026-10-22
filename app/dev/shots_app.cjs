// Screenshots of the whole app in a browser with a stand-in backend (see harness.html). Usage: node dev/shots_app.cjs <outDir> [port]
// Needs `npx vite --port <port> --strictPort` running and playwright-core; CHROME overrides the browser path, SCHEME=dark|light the appearance.
const { chromium } = require("playwright-core");
const fs = require("fs");
const path = require("path");
const out = process.argv[2] || "shots_app";
const port = process.argv[3] || "5199";
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
fs.mkdirSync(out, { recursive: true });
const PROJECT = {
  name: "Runnells Weekend", resolved_dir: "/Users/pierce/Documents/Post House/Runnells Weekend",
  sources: [{ kind: "aroll", name: "Interviews", root_path: "/Volumes/Runnells/Interviews", files: { "wknd_Bob_01.mp4": { transcript_status: "done" }, "wknd_Mitch_01.mp4": { transcript_status: "done" } } },
            { kind: "broll", name: "Job site", root_path: "/Volumes/Runnells/Job site", files: { "DJI_0021.mp4": {} } }],
};
const ANGLES = [1, 2, 3].map((n) => ({ idea_id: `a${n}`, kind: "story_angle", created_at: Date.now() / 1000, refinement_count: 0, selected_platform_key: "", selected_aspect_key: "",
  data: { angle_id: `ang${n}`, brief: { title: ["Why we never cut corners on a sub's work", "The inspector missed it", "Feeling good versus making money"][n - 1], hook: "Bob walks the job after the sub says done.", why_it_works: "A real person making the call nobody sees.", tone: "plain, a little funny", target_audience: "homeowners", call_to_action: "DM us if you want yours", target_duration_sec: 45 },
    source_ranges: [{ source_path: "/Volumes/Runnells/Interviews/wknd_Bob_01.mp4", source_start_sec: 120, source_end_sec: 140 }, { source_path: "/Volumes/Runnells/Interviews/wknd_Bob_01.mp4", source_start_sec: 300, source_end_sec: 318 }] } }));
(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: process.env.SCHEME || "light" })).newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const shot = async (name) => {
    await page.waitForTimeout(350);
    await page.screenshot({ path: path.join(out, name + ".png"), fullPage: true });
    const m = await page.evaluate(() => {
      const vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.display !== "none" && s.visibility !== "hidden"; };
      const ctl = [...document.querySelectorAll("button, input, select, textarea, a[href]")].filter(vis);
      return { controls: ctl.length, under24: ctl.filter((c) => c.getBoundingClientRect().height < 24).length, primary: ctl.filter((c) => /btn-primary|pm-tab-submit/.test(c.className)).length };
    });
    console.log(name, JSON.stringify(m));
  };
  await page.goto(`http://localhost:${port}/harness.html?app=1`);
  await page.waitForTimeout(1200);
  await page.evaluate(() => window.__backend({ type: "ready", version: "1.0", settings: { has_env: false, active_source: "none", llm_via_cli: false } }));
  await shot("09_welcome");
  await page.getByRole("button", { name: "Skip" }).first().click().catch(() => {});
  await page.evaluate(() => window.__backend({ type: "settings", settings: { has_env: false, active_source: "none", llm_via_cli: false, welcome_seen: true, tour_seen: true, auto_include_nudge_seen: true } }));
  await page.evaluate(() => window.__backend({ type: "projects_list", projects: [
    { name: "Runnells Weekend", path: "/x/a", updated_at: Date.now() / 1000 - 3600, source_count: 2 }, { name: "Wall Removal", path: "/x/b", updated_at: Date.now() / 1000 - 90000, source_count: 4 }] }));
  await shot("10_start");
  await page.getByRole("button", { name: /New project/i }).first().click().catch(() => {});
  await shot("11_new_project");
  await page.keyboard.press("Escape");
  await page.evaluate((p) => window.__backend({ type: "project_loaded", project: p }), PROJECT);
  await shot("12_project_tab");
  await page.getByRole("button", { name: "Settings" }).click();
  await shot("13_settings_menu");
  await page.getByRole("menuitem", { name: /API key/ }).click();
  await shot("14_api_key_modal");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: /Close|Cancel|×/ }).first().click().catch(() => {});
  await page.evaluate((a) => window.__backend({ type: "ideas_list", ideas: a }), ANGLES);
  await page.getByRole("tab", { name: /Ideas/ }).click();
  await shot("15_ideas");
  await page.getByRole("button", { name: "Activity" }).click();
  await shot("16_activity_open");
  console.log("page errors:", JSON.stringify(errors));
  await browser.close();
})().catch((e) => { console.error("FAILED:", e); process.exit(1); });
