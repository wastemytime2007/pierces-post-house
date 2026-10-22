// Drives the REVIEW PAGE's graphics editor in a simulated browser (jsdom): pick a graphic by what it says (the list) or where it is (click the picture), drag its box, scale it, put it back, and read what travels
// with the notes (`graphic_edits`) the way the Review tab does.
//   npm i jsdom (once, anywhere; set NODE_PATH to its node_modules)   NODE_PATH=... node labs/review_loop/tests/drive_review_graphics.js
const assert = require("assert");
const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");

const template = fs.readFileSync(path.join(__dirname, "..", "review_template.html"), "utf8");
const graphics = [
  { id: "overlay-clipitem-1", name: "Graphic - Overlay.mov", label: "Overlay (navy tint and lines)", track: 2, start: 0, end: 2.6, box: [0, 0, 1, 1], dx: 0, dy: 0, scale: 100, picture: "overlay-clipitem-1.png" },
  { id: "overlay-clipitem-2", name: "Graphic - Title.mov", label: "Title: If it is someone's home never cut corners", track: 3, start: 0, end: 2.6, box: [0.06, 0.4, 0.93, 0.47], dx: 0, dy: 0, scale: 100, picture: "overlay-clipitem-2.png" },
  { id: "overlay-clipitem-3", name: "Graphic - Tag Mitch.mov", label: "Name tag: Mitch · Operator / CEO", track: 4, start: 2.4, end: 6.1, box: [0.04, 0.55, 0.43, 0.64], dx: 0, dy: 0, scale: 100, picture: "overlay-clipitem-3.png",
    fields: { name: "Mitch", sub: "Operator / CEO" } },
  { id: "overlay-clipitem-4", name: "captions.mov", label: "captions", track: 5, start: 0, end: 12, box: [0.06, 0.69, 0.94, 0.81], dx: 0, dy: 0, scale: 100, picture: "overlay-clipitem-4.png", fields: null,
    lines: [{ at: 1.0, text: "This is where someone lives,", pos: "bottom", start: 0.5, end: 1.5 }, { at: 2.0, text: "not just a house.", pos: "bottom", start: 1.6, end: 2.4 }, { at: 3.0, text: "You never cut a corner.", pos: "bottom", start: 2.5, end: 3.5 }] },
];
graphics[1].fields = { small: "If it is someone's home never cut corners" };
const data = {
  sequence: "Demo cut", duration: 12, preview: "preview.mp4", clips: [{ idx: 1, start: 0, end: 12, source: "a.mov", src_in: 0, src_out: 12 }], beatmap: [], graphics, frame: { width: 270, height: 480 },
};
const html = template.replace("/*__DATA__*/null", JSON.stringify(data).replace(/<\//g, "<\\/"));
const dom = new JSDOM(html, {
  url: "http://127.0.0.1:47821/review.html", runScripts: "dangerously", pretendToBeVisual: true,
  beforeParse(win) {
    win.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, { get: () => () => ({ width: 0 }), set: () => true });
    win.Element.prototype.getBoundingClientRect = function () { return { left: 0, top: 0, right: 300, bottom: 500, width: 300, height: 500, x: 0, y: 0 }; };   // a 300 x 500 picture
    try { Object.defineProperty(win, "parent", { value: { postMessage: (msg) => win.dispatchEvent(new win.MessageEvent("message", { data: msg })) }, configurable: true }); } catch (e) { throw new Error("stand-in host: " + e.message); }
  },
});
const w = dom.window, doc = w.document;
const errors = [];
w.addEventListener("error", (e) => errors.push(e.message));
const heard = [];
w.addEventListener("message", (e) => { if (e.data && e.data.type) heard.push(e.data); });
const ptr = (el, type, x, y) => el.dispatchEvent(new w.MouseEvent(type, { bubbles: true, clientX: x, clientY: y }));
const asked = () => new Promise((resolve) => {
  const id = "t" + Math.random();
  const h = (e) => { if (e.data && e.data.type === "review:notes" && e.data.id === id) { w.removeEventListener("message", h); resolve(e.data.payload); } };
  w.addEventListener("message", h);
  w.dispatchEvent(new w.MessageEvent("message", { data: { type: "review:get-notes", id } }));
});

(async () => {
  assert.deepStrictEqual(errors, [], "no script errors: " + errors.join("; "));
  assert.strictEqual(doc.getElementById("gpanel").style.display, "block", "the graphics panel shows when the cut has graphics");
  const rows = [...doc.querySelectorAll("#glist .grow")];
  assert.strictEqual(rows.length, 4);
  assert(rows[1].textContent.includes("Title: If it is someone's home never cut corners"), "picked by what it says: " + rows[1].textContent);
  assert.strictEqual(w.getComputedStyle(doc.getElementById("gbox")).display, "none", "nothing picked yet: no box");

  // pick the title from the list: its box appears where it is
  rows[1].click();
  const box = doc.getElementById("gbox");
  assert.strictEqual(box.style.display, "block");
  assert(Math.abs(parseFloat(box.style.left) - 6) < 0.01 && Math.abs(parseFloat(box.style.top) - 40) < 0.01 && Math.abs(parseFloat(box.style.width) - 87) < 0.01, `box at ${box.style.left} ${box.style.top} ${box.style.width}`);
  assert.strictEqual(doc.getElementById("gctl").style.display, "flex", "the size slider shows once one is picked");
  assert.strictEqual(doc.getElementById("gghost").style.opacity, "0", "unmoved: the picture already shows it");

  // drag it: 30 px across and 50 px down of a 300 x 500 picture = +0.1, +0.1
  ptr(box, "pointerdown", 100, 100);
  ptr(box, "pointermove", 130, 150);
  ptr(box, "pointerup", 130, 150);
  assert(Math.abs(parseFloat(box.style.left) - 16) < 0.01 && Math.abs(parseFloat(box.style.top) - 50) < 0.01, `moved box at ${box.style.left} ${box.style.top}`);
  assert.strictEqual(doc.getElementById("gghost").style.opacity, "0.7", "moved: a ghost of the graphic follows");
  assert(doc.getElementById("gghost").style.transform.includes("translate(10%, 10%)"), doc.getElementById("gghost").style.transform);
  assert(doc.querySelector("#glist .grow.sel .gc").textContent === "changed");
  assert.strictEqual(heard.filter((m) => m.type === "review:graphics").pop().n, 1, "the tab is told one graphic changed");

  // scale with the slider and the wheel
  const slider = doc.getElementById("gscale");
  slider.value = "60";
  slider.dispatchEvent(new w.Event("input", { bubbles: true }));
  assert.strictEqual(doc.getElementById("gscaleout").textContent, "60%");
  const wheel = new w.WheelEvent("wheel", { bubbles: true, cancelable: true, deltaY: -100 });
  box.dispatchEvent(wheel);
  assert.strictEqual(doc.getElementById("gscaleout").textContent, "62%", "the wheel up grows it a little");
  const w62 = (0.93 - 0.06) * 0.62 * 100;
  assert(Math.abs(parseFloat(box.style.width) - w62) < 0.05, `scaled box width ${box.style.width} vs ${w62}`);

  // what travels with the notes
  let p = await asked();
  assert.strictEqual(JSON.stringify(p.graphic_edits), JSON.stringify([{ id: "overlay-clipitem-2", dx: 0.1, dy: 0.1, scale: 62 }]), "the tab reads the change with the notes");

  // pick by where it is: Pick on the picture, then click on the name tag (x 0.2, y 0.6); the full-frame overlay is never picked by a click
  doc.getElementById("gpick").click();
  assert(doc.getElementById("gedit").classList.contains("pick"));
  ptr(doc.getElementById("gedit"), "pointerdown", 0.2 * 300, 0.6 * 500);
  assert(doc.querySelector("#glist .grow.sel").textContent.includes("Name tag: Mitch"), "picked by location: " + doc.querySelector("#glist .grow.sel").textContent);
  ptr(doc.getElementById("gedit"), "pointerdown", 0.5 * 300, 0.05 * 500);               // empty top of the frame: only the overlay is there, and it is not picked by a click
  assert.strictEqual(doc.querySelector("#glist .grow.sel"), null, "a click on nothing picks nothing");

  // put one back, then all
  rows[1].click();
  doc.getElementById("greset").click();
  p = await asked();
  assert.deepStrictEqual(JSON.parse(JSON.stringify(p.graphic_edits)), [], "put back: nothing to apply");
  assert.strictEqual(heard.filter((m) => m.type === "review:graphics").pop().n, 0);

  // kept with the version: a reload of the same version finds the edit again
  rows[2].click();
  ptr(doc.getElementById("gbox"), "pointerdown", 100, 100);
  ptr(doc.getElementById("gbox"), "pointermove", 70, 100);
  ptr(doc.getElementById("gbox"), "pointerup", 70, 100);
  const key = [...Array(w.localStorage.length).keys()].map((i) => w.localStorage.key(i)).find((k) => k.includes(":graphics:"));
  assert.strictEqual(JSON.stringify(JSON.parse(w.localStorage.getItem(key))), JSON.stringify({ "overlay-clipitem-3": { dx: -0.1, dy: 0, scale: 100 } }), "kept in the page's storage with this version");

  // the words: a title's topic line, a name tag's role, and the captions line by line (words and top or bottom)
  rows[1].click();
  let inp = doc.querySelector('#gwords input[data-field="small"]');
  assert(inp && inp.value === "If it is someone's home never cut corners", "the title's words are in a box to edit");
  inp.value = "Never cut corners on someone's home";
  inp.dispatchEvent(new w.Event("input", { bubbles: true }));
  assert(doc.querySelector("#glist .grow.sel .gc").textContent === "changed", "changing only the words counts as a change");
  rows[2].click();
  inp = doc.querySelector('#gwords input[data-field="sub"]');
  inp.value = "Owner and operator";
  inp.dispatchEvent(new w.Event("input", { bubbles: true }));
  rows[3].click();
  const lineBoxes = [...doc.querySelectorAll("#gwords .gl input")];
  assert.strictEqual(lineBoxes.length, 3, "every caption line is listed");
  lineBoxes[0].value = "This is where SOMEONE lives,";
  lineBoxes[0].dispatchEvent(new w.Event("input", { bubbles: true }));
  const sels = [...doc.querySelectorAll("#gwords .gl select")];
  sels[2].value = "top";
  sels[2].dispatchEvent(new w.Event("change", { bubbles: true }));
  p = await asked();
  const byId = Object.fromEntries(p.graphic_edits.map((e) => [e.id, e]));
  assert.deepStrictEqual(JSON.parse(JSON.stringify(byId["overlay-clipitem-2"].text)), { small: "Never cut corners on someone's home" }, "only the changed field travels");
  assert.deepStrictEqual(JSON.parse(JSON.stringify(byId["overlay-clipitem-3"].text)), { sub: "Owner and operator" });
  assert.deepStrictEqual(JSON.parse(JSON.stringify(byId["overlay-clipitem-4"].lines)), [{ at: 1.0, text: "This is where SOMEONE lives,", pos: "bottom" }, { at: 3.0, text: "You never cut a corner.", pos: "top" }], "only the changed lines travel, with their moment");
  assert(!("lines" in byId["overlay-clipitem-2"]) && !("text" in byId["overlay-clipitem-4"]));
  // typing the original words back is not a change
  rows[1].click();
  inp = doc.querySelector('#gwords input[data-field="small"]');
  inp.value = "If it is someone's home never cut corners";
  inp.dispatchEvent(new w.Event("input", { bubbles: true }));
  p = await asked();
  assert(!p.graphic_edits.some((e) => e.id === "overlay-clipitem-2"), "the same words again: no change");
  // put them all back
  doc.getElementById("gresetall").click();
  p = await asked();
  assert.deepStrictEqual(JSON.parse(JSON.stringify(p.graphic_edits)), []);
  // the earlier storage check follows from a clean state
  rows[2].click();
  ptr(doc.getElementById("gbox"), "pointerdown", 100, 100);
  ptr(doc.getElementById("gbox"), "pointermove", 70, 100);
  ptr(doc.getElementById("gbox"), "pointerup", 70, 100);
  assert.deepStrictEqual(errors, [], errors.join("; "));
  console.log("review page graphics: list by content, pick by location, drag, wheel and slider scale, ghost, put back, edits travel with the notes and are kept with the version");
  console.log("ALL CHECKS PASSED");
  process.exit(0);
})().catch((e) => { console.error("FAILED:", e.message); process.exit(1); });
