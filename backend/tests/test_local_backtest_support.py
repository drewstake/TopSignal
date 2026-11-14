from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_local_cache_runtime_dependencies_are_pinned():
    requirements = (REPO_ROOT / "backend" / "requirements.txt").read_text(
        encoding="utf-8"
    ).splitlines()

    assert "numpy==2.2.6" in requirements
    assert "pyarrow==25.0.0" in requirements


def test_local_cache_environment_defaults_are_documented_for_authoritative_host():
    template = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    expected = {
        "TOPSIGNAL_DATABENTO_CACHE_DIR=D:/TopSignal/data/databento": 1,
        "TOPSIGNAL_BACKTEST_CACHE_MAX_ENTRIES=8": 1,
        "TOPSIGNAL_BACKTEST_CACHE_MAX_BYTES=536870912": 1,
        "TOPSIGNAL_BACKTEST_RESULT_CACHE_MAX_ENTRIES=8": 1,
        "TOPSIGNAL_BACKTEST_RESULT_CACHE_MAX_BYTES=268435456": 1,
    }

    for setting, profile_count in expected.items():
        assert template.count(setting) == profile_count
