// Every colour, size and shape the stylesheet asks for must actually exist.
// A `var(--thing)` that was never defined silently falls back to nothing, or to a hard-coded hex that does not follow light and dark.
// Found this way on 2026-10-22: --attention and --good were never defined, so a green dot and an orange icon were pinned to one colour in both appearances.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const CSS = fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src", "styles.css"), "utf8");

// --i and --n are handed in from JSX (`style={{ "--i": index }}`) to stagger an entrance, so they are defined at the element, not here.
const FROM_JSX = ["--i", "--n"];
const defined = new Set([...[...CSS.matchAll(/(--[a-z0-9-]+)\s*:/gi)].map((m) => m[1]), ...FROM_JSX]);
const used = [...CSS.matchAll(/var\(\s*(--[a-z0-9-]+)\s*([,)])/gi)].map((m) => ({ name: m[1], hasFallback: m[2] === "," }));

test("no variable is used without being defined somewhere", () => {
  const missing = [...new Set(used.filter((u) => !defined.has(u.name)).map((u) => u.name))];
  assert.deepEqual(missing, [], `used but never defined: ${missing.join(", ")}`);
});

test("a variable that exists is not shadowed by a hard-coded fallback colour", () => {
  // `var(--success, #30a46c)` is a smell: the token exists and follows the appearance, the hex does not.
  const shadowed = [...new Set(used.filter((u) => u.hasFallback && defined.has(u.name)).map((u) => u.name))]
    .filter((n) => new RegExp(`var\\(\\s*${n}\\s*,\\s*#`, "i").test(CSS));
  assert.deepEqual(shadowed, [], `defined, but still given a hard-coded fallback: ${shadowed.join(", ")}`);
});

test("both appearances define the same set of tokens", () => {
  // The dark set is at :root; the light set is inside the prefers-color-scheme block. One missing from either is a colour that does not change.
  const lightBlock = CSS.match(/prefers-color-scheme:\s*light[^{]*\{([\s\S]*?)\n  \}/);
  assert.ok(lightBlock, "a light appearance block exists");
  const light = new Set([...lightBlock[1].matchAll(/(--[a-z0-9-]+)\s*:/gi)].map((m) => m[1]));
  // only the tokens that are colours need an answer in both; sizes and shapes are shared
  const colourish = /(bg|fg|accent|warn|success|separator|material|fill|shadow|on-accent)/i;
  const rootBlock = CSS.match(/:root\s*\{([\s\S]*?)\n\}/);
  const root = new Set([...rootBlock[1].matchAll(/(--[a-z0-9-]+)\s*:/gi)].map((m) => m[1]));
  // A token that is deliberately the same in both is allowed, but it has to be a plain white or black:
  // anything else that does not change is a colour someone forgot to answer for the other appearance.
  const valueOf = (n) => (rootBlock[1].match(new RegExp(`${n}\\s*:\\s*([^;]+);`)) || [])[1]?.trim();
  const constant = (v) => /^#(fff|ffffff|000|000000)$/i.test(v || "");
  const missingInLight = [...root].filter((n) => colourish.test(n) && !light.has(n) && !constant(valueOf(n)));
  assert.deepEqual(missingInLight, [], `colour tokens with no light answer: ${missingInLight.join(", ")}`);
});
