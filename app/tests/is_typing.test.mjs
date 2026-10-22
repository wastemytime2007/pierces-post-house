// Which element owns a bare keystroke. Only somewhere a person is typing does: a text field, a text area, editable content.
// Found on 2026-10-22: Command-Z did nothing right after ticking a story, because the handler skipped every INPUT, and a
// ticked checkbox still has the focus. That is the commonest moment to want undo.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Read the function out of App.jsx (Node cannot import .jsx) and run it on plain objects shaped like elements.
const SRC = fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src", "App.jsx"), "utf8");
const body = SRC.slice(SRC.indexOf("export function isTyping(el) {"), SRC.indexOf("export async function sendCommand"));
// eslint-disable-next-line no-new-func
const isTyping = Function(`${body.replace("export function", "function")}; return isTyping;`)();

const el = (tag, type) => ({ tagName: tag, type, isContentEditable: false });

test("a text field, a search field and a text area are typing", () => {
  for (const t of [undefined, "text", "search", "password", "email", "number", "url"]) {
    assert.equal(isTyping(el("INPUT", t)), true, `input type=${t}`);
  }
  assert.equal(isTyping(el("TEXTAREA")), true);
  assert.equal(isTyping({ tagName: "DIV", isContentEditable: true }), true);
});

test("a checkbox, a radio and a button are not typing, so a bare key still reaches the app", () => {
  for (const t of ["checkbox", "radio", "button", "submit", "reset", "range", "color", "file"]) {
    assert.equal(isTyping(el("INPUT", t)), false, `input type=${t}`);
  }
});

test("an ordinary element, and nothing at all, are not typing", () => {
  assert.equal(isTyping(el("DIV")), false);
  assert.equal(isTyping(el("BUTTON")), false);
  assert.equal(isTyping(el("SELECT")), false);
  assert.equal(isTyping(null), false);
  assert.equal(isTyping(undefined), false);
});

test("the type is read whatever its case", () => {
  assert.equal(isTyping(el("INPUT", "CHECKBOX")), false);
  assert.equal(isTyping(el("INPUT", "Text")), true);
});
