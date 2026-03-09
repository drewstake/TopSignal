const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const repoRoot = path.resolve(__dirname, "..");
const frontendDir = path.join(repoRoot, "frontend");
const vitePath = path.join(frontendDir, "node_modules", "vite", "bin", "vite.js");

if (!fs.existsSync(vitePath)) {
  console.error(`Missing Vite CLI entrypoint: ${vitePath}`);
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

const child = spawn(process.execPath, [vitePath], {
  cwd: frontendDir,
  env: process.env,
  stdio: ["inherit", "pipe", "pipe"],
  windowsHide: true,
});

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
