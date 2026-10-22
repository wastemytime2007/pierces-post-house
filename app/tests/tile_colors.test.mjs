// The project cover's fallback color must never throw: a crash here blanks the whole home screen (it did, on Ryan's real project names, 2026-10-21).
// Run: node --test tests/tile_colors.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { tileColors } from "../src/tileColors.js";

const NAMES = ["Runnells Weekend", "Runnells Wall Removal", "Test2", "Runnells Hanging Mirror", "new", "Runnells Kitchen and Doors - 3 Reels",
  "Windows - planned to keep them, teardown changed our minds", "How to decide if you should keep-replace windows", "1546 Arthur_How to take over property", "How to remove wallpaper",
  "Runnells Tiling", "Mitch Interview", "Runnells House", "Test Project_Post House", "Test", "", "é", "日本語のプロジェクト"];

test("every name, including empty and non-Latin ones, gets two valid rgb colors", () => {
  for (const n of NAMES) {
    const c = tileColors(n);
    assert.equal(c.length, 2, n);
    for (const x of c) assert.match(x, /^rgb\(\d{1,3}, \d{1,3}, \d{1,3}\)$/, `${n}: ${x}`);
  }
});

test("a thousand generated names never throw", () => {
  for (let i = 0; i < 1000; i++) {
    const n = Array.from({ length: 1 + (i % 40) }, (_, j) => String.fromCharCode(32 + ((i * 7 + j * 13) % 1000))).join("");
    assert.doesNotThrow(() => tileColors(n));
  }
});

test("the same name always gives the same colors", () => {
  assert.deepEqual(tileColors("Runnells Weekend"), tileColors("Runnells Weekend"));
});
