// Screenshots of the whole app in a browser with a stand-in backend (see harness.html). Usage: node dev/shots_app.cjs <outDir> [port]
// Needs `npx vite --port <port> --strictPort` running and playwright-core; CHROME overrides the browser path, SCHEME=dark|light the appearance.
const { chromium } = require("playwright-core");
const fs = require("fs");
const path = require("path");
const out = process.argv[2] || "shots_app";
const FR2 = process.env.FRAMES || path.join(__dirname, "frames");
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
  const page = await (await browser.newContext({ viewport: { width: parseInt(process.env.W || "1280", 10), height: parseInt(process.env.H || "800", 10) }, colorScheme: process.env.SCHEME || "light" })).newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const shot = async (name) => {
    await page.waitForTimeout(350);
    await page.screenshot({ path: path.join(out, name + ".png"), fullPage: true });
    const m = await page.evaluate(() => {
      const vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.display !== "none" && s.visibility !== "hidden"; };
      const ctl = [...document.querySelectorAll("button, input, select, textarea, a[href]")].filter(vis);
      const overflowX = document.documentElement.scrollWidth > window.innerWidth + 1 ? document.documentElement.scrollWidth : 0;
      return { overflowX, controls: ctl.length, under24: ctl.filter((c) => c.getBoundingClientRect().height < 24).length, primary: ctl.filter((c) => /btn-primary|pm-tab-submit/.test(c.className)).length };
    });
    console.log(name, JSON.stringify(m));
    if (process.env.AXE) {
      if (!(await page.evaluate(() => !!window.axe))) await page.addScriptTag({ path: require.resolve("axe-core/axe.min.js") });
      const v = await page.evaluate(async () => (await window.axe.run(document, { resultTypes: ["violations"] })).violations.map((x) => ({ id: x.id, impact: x.impact, n: x.nodes.length, ex: x.nodes[0].target.join(" ").slice(0, 70) })));
      for (const x of v) console.log("   AXE", x.impact, x.id, "x" + x.n, x.ex);
    }
  };
  await page.goto(`http://localhost:${port}/harness.html?app=1`);
  await page.waitForTimeout(1200);
  await page.evaluate(() => window.__backend({ type: "ready", version: "1.0", settings: { has_env: false, active_source: "none", llm_via_cli: false } }));
  await shot("09_welcome");
  await page.getByRole("button", { name: "Next" }).click();
  await shot("09b_welcome_rooms");
  await page.getByRole("button", { name: "Next" }).click();
  await shot("09c_welcome_key");
  await page.getByRole("button", { name: "Next" }).click();
  await shot("09d_welcome_ready");
  await page.getByRole("button", { name: "Skip" }).first().click().catch(() => {});
  await page.evaluate(() => window.__backend({ type: "settings", settings: { has_env: false, active_source: "none", llm_via_cli: false, welcome_seen: true, tour_seen: true, auto_include_nudge_seen: true } }));
  // Real frames: dev/frames/*.jpg if present (cut from your own footage with thumbs.py), else the posters stay as brand-color tiles
  const NAMES = ["Runnells Weekend", "Runnells Wall Removal", "Runnells Hanging Mirror", "Kitchen and Doors, 3 Reels", "How to remove wallpaper", "Test2", "Tiling the bathroom", "Mitch interview"];
  await page.evaluate((names) => window.__backend({ type: "projects_list", projects: names.map((n, i) => ({ name: n, dir: "/x/" + i, updated_at: Date.now() / 1000 - 3600 * (i * i + 1), source_count: n === "Test2" ? 0 : 2 + i, summary: { transcribed: i === 4 ? 0 : 2, stories: [4, 3, 0, 1, 0, 0][i] ?? 2, exports: [1, 0, 0, 0, 0, 0][i] ?? 0 } })) }), NAMES);
  const FR = process.env.FRAMES || path.join(__dirname, "frames");
  if (fs.existsSync(FR)) {
    const files = fs.readdirSync(FR).filter((f) => f.endsWith(".jpg")).sort();
    for (let i = 0; i < Math.min(files.length, NAMES.length - 1); i++) {
      const uri = "data:image/jpeg;base64," + fs.readFileSync(path.join(FR, files[i])).toString("base64");
      await page.evaluate(([n, u]) => window.__backend({ type: "project_poster", name: n, poster: u }), [NAMES[i], uri]);
    }
  }
  await shot("10_start");
  await page.locator(".cover").first().click({ button: "right" });
  await shot("10b_cover_menu");
  await page.keyboard.press("Escape");
  await page.mouse.click(5, 400);
  await page.getByRole("button", { name: /New project/i }).first().click().catch(() => {});
  await shot("11_new_project");
  await page.keyboard.press("Escape");
  await page.evaluate((p) => window.__backend({ type: "project_loaded", project: p }), PROJECT);
  if (fs.existsSync(FR2)) {
    const f3 = fs.readdirSync(FR2).filter((f) => f.endsWith(".jpg")).sort();
    const u = (k) => "data:image/jpeg;base64," + fs.readFileSync(path.join(FR2, f3[k % f3.length])).toString("base64");
    await page.evaluate((x) => window.__backend({ type: "frame_thumbs", thumbs: x }), { "src:wknd_Bob_01.mp4": u(0), "src:wknd_Mitch_01.mp4": u(1), "src:DJI_0021.mp4": u(2) });
  }
  await shot("12_project_tab");
  // processing running: proxies done, transcription partway, audio sync pairs
  await page.evaluate(() => {
    const e = (ev) => window.__backend(ev);
    e({ type: "pipeline_started", job_id: "j1" });
    e({ type: "stage_started", job_id: "j1", stage: "proxies", total: 3 });
    ["wknd_Bob_01.mp4", "wknd_Mitch_01.mp4", "DJI_0021.mp4"].forEach((f, i) => e({ type: "file_done", job_id: "j1", stage: "proxies", file: f, status: "ok", completed: i + 1, total: 3, elapsed_sec: 12 }));
    e({ type: "stage_complete", job_id: "j1", stage: "proxies", success: 3, failed: 0, skipped: 0 });
    e({ type: "stage_started", job_id: "j1", stage: "transcribe", total: 2 });
    e({ type: "file_done", job_id: "j1", stage: "transcribe", file: "wknd_Bob_01.mp4", status: "ok", completed: 1, total: 2, elapsed_sec: 94 });
    e({ type: "audio_sync_pair", job_id: "j1", i: 1, total: 2, aroll: "wknd_Bob_01.mp4", audio: "Bob_lav.WAV", score: 31.2, offset: 4.82, reliable: true });
  });
  await page.waitForTimeout(400);
  await shot("12b_processing");
  await page.evaluate(() => window.__backend({ type: "pipeline_complete", job_id: "j1" }));
  await page.getByRole("button", { name: /Process again/ }).click().catch(() => {});
  await shot("12c_process_dialog");
  await page.getByRole("button", { name: /Close|Cancel|×/ }).first().click().catch(() => {});
  await page.getByRole("button", { name: "Activity" }).hover();
  await page.waitForTimeout(900);
  await shot("12g_tooltip");
  const tipOk = await page.evaluate(() => ({ shown: !!document.querySelector(".tooltip"), text: document.querySelector(".tooltip")?.textContent, titleLeft: document.querySelectorAll("[title]").length }));
  console.log("tooltip:", JSON.stringify(tipOk));
  await page.mouse.move(600, 500);
  await page.keyboard.press("Meta+k");
  await shot("12d_palette");
  await page.keyboard.type("cre");
  await shot("12e_palette_filtered");
  await page.keyboard.press("Escape");
  await page.keyboard.press("?");
  await shot("12f_shortcuts");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Settings" }).click();
  await shot("13_settings_menu");
  await page.getByRole("menuitem", { name: /API key/ }).click();
  await shot("14_api_key_modal");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: /Close|Cancel|×/ }).first().click().catch(() => {});
  await page.getByRole("tab", { name: /Stories/ }).click();
  await page.evaluate(() => window.__backend({ type: "ideas_list", ideas: [] }));
  await shot("15a_stories_empty");
  await page.evaluate((a) => window.__backend({ type: "ideas_list", ideas: a }), ANGLES);
  await page.evaluate(() => window.__backend({ type: "exports_listed", exports: [
    { name: "Runnells Weekend - 3 stories.xml", path: "/x/exports/Runnells Weekend - 3 stories.xml", mtime: Date.now() / 1000 - 1500, session: null },
    { name: "Runnells Weekend - first pass.xml", path: "/x/exports/Runnells Weekend - first pass.xml", mtime: Date.now() / 1000 - 90000, session: { versions: 2 } }] }));
  await page.getByRole("tab", { name: /Trim/ }).click();
  await shot("14b_trim");
  await page.getByRole("tab", { name: /Stories/ }).click();
  if (fs.existsSync(FR2)) {
    const f2 = fs.readdirSync(FR2).filter((f) => f.endsWith(".jpg")).sort();
    const th = {};
    [120, 300].forEach((sec, i) => { th[`f:/Volumes/Runnells/Interviews/wknd_Bob_01.mp4:${sec}`] = "data:image/jpeg;base64," + fs.readFileSync(path.join(FR2, f2[i % f2.length])).toString("base64"); });
    await page.evaluate((x) => window.__backend({ type: "frame_thumbs", thumbs: x }), th);
  }
  await shot("15_ideas");
  // the planning panel
  await page.getByRole("button", { name: /Plan with AI/ }).click();
  await shot("15b_plan_panel");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: /Close|Cancel|×/ }).first().click().catch(() => {});
  // choose two ideas, then the export dialog
  const boxes = page.locator(".storyboard-check input");
  await boxes.nth(0).check({ force: true });
  await boxes.nth(1).check({ force: true });
  await shot("15c_ideas_chosen");
  await page.getByRole("button", { name: /Open in Premiere/ }).click().catch(() => {});
  await shot("15d_export_dialog");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: /Close|Cancel|×/ }).first().click().catch(() => {});
  await page.getByRole("button", { name: "Activity" }).click();
  await shot("16_activity_open");
  await page.getByRole("button", { name: /Hide activity/ }).click();
  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByRole("menuitem", { name: /Audiences/ }).click();
  await shot("17_audiences_dialog");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: /Close|Cancel|×/ }).first().click().catch(() => {});
  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByRole("menuitem", { name: /Default includes/ }).click();
  await shot("18_includes_dialog");
  await page.getByRole("button", { name: /Close|Cancel|×/ }).first().click().catch(() => {});
  // the first-run setup screen
  await page.goto(`http://localhost:${port}/harness.html?app=1&setup=1`);
  await page.waitForTimeout(1500);
  await shot("19_setup");
  console.log("page errors:", JSON.stringify(errors));
  await browser.close();
})().catch((e) => { console.error("FAILED:", e); process.exit(1); });
