const { spawn } = require("node:child_process");
const path = require("node:path");
const readline = require("node:readline");

const repoRoot = path.resolve(__dirname, "..");
const commands = [
  { name: "BACKEND", script: path.join(__dirname, "dev-backend.cjs") },
  { name: "FRONTEND", script: path.join(__dirname, "dev-frontend.cjs") },
];

const children = new Map();
let shuttingDown = false;
let remainingChildren = commands.length;
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
  if (remainingChildren === 0) {
    process.exit(exitCode);
  }
}

for (const command of commands) {
  const child = spawn(process.execPath, [command.script], {
    cwd: repoRoot,
    env: process.env,
    stdio: ["inherit", "pipe", "pipe"],
    windowsHide: true,
  });

  children.set(command.name, child);
  prefixStream(command.name, child.stdout, (line) => process.stdout.write(line));
  prefixStream(command.name, child.stderr, (line) => process.stderr.write(line));

  child.on("exit", (code, signal) => {
    remainingChildren -= 1;

    if (!shuttingDown) {
      shuttingDown = true;
      exitCode = code ?? (signal ? 1 : 0);
      for (const [name, sibling] of children) {
        if (name !== command.name) {
          stopChild(sibling);
        }
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

    shuttingDown = true;
    exitCode = 0;
    for (const child of children.values()) {
      stopChild(child);
    }
    maybeExit();
  });
}
