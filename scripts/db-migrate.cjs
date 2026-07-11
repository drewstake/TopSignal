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

const args = [path.join(backendDir, "tools", "migrate_db.py"), ...process.argv.slice(2)];
const child = spawn(pythonPath, args, {
  cwd: repoRoot,
  stdio: "inherit",
  windowsHide: true,
});

child.on("exit", (code, signal) => {
  process.exit(signal ? 1 : (code ?? 1));
});
