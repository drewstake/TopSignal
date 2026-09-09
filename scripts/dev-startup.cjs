const { waitForHttpReady } = require("./dev-utils.cjs");

// A free-port probe is only a snapshot: another supervisor can reclaim its
// port while reloading. Retry exited backends on a fresh port before Vite starts.
async function startReadyBackend({ selectPort, start, instanceId, write, retries = 5,
  waitForReady = waitForHttpReady, isStopping = () => false }) {
  const failedPorts = [];
  for (let attempt = 0; ; attempt += 1) {
    if (isStopping()) return null;
    const port = await selectPort(failedPorts);
    if (isStopping()) return null;
    const child = start(port);
    const controller = new AbortController();
    let exited = false;
    let onExit;
    let onError;
    const failure = new Promise((_, reject) => {
      onExit = (code, signal) => {
        exited = true;
        reject(new Error(`Backend on port ${port} exited before readiness with ${signal ? `signal ${signal}` : `code ${code ?? 0}`}.`));
      };
      onError = reject;
      child.once("exit", onExit);
      child.once("error", onError);
    });
    try {
      const url = `http://127.0.0.1:${port}/ready`;
      write(`[DEV] Waiting for backend readiness at ${url}...`);
      await Promise.race([
        failure,
        waitForReady(url, { expectedInstanceId: instanceId, signal: controller.signal }),
      ]);
      return port;
    } catch (error) {
      if (isStopping()) return null;
      // A timeout or spawn error needs cleanup by the supervisor, not another
      // backend alongside a process that might still be alive.
      if (!exited || attempt >= retries) throw error;
      failedPorts.push(port);
      write(`[DEV] ${error.message} Retrying on a new port (${attempt + 1}/${retries})...`);
    } finally {
      controller.abort();
      child.removeListener("exit", onExit);
      child.removeListener("error", onError);
    }
  }
}

module.exports = { startReadyBackend };
