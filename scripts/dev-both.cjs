const { spawn } = require("node:child_process");
const path = require("node:path");

const repoRoot = path.resolve(__dirname, "..");
const devScript = path.join(__dirname, "dev.cjs");

function stopProcessTree(child) {
  if (child.exitCode !== null || child.signalCode !== null) {
    return;
  }
  if (process.platform === "win32") {
    const killer = spawn("taskkill", ["/pid", String(child.pid), "/t", "/f"], {
      stdio: "ignore",
      windowsHide: true,
    });
    killer.unref();
  } else {
    // Each dev supervisor handles SIGTERM and shuts down its own servers.
    child.kill("SIGTERM");
  }
}

function startBothProfiles({
  spawnProcess = spawn,
  stopProcess = stopProcessTree,
  write = (message) => process.stdout.write(`${message}\n`),
  onExit = (code) => { process.exitCode = code; },
} = {}) {
  const running = new Map();
  let cloudStarted = false;
  let stopping = false;
  let exitCode = 0;

  function startCloud() {
    if (cloudStarted || stopping) {
      return;
    }
    cloudStarted = true;
    launch("CLOUD", []);
  }

  function launch(name, args) {
    write(`[BOTH] Starting ${name === "LOCAL" ? "local ProjectX workspace at http://127.0.0.1:5174" : "regular cloud app (use the Vite URL printed below)"}.`);
    const child = spawnProcess(process.execPath, [devScript, ...args], {
      cwd: repoRoot,
      env: { ...process.env },
      stdio: ["inherit", "inherit", "inherit", "ipc"],
      windowsHide: true,
    });
    running.set(name, child);
    let finished = false;

    function finish(code) {
      if (finished) {
        return;
      }
      finished = true;
      running.delete(name);
      if (!stopping) {
        exitCode = code || exitCode;
        write(`[BOTH] ${name} stopped${code ? ` with code ${code}` : ""}. Other running profiles stay available.`);
        if (name === "LOCAL") {
          startCloud();
        }
      }
      if (running.size === 0 && (cloudStarted || stopping)) {
        onExit(exitCode);
      }
    }

    child.on("error", (error) => {
      write(`[BOTH] Could not start ${name}: ${error.message}`);
      finish(1);
    });
    child.on("exit", (code, signal) => finish(code ?? (signal ? 1 : 0)));
    child.on("message", (message) => {
      if (name === "LOCAL" && message?.type === "topsignal-dev-ready") {
        // Wait for the local backend to bind before the cloud supervisor picks
        // its port, preventing both profiles from choosing the same free port.
        startCloud();
      }
    });
  }

  write("[BOTH] Starting both workspaces. Press Ctrl+C to stop both.");
  launch("LOCAL", ["--offline", "--topstep"]);

  return {
    stop() {
      if (stopping) {
        return;
      }
      stopping = true;
      exitCode = 0;
      for (const child of running.values()) {
        stopProcess(child);
      }
      if (running.size === 0) {
        onExit(exitCode);
      }
    },
  };
}

if (require.main === module) {
  const profiles = startBothProfiles();
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.on(signal, () => profiles.stop());
  }
}

module.exports = { startBothProfiles };
