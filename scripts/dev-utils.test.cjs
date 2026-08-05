const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const {
  classifyBackendDevChange,
  createEnvironmentSnapshot,
  parseDotEnvFile,
  runDatabaseMigrations,
} = require("./dev-utils.cjs");

test("backend code reload and environment restart are intentionally different", () => {
  assert.equal(classifyBackendDevChange("app/main.py"), "code_reload");
  assert.equal(classifyBackendDevChange("app\\main.py"), "code_reload");
  assert.equal(classifyBackendDevChange(".env"), "supervisor_restart");
  assert.equal(classifyBackendDevChange("backend/.env"), "supervisor_restart");
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
