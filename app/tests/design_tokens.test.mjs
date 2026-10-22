// Contrast of the design tokens, computed from src/styles.css (docs/design/UI_SYSTEM.md has the reasoning).
// Run: node --test tests/design_tokens.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
const root = css.match(/:root \{([\s\S]*?)\n\}/)[1];
const light = css.match(/@media \(prefers-color-scheme: light\) \{\s*:root \{([\s\S]*?)\n  \}/)[1];
const vars = (block) => Object.fromEntries([...block.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6})/g)].map((m) => [m[1], m[2]]));
const DARK = vars(root);
const LIGHT = { ...DARK, ...vars(light) };

const lin = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
const lum = (h) => { const [r, g, b] = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)); return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b); };
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

// text on the surfaces it appears on; the tertiary text is never on the hover surface (see the stylesheet's comment)
const TEXT = [
  ["fg-0", ["bg-0", "bg-1", "bg-2", "bg-3"]],
  ["fg-1", ["bg-0", "bg-1", "bg-2", "bg-3"]],
  ["fg-2", ["bg-0", "bg-1", "bg-2"]],
  ["fg-3", ["bg-0", "bg-1", "bg-2"]],
  ["accent", ["bg-0", "bg-1", "bg-2"]],
  ["warn", ["bg-0", "bg-1", "bg-2"]],
  ["success", ["bg-0", "bg-1", "bg-2"]],
  ["error", ["bg-0", "bg-1", "bg-2"]],
];

for (const [name, T] of [["dark", DARK], ["light", LIGHT]]) {
  test(`${name}: text reaches 4.5:1 on the surfaces it is used on`, () => {
    const fails = [];
    for (const [fg, surfaces] of TEXT) for (const s of surfaces) { const r = ratio(T[fg], T[s]); if (r < 4.5) fails.push(`${fg} ${T[fg]} on ${s} ${T[s]} = ${r.toFixed(2)}`); }
    assert.deepEqual(fails, []);
  });
  test(`${name}: white on the primary button fill reaches 4.5:1, and the pressed fill too`, () => {
    assert.ok(ratio(T["on-accent"], T["accent-fill"]) >= 4.5, `fill ${ratio(T["on-accent"], T["accent-fill"]).toFixed(2)}`);
    assert.ok(ratio(T["on-accent"], T["accent-fill-pressed"]) >= 4.5);
  });
  test(`${name}: the focus ring (the accent) reaches 3:1 against the window and the sidebar`, () => {
    for (const s of ["bg-0", "bg-1"]) assert.ok(ratio(T.accent, T[s]) >= 3, `${s} ${ratio(T.accent, T[s]).toFixed(2)}`);
  });
}

test("no text smaller than 11 px is set anywhere in the stylesheet", () => {
  const small = [...css.matchAll(/font-size:\s*(\d+(?:\.\d+)?)px/g)].filter((m) => parseFloat(m[1]) < 11).map((m) => m[0]);
  assert.deepEqual(small, []);
});
