"""Run the backend suite without loading operator credentials or broker networking.

Usage from the repository root: backend/.venv/Scripts/python backend/tools/run_offline_tests.py
Loopback sockets remain available for asyncio and local test servers. PostgreSQL
integration tests require a separately provisioned disposable database and are
deliberately excluded from this offline command.
"""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import sys


def main() -> int:
    for name in tuple(os.environ):
        if name.startswith(("PROJECTX_", "TOPSTEP_", "TOPSTEPX_", "DATABENTO_", "SUPABASE_", "GEMINI_", "TOPSIGNAL_")) or name in {
            "DATABASE_URL", "CREDENTIALS_ENCRYPTION_KEY", "AUTH_REQUIRED",
            "ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS", "ALLOW_INSECURE_LOCAL_CREDENTIALS_KEY",
            "MIGRATION_DATABASE_URL", "ALLOW_QUERY_BEARER_TOKENS", "DEFAULT_USER_ID",
        }:
            os.environ.pop(name, None)
    os.environ.update(
        PYTHON_DOTENV_DISABLED="1",
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        AUTH_REQUIRED="true",
        TOPSIGNAL_DB_SCHEMA_INIT="skip",
        TOPSIGNAL_LIVE_EXECUTION_ENABLED="false",
        TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION="false",
        TOPSIGNAL_BOT_WORKER_ENABLED="false",
    )
    blocked_connections: list[str] = []

    def deny_external_network(event: str, args: tuple) -> None:
        if event != "socket.connect":
            return
        address = args[1]
        if isinstance(address, tuple):
            try:
                if ipaddress.ip_address(address[0]).is_loopback:
                    return
            except ValueError:
                pass
        elif isinstance(address, (str, bytes)):
            # Unix domain sockets cannot route to a remote broker.
            return
        blocked_connections.append("external_connection_blocked")
        raise RuntimeError("Offline test runner blocked an external network connection")

    sys.addaudithook(deny_external_network)
    backend = Path(__file__).resolve().parents[1]
    os.chdir(backend)
    sys.path.insert(0, str(backend))
    import pytest

    result = int(pytest.main(sys.argv[1:] or ["tests", "-q", "-ra"]))
    print(f"Offline guard: external connections blocked={len(blocked_connections)}; dotenv disabled; live gates disabled")
    return result if not blocked_connections else 1


if __name__ == "__main__":
    raise SystemExit(main())
