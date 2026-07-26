import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.services.projectx_hubs import ProjectXHubRunner, _append_query
from app.services.streaming_pnl_tracker import StreamingPnlTracker


def _unused_client_factory():
    raise AssertionError("unit dispatch tests must not create a provider client")


def test_market_gateway_quote_dispatch_preserves_contract_id_argument():
    tracker = StreamingPnlTracker()
    runner = ProjectXHubRunner(
        tracker=tracker,
        client_factory=_unused_client_factory,
        market_hub_url="wss://example.test/hubs/market",
        user_hub_url="",
    )

    runner._dispatch_frame(
        "market",
        {
            "type": 1,
            "target": "GatewayQuote",
            "arguments": [
                "CON.F.US.MNQ.H26",
                {
                    "symbol": "F.US.MNQ",
                    "lastPrice": 17425.25,
                    "timestamp": "2026-03-01T12:01:00Z",
                },
            ],
        },
    )

    update = tracker.get_market_price_update(contract_id="CON.F.US.MNQ.H26")

    assert update is not None
    assert update.contract_id == "CON.F.US.MNQ.H26"
    assert update.mark_price == 17425.25


def test_append_query_normalizes_documented_https_hub_url_to_wss():
    assert (
        _append_query("https://rtc.topstepx.com/hubs/market", {"access_token": "token"})
        == "wss://rtc.topstepx.com/hubs/market?access_token=token"
    )


def test_dispatch_circuit_isolates_repeated_tracker_failures():
    class FailingTracker(StreamingPnlTracker):
        def __init__(self):
            super().__init__()
            self.market_calls = 0

        def ingest_market_event(self, payload):
            self.market_calls += 1
            raise ValueError("bad market payload")

    tracker = FailingTracker()
    runner = ProjectXHubRunner(
        tracker=tracker,
        client_factory=_unused_client_factory,
        market_hub_url="wss://example.test/hubs/market",
        user_hub_url="",
        dispatch_failure_threshold=2,
        dispatch_recovery_seconds=60,
    )

    runner._dispatch_payload("market", {"bad": True})
    runner._dispatch_payload("market", {"bad": True})
    runner._dispatch_payload("market", {"bad": True})

    health = runner.dispatch_health()["market"]
    assert tracker.market_calls == 2
    assert health["state"] == "open"
    assert health["total_failures"] == 2
    assert health["skipped_dispatches"] == 1


def test_user_hub_dispatch_is_scoped_to_explicit_owner():
    contract_id = "CON.F.US.MNQ.H26"
    tracker = StreamingPnlTracker(owner_user_id="user-a", owner_account_id=101)
    runner = ProjectXHubRunner(
        tracker=tracker,
        client_factory=_unused_client_factory,
        user_id="user-a",
        account_id=101,
        user_hub_url="wss://example.test/hubs/user",
    )

    runner._dispatch_payload(
        "user",
        {
            "accountId": 101,
            "contractId": contract_id,
            "netQty": 1,
            "avgPrice": 100.0,
            "updatedAt": "2026-03-01T12:01:00Z",
        },
    )

    assert ("user-a", 101, contract_id) in tracker.position_by_scope


def test_user_hub_refuses_process_global_unscoped_configuration():
    with pytest.raises(ValueError, match="explicit user_id and account_id"):
        ProjectXHubRunner(
            tracker=StreamingPnlTracker(),
            client_factory=_unused_client_factory,
            user_hub_url="wss://example.test/hubs/user",
        )


def test_hub_runner_refuses_an_implicit_environment_credential_factory():
    with pytest.raises(TypeError):
        ProjectXHubRunner(  # type: ignore[call-arg]
            tracker=StreamingPnlTracker(),
            market_hub_url="wss://example.test/hubs/market",
            user_hub_url="",
        )
