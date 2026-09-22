import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

// Let jsdom provide browser Storage. Node's experimental implementation can
// otherwise shadow it without a storage file and break storage events as well.
const storageFlag = "--no-experimental-webstorage";
const flags = process.allowedNodeEnvironmentFlags.has(storageFlag) ? [storageFlag] : [];
const result = spawnSync(process.execPath, [
  ...flags,
  fileURLToPath(new URL("../node_modules/vitest/vitest.mjs", import.meta.url)),
  "run",
  ...process.argv.slice(2),
], {
  stdio: "inherit",
  // Vitest creates workers with its own execArgv; inherit the flag there too.
  env: { ...process.env, NODE_OPTIONS: [process.env.NODE_OPTIONS, ...flags].filter(Boolean).join(" ") },
});
if (result.error) console.error(result.error.message);
process.exit(result.status ?? 1);
