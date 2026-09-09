const test = require("node:test");
const assert = require("node:assert/strict");
const net = require("node:net");
const { spawn } = require("node:child_process");
const { EventEmitter, once } = require("node:events");
const { startReadyBackend } = require("./dev-startup.cjs");
const { findAvailablePort } = require("./dev-utils.cjs");

test("a port taken after selection is retried and readiness belongs to the replacement backend", async () => {
  const competitor = net.createServer(socket => socket.destroy());
  competitor.listen(0, "127.0.0.1");
  await once(competitor, "listening");
  const preferred = competitor.address().port;
  const children = [];
  const selections = [];
  const messages = [];
  const instanceId = "replacement-backend";
  try {
    const port = await startReadyBackend({
      async selectPort(excludedPorts) {
        selections.push([...excludedPorts]);
        // Simulate a probe returning just before another process takes the port.
        if (selections.length === 1) return preferred;
        return findAvailablePort(preferred, { excludedPorts });
      },
      start(port) {
        const child = spawn(process.execPath, ["-e", `
          require('node:http').createServer((req, res) => {
            res.setHeader('X-TopSignal-Dev-Instance', ${JSON.stringify(instanceId)});
            res.end('ready');
          }).listen(${port}, '127.0.0.1');
        `], { stdio: "ignore", windowsHide: true });
        children.push(child);
        return child;
      },
      instanceId,
      write: message => messages.push(message),
    });
    assert.notEqual(port, preferred);
    assert.deepEqual(selections, [[], [preferred]]);
    assert.equal(children[0].exitCode, 1);
    assert.equal(children[1].exitCode, null);
    assert.match(messages.join("\n"), /Retrying on a new port \(1\/5\)/);
    const response = await fetch(`http://127.0.0.1:${port}/ready`);
    assert.equal(response.headers.get("x-topsignal-dev-instance"), instanceId);
  } finally {
    for (const child of children) {
      if (child.exitCode === null && child.signalCode === null) {
        const stopped = once(child, "exit");
        child.kill();
        await stopped;
      }
    }
    await new Promise(resolve => competitor.close(resolve));
  }
});

test("failed startups have bounded retries and cancel every obsolete readiness poll", async () => {
  const signals = [];
  let launches = 0;
  await assert.rejects(startReadyBackend({
    selectPort: async failed => 8000 + failed.length,
    start() {
      launches += 1;
      const child = new EventEmitter();
      setImmediate(() => child.emit("exit", 1, null));
      return child;
    },
    instanceId: "test",
    write() {},
    retries: 2,
    waitForReady(_url, { signal }) {
      signals.push(signal);
      return new Promise((_, reject) => {
        signal.addEventListener("abort", () => reject(signal.reason), { once: true });
      });
    },
  }), /Backend on port 8002 exited before readiness/);
  assert.equal(launches, 3);
  assert.ok(signals.every(signal => signal.aborted));
});

test("readiness timeouts do not start another backend alongside a live process", async () => {
  let launches = 0;
  await assert.rejects(startReadyBackend({
    selectPort: async () => 8000,
    start() { launches += 1; return new EventEmitter(); },
    instanceId: "test",
    write() {},
    waitForReady: async () => { throw new Error("readiness timeout"); },
  }), /readiness timeout/);
  assert.equal(launches, 1);
});

test("stopping during startup prevents a replacement backend", async () => {
  let stopping = false;
  let launches = 0;
  const port = await startReadyBackend({
    selectPort: async () => 8000,
    start() {
      launches += 1;
      const child = new EventEmitter();
      setImmediate(() => { stopping = true; child.emit("exit", 0, null); });
      return child;
    },
    instanceId: "test",
    write() {},
    isStopping: () => stopping,
    waitForReady: () => new Promise(() => {}),
  });
  assert.equal(port, null);
  assert.equal(launches, 1);
});
