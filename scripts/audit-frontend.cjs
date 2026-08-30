const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const repoRoot = path.resolve(__dirname, "..");
const allowedAdvisories = new Map();

const auditArgs = ["--prefix", "frontend", "audit", "--omit=dev", "--json"];
let npmCommand = "npm";
let npmArgs = auditArgs;

if (process.platform === "win32") {
  // Node 24 no longer launches .cmd shims directly with spawnSync. Prefer the
  // npm JavaScript entry point so arguments remain structured and do not pass
  // through a command shell. npm_execpath is populated inside npm scripts;
  // the sibling path covers direct `node scripts/audit-frontend.cjs` usage.
  const npmCliCandidates = [
    process.env.npm_execpath,
    path.join(path.dirname(process.execPath), "node_modules", "npm", "bin", "npm-cli.js"),
  ].filter(Boolean);
  const npmCli = npmCliCandidates.find((candidate) => fs.existsSync(candidate));
  if (npmCli) {
    npmCommand = process.execPath;
    npmArgs = [npmCli, ...auditArgs];
  } else {
    npmCommand = process.env.ComSpec || "cmd.exe";
    npmArgs = ["/d", "/s", "/c", "npm.cmd", ...auditArgs];
  }
}

const result = spawnSync(
  npmCommand,
  npmArgs,
  {
    cwd: repoRoot,
    encoding: "utf8",
    windowsHide: true,
  },
);

if (result.error) {
  throw result.error;
}

let report;
try {
  report = JSON.parse(result.stdout);
} catch {
  process.stdout.write(result.stdout);
  process.stderr.write(result.stderr);
  process.stderr.write("Unable to parse the frontend npm audit report.\n");
  process.exit(result.status ?? 1);
}

const vulnerabilities = report.vulnerabilities ?? {};
const allowedPackageCache = new Map();

function advisoryId(via) {
  if (typeof via.url !== "string") {
    return null;
  }
  return via.url.split("/").at(-1) ?? null;
}

function isAllowedPackage(packageName, visiting = new Set()) {
  if (allowedPackageCache.has(packageName)) {
    return allowedPackageCache.get(packageName);
  }
  if (visiting.has(packageName)) {
    return false;
  }

  const vulnerability = vulnerabilities[packageName];
  if (!vulnerability || !Array.isArray(vulnerability.via) || vulnerability.via.length === 0) {
    return false;
  }

  const nextVisiting = new Set(visiting);
  nextVisiting.add(packageName);
  const allowed = vulnerability.via.every((via) => {
    if (typeof via === "string") {
      return isAllowedPackage(via, nextVisiting);
    }
    const id = advisoryId(via);
    return id !== null && allowedAdvisories.has(id);
  });
  allowedPackageCache.set(packageName, allowed);
  return allowed;
}

const unexpectedPackages = Object.keys(vulnerabilities).filter(
  (packageName) => !isAllowedPackage(packageName),
);

if (unexpectedPackages.length > 0) {
  process.stdout.write(result.stdout);
  process.stderr.write(result.stderr);
  process.stderr.write(
    `Frontend npm audit found non-allowlisted vulnerabilities in: ${unexpectedPackages.join(", ")}\n`,
  );
  process.exit(result.status ?? 1);
}

if (Object.keys(vulnerabilities).length === 0) {
  process.stdout.write("Frontend production dependency audit found 0 vulnerabilities.\n");
  process.exit(0);
}

process.stdout.write("Frontend npm audit reported only scoped, non-applicable advisories:\n");
for (const [id, reason] of allowedAdvisories) {
  process.stdout.write(`- ${id}: ${reason}\n`);
}

