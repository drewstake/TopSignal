import os
from pathlib import Path
import subprocess
import sys
import pytest

from tools.run_offline_tests import portable_pytest_arguments


def test_windows_test_root_is_unique_short_and_only_owned_data_is_cleaned(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.run_offline_tests.tempfile.gettempdir", lambda: str(tmp_path))
    sentinel = tmp_path / "keep.txt"
    sentinel.write_text("pre-existing data")
    roots = []
    for _ in range(2):
        with portable_pytest_arguments(["tests", "-q"], platform="win32") as args:
            root = Path(args[-1])
            roots.append(root)
            assert args[:-2] == ["tests", "-q"] and args[-2] == "--basetemp"
            assert root.parent == tmp_path.resolve() and len(root.name) <= 12
            (root / "fixture").mkdir()
            (root / "fixture" / "result.json").write_text("{}")
        assert not root.exists()
    assert roots[0] != roots[1]
    assert sentinel.read_text() == "pre-existing data"


@pytest.mark.parametrize("joined", [False, True])
def test_explicit_pytest_root_is_not_replaced_or_cleaned(tmp_path, joined):
    sentinel = tmp_path / "keep.txt"
    sentinel.write_text("keep")
    args = ["tests"] + ([f"--basetemp={tmp_path}"] if joined else ["--basetemp", str(tmp_path)])
    with portable_pytest_arguments(args, platform="win32") as resolved:
        assert resolved == args
    assert sentinel.read_text() == "keep"


def test_non_windows_pytest_defaults_are_unchanged():
    with portable_pytest_arguments(["tests", "-q"], platform="linux") as args:
        assert args == ["tests", "-q"]


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
