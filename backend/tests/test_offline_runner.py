import os
from pathlib import Path
import subprocess
import sys


def test_offline_runner_blocks_remote_connections_and_strips_credentials(tmp_path):
    fixture = tmp_path / "test_network_guard.py"
    fixture.write_text('''
import os
import socket
import pytest

def test_isolation():
    assert os.environ.get("PROJECTX_API_KEY") is None
    assert os.environ["PYTHON_DOTENV_DISABLED"] == "1"
    assert os.environ["DATABASE_URL"] == "sqlite+pysqlite:///:memory:"
    assert os.environ["TOPSIGNAL_LIVE_EXECUTION_ENABLED"] == "false"
    with socket.socket() as connection:
        with pytest.raises(RuntimeError, match="Offline test runner blocked"):
            connection.connect(("192.0.2.1", 443))
''', encoding="utf-8")
    runner = Path(__file__).resolve().parents[1] / "tools" / "run_offline_tests.py"
    result = subprocess.run(
        [sys.executable, str(runner), str(fixture), "-q"],
        env=dict(os.environ, PROJECTX_API_KEY="fixture-secret", TOPSIGNAL_LIVE_EXECUTION_ENABLED="true"),
        capture_output=True, text=True, timeout=15,
    )
    # Even a caught attempted connection makes the overall audit fail.
    assert result.returncode == 1
    assert "1 passed" in result.stdout
    assert "external connections blocked=1" in result.stdout
    assert "fixture-secret" not in result.stdout + result.stderr
