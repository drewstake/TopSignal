import { fileURLToPath } from "node:url";
const frontend = fileURLToPath(new URL("../../../frontend/", import.meta.url));
export default {
  root: fileURLToPath(new URL("../../../", import.meta.url)),
  envDir: false,
  resolve: { alias: { vitest: `${frontend}/node_modules/vitest/dist/index.js` } },
  test: {
    include: ["docs/audits/2026-10-26/audit-probes.test.ts"],
    environment: "jsdom", maxWorkers: 1,
  },
};
