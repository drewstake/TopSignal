const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { findAvailablePort, isPortAvailable, parseDotEnvFile, parsePort } = require("./dev-utils.cjs");

const repoRoot = path.resolve(__dirname, "..");
const backendDir = path.join(repoRoot, "backend");
const pythonPath =
  process.platform === "win32"
    ? path.join(backendDir, ".venv", "Scripts", "python.exe")
    : path.join(backendDir, ".venv", "bin", "python");

if (!fs.existsSync(pythonPath)) {
  console.error(`Missing backend Python executable: ${pythonPath}`);
  process.exit(1);
}

const backendEnv = parseDotEnvFile(path.join(backendDir, ".env"));
const childEnv = {
  ...process.env,
  ...backendEnv,
};
let backendPort = 8000;

if (!childEnv.TOPSIGNAL_DB_SCHEMA_INIT) {
  childEnv.TOPSIGNAL_DB_SCHEMA_INIT = "skip";
  console.log("TOPSIGNAL_DB_SCHEMA_INIT=skip for fast dev startup. Run `npm run db:init` after schema changes.");
}

function killProcessTree(pid) {
  if (process.platform === "win32") {
    const killer = spawn("taskkill", ["/pid", String(pid), "/t", "/f"], {
      stdio: "ignore",
      windowsHide: true,
    });
    killer.unref();
    return;
  }

  process.kill(pid, "SIGTERM");
  const timeout = setTimeout(() => {
    try {
      process.kill(pid, "SIGKILL");
    } catch {
      // The child already exited.
    }
  }, 5000);
  timeout.unref();
}

// On Windows, Uvicorn's --reload supervisor can propagate console control
// events back to the parent npm dev wrapper and stop the frontend. Use a small
// wrapper-side watcher there and restart plain Uvicorn ourselves instead.
const useWrapperReload =
  process.platform === "win32" &&
  process.env.TOPSIGNAL_DEV_BACKEND_UVICORN_RELOAD !== "1";

let child = null;
let shuttingDown = false;
let restarting = false;
let restartTimer = null;
let watcher = null;

function startBackend() {
  const args = ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(backendPort)];
  if (!useWrapperReload) {
    args.splice(3, 0, "--reload");
  }

  child = spawn(pythonPath, args, {
    cwd: backendDir,
    env: childEnv,
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });

  child.stdout.pipe(process.stdout);
  child.stderr.pipe(process.stderr);

  child.on("exit", (code, signal) => {
    child = null;

    if (shuttingDown) {
      process.exit(0);
      return;
    }

    if (restarting) {
      restarting = false;
      setTimeout(startBackend, 300);
      return;
    }

    if (signal) {
      process.exit(1);
      return;
    }
    process.exit(code ?? 1);
  });
}

function shouldRestartForChange(fileName) {
  if (!fileName) {
    return true;
  }

  const normalized = String(fileName).replaceAll("\\", "/");
  if (normalized.includes("__pycache__/")) {
    return false;
  }

  return (
    normalized.endsWith(".py") ||
    normalized === ".env" ||
    normalized.endsWith("/.env")
  );
}

function scheduleRestart(fileName) {
  if (shuttingDown || !shouldRestartForChange(fileName)) {
    return;
  }

  if (restartTimer) {
    clearTimeout(restartTimer);
  }

  restartTimer = setTimeout(() => {
    restartTimer = null;
    if (shuttingDown) {
      return;
    }

    console.log(`Detected backend change${fileName ? ` in ${fileName}` : ""}; restarting...`);
    if (!child || child.exitCode !== null) {
      startBackend();
      return;
    }

    restarting = true;
    killProcessTree(child.pid);
  }, 250);
}

function startWrapperReloadWatcher() {
  if (!useWrapperReload) {
    return;
  }

  watcher = fs.watch(backendDir, { recursive: true }, (_eventType, fileName) => {
    scheduleRestart(fileName);
  });
}

function shutdown() {
  if (shuttingDown) {
    return;
  }
  shuttingDown = true;

  if (restartTimer) {
    clearTimeout(restartTimer);
  }

  if (watcher) {
    watcher.close();
  }

  if (!child || child.exitCode !== null) {
    process.exit(0);
    return;
  }

  killProcessTree(child.pid);
}

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, shutdown);
}

async function resolveBackendPort() {
  const preferredPort = parsePort(
    childEnv.TOPSIGNAL_DEV_BACKEND_PORT,
    8000,
    "TOPSIGNAL_DEV_BACKEND_PORT",
  );
  const strictPort = childEnv.TOPSIGNAL_DEV_BACKEND_PORT_STRICT === "1";

  if (strictPort) {
    if (!(await isPortAvailable(preferredPort))) {
      throw new Error(
        `Backend port ${preferredPort} is already in use on 127.0.0.1. ` +
          "Stop that process or set TOPSIGNAL_DEV_BACKEND_PORT to another port.",
      );
    }

    return preferredPort;
  }

  const availablePort = await findAvailablePort(preferredPort);
  if (availablePort !== preferredPort) {
    console.log(`Port ${preferredPort} is in use, using backend port ${availablePort}.`);
  }

  return availablePort;
}

async function main() {
  backendPort = await resolveBackendPort();
  console.log(`Backend API target: http://127.0.0.1:${backendPort}`);
  startBackend();
  startWrapperReloadWatcher();
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
