const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');
const root = path.resolve(__dirname, '..');
test('release archive, broker, CI, recording and benchmark integrity gates', () => {
  const local = path.join(root, 'backend', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const python = fs.existsSync(local) ? local : (process.platform === 'win32' ? 'python' : 'python3');
  const result = spawnSync(python, ['scripts/test_release.py', '-v'], { cwd: root, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stdout + result.stderr);
});
test('release switch recovery and operator STOP ownership', { skip: process.platform !== 'win32' }, () => {
  const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-File', 'scripts/test-release-switch.ps1'], { cwd: root, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stdout + result.stderr);
});
