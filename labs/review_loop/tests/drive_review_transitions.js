// Drives the REVIEW PAGE (review_template.html) in a simulated browser (jsdom), the way Ryan uses the Review tab's frame: click a cut on the timeline map, walk the menu
// (Add transition > Film Impact Push), see the badge, change it, remove it, and read the choices back the way the tab does (postMessage review:get-notes).
//   npm i jsdom (once, in any folder; set NODE_PATH to its node_modules)   NODE_PATH=... node labs/review_loop/tests/drive_review_transitions.js
const assert = require("assert");
const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");

const template = fs.readFileSync(path.join(__dirname, "..", "review_template.html"), "utf8");
const data = {
  sequence: "Demo cut", duration: 40, preview: "preview.mp4", clips: [[1, 0, 10], [2, 10, 22], [3, 22, 31], [4, 31.5, 40]].map(([idx, start, end]) => ({ idx, start, end, source: "a.mov", src_in: 0, src_out: end - start })), beatmap: [
    { name: "Clips", kind: "blocks", items: [
      { start: 0, end: 10, label: "clip 1 (a)", clip: 1 }, { start: 10, end: 22, label: "clip 2 (b)", clip: 2 },
      { start: 22, end: 31, label: "clip 3 (c)", clip: 3 }, { start: 31.5, end: 40, label: "clip 4 (d)", clip: 4 } ] },
    { name: "Cuts", kind: "ticks", items: [{ start: 10, end: 10, label: "cut to clip 2 (b)" }, { start: 22, end: 22, label: "cut to clip 3 (c)" }] } ],
};
const html = template.replace("/*__DATA__*/null", JSON.stringify(data).replace(/<\//g, "<\\/"));
const dom = new JSDOM(html, { url: "http://127.0.0.1:47821/review.html", runScripts: "dangerously", pretendToBeVisual: true,
  beforeParse(win) { try { Object.defineProperty(win, "parent", { value: { postMessage: (msg) => win.dispatchEvent(new win.MessageEvent("message", { data: msg })) }, configurable: true }); } catch (e) { throw new Error("cannot stand in for the Review tab: " + e.message); }
    win.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, { get: () => () => ({ width: 0 }), set: () => true }); } });          // no canvas package: the drawing layer is not under test
const w = dom.window, doc = w.document;
const errors = [];
w.addEventListener("error", (e) => errors.push(e.message));
const fire = (el, type, x = 100, y = 100) => el.dispatchEvent(new w.MouseEvent(type, { bubbles: true, clientX: x, clientY: y }));
const asked = () => new Promise((resolve) => {
  const id = "t" + Math.random();
  const h = (e) => { if (e.data && e.data.type === "review:notes" && e.data.id === id) { w.removeEventListener("message", h); resolve(e.data.payload); } };
  w.addEventListener("message", h);
  w.dispatchEvent(new w.MessageEvent("message", { data: { type: "review:get-notes", id } }));
});
// the page tells its parent through window.parent.postMessage; the stand-in parent (see beforeParse) hands every message back to the page's own window so the test can hear it

(async () => {
  assert.deepStrictEqual(errors, [], "the page loads without script errors: " + errors.join("; "));
  const cuts = [...doc.querySelectorAll("#beatmap .cutpt")];
  assert.strictEqual(cuts.length, 2, "two cuts between touching clips; the 0.5 s gap between clips 3 and 4 is not a cut");
  assert(Math.abs(parseFloat(cuts[0].style.left) - 25) < 0.01 && Math.abs(parseFloat(cuts[1].style.left) - 55) < 0.01, cuts.map((c) => c.style.left).join());

  // the green tick on the Cuts lane sits on a cut: it opens the same menu, not a note
  fire(doc.querySelector("#beatmap .lane.Cuts .tk"), "click", 300, 200);
  assert(doc.getElementById("cutmenu"), "a click on the Cuts lane tick opens the transition menu");
  assert(!doc.getElementById("editor") || doc.getElementById("editor").classList.contains("hidden") || w.getComputedStyle(doc.getElementById("editor")).display === "none", "and no note editor opened");
  fire(doc.body, "click");
  assert(!doc.getElementById("cutmenu"), "clicking elsewhere closes it");
  fire(cuts[0], "click", 300, 200);
  const menu = doc.getElementById("cutmenu");
  assert(menu, "menu opens");
  assert.strictEqual(menu.style.left, "306px", "next to the mouse");
  assert(menu.textContent.includes("Add transition") && menu.textContent.includes("Film Impact Push"));
  assert.deepStrictEqual([...menu.querySelectorAll("[data-set]")].map((e) => e.dataset.set), ["left"], "one entry, Film Impact Push, no direction to choose");
  assert(!menu.textContent.includes("Remove transition here"));
  fire(menu.querySelector('[data-set="left"]'), "click");
  assert(!doc.getElementById("cutmenu"), "menu closes after a choice");
  assert(cuts[0].classList.contains("on"));
  assert(doc.querySelector("#beatmap .cutbadge").textContent === "Push");

  fire(cuts[1], "click", 500, 220);
  fire(doc.querySelector('#cutmenu [data-set="left"]'), "click");
  assert(doc.getElementById("transcount").textContent === "2 Film Impact Pushes chosen", doc.getElementById("transcount").textContent);

  // 2026-10-28: the filmstrip is where the eye reads a cut, so it carries the same handles; one opens the same menu, and a chosen Push shows there too
  const fs = [...doc.querySelectorAll("#filmstrip .fscut")];
  assert.strictEqual(fs.length, 2, "the filmstrip has a handle per cut: " + fs.length);
  assert(fs[0].classList.contains("on") && fs[1].classList.contains("on"), "the chosen Pushes show on the filmstrip handles");
  fire(fs[0], "click", 320, 160);
  assert(doc.getElementById("cutmenu") && doc.getElementById("cutmenu").textContent.includes("Add transition"), "a click on a filmstrip handle opens the transition menu");
  fire(doc.body, "click");
  assert(!doc.getElementById("cutmenu"));

  let p = await asked();
  assert.strictEqual(JSON.stringify(p.transitions), JSON.stringify([{ timeline_sec: 10, direction: "left" }, { timeline_sec: 22, direction: "left" }]), "the tab reads the choices with the notes");

  fire(cuts[1], "click", 500, 220);
  fire(doc.querySelector("#cutmenu [data-clear]"), "click");
  p = await asked();
  assert.strictEqual(JSON.stringify(p.transitions), JSON.stringify([{ timeline_sec: 10, direction: "left" }]), "removed one");

  const stored = JSON.parse(w.localStorage.getItem([...Array(w.localStorage.length).keys()].map((i) => w.localStorage.key(i)).find((k) => k.endsWith(":transitions"))));
  assert.strictEqual(JSON.stringify(stored), JSON.stringify([{ timeline_sec: 10, direction: "left" }]), "kept with the version, like notes");
  assert.deepStrictEqual(errors, [], errors.join("; "));
  console.log("review page: cut handles between clips only, on the Clips lane and on the filmstrip, menu at the mouse, badges, change, remove, choices returned with the notes and kept in the page's storage");
  console.log("ALL CHECKS PASSED");
  process.exit(0);
})().catch((e) => { console.error("FAILED:", e.message); process.exit(1); });
