const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { watchBackendSources } = require("./backend-source-watcher.cjs");
const { classifyBackendDevChange } = require("./dev-utils.cjs");

test("runtime storage and test activity cannot restart streams; actual source edits still reload", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "topsignal-source-watch-"));
  const changes = [];
  const write = (name, text = "data") => {
    const target = path.join(root, name);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, text);
  };
  let watcher;
  try {
    write("app/main.py", "before");
    watcher = watchBackendSources(root, name => changes.push(name.replaceAll("\\", "/")), { intervalMs: 60000 });
    write("storage/offline/topsignal.sqlite3-wal");
    write("storage/cache/helper.py");
    write(".venv/package.py");
    write("tests/test_market_depth.py");
    write("app/__pycache__/main.pyc");
    watcher.scan();
    assert.deepEqual(changes, []);

    write("app/main.py", "edited source");
    watcher.scan();
    assert.deepEqual(changes.splice(0), ["app/main.py"]);
    watcher.scan();
    assert.deepEqual(changes, []);

    write("app/services/new.py");
    watcher.scan();
    assert.deepEqual(changes.splice(0), ["app/services/new.py"]);
    fs.renameSync(path.join(root, "app/services/new.py"), path.join(root, "app/services/renamed.py"));
    watcher.scan();
    assert.deepEqual(changes.splice(0).sort(), ["app/services/new.py", "app/services/renamed.py"]);
    fs.unlinkSync(path.join(root, "app/services/renamed.py"));
    watcher.scan();
    assert.deepEqual(changes.splice(0), ["app/services/renamed.py"]);

    write(".env", "TEST_SETTING=changed");
    watcher.scan();
    assert.deepEqual(changes.map(classifyBackendDevChange), ["supervisor_restart"]);
    changes.length = 0;
    watcher.close();
    write("app/main.py", "after close");
    watcher.scan();
    assert.deepEqual(changes, []);
  } finally {
    watcher?.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});
