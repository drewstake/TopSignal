"""Offline test defaults independent of a developer's trading environment."""
import os
import pytest


@pytest.fixture(autouse=True)
def isolated_action_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("TOPSIGNAL_TOPBOT_AUDIT_DIR", str(tmp_path / "action-audit"))

# Set before application imports (.env never overrides existing variables).
for name in ("TOPSIGNAL_LIVE_EXECUTION_ENABLED", "TOPSIGNAL_BOT_WORKER_ENABLED",
             "TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION"):
    os.environ[name] = "false"
# Existing strategy characterization tests deliberately exercise the opt-in
# legacy implementation. Retirement-gate tests explicitly turn this off.
os.environ["TOPSIGNAL_ENABLE_LEGACY_STRATEGIES"] = "true"
