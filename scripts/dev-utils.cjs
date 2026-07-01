const fs = require("node:fs");
const net = require("node:net");

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

async function findAvailablePort(preferredPort, options = {}) {
  const host = options.host ?? "127.0.0.1";
  const maxPort = options.maxPort ?? 65535;

  for (let port = preferredPort; port <= maxPort; port += 1) {
    if (await isPortAvailable(port, host)) {
      return port;
    }
  }

  throw new Error(`No available TCP port found on ${host} at or above ${preferredPort}.`);
}

module.exports = {
  findAvailablePort,
  isPortAvailable,
  parseDotEnvFile,
  parsePort,
};
