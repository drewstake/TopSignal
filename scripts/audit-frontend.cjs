const { spawnSync } = require("node:child_process");
const path = require("node:path");

const repoRoot = path.resolve(__dirname, "..");
const allowedAdvisories = new Map([
  [
    "GHSA-qwww-vcr4-c8h2",
    "TopSignal is a client-side Vite SPA and does not use React Router's unstable RSC APIs.",
  ],
]);

const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
const result = spawnSync(
  npmCommand,
  ["--prefix", "frontend", "audit", "--omit=dev", "--json"],
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

