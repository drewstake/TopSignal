const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { test } = require("node:test");
const { startBothProfiles } = require("./dev-both.cjs");

function harness() {
  const launches = [];
  const stopped = [];
  const exits = [];
  const profiles = startBothProfiles({
    spawnProcess(command, args, options) {
      const child = new EventEmitter();
      launches.push({ command, args, options, child });
      return child;
    },
    stopProcess: (child) => stopped.push(child),
    onExit: (code) => exits.push(code),
    write() {},
  });
  return { launches, stopped, exits, profiles };
}

test("starts connected local first, then cloud only after the local backend has bound its port", () => {
  const h = harness();
  assert.equal(h.launches.length, 1);
  assert.deepEqual(h.launches[0].args.slice(1), ["--offline", "--topstep"]);
  h.launches[0].child.emit("message", { type: "unrelated" });
  assert.equal(h.launches.length, 1);
  h.launches[0].child.emit("message", { type: "topsignal-dev-ready", backendPort: 8000 });
  assert.equal(h.launches.length, 2);
  assert.deepEqual(h.launches[1].args.slice(1), []);
  assert.notEqual(h.launches[0].options.env, h.launches[1].options.env);
  assert.deepEqual(h.launches[0].options.env, h.launches[1].options.env);
  h.launches[0].child.emit("message", { type: "topsignal-dev-ready" });
  assert.equal(h.launches.length, 2);
});

test("a cloud failure keeps the connected local workspace running", () => {
  const h = harness();
  h.launches[0].child.emit("message", { type: "topsignal-dev-ready" });
  h.launches[1].child.emit("exit", 1, null);
  assert.deepEqual(h.stopped, []);
  assert.deepEqual(h.exits, []);
  h.launches[0].child.emit("exit", 0, null);
  assert.deepEqual(h.exits, [1]);
});

test("cloud can still start when local startup fails before readiness", () => {
  const h = harness();
  h.launches[0].child.emit("exit", 1, null);
  assert.equal(h.launches.length, 2);
  assert.deepEqual(h.exits, []);
  h.launches[1].child.emit("exit", 0, null);
  assert.deepEqual(h.exits, [1]);
});

test("shutdown stops both supervisors and never restarts a stopped profile", () => {
  const h = harness();
  h.launches[0].child.emit("message", { type: "topsignal-dev-ready" });
  h.profiles.stop();
  h.profiles.stop();
  assert.deepEqual(h.stopped, h.launches.map(({ child }) => child));
  h.launches[0].child.emit("exit", null, "SIGTERM");
  h.launches[1].child.emit("exit", null, "SIGTERM");
  assert.deepEqual(h.exits, [0]);
  assert.equal(h.launches.length, 2);
});

test("shutdown during startup prevents a late readiness event from launching cloud", () => {
  const h = harness();
  h.profiles.stop();
  h.launches[0].child.emit("message", { type: "topsignal-dev-ready" });
  h.launches[0].child.emit("exit", null, "SIGTERM");
  assert.equal(h.launches.length, 1);
  assert.deepEqual(h.exits, [0]);
});

test("a child spawn error is handled once even if an exit event follows", () => {
  const h = harness();
  h.launches[0].child.emit("error", new Error("spawn failed"));
  h.launches[0].child.emit("exit", 1, null);
  assert.equal(h.launches.length, 2);
  h.launches[1].child.emit("exit", 1, null);
  assert.deepEqual(h.exits, [1]);
});
