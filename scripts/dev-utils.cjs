const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const https = require("node:https");
const net = require("node:net");
const path = require("node:path");

function parseDotEnvFile(filePath) {
  if (!fs.existsSync(filePath)) {
    return {};
  }

  const env = {};
  const content = fs.readFileSync(filePath, "utf8");

  for (const rawLine of content.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) {
      continue;
    }

    const normalized = line.startsWith("export ") ? line.slice("export ".length) : line;
    const equalsIndex = normalized.indexOf("=");
    if (equalsIndex <= 0) {
      continue;
    }

    const key = normalized.slice(0, equalsIndex).trim();
    let value = normalized.slice(equalsIndex + 1).trim();
    if (!key) {
      continue;
    }

    const isWrappedInMatchingQuotes =
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"));
    if (isWrappedInMatchingQuotes) {
      value = value.slice(1, -1);
    }

    env[key] = value;
  }

  return env;
}

function parsePort(value, defaultPort, envName) {
  const rawValue = String(value ?? "").trim();
  if (!rawValue) {
    return defaultPort;
  }

  const port = Number(rawValue);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`${envName} must be an integer TCP port between 1 and 65535.`);
  }

  return port;
}

function createEnvironmentSnapshot(parentEnvironment, fileEnvironment) {
  return {
    ...parentEnvironment,
    ...fileEnvironment,
  };
}

function createBackendEnvironmentSnapshot(parentEnvironment, fileEnvironment) {
  const snapshot = createEnvironmentSnapshot(parentEnvironment, fileEnvironment);
  if (parentEnvironment.TOPSIGNAL_DEV_MIGRATIONS_APPLIED === "1") {
    // The supervisor has already selected the port used by frontend and readiness.
    // Loading .env again must not change that port or the startup identity.
    for (const key of ["TOPSIGNAL_DEV_BACKEND_PORT", "TOPSIGNAL_DEV_BACKEND_PORT_STRICT", "TOPSIGNAL_DEV_INSTANCE_ID"]) {
      snapshot[key] = parentEnvironment[key];
    }
  }
  return snapshot;
}

async function assertLocalFrontendAvailable() {
  if (!(await isPortAvailable(5174))) {
    throw new Error(
      "Local frontend port 5174 is already in use; no additional local backend was started. " +
      "If your local workspace is already running, open http://127.0.0.1:5174. " +
      "To replace it, stop its terminal with Ctrl+C and rerun this command. " +
      "The combined launcher will still try to start the cloud app.",
    );
  }
}

function classifyBackendDevChange(fileName) {
  if (!fileName) {
    return "ignore";
  }

  const normalized = String(fileName).replaceAll("\\", "/");
  const pathSegments = normalized.split("/");
  if (pathSegments.includes(".venv")) {
    return "ignore";
  }
  if (normalized.includes("__pycache__/") || normalized.endsWith(".pyc")) {
    return "ignore";
  }
  if (normalized === ".env" || normalized.endsWith("/.env")) {
    return "supervisor_restart";
  }
  if (normalized.endsWith(".py")) {
    return "code_reload";
  }
  return "ignore";
}

function isPortAvailable(port, host = "127.0.0.1") {
  return new Promise((resolve, reject) => {
    const server = net.createServer();

    server.unref();
    server.once("error", (error) => {
      if (error.code === "EADDRINUSE" || error.code === "EACCES") {
        resolve(false);
        return;
      }

      reject(error);
    });
    server.listen({ host, port }, () => {
      server.close(() => resolve(true));
    });
  });
}

function requestHttpStatus(url, timeoutMs, expectedInstanceId, signal) {
  return new Promise((resolve, reject) => {
    const parsedUrl = new URL(url);
    const client = parsedUrl.protocol === "https:" ? https : http;
    const request = client.get(parsedUrl, { signal }, (response) => {
      response.resume();
      const status = response.statusCode ?? 0;
      if (status >= 200 && status < 300 && expectedInstanceId
        && response.headers["x-topsignal-dev-instance"] !== expectedInstanceId) {
        reject(new Error("Backend readiness response belongs to a different development process"));
        return;
      }
      resolve(status);
    });

    request.setTimeout(timeoutMs, () => {
      request.destroy(new Error(`Request timed out after ${timeoutMs}ms`));
    });
    request.once("error", reject);
  });
}

async function waitForHttpReady(url, options = {}) {
  const timeoutMs = options.timeoutMs ?? 60000;
  const intervalMs = options.intervalMs ?? 100;
  const requestTimeoutMs = options.requestTimeoutMs ?? 15000;
  const requestStatus = options.requestStatus ?? requestHttpStatus;
  const startedAt = Date.now();
  let lastResult = "no response";

  while (true) {
    options.signal?.throwIfAborted();
    try {
      const status = await requestStatus(url, requestTimeoutMs, options.expectedInstanceId, options.signal);
      if (status >= 200 && status < 300) {
        return;
      }
      lastResult = `HTTP ${status}`;
    } catch (error) {
      options.signal?.throwIfAborted();
      lastResult = error instanceof Error ? error.message : String(error);
    }

    if (Date.now() - startedAt >= timeoutMs) {
      throw new Error(`Timed out waiting for ${url} (${lastResult}).`);
    }

    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

async function findAvailablePort(preferredPort, options = {}) {
  const host = options.host ?? "127.0.0.1";
  const maxPort = options.maxPort ?? 65535;
  const excludedPorts = new Set(options.excludedPorts ?? []);

  for (let port = preferredPort; port <= maxPort; port += 1) {
    if (excludedPorts.has(port)) {
      continue;
    }
    if (await isPortAvailable(port, host)) {
      return port;
    }
  }

  throw new Error(`No available TCP port found on ${host} at or above ${preferredPort}.`);
}

function runDatabaseMigrations({
  repoRoot,
  environment,
  spawnSyncImpl = spawnSync,
}) {
  const migrationScript = path.join(repoRoot, "scripts", "db-migrate.cjs");
  const result = spawnSyncImpl(process.execPath, [migrationScript], {
    cwd: repoRoot,
    env: environment,
    stdio: "inherit",
    windowsHide: true,
  });

  if (result.error) {
    throw new Error(`Could not run database migrations: ${result.error.message}`);
  }
  if (result.signal) {
    throw new Error(`Database migration process stopped by signal ${result.signal}.`);
  }
  if (result.status !== 0) {
    throw new Error(`Database migration process failed with code ${result.status ?? "unknown"}.`);
  }
}

module.exports = {
  assertLocalFrontendAvailable,
  classifyBackendDevChange,
  createBackendEnvironmentSnapshot,
  createEnvironmentSnapshot,
  findAvailablePort,
  isPortAvailable,
  parseDotEnvFile,
  parsePort,
  runDatabaseMigrations,
  waitForHttpReady,
};
