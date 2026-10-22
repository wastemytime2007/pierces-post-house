// A shortcut list that promises a key the app does not listen for is worse than no list.
// Every key in the sheet is checked against the code that would have to handle it: the native menu (src-tauri/src/main.rs),
// the window key handlers, or the review page's own transport.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const read = (p) => fs.readFileSync(path.join(HERE, "..", p), "utf8");

// Node cannot import .jsx, and this list is plain data, so it is read out of the component as text.
const SHEET = read("src/components/ShortcutsSheet.jsx");
const GROUPS = (() => {
  const start = SHEET.indexOf("export const GROUPS = ");
  const open = SHEET.indexOf("[", start);
  let depth = 0, i = open;
  for (; i < SHEET.length; i += 1) {
    if (SHEET[i] === "[") depth += 1;
    else if (SHEET[i] === "]") { depth -= 1; if (!depth) break; }
  }
  // eslint-disable-next-line no-new-func
  return Function(`return ${SHEET.slice(open, i + 1)}`)();
})();

const SOURCES = [
  read("src-tauri/src/main.rs"),
  read("src/App.jsx"),
  read("src/screens/tabs/ReviewTab.jsx"),
  read("src/screens/tabs/TrimTab.jsx"),
  read("src/components/CommandPalette.jsx"),
  read("../labs/review_loop/review_template.html"),
].join("\n");

// How each key would appear in the code that handles it. A key with no entry here has to be added deliberately,
// which is the point: the list cannot grow without someone saying where it is handled.
const EVIDENCE = {
  "⌘K": [/CmdOrCtrl\+K/, /"k"/], "⌘,": [/CmdOrCtrl\+,/], "⌘Z": [/CmdOrCtrl\+Z/, /"z"/], "⇧⌘Z": [/CmdOrCtrl\+Shift\+Z/],
  "?": [/"\?"/], "⌘⌥L": [/CmdOrCtrl\+Alt\+L/],
  "⌘N": [/CmdOrCtrl\+N/], "⇧⌘O": [/CmdOrCtrl\+Shift\+O/],
  "⌘1": [/CmdOrCtrl\+1/], "⌘2": [/CmdOrCtrl\+2/], "⌘3": [/CmdOrCtrl\+3/], "⌘4": [/CmdOrCtrl\+4/],
  "⌘O": [/CmdOrCtrl\+O/], "⌘G": [/CmdOrCtrl\+G/], "⌘E": [/CmdOrCtrl\+E/],
  "⌘[": [/CmdOrCtrl\+\[/], "⌘]": [/CmdOrCtrl\+\]/],
  "⌘⌥S": [/CmdOrCtrl\+Alt\+S/], "⌘⌥B": [/CmdOrCtrl\+Alt\+B/], "⇧⌘R": [/CmdOrCtrl\+Shift\+R/],
  "← → ↑ ↓": [/ArrowRight/], "Space": [/" "/, /=== ' '/], "Shift-click": [/shiftKey/], "⌘↵": [/Enter/], "Esc": [/Escape/],
  "L": [/'l'/, /"l"/], "K": [/'k'/, /"k"/], "J": [/'j'/, /"j"/],
  ", .": [/'\.'/, /"\."/], "I  O": [/'i'/, /"i"/], "Home  End": [/'Home'/, /"Home"/],
  n: [/'n'/, /"n"/], d: [/'d'/, /"d"/],
};

test("every key the sheet lists is handled somewhere in the app", () => {
  const unhandled = [];
  for (const g of GROUPS) {
    for (const [key, label] of g.rows) {
      const pats = EVIDENCE[key];
      if (!pats) { unhandled.push(`${key} (${g.title}: ${label}) has no known handler pattern`); continue; }
      if (!pats.some((re) => re.test(SOURCES))) unhandled.push(`${key} (${g.title}: ${label}) is listed but nothing listens for it`);
    }
  }
  assert.deepEqual(unhandled, [], unhandled.join("\n"));
});

test("the sheet has no duplicate rows within a group, and every row has both a key and a name", () => {
  for (const g of GROUPS) {
    const labels = g.rows.map(([, l]) => l);
    assert.equal(new Set(labels).size, labels.length, `${g.title} repeats a row`);
    for (const [k, l] of g.rows) {
      assert.ok(k && k.trim(), `${g.title} has a row with no key`);
      assert.ok(l && l.trim(), `${g.title} has a key with no name`);
    }
  }
});

test("the four steps are all there, because they are the app's main navigation", () => {
  const steps = GROUPS.find((g) => g.title === "Steps");
  assert.deepEqual(steps.rows.map(([, l]) => l), ["Footage", "Stories", "Trim", "Finish"]);
});
