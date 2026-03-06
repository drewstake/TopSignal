export const ENABLE_PERF_LOGS =
  import.meta.env.DEV || String(import.meta.env.VITE_PERF_LOGS ?? "").toLowerCase() === "true";

export function logPerfInfo(...args: Parameters<typeof console.info>) {
  if (!ENABLE_PERF_LOGS) {
    return;
  }
  console.info(...args);
}
