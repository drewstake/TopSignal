const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { classify, jsxShape } = require("./dashboard-eligibility.cjs");

test("chart paint slots accept literal colors and preserve executable expressions", () => {
  assert.equal(jsxShape('<path stroke="#fff"/>'), jsxShape('<path stroke="#abc123"/>'));
  assert.notEqual(jsxShape('<path stroke="#fff"/>'), jsxShape('<path stroke={getPrice()}/>'));
});

test("classify accumulated display changes against BOTH serving revisions", () => {
  const original = process.cwd();
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ts-classify-"));
  const metric = "frontend/src/components/metrics/GaugeBar.tsx";
  const css = "frontend/src/styles/workspace.css";
  function write(name, content) {
    fs.mkdirSync(path.dirname(name), { recursive: true });
    fs.writeFileSync(name, content);
  }
  function git(...args) { return execFileSync("git", args, { encoding: "utf8" }).trim(); }
  function commit() {
    git("add", ".");
    git("-c","user.name=Fixture","-c","user.email=fixture@example.invalid","commit","-qm","candidate");
    return git("rev-parse","HEAD");
  }
  try {
    process.chdir(root);
    git("init","-q");
    write(metric, 'export const gauge = <span className="p-1">Balance</span>');
    write(css, ".chart { gap: 5px; }");
    const base = commit();
    write(metric, 'export const gauge = <span className="p-2">Net balance</span>');
    const text = commit();
    write(css, ".chart { gap: 8px; }");
    const layout = commit();
    assert.equal(classify(base, text, layout).lane, "dashboard");
    write(metric, 'export const gauge = <span onClick={routeOrder}>Net balance</span>');
    const mixed = commit();
    assert.equal(classify(base, layout, mixed).lane, "full");
    assert.equal(classify(base, mixed, layout).lane, "full");
    const recorder = "frontend/src/pages/recorders/CaptureHealth.tsx";
    write(recorder, 'poll("/api/health"); const view = <dt>Last saved recording</dt>;');
    const recorderBase = commit();
    write(recorder, 'poll("/api/health"); const view = <dt>Last saved capture</dt>;');
    const label = commit();
    assert.equal(classify(recorderBase, recorderBase, label).lane, "dashboard");
    write(recorder, 'poll("/api/orders"); const view = <dt>Last saved capture</dt>;');
    assert.equal(classify(recorderBase, label, commit()).lane, "full");
    write("frontend/src/lib/api.ts", "export const base='/orders';");
    assert.equal(classify(base, layout, commit()).lane, "full");
  } finally {
    process.chdir(original);
    fs.rmSync(root, { recursive: true, force: true });
  }
});
