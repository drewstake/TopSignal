const { spawn } = require("node:child_process");
const path = require("node:path");
const readline = require("node:readline");
const { findAvailablePort, parseDotEnvFile, parsePort } = require("./dev-utils.cjs");

const repoRoot = path.resolve(__dirname, "..");
const backendDir = path.join(repoRoot, "backend");

const children = new Map();
const states = new Map();
const earlyExitRestartLimit = 5;
const earlyExitWindowMs = 60000;
const keepAlive = setInterval(() => {}, 60 * 60 * 1000);
let shuttingDown = false;
let exitCode = 0;

function prefixStream(name, stream, writer) {
  if (!stream) {
    return;
  }

  const lineReader = readline.createInterface({
    input: stream,
    crlfDelay: Infinity,
  });

  lineReader.on("line", (line) => {
    writer(`[${name}] ${line}\n`);
  });
}

function stopChild(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) {
    return;
  }

  if (process.platform === "win32") {
    const killer = spawn("taskkill", ["/pid", String(child.pid), "/t", "/f"], {
      stdio: "ignore",
      windowsHide: true,
    });
    killer.unref();
    return;
  }

  child.kill("SIGTERM");
  const timeout = setTimeout(() => {
    if (child.exitCode === null && child.signalCode === null) {
      child.kill("SIGKILL");
    }
  }, 5000);
  timeout.unref();
}

function maybeExit() {
  if ([...states.values()].every((state) => !state.child)) {
    clearInterval(keepAlive);
    process.exit(exitCode);
  }
}

function formatExitReason(code, signal) {
  if (signal) {
    return `signal ${signal}`;
  }
  return `code ${code ?? 0}`;
}

function startCommand(command) {
  if (shuttingDown) {
    return;
  }

  const state = states.get(command.name);
  const child = spawn(process.execPath, [command.script], {
    cwd: repoRoot,
    env: command.env,
    stdio: ["inherit", "pipe", "pipe"],
    windowsHide: true,
  });

  state.child = child;
  state.startedAt = Date.now();
  children.set(command.name, child);
  prefixStream(command.name, child.stdout, (line) => process.stdout.write(line));
  prefixStream(command.name, child.stderr, (line) => process.stderr.write(line));

  child.on("exit", (code, signal) => {
    const runtimeMs = Date.now() - state.startedAt;
    process.stderr.write(
      `[${command.name}] process exited after ${Math.round(runtimeMs / 1000)}s with ${formatExitReason(
        code,
        signal,
      )}.\n`,
    );

    if (state.child === child) {
      state.child = null;
    }

    if (shuttingDown) {
      maybeExit();
      return;
    }

    const canRestart =
      runtimeMs < earlyExitWindowMs && state.restartCount < earlyExitRestartLimit;

    if (canRestart) {
      state.restartCount += 1;
      process.stderr.write(
        `[${command.name}] exited during startup with ${formatExitReason(
          code,
          signal,
        )}; restarting (${state.restartCount}/${earlyExitRestartLimit})...\n`,
      );
      setTimeout(() => startCommand(command), 500);
      return;
    }

    shuttingDown = true;
    exitCode = code ?? (signal ? 1 : 0);
    for (const [name, sibling] of children) {
      if (name !== command.name) {
        stopChild(sibling);
      }
    }

    maybeExit();
  });
}

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    if (shuttingDown) {
      return;
    }

    process.stderr.write(`Received ${signal}; stopping dev servers...\n`);
    shuttingDown = true;
    exitCode = 0;
    for (const state of states.values()) {
      const child = state.child;
      stopChild(child);
    }
    maybeExit();
  });
}

async function resolveBackendPort() {
  const backendEnv = parseDotEnvFile(path.join(backendDir, ".env"));
  const preferredPort = parsePort(
    backendEnv.TOPSIGNAL_DEV_BACKEND_PORT ?? process.env.TOPSIGNAL_DEV_BACKEND_PORT,
    8000,
    "TOPSIGNAL_DEV_BACKEND_PORT",
  );
  const availablePort = await findAvailablePort(preferredPort);

  if (availablePort !== preferredPort) {
    process.stdout.write(
      `[DEV] Backend port ${preferredPort} is in use; using http://127.0.0.1:${availablePort}.\n`,
    );
  }

  return availablePort;
}

async function main() {
  const backendPort = await resolveBackendPort();
  const apiBaseUrl = process.env.VITE_API_BASE_URL ?? `http://127.0.0.1:${backendPort}`;
  const commands = [
    {
      name: "BACKEND",
      script: path.join(__dirname, "dev-backend.cjs"),
      env: {
        ...process.env,
        TOPSIGNAL_DEV_BACKEND_PORT: String(backendPort),
        TOPSIGNAL_DEV_BACKEND_PORT_STRICT: "1",
      },
    },
    {
      name: "FRONTEND",
      script: path.join(__dirname, "dev-frontend.cjs"),
      env: {
        ...process.env,
        VITE_API_BASE_URL: apiBaseUrl,
      },
    },
  ];

  if (process.env.VITE_API_BASE_URL) {
    process.stdout.write(`[DEV] Frontend VITE_API_BASE_URL is set to ${process.env.VITE_API_BASE_URL}.\n`);
  } else if (backendPort !== 8000) {
    process.stdout.write(`[DEV] Frontend VITE_API_BASE_URL set to ${apiBaseUrl}.\n`);
  }

  for (const command of commands) {
    states.set(command.name, {
      child: null,
      restartCount: 0,
      startedAt: 0,
    });
    startCommand(command);
  }
}

main().catch((error) => {
  clearInterval(keepAlive);
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exit(1);
});
