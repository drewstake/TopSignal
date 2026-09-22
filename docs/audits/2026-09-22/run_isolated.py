"""Launch audit commands with no inherited application secrets or cloud data.

Usage: backend/.venv/bin/python docs/audits/2026-09-22/run_isolated.py MODE
Modes: backend-tests, frontend-tests, frontend-build, frontend-lint, backend, frontend.
Persistent audit fixture data is under /tmp/topsignal-audit-20260922.
"""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
DATA = Path("/tmp/topsignal-audit-20260922")
DATA.mkdir(exist_ok=True)
env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "LANG", "SYSTEMROOT") if k in os.environ}
env.update({
    "PYTHON_DOTENV_DISABLED": "1",
    "PYTHONPATH": f"{HERE}{os.pathsep}{ROOT / 'backend'}",
    "DATABASE_URL": f"sqlite+pysqlite:///{DATA / 'audit.sqlite3'}",
    "AUTH_REQUIRED": "false", "TOPSIGNAL_ENV": "development",
    "TOPSIGNAL_OFFLINE_DEV": "1", "TOPSIGNAL_LOCAL_PROJECTX": "0",
    "TOPSIGNAL_DB_SCHEMA_INIT": "full",
    "TOPSIGNAL_BOT_WORKER_ENABLED": "false",
    "TOPSIGNAL_LIVE_EXECUTION_ENABLED": "false",
    "TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION": "false",
    "PROJECTX_STREAMING_ENABLED": "false",
    "ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS": "false",
    "ALLOWED_ORIGINS": "http://127.0.0.1:5185",
    "JOURNAL_IMAGE_STORAGE_BACKEND": "local",
    "JOURNAL_IMAGE_STORAGE_DIR": str(DATA / "images"),
    "TOPSIGNAL_DATABENTO_CACHE_DIR": str(DATA / "empty-history"),
    "VITE_API_BASE_URL": "http://127.0.0.1:8015",
    "VITE_SUPABASE_URL": "", "VITE_SUPABASE_ANON_KEY": "",
    "VITE_OFFLINE_MODE": "true", "VITE_LOCAL_PROJECTX": "false",
    "VITE_LOCAL_LIVE_ORDERS": "false", "VITE_DEMO_MODE": "false",
})
python = str(ROOT / "backend/.venv/bin/python")
mode = sys.argv[1]
if mode == "frontend-tests-compatible":
    env["NODE_OPTIONS"] = "--no-experimental-webstorage"
    mode = "frontend-tests"
if mode == "frontend-tests":
    env["VITE_OFFLINE_MODE"] = "false"
    env["VITE_API_BASE_URL"] = "http://127.0.0.1:8000"
commands = {
    "backend-tests-standard": ([python, "tools/run_offline_tests.py", "tests", "-q", "--tb=short"], ROOT / "backend"),
    "backend-tests": ([python, "-m", "pytest", "tests", "-q", "--tb=short"], ROOT / "backend"),
    "frontend-tests": (["npm", "run", "test", "--", "--reporter=dot"], ROOT / "frontend"),
    "frontend-build": (["npm", "run", "build"], ROOT / "frontend"),
    "frontend-lint": (["npm", "run", "lint"], ROOT / "frontend"),
    "backend": ([python, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8015"], ROOT / "backend"),
    "frontend": (["node", "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", "5185", "--strictPort"], ROOT / "frontend"),
}
if mode == "backend-tests":
    env["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
if mode == "backend-tests-standard":
    # The repository runner installs its own audit hook and resets test defaults.
    env["PYTHONPATH"] = str(ROOT / "backend")
command, cwd = commands[mode]
sys.exit(subprocess.call(command + sys.argv[2:], cwd=cwd, env=env))
