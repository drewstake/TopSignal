const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const test = require("node:test");

const root = path.resolve(__dirname, "..");
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), "utf8");

test("startup task uses a dedicated limited identity and validates immutable ACLs", () => {
  const installer = read("scripts/install-windows-startup-task.ps1");
  const packageJson = JSON.parse(read("package.json"));

  assert.match(installer, /TopSignalSvc/);
  assert.match(installer, /Resolve-LocalServiceUser/);
  assert.match(installer, /Get-LocalGroupMember\s+-SID/);
  assert.match(installer, /Assert-HardenedDeployment/);
  assert.match(installer, /Get-ChildItem[^\n]+-Recurse/);
  assert.match(installer, /Get-WriteRightsMask/);
  assert.match(installer, /service identity owns/);
  assert.match(installer, /FileAttributes\]::ReparsePoint/);
  assert.match(installer, /\.git/);
  assert.match(installer, /-LogonType\s+Password/);
  assert.match(installer, /-RunLevel\s+Limited/);
  assert.doesNotMatch(installer, /-UserId\s+["']?(?:SYSTEM|NT AUTHORITY\\SYSTEM)/i);
  assert.doesNotMatch(installer, /-RunLevel\s+Highest/i);
  assert.doesNotMatch(installer, /ExecutionPolicy\s+Bypass/i);
  assert.match(installer, /powershell\.exe/);
  assert.match(installer, /pwsh\.exe/);
  assert.match(installer, /-WorkerHealthStartupGraceSeconds \$WorkerHealthStartupGraceSeconds/);
  assert.match(installer, /-WorkerHealthRestartAfterSeconds \$WorkerHealthRestartAfterSeconds/);
  assert.equal(packageJson.scripts.prod, packageJson.scripts["prod:backend"]);
  assert.doesNotMatch(packageJson.scripts.prod, /ExecutionPolicy\s+Bypass/i);
});

test("supervisor separates liveness, worker health, readiness, and child-tree recovery", () => {
  const supervisor = read("scripts/run-production.ps1");

  assert.match(supervisor, /FileShare\]::None/);
  assert.match(supervisor, /Test-LoopbackPortInUse/);
  assert.match(supervisor, /waiting 60 seconds instead of entering an occupied-port restart loop/);
  assert.match(supervisor, /JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE/);
  assert.match(supervisor, /AssignProcessToJobObject/);
  assert.match(supervisor, /Add-ProcessToSupervisorJob[^]*migrationProcess/);
  assert.match(supervisor, /PYTHONDONTWRITEBYTECODE\s*=\s*"1"/);
  assert.match(supervisor, /TOPSIGNAL_ENV\s*=\s*"production"/);
  const securityCheck = supervisor.indexOf("_validate_runtime_security_configuration");
  const migrationStart = supervisor.indexOf("Applying checked migrations");
  assert.ok(securityCheck >= 0 && securityCheck < migrationStart);
  assert.match(supervisor, /Add-ProcessToSupervisorJob[^]*securityCheckProcess/);
  assert.match(supervisor, /mutable Git working tree/);
  assert.match(supervisor, /\.IsSystem/);
  assert.match(supervisor, /health\/worker\?require_enabled=true/);
  assert.match(supervisor, /WorkerHealthStartupGraceSeconds\s*=\s*120/);
  assert.match(supervisor, /WorkerHealthRestartAfterSeconds\s*=\s*300/);
  assert.match(supervisor, /if \(-not \$backendIsLive\)\s*\{\s*continue/s);
  assert.match(supervisor, /single bounded restart/);
  assert.match(supervisor, /Readiness is an operator signal and does not itself trigger restart/);
  assert.doesNotMatch(supervisor, /persistent readiness failure/i);
  assert.match(supervisor, /taskkill\.exe/);
  assert.match(supervisor, /@\("\/PID", \$Process\.Id\.ToString\(\), "\/T", "\/F"\)/);
  assert.match(supervisor, /serve-production-frontend\.py/);
  assert.match(supervisor, /--host", "127\.0\.0\.1"/);
});

test("production frontend server is loopback-only and supplies control-plane headers", () => {
  const server = read("scripts/serve-production-frontend.py");
  const builder = read("scripts/build-production-frontend.ps1");

  assert.match(server, /args\.host != "127\.0\.0\.1"/);
  assert.match(server, /Loopback Host header required/);
  assert.match(server, /Content-Security-Policy/);
  assert.match(server, /frame-ancestors 'none'/);
  assert.match(server, /X-Content-Type-Options/);
  assert.match(server, /X-Frame-Options/);
  assert.match(server, /Cache-Control/);
  assert.match(server, /urlsplit\(self\.path\)\.path/);
  assert.match(server, /React Router routes are served by index\.html/);
  assert.match(builder, /VITE_API_BASE_URL\s*=\s*"http:\/\/127\.0\.0\.1:\$ApiPort"/);
  assert.match(builder, /VITE_DEMO_MODE\s*=\s*"false"/);
});

test("operations guide grants before removing inheritance and distinguishes local logs from alerts", () => {
  const guide = read("docs/windows-24x7-operations.md");
  const releaseGrant = guide.indexOf("icacls.exe $releaseRoot /grant:r");
  const releaseProtection = guide.indexOf("icacls.exe $releaseRoot /inheritance:r");
  const runtimeGrant = guide.indexOf("icacls.exe $runtimeRoot /grant:r");
  const runtimeProtection = guide.indexOf("icacls.exe $runtimeRoot /inheritance:r");

  assert.ok(releaseGrant >= 0 && releaseGrant < releaseProtection);
  assert.ok(runtimeGrant >= 0 && runtimeGrant < runtimeProtection);
  assert.match(guide, /\$\{servicePrincipal\}:\(OI\)\(CI\)\(RX\)/);
  assert.match(guide, /including backend\\\.venv and frontend\\dist/);
  assert.match(guide, /local diagnostic indicators only/);
  assert.match(guide, /does not send\s+email, SMS, push, or remote monitoring alerts/);
  assert.match(guide, /http:\/\/127\.0\.0\.1:4173\//);
});

test("PowerShell 5.1 and 7 parsers accept production scripts when available", (t) => {
  if (process.platform !== "win32") {
    t.skip("Windows-specific parser coverage");
    return;
  }
  const scripts = [
    "scripts/run-production.ps1",
    "scripts/install-windows-startup-task.ps1",
    "scripts/build-production-frontend.ps1",
  ];
  const parserCommand = [
    "$errors = $null",
    "[System.Management.Automation.Language.Parser]::ParseFile($env:TOPSIGNAL_PARSE_FILE, [ref]$null, [ref]$errors) | Out-Null",
    "if ($errors.Count -ne 0) { $errors | ForEach-Object { Write-Error $_.Message }; exit 1 }",
  ].join("; ");

  let parsersRun = 0;
  for (const executable of ["powershell.exe", "pwsh.exe"]) {
    for (const script of scripts) {
      const result = spawnSync(executable, ["-NoProfile", "-NonInteractive", "-Command", parserCommand], {
        cwd: root,
        encoding: "utf8",
        env: { ...process.env, TOPSIGNAL_PARSE_FILE: path.join(root, script) },
      });
      if (result.error?.code === "ENOENT") {
        break;
      }
      parsersRun += 1;
      assert.equal(result.status, 0, `${executable} rejected ${script}: ${result.stderr}`);
    }
  }
  assert.ok(parsersRun >= scripts.length, "No PowerShell parser was available");
});

test("frontend server rejects a non-loopback bind without opening a listener", (t) => {
  const candidates = process.platform === "win32"
    ? [path.join(root, "backend", ".venv", "Scripts", "python.exe"), "python.exe"]
    : [path.join(root, "backend", ".venv", "bin", "python"), "python3"];
  const python = candidates.find((candidate) => {
    if (path.isAbsolute(candidate)) return fs.existsSync(candidate);
    const probe = spawnSync(candidate, ["--version"], { encoding: "utf8" });
    return !probe.error && probe.status === 0;
  });
  if (!python) {
    t.skip("Python is unavailable");
    return;
  }
  const result = spawnSync(
    python,
    [
      path.join(root, "scripts", "serve-production-frontend.py"),
      "--directory", path.join(root, "frontend", "dist"),
      "--host", "0.0.0.0",
      "--log-file", path.join(root, "must-not-be-created.log"),
    ],
    { cwd: root, encoding: "utf8" },
  );
  assert.notEqual(result.status, 0);
  assert.match(`${result.stdout}${result.stderr}`, /must be exactly 127\.0\.0\.1/);
  assert.equal(fs.existsSync(path.join(root, "must-not-be-created.log")), false);
});
