// Drives the click-a-cut PAGE (cut_menu.html) in a simulated browser (jsdom) against the real server, the way Ryan would use it:
// open an XML, see the cuts, click a cut, walk the menu (Add transition > Film Impact Push > a direction), see the badge, change it, remove it, save, read what was written.
//   npm i jsdom (once, in any folder; set NODE_PATH to its node_modules)   NODE_PATH=... node labs/uxp_transitions/test_cut_menu_page.js
const assert = require("assert");
const { JSDOM } = require("jsdom");
const { execFileSync, spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const here = __dirname;
const work = fs.mkdtempSync(path.join(os.tmpdir(), "cutpage-"));
const builder = fs.mkdtempSync(path.join(os.tmpdir(), "cutbuild-"));
execFileSync("python3", [path.join(here, "make_demo_input.py"), builder]);
const xml = path.join(work, "three cuts.xml");
fs.copyFileSync(path.join(builder, "2 - three cuts, no transition, no sound.xml"), xml);
const original = fs.readFileSync(xml);

const port = 8800 + Math.floor(Math.random() * 100);
const server = spawn("python3", [path.join(here, "cut_menu.py"), "--port", String(port), "--no-open", "--roots", work], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fire = (el, type, x = 100, y = 100) => el.dispatchEvent(new el.ownerDocument.defaultView.MouseEvent(type, { bubbles: true, clientX: x, clientY: y }));

(async () => {
  for (let i = 0; i < 40; i++) { try { await fetch(`http://127.0.0.1:${port}/`); break; } catch (e) { await sleep(150); } }
  const html = await (await fetch(`http://127.0.0.1:${port}/`)).text();
  const dom = new JSDOM(html, { url: `http://127.0.0.1:${port}/`, runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, `http://127.0.0.1:${port}/`), o); } });
  const doc = dom.window.document;
  await sleep(800);

  const files = [...doc.querySelectorAll(".file")];
  assert.strictEqual(files.length, 1, "lists the one exported XML");
  assert(files[0].textContent.includes("three cuts.xml"));
  fire(files[0], "click");
  await sleep(800);
  assert(!doc.getElementById("work").classList.contains("hidden"), "the work area opens");
  assert.strictEqual(doc.getElementById("title").textContent, "Push and whoosh demo (3 cuts)");
  const cuts = [...doc.querySelectorAll(".cut:not(.off)")];
  assert.deepStrictEqual(cuts.map((c) => c.dataset.frame), ["100", "185", "290"], "three clickable cuts between the four clips");
  assert.strictEqual(doc.querySelectorAll(".clip").length, 4);
  assert.strictEqual(doc.getElementById("save").disabled, true, "nothing to save yet");

  // click the first cut: the menu opens at the mouse with Add transition > Film Impact Push > four directions
  fire(cuts[0], "click", 300, 200);
  const menu = doc.getElementById("menu");
  assert(!menu.classList.contains("hidden"), "menu opens");
  assert.strictEqual(menu.style.left, "306px", "next to the mouse");
  assert(menu.textContent.includes("Add transition") && menu.textContent.includes("Film Impact Push"));
  const dirs = [...menu.querySelectorAll("[data-set]")].map((e) => e.dataset.set);
  assert.deepStrictEqual(dirs, ["left", "up", "down", "right"]);
  assert(!menu.textContent.includes("Remove transition here"), "no remove entry before anything is set");
  fire(menu.querySelector('[data-set="left"]'), "click");
  assert(menu.classList.contains("hidden"), "menu closes after a choice");
  let strip = [...doc.querySelectorAll(".cut")];
  assert(strip[0].classList.contains("on") && strip[0].textContent.includes("Push ←") && strip[0].textContent.includes("auto"), "left shows as automatic: " + strip[0].textContent);

  // third cut: Up (marked for Ryan's preset)
  fire(doc.querySelectorAll(".cut:not(.off)")[2], "click", 500, 220);
  fire(doc.getElementById("menu").querySelector('[data-set="up"]'), "click");
  strip = [...doc.querySelectorAll(".cut")];
  assert(strip[2].classList.contains("manual") && strip[2].textContent.includes("Push ↑") && strip[2].textContent.includes("marked"), strip[2].textContent);
  assert(doc.getElementById("count").textContent.includes("2 cuts chosen: 1 left push (automatic), 1 marked"), doc.getElementById("count").textContent);

  // change the middle cut to down, then remove it again
  fire(doc.querySelectorAll(".cut:not(.off)")[1], "click", 400, 210);
  fire(doc.getElementById("menu").querySelector('[data-set="down"]'), "click");
  assert(doc.getElementById("count").textContent.includes("3 cuts chosen"));
  fire(doc.querySelectorAll(".cut:not(.off)")[1], "click", 400, 210);
  const rm = doc.getElementById("menu").querySelector("[data-clear]");
  assert(rm && rm.textContent.includes("Remove transition here"), "remove appears once set");
  fire(rm, "click");
  assert(doc.getElementById("count").textContent.includes("2 cuts chosen"), "the middle cut is clear again");

  // save
  assert.strictEqual(doc.getElementById("save").disabled, false);
  fire(doc.getElementById("save"), "click");
  await sleep(1500);
  const result = doc.getElementById("result").textContent;
  assert(result.includes("Saved.") && result.includes("2 cuts: 1 left push") && result.includes("1 marked"), result);
  const out = path.join(work, "three cuts - with transitions.xml");
  assert(fs.existsSync(out), "the new file is next to the original");
  assert(fs.readFileSync(xml).equals(original), "the original is untouched");
  const text = fs.readFileSync(out, "utf8");
  assert(text.includes("<name>PH transition</name><comment>AE.AE_Impact_Push|6|0.5</comment><in>100</in>"), "the left push at 100");
  assert(text.includes("<name>PH Push Up</name>") && text.includes("<in>290</in>"), "the up marker at 290");
  assert(!text.includes("<in>185</in><out>-1</out>"), "nothing at the cut that was cleared");
  console.log("page: opened the XML, three cuts shown, menu at the mouse (Add transition > Film Impact Push > Left/Up/Down/Right), badges, change, remove, save; file written correctly, original untouched");
  console.log("ALL CHECKS PASSED");
})().catch((e) => { console.error("FAILED:", e.message); process.exitCode = 1; }).finally(() => { server.kill(); setTimeout(() => process.exit(process.exitCode || 0), 200); });
