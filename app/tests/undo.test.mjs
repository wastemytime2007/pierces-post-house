// Undo covers the choices a person makes, so a wrong click costs nothing (HIG undo-and-redo.md).
import test from "node:test";
import assert from "node:assert/strict";
import { record, undo, redo, canUndo, canRedo, nextUndoLabel, clearUndo } from "../src/undo.js";

test.beforeEach(() => clearUndo());

test("a recorded change goes back and comes forward again", () => {
  let v = "a";
  record("choosing that story", () => { v = "a"; }, () => { v = "b"; });
  v = "b";
  assert.equal(undo(), "choosing that story");
  assert.equal(v, "a");
  assert.equal(redo(), "choosing that story");
  assert.equal(v, "b");
});

test("nothing to undo is not an error, it is nothing", () => {
  assert.equal(canUndo(), false);
  assert.equal(undo(), null);
  assert.equal(redo(), null);
});

test("a new change drops what was ahead, so redo cannot jump a branch", () => {
  const seen = [];
  record("one", () => seen.push("undo one"), () => seen.push("redo one"));
  undo();
  assert.equal(canRedo(), true);
  record("two", () => seen.push("undo two"), () => seen.push("redo two"));
  assert.equal(canRedo(), false);
  assert.equal(nextUndoLabel(), "two");
});

test("the history is bounded, so a long session cannot grow without end", () => {
  for (let i = 0; i < 60; i += 1) record(`change ${i}`, () => {}, () => {});
  let n = 0;
  while (undo()) n += 1;
  assert.equal(n, 40);
});

test("leaving a project starts a fresh history", () => {
  record("one", () => {}, () => {});
  clearUndo();
  assert.equal(canUndo(), false);
  assert.equal(canRedo(), false);
});
