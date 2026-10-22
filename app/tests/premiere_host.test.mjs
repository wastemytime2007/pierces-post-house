// The Premiere-side half of the link: host.jsx's job inbox, run against a stand-in for Premiere's File, Folder and app objects.
// Proves the logic (what it accepts, what it refuses, what it answers, that it never evaluates the job). It cannot prove Premiere accepts newProject/importFiles/save: that needs a run in Premiere.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HOST = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "premiere_extension", "jsx", "host.jsx");
const JOBS = "/Support/Post House/premiere_jobs";

function world({ newProject = () => true, importFiles = () => true, save = () => {}, existing = {} } = {}) {
  const files = new Map(Object.entries(existing));                 // path -> text
  const calls = [];
  class File {
    constructor(p) { this.path = p; }
    get exists() { return files.has(this.path); }
    get name() { return this.path.split("/").pop(); }
    get fsName() { return this.path; }
    open(mode) { this.mode = mode; this.buf = mode === "w" ? "" : null; if (mode === "r" && !files.has(this.path)) return false; if (mode === "a" && !files.has(this.path)) files.set(this.path, ""); return true; }
    write(t) { this.buf = (this.buf ?? "") + t; }
    writeln(t) { files.set(this.path, (files.get(this.path) ?? "") + t + "\n"); }
    read() { return files.get(this.path) ?? ""; }
    close() { if (this.mode === "w") files.set(this.path, this.buf); }
    rename(n) { const to = this.path.replace(/[^/]*$/, n); if (!files.has(this.path)) return false; files.set(to, files.get(this.path)); files.delete(this.path); this.path = to; return true; }
    remove() { files.delete(this.path); return true; }
  }
  class Folder {
    constructor(p) { this.path = p; }
    get exists() { return true; }
    create() { return true; }
    getFiles(pattern) {
      const ext = pattern.replace("*", "");
      return [...files.keys()].filter((k) => k.startsWith(this.path + "/") && k.endsWith(ext)).map((k) => new File(k));
    }
  }
  Folder.userData = { fsName: "/Support" };
  const app = {
    version: "26.0",
    project: { rootItem: {}, importFiles: (...a) => { calls.push(["importFiles", ...a]); return importFiles(...a); }, save: () => { calls.push(["save"]); save(); } },
    newProject: (p) => { calls.push(["newProject", p]); return newProject(p); },
  };
  const ctx = vm.createContext({ File, Folder, app, JSON, Date, decodeURI, console });
  vm.runInContext(fs.readFileSync(HOST, "utf8"), ctx);
  return { ctx, files, calls };
}

const job = (xml = "/x/Story.xml", project = "/x/Story.prproj") => `xml=${xml}\nproject=${project}\n`;

test("a job makes the named project, imports the XML into it, saves, and answers ok", () => {
  const w = world({ existing: { "/x/Story.xml": "<xmeml/>", [`${JOBS}/a1.job`]: job() } });
  assert.equal(w.ctx.postHouseTick(), 1);
  assert.deepEqual(w.calls.map((c) => c[0]), ["newProject", "importFiles", "save"]);
  assert.equal(w.calls[0][1], "/x/Story.prproj");
  assert.deepEqual([...w.calls[1][1]], ["/x/Story.xml"]);
  assert.match(w.files.get(`${JOBS}/a1.result`), /^ok=1\nproject=\/x\/Story\.prproj/);
  assert.ok(!w.files.has(`${JOBS}/a1.job`) && !w.files.has(`${JOBS}/a1.running`));
});

test("it keeps a heartbeat the app can read (version 2, a time, the Premiere version)", () => {
  const w = world();
  w.ctx.postHouseTick();
  assert.match(w.files.get(`${JOBS}/link.txt`), /^v=2\nt=\d+\napp=26\.0\n$/);
});

test("it refuses to overwrite a project that is already there", () => {
  const w = world({ existing: { "/x/Story.xml": "<xmeml/>", "/x/Story.prproj": "old", [`${JOBS}/b.job`]: job() } });
  w.ctx.postHouseTick();
  assert.equal(w.calls.length, 0);
  assert.match(w.files.get(`${JOBS}/b.result`), /^error=.*already exists/);
});

test("it refuses a job that is not an XML into a .prproj, or whose XML is missing", () => {
  for (const [name, text, why] of [
    ["c", job("/x/Story.txt", "/x/S.prproj"), /no XML/],
    ["d", job("/x/Story.xml", "/x/S.txt"), /\.prproj/],
    ["e", job("/x/Missing.xml", "/x/S.prproj"), /not there/],
  ]) {
    const w = world({ existing: { "/x/Story.txt": "x", "/x/Story.xml": "<xmeml/>", [`${JOBS}/${name}.job`]: text } });
    w.ctx.postHouseTick();
    assert.equal(w.calls.length, 0, name);
    assert.match(w.files.get(`${JOBS}/${name}.result`), why, name);
  }
});

test("a failure at each step is answered with what failed, and it keeps going", () => {
  const cases = [
    [{ newProject: () => { throw new Error("no way"); } }, /would not make the project.*no way/],
    [{ importFiles: () => false }, /did not import/],
    [{ save: () => { throw new Error("disk"); } }, /would not save.*disk/],
  ];
  for (const [opts, why] of cases) {
    const w = world({ ...opts, existing: { "/x/Story.xml": "<xmeml/>", [`${JOBS}/f.job`]: job() } });
    assert.equal(w.ctx.postHouseTick(), 1);
    assert.match(w.files.get(`${JOBS}/f.result`), why);
  }
});

test("a job is never evaluated: code in its text does nothing", () => {
  const w = world({ existing: { "/x/Story.xml": "<xmeml/>", [`${JOBS}/g.job`]: job() + "x=app.project.rootItem.evil()\n" + "evil=1;throw 1\n" } });
  assert.doesNotThrow(() => w.ctx.postHouseTick());
  assert.deepEqual(w.calls.map((c) => c[0]), ["newProject", "importFiles", "save"]);
});

test("a job another tick already took is not run twice", () => {
  const w = world({ existing: { "/x/Story.xml": "<xmeml/>", [`${JOBS}/h.job`]: job() } });
  w.ctx.postHouseTick();
  assert.equal(w.ctx.postHouseTick(), 0);
  assert.equal(w.calls.filter((c) => c[0] === "newProject").length, 1);
});
