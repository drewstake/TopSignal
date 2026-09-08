const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const http = require("node:http");
const { once } = require("node:events");

const {
  classifyBackendDevChange,
  createEnvironmentSnapshot,
  parseDotEnvFile,
  runDatabaseMigrations,
  waitForHttpReady,
} = require("./dev-utils.cjs");

test("backend code reload and environment restart are intentionally different", () => {
  assert.equal(classifyBackendDevChange("app/main.py"), "code_reload");
  assert.equal(classifyBackendDevChange("app\\main.py"), "code_reload");
  assert.equal(classifyBackendDevChange(".env"), "supervisor_restart");
  assert.equal(classifyBackendDevChange("backend/.env"), "supervisor_restart");
  assert.equal(classifyBackendDevChange(".venv/Lib/site-packages/numpy/exceptions.py"), "ignore");
  assert.equal(classifyBackendDevChange(".venv\\Lib\\site-packages\\openpyxl\\_constants.py"), "ignore");
  assert.equal(classifyBackendDevChange("backend/.venv/Lib/site-packages/pyarrow/__init__.py"), "ignore");
  assert.equal(classifyBackendDevChange("app/__pycache__/main.pyc"), "ignore");
  assert.equal(classifyBackendDevChange("README.md"), "ignore");
});

test("environment files are read as snapshots that change only after supervisor reconstruction", () => {
  assert.deepEqual(parseDotEnvFile("/path/that/does/not/exist"), {});
  const fileEnvironment = { TEST_RUNTIME_MARKER: "before" };
  const runningSupervisorEnvironment = createEnvironmentSnapshot({}, fileEnvironment);

  fileEnvironment.TEST_RUNTIME_MARKER = "after";

  assert.equal(runningSupervisorEnvironment.TEST_RUNTIME_MARKER, "before");
  assert.equal(createEnvironmentSnapshot({}, fileEnvironment).TEST_RUNTIME_MARKER, "after");
});

test("backend startup migrations use the same environment as the backend", () => {
  const repoRoot = path.join(path.sep, "workspace", "topsignal");
  const environment = { DATABASE_URL: "postgresql://configured-database" };
  const calls = [];

  runDatabaseMigrations({
    repoRoot,
    environment,
    spawnSyncImpl(command, args, options) {
      calls.push({ command, args, options });
      return { status: 0, signal: null };
    },
  });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].command, process.execPath);
  assert.deepEqual(calls[0].args, [path.join(repoRoot, "scripts", "db-migrate.cjs")]);
  assert.equal(calls[0].options.cwd, repoRoot);
  assert.equal(calls[0].options.env, environment);
  assert.equal(calls[0].options.stdio, "inherit");
});

test("backend startup stops when migrations fail", () => {
  assert.throws(
    () => runDatabaseMigrations({
      repoRoot: path.join(path.sep, "workspace", "topsignal"),
      environment: {},
      spawnSyncImpl: () => ({ status: 1, signal: null }),
    }),
    /Database migration process failed with code 1/,
  );
});

test("backend readiness polling waits for a successful response", async () => {
  const statuses = [503, 503, 200];
  let attempts = 0;
  const requestTimeouts = [];

  await waitForHttpReady("http://127.0.0.1:8000/ready", {
    intervalMs: 1,
    timeoutMs: 1000,
    requestStatus: async (_url, requestTimeoutMs) => {
      requestTimeouts.push(requestTimeoutMs);
      const status = statuses[attempts] ?? 200;
      attempts += 1;
      return status;
    },
  });

  assert.equal(attempts, 3);
  assert.deepEqual(requestTimeouts, [15000, 15000, 15000]);
});

test("backend readiness polling reports the last connection failure", async () => {
  await assert.rejects(
    waitForHttpReady("http://127.0.0.1:8000/ready", {
      timeoutMs: 0,
      requestStatus: async () => {
        throw new Error("connection refused");
      },
    }),
    /Timed out waiting for http:\/\/127\.0\.0\.1:8000\/ready \(connection refused\)\./,
  );
});

test("a healthy backend from another launch cannot satisfy startup readiness", async () => {
  let instanceId;
  const server = http.createServer((_request, response) => {
    if (instanceId) response.setHeader("X-TopSignal-Dev-Instance", instanceId);
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end('{"status":"ready"}');
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  try {
    const url = `http://127.0.0.1:${server.address().port}/ready`;
    for (const otherInstance of [undefined, "original-cloud-backend"]) {
      instanceId = otherInstance;
      await assert.rejects(
        waitForHttpReady(url, { timeoutMs: 0, expectedInstanceId: "new-offline-backend" }),
        /Backend readiness response belongs to a different development process/,
      );
    }
    instanceId = "new-offline-backend";
    await waitForHttpReady(url, { timeoutMs: 0, expectedInstanceId: instanceId });
    // Existing health polling without an instance requirement keeps working.
    instanceId = undefined;
    await waitForHttpReady(url, { timeoutMs: 0 });
  } finally {
    await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  }
});
