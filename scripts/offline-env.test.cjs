const assert = require("node:assert/strict");
const { test } = require("node:test");
const { spawnSync } = require("node:child_process");
const path = require("node:path");
const { offlineEnvironment } = require("./offline-env.cjs");

const repoRoot = path.resolve(__dirname, "..");

test("offline profile overrides cloud credentials, storage and live trading settings", () => {
  const original = {
    DATABASE_URL: "postgresql://cloud.invalid/private",
    SUPABASE_URL: "https://cloud.invalid",
    SUPABASE_SERVICE_ROLE_KEY: "private",
    PROJECTX_API_KEY: "private",
    VITE_SUPABASE_URL: "https://cloud.invalid",
    AUTH_REQUIRED: "true",
    TOPSIGNAL_ENV: "production",
    TOPSIGNAL_LIVE_EXECUTION_ENABLED: "true",
    TOPSIGNAL_BOT_WORKER_ENABLED: "true",
    JOURNAL_IMAGE_STORAGE_BACKEND: "supabase",
    TOPSIGNAL_DATABENTO_CACHE_DIR: "C:/local-history",
  };
  const env = offlineEnvironment(original, repoRoot);
  assert.match(env.DATABASE_URL, /^sqlite\+pysqlite:\/\/\//);
  for (const key of ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "PROJECTX_API_KEY", "VITE_SUPABASE_URL"]) {
    assert.equal(env[key], "");
  }
  assert.equal(env.AUTH_REQUIRED, "false");
  assert.equal(env.TOPSIGNAL_ENV, "development");
  assert.equal(env.TOPSIGNAL_LIVE_EXECUTION_ENABLED, "false");
  assert.equal(env.TOPSIGNAL_BOT_WORKER_ENABLED, "false");
  assert.equal(env.JOURNAL_IMAGE_STORAGE_BACKEND, "local");
  assert.equal(env.TOPSIGNAL_DATABENTO_CACHE_DIR, original.TOPSIGNAL_DATABENTO_CACHE_DIR);
  assert.equal(original.AUTH_REQUIRED, "true");
});

test("connected local profile retains Topstep settings without enabling cloud storage or orders", () => {
  const configured = {
    PROJECTX_API_BASE_URL: "https://api.topstepx.com",
    PROJECTX_USERNAME: "test-user",
    PROJECTX_API_KEY: "test-key",
    CREDENTIALS_ENCRYPTION_KEY: "test-encryption-key",
    SUPABASE_URL: "https://cloud.invalid",
    DATABASE_URL: "postgresql://cloud.invalid/private",
    TOPSIGNAL_LIVE_EXECUTION_ENABLED: "true",
    TOPSIGNAL_BOT_WORKER_ENABLED: "true",
    TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION: "true",
    PROJECTX_STREAMING_ENABLED: "true",
  };
  const env = offlineEnvironment(configured, repoRoot, { projectx: true });
  assert.equal(env.PROJECTX_API_KEY, configured.PROJECTX_API_KEY);
  assert.equal(env.PROJECTX_API_BASE_URL, configured.PROJECTX_API_BASE_URL);
  assert.equal(env.CREDENTIALS_ENCRYPTION_KEY, configured.CREDENTIALS_ENCRYPTION_KEY);
  assert.equal(env.ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS, "true");
  assert.equal(env.VITE_LOCAL_PROJECTX, "true");
  assert.equal(env.SUPABASE_URL, "");
  assert.match(env.DATABASE_URL, /^sqlite\+pysqlite:/);
  assert.equal(env.TOPSIGNAL_LIVE_EXECUTION_ENABLED, "false");
  assert.equal(env.TOPSIGNAL_BOT_WORKER_ENABLED, "true");
  assert.equal(env.TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION, "false");
  assert.equal(env.PROJECTX_STREAMING_ENABLED, "false");
  const disconnected = offlineEnvironment(env, repoRoot);
  assert.equal(disconnected.PROJECTX_API_KEY, "");
  assert.equal(disconnected.ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS, "false");
  assert.equal(disconnected.TOPSIGNAL_BOT_WORKER_ENABLED, "false");
});

test("offline app boots, saves across restarts and retains normal readiness safeguards", () => {
  const python = path.join(repoRoot, "backend", ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
  const result = spawnSync(python, ["-c", `
import asyncio, json, os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlsplit

async def request(app, method, path, body=None):
    url = urlsplit(path)
    events = []
    consumed = False
    async def receive():
        nonlocal consumed
        if consumed:
            await asyncio.Event().wait()
        consumed = True
        return {'type': 'http.request', 'body': json.dumps(body).encode() if body else b'', 'more_body': False}
    async def send(event):
        events.append(event)
    scope = dict(type='http', asgi={'version': '3.0'}, http_version='1.1', method=method,
        scheme='http', path=url.path, raw_path=url.path.encode(), query_string=url.query.encode(), root_path='',
        headers=[(b'content-type', b'application/json')], client=('127.0.0.1', 1234), server=('testserver', 80))
    await app(scope, receive, send)
    status = next(e['status'] for e in events if e['type'] == 'http.response.start')
    raw = b''.join(e.get('body', b'') for e in events if e['type'] == 'http.response.body')
    return status, json.loads(raw)

with TemporaryDirectory() as directory:
    os.environ['DATABASE_URL'] = 'sqlite+pysqlite:///' + (Path(directory) / 'test.sqlite3').as_posix()
    os.environ['JOURNAL_IMAGE_STORAGE_DIR'] = directory
    from app.main import app, app_lifespan
    from app.db import engine
    from app.services.journal_storage import save_journal_image
    async def verify():
        async with app_lifespan(app):
            assert (await request(app, 'GET', '/ready'))[0] == 200
            assert (await request(app, 'GET', '/api/auth/me'))[0] == 200
            assert (await request(app, 'GET', '/api/accounts?refresh_provider=false'))[0] == 200
            status, expense = await request(app, 'POST', '/api/expenses', {'expense_date': '2026-09-07', 'amount_cents': 123, 'category': 'other', 'description': 'offline persistence check'})
            assert status == 201, expense
            expense_id = expense['id']
            save_journal_image(object_key='offline/check.png', file_bytes=b'local-test', mime_type='image/png')
            assert (Path(directory) / 'offline/check.png').read_bytes() == b'local-test'
        async with app_lifespan(app):
            _, result = await request(app, 'GET', '/api/expenses')
            assert any(row['id'] == expense_id for row in result['items'])
            os.environ['TOPSIGNAL_OFFLINE_DEV'] = '0'
            assert (await request(app, 'GET', '/ready'))[0] == 503
            os.environ['AUTH_REQUIRED'] = 'true'
            assert (await request(app, 'GET', '/api/accounts'))[0] == 401
    with patch('socket.create_connection', side_effect=AssertionError('unexpected external connection')):
        asyncio.run(verify())
    engine.dispose()
print('offline persistence and isolation passed')
`], {
    cwd: path.join(repoRoot, "backend"),
    env: offlineEnvironment(process.env, repoRoot),
    encoding: "utf8",
    timeout: 60000,
    windowsHide: true,
  });
  assert.equal(result.status, 0, result.stdout + result.stderr);
});
