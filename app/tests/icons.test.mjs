// Every icon the app asks for by name must exist, and every class the app styles must be styled.
// Found this way on 2026-10-22: the "app has updated, restart it" bar had been shipping with no icon and no styling at all,
// because the script that was meant to add them stopped at an earlier error and the build still passed.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.join(HERE, "..", "src");
const read = (p) => fs.readFileSync(p, "utf8");

function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    return e.isDirectory() ? walk(full) : /\.(jsx|js)$/.test(e.name) ? [full] : [];
  });
}

const FILES = walk(SRC);
const ALL = FILES.map(read).join("\n");
const ICONS = read(path.join(SRC, "components", "Icon.jsx"));

test("every icon asked for by name exists in the set", () => {
  const defined = new Set([...ICONS.matchAll(/^\s{2}([a-zA-Z0-9]+):\s/gm)].map((m) => m[1]));
  const asked = [...ALL.matchAll(/<Icon\s+name="([a-zA-Z0-9]+)"/g)].map((m) => m[1]);
  const missing = [...new Set(asked.filter((n) => !defined.has(n)))];
  assert.deepEqual(missing, [], `asked for but not in the icon set: ${missing.join(", ")}`);
  assert.ok(asked.length > 20, "the app does use icons, so this test is actually looking at something");
});

test("the icon set has no repeated name, so one name cannot mean two drawings", () => {
  const names = [...ICONS.matchAll(/^\s{2}([a-zA-Z0-9]+):\s/gm)].map((m) => m[1]);
  const seen = new Set();
  const twice = names.filter((n) => (seen.has(n) ? true : (seen.add(n), false)));
  assert.deepEqual(twice, [], `defined more than once: ${twice.join(", ")}`);
});

test("the classes this app invents for itself are all styled", () => {
  // Only the app's own prefixes, so classes that come from elsewhere are not failed for being elsewhere.
  const CSS = read(path.join(SRC, "styles.css"));
  const MINE = /^(ph-|stale-|set-|sc-|trim-|rv-|sp-|cp-|cover-|frame-pick|board-|export-open|idea-|page-tip|switch)/;
  const used = new Set();
  for (const m of ALL.matchAll(/className=\{?["`]([^"`{}]+)["`]/g)) {
    for (const cls of m[1].split(/\s+/)) if (cls && MINE.test(cls)) used.add(cls);
  }
  const unstyled = [...used].filter((c) => !new RegExp(`\\.${c}[\\s,:.{>~+[]`).test(CSS));
  assert.deepEqual(unstyled, [], `used in the app but never styled: ${unstyled.join(", ")}`);
});
