const test = require("node:test");
const assert = require("node:assert/strict");

const {
  classifyBackendDevChange,
  createEnvironmentSnapshot,
  parseDotEnvFile,
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
