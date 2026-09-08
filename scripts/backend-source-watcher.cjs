const fs = require("node:fs");
const path = require("node:path");

// Watch only inputs to the running application. Recursive fs.watch on the
// backend also sees SQLite/cache writes; on Windows its buffer can overflow
// and report a null filename, which must never be interpreted as a code edit.
function sourceSnapshot(backendDir) {
  const snapshot = new Map();
  function visit(relativePath) {
    const absolutePath = path.join(backendDir, relativePath);
    let stat;
    try {
      stat = fs.lstatSync(absolutePath, { bigint: true });
    } catch (error) {
      if (error.code === "ENOENT") return;
      throw error;
    }
    if (stat.isDirectory()) {
      if (path.basename(relativePath) === "__pycache__") return;
      for (const name of fs.readdirSync(absolutePath)) visit(path.join(relativePath, name));
    } else if (stat.isFile() && (relativePath.endsWith(".py") || relativePath === ".env")) {
      snapshot.set(relativePath, `${stat.mtimeNs}:${stat.ctimeNs}:${stat.size}`);
    }
  }
  visit("app");
  visit(".env");
  return snapshot;
}

function watchBackendSources(backendDir, onChange, { intervalMs = 1000, onError = console.error } = {}) {
  let previous = sourceSnapshot(backendDir);
  let closed = false;
  function scan() {
    if (closed) return;
    let current;
    try {
      current = sourceSnapshot(backendDir);
    } catch (error) {
      // Keep the last complete snapshot when a directory is temporarily unreadable.
      onError(error);
      return;
    }
    const changed = [...new Set([...previous.keys(), ...current.keys()])]
      .filter(name => previous.get(name) !== current.get(name));
    previous = current;
    for (const name of changed) onChange(name);
  }
  const timer = setInterval(scan, intervalMs);
  return {
    scan,
    close() {
      closed = true;
      clearInterval(timer);
    },
  };
}

module.exports = { watchBackendSources };
