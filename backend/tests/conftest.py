"""Offline test defaults independent of a developer's trading environment."""
import os
import pytest


if os.name == "nt":
    @pytest.fixture
    def tmp_path(tmp_path_factory):
        # Owner/artifact hashes already consume 128 characters. Pytest's full
        # test-name directory can push these fixtures beyond Windows MAX_PATH.
        # Numbered directories remain unique and use pytest's normal retention.
        return tmp_path_factory.mktemp("t")


@pytest.fixture(autouse=True)
def isolated_action_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("TOPSIGNAL_TOPBOT_AUDIT_DIR", str(tmp_path / "action-audit"))


@pytest.fixture(autouse=True)
def closed_archive_writers():
    # The archive keeps writer connections open between transactions; release
    # them so temporary directories can be removed (required on Windows).
    yield
    import sys
    module = sys.modules.get("app.services.market_archive")
    if module is not None:
        module.close_writers()

# Set before application imports (.env never overrides existing variables).
for name in ("TOPSIGNAL_LIVE_EXECUTION_ENABLED", "TOPSIGNAL_BOT_WORKER_ENABLED",
             "TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION"):
    os.environ[name] = "false"
# Existing strategy characterization tests deliberately exercise the opt-in
# legacy implementation. Retirement-gate tests explicitly turn this off.
os.environ["TOPSIGNAL_ENABLE_LEGACY_STRATEGIES"] = "true"
