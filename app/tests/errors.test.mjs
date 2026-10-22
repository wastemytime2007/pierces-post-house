// An error should say what happened and what to do, and never lose the raw text (HIG alerts.md, writing.md).
import test from "node:test";
import assert from "node:assert/strict";
import { explain, explainLine } from "../src/errors.js";

test("a stale backend tells the person to restart, not what the command was called", () => {
  const e = explain("This app needs restarting to finish updating.");
  assert.match(e.what, /has updated/i);
  assert.match(e.next, /open it again/i);
  assert.doesNotMatch(explainLine("This app needs restarting to finish updating."), /command|open_in_premiere_project/i);
});

test("a missing file says it may have moved, and what to do", () => {
  const e = explain("that XML is not there: /Volumes/RDOSS_2025/gone.xml");
  assert.match(e.what, /not where it was/i);
  assert.match(e.next, /choose it again/i);
});

test("a full disk and a locked drive each get their own next step", () => {
  assert.match(explain("OSError: [Errno 28] No space left on device").next, /free some space/i);
  assert.match(explain("PermissionError: [Errno 13] Permission denied: '/Volumes/x'").next, /connected|locked/i);
});

test("the raw text is always kept, even when it is understood", () => {
  const raw = "PermissionError: [Errno 13] Permission denied: '/Volumes/x'";
  assert.equal(explain(raw).raw, raw);
});

test("something unrecognised keeps its own words and invents no advice", () => {
  const e = explain("Traceback (most recent call last):\n  File \"x.py\", line 1\nValueError: odd shape 7");
  assert.equal(e.what, "ValueError: odd shape 7");
  assert.equal(e.next, "");
});

test("a very long single line is cut rather than filling the screen", () => {
  const e = explain("x".repeat(400));
  assert.ok(e.what.length <= 180);
  assert.match(e.what, /…$/);
  assert.equal(e.raw.length, 400);
});

test("nothing at all is still a sentence", () => {
  assert.equal(explain("").what, "Something went wrong.");
  assert.equal(explain(null).what, "Something went wrong.");
});
