const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

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

// Uvicorn reload uses console control events on Windows. A separate process
// group keeps those restarts from spilling into the frontend watcher.
const child = spawn(
  pythonPath,
  ["-m", "uvicorn", "app.main:app", "--reload", "--port", "8000"],
  {
    cwd: backendDir,
    env: process.env,
    stdio: ["inherit", "pipe", "pipe"],
    detached: process.platform === "win32",
    windowsHide: true,
  },
);

child.stdout.pipe(process.stdout);
child.stderr.pipe(process.stderr);

let shuttingDown = false;

function shutdown() {
  if (shuttingDown || child.exitCode !== null) {
    return;
  }
  shuttingDown = true;
  killProcessTree(child.pid);
}

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, shutdown);
}

child.on("exit", (code, signal) => {
  if (signal) {
    process.exit(shuttingDown ? 0 : 1);
    return;
  }
  process.exit(code ?? (shuttingDown ? 0 : 1));
});
