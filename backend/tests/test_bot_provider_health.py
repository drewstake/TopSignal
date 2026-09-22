from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace

import app.services.bot_provider_health as health_module
from app.services.bot_provider_health import BotProviderHealth
from app.services.projectx_client import ProjectXClientError


def test_success_is_scoped_to_user_and_cached():
    calls = []

    def factory(user):
        def accounts(**kwargs):
            calls.append((user, kwargs))
            if user == "b":
                raise ProjectXClientError("private credential detail", status_code=401)
            return []
        return SimpleNamespace(timeout_seconds=20, list_accounts=accounts)

    health = BotProviderHealth(factory, interval=60)
    assert health.refresh("a", wait=True)["status"] == "ok"
    assert health.refresh("b", wait=True)["status"] == "error"
    assert health.refresh("a", wait=True)["status"] == "ok"
    assert health.inspect("c")["status"] == "unknown"
    assert calls == [("a", {"only_active_accounts": False}), ("b", {"only_active_accounts": False})]
    assert "private" not in str(health.inspect("b"))


def test_expired_success_requires_a_new_probe():
    calls = []
    client = SimpleNamespace(timeout_seconds=20, list_accounts=lambda **kwargs: calls.append(kwargs))
    health = BotProviderHealth(lambda user: client, interval=60)
    assert health.refresh("a", wait=True)["status"] == "ok"
    health._users["a"].checked_at -= timedelta(seconds=121)
    health._users["a"].retry_at -= timedelta(seconds=121)
    assert health.inspect("a")["status"] == "stale"
    assert health.refresh("a", wait=True)["status"] == "ok"
    assert len(calls) == 2


def test_concurrent_refreshes_share_one_probe_and_late_success_cannot_authorize(monkeypatch):
    entered, release = Event(), Event()
    calls = []

    def accounts(**kwargs):
        calls.append(kwargs)
        entered.set()
        assert release.wait(2)

    health = BotProviderHealth(
        lambda user: SimpleNamespace(timeout_seconds=20, list_accounts=accounts), interval=60,
    )
    try:
        assert health.refresh("a")["status"] == "checking"
        assert entered.wait(1)
        assert health.refresh("a")["status"] == "checking"
        observation = health._users["a"]
        monkeypatch.setattr(health_module, "monotonic", lambda: observation.deadline + 1)
        assert health.refresh("a", wait=True)["status"] == "timeout"
        release.set()
        assert observation.done.wait(1)
        assert health.inspect("a")["status"] == "timeout"
        assert health.refresh("a", wait=True)["status"] == "timeout"
        assert len(calls) == 1
    finally:
        release.set()


def test_rate_limit_retry_after_survives_normal_freshness_expiry():
    calls = []

    def accounts(**kwargs):
        calls.append(kwargs)
        raise ProjectXClientError("slow down", status_code=429, retry_after_seconds=300)

    health = BotProviderHealth(
        lambda user: SimpleNamespace(timeout_seconds=20, list_accounts=accounts), interval=60,
    )
    result = health.refresh("a", wait=True)
    assert result["status"] == "throttled"
    assert datetime.fromisoformat(result["retry_at"]) >= datetime.now(timezone.utc) + timedelta(seconds=299)
    health._users["a"].checked_at -= timedelta(seconds=121)
    assert health.refresh("a", wait=True)["status"] == "throttled"
    assert len(calls) == 1


def test_hung_probes_have_a_global_concurrency_limit():
    release = Event()
    health = BotProviderHealth(
        lambda user: SimpleNamespace(timeout_seconds=20, list_accounts=lambda **kwargs: release.wait(2)),
        interval=60,
    )
    try:
        for user in range(4):
            assert health.refresh(str(user))["status"] == "checking"
        assert health.refresh("overflow")["status"] == "unknown"
        assert sum(value.in_flight for value in health._users.values()) == 4
    finally:
        release.set()
        for observation in health._users.values():
            if observation.in_flight:
                assert observation.done.wait(1)
