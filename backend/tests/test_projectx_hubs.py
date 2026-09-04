import asyncio
import logging
import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.services.projectx_hubs as projectx_hubs_module
from app.services.projectx_hubs import ProjectXHubRunner, _append_query, _signalr_handshake
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


def test_hub_disconnect_log_does_not_expose_bearer_token(monkeypatch, caplog):
    fixture_token = "sensitive-fixture-bearer-token"

    class StubClient:
        def get_access_token(self):
            return fixture_token

    def fail_connect(url, **_kwargs):
        raise RuntimeError(f"failed to connect to {url}")

    async def stop_after_first_failure(_delay):
        raise asyncio.CancelledError()

    runner = ProjectXHubRunner(
        tracker=StreamingPnlTracker(),
        client_factory=StubClient,
        market_hub_url="wss://example.test/hubs/market",
        user_hub_url="",
    )
    monkeypatch.setattr(projectx_hubs_module.websockets, "connect", fail_connect)
    monkeypatch.setattr(projectx_hubs_module.asyncio, "sleep", stop_after_first_failure)
    caplog.set_level(logging.WARNING, logger=projectx_hubs_module.logger.name)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            runner._consume_hub(
                "market",
                "wss://example.test/hubs/market",
            )
        )

    assert fixture_token not in caplog.text
    assert "error_type=RuntimeError" in caplog.text


def test_user_hub_disconnect_invalidates_classification(monkeypatch):
    invalidations = []

    class StubClient:
        def get_access_token(self):
            return "fixture-token"

    def fail_connect(_url, **_kwargs):
        raise ConnectionError("offline")

    async def stop_after_failure(_delay):
        raise asyncio.CancelledError()

    runner = ProjectXHubRunner(
        tracker=StreamingPnlTracker(owner_user_id="user-a", owner_account_id=101),
        client_factory=StubClient,
        user_id="user-a",
        account_id=101,
        market_hub_url="",
        user_hub_url="wss://example.test/hubs/user",
        on_user_account=lambda _payload: None,
        on_user_disconnect=lambda: invalidations.append(True),
    )
    monkeypatch.setattr(projectx_hubs_module.websockets, "connect", fail_connect)
    monkeypatch.setattr(projectx_hubs_module.asyncio, "sleep", stop_after_failure)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runner._consume_hub("user", "wss://example.test/hubs/user"))

    assert invalidations == [True]


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


def test_gateway_user_account_is_not_misrouted_as_a_position():
    observed = []
    tracker = StreamingPnlTracker(owner_user_id="user-a", owner_account_id=101)
    runner = ProjectXHubRunner(
        tracker=tracker,
        client_factory=_unused_client_factory,
        user_id="user-a",
        account_id=101,
        market_hub_url="",
        user_hub_url="wss://example.test/hubs/user",
        on_user_account=observed.append,
    )

    runner._dispatch_frame(
        "user",
        {
            "type": 1,
            "target": "GatewayUserAccount",
            "arguments": [{"id": 101, "simulated": False}],
        },
    )

    assert observed == [{"id": 101, "simulated": False}]
    assert tracker.position_by_scope == {}


def test_signalr_handshake_waits_for_ack_before_subscriptions():
    class Websocket:
        def __init__(self):
            self.sent = []

        async def send(self, message):
            self.sent.append(message)

        async def recv(self):
            return '{}\x1e{"type":1,"target":"GatewayQuote","arguments":[]}\x1e'

    websocket = Websocket()
    frames = asyncio.run(_signalr_handshake(websocket))

    assert websocket.sent == ['{"protocol": "json", "version": 1}\x1e']
    assert frames == [{"type": 1, "target": "GatewayQuote", "arguments": []}]


@pytest.mark.parametrize("response", ["not-json\x1e", "[]\x1e", '{"error":"denied"}\x1e'])
def test_signalr_handshake_rejects_malformed_or_error_response(response):
    class Websocket:
        async def send(self, _message):
            return None

        async def recv(self):
            return response

    with pytest.raises(ConnectionError, match="handshake"):
        asyncio.run(_signalr_handshake(Websocket()))


def test_gateway_user_account_ignores_another_account_in_same_user_snapshot():
    observed = []
    runner = ProjectXHubRunner(
        tracker=StreamingPnlTracker(owner_user_id="user-a", owner_account_id=101),
        client_factory=_unused_client_factory,
        user_id="user-a",
        account_id=101,
        market_hub_url="",
        user_hub_url="wss://example.test/hubs/user",
        on_user_account=observed.append,
    )

    runner._dispatch_frame(
        "user",
        {
            "type": 1,
            "target": "GatewayUserAccount",
            "arguments": [{"id": 202, "simulated": True}],
        },
    )

    assert observed == []


def test_user_account_refresh_resubscribes_before_classification_ttl(monkeypatch):
    sent = []

    class Websocket:
        async def send(self, message):
            sent.append(message)

    calls = 0

    async def one_interval(_seconds):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise asyncio.CancelledError()

    runner = ProjectXHubRunner(
        tracker=StreamingPnlTracker(owner_user_id="user-a", owner_account_id=101),
        client_factory=_unused_client_factory,
        user_id="user-a",
        account_id=101,
        market_hub_url="",
        user_hub_url="wss://example.test/hubs/user",
        on_user_account=lambda _payload: None,
    )
    monkeypatch.setattr(projectx_hubs_module.asyncio, "sleep", one_interval)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runner._refresh_user_account_loop(Websocket()))

    assert any("UnsubscribeAccounts" in message for message in sent)
    assert any("SubscribeAccounts" in message for message in sent)


def test_default_user_hub_subscribes_to_all_account_scoped_streams():
    messages = projectx_hubs_module._default_user_subscription_messages(101)

    assert messages == [
        {"type": 1, "target": "SubscribeAccounts", "arguments": []},
        {"type": 1, "target": "SubscribePositions", "arguments": [101]},
        {"type": 1, "target": "SubscribeOrders", "arguments": [101]},
        {"type": 1, "target": "SubscribeTrades", "arguments": [101]},
    ]


def test_one_shot_account_probe_closes_without_disconnect_invalidation(monkeypatch):
    sent = []
    invalidations = []

    class Client:
        def get_access_token(self):
            return "fixture-token"

    class Websocket:
        def __init__(self):
            self.messages = [
                "{}\x1e",
                (
                    '{"type":1,"target":"GatewayUserAccount",'
                    '"arguments":[{"id":101,"simulated":true}]}\x1e'
                ),
            ]

        async def send(self, message):
            sent.append(message)

        async def recv(self):
            return self.messages.pop(0)

    websocket = Websocket()

    class Connection:
        async def __aenter__(self):
            return websocket

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(
        projectx_hubs_module.websockets,
        "connect",
        lambda *_args, **_kwargs: Connection(),
    )
    observed = []
    runner = ProjectXHubRunner(
        tracker=StreamingPnlTracker(owner_user_id="user-a", owner_account_id=101),
        client_factory=Client,
        user_id="user-a",
        account_id=101,
        market_hub_url="",
        user_hub_url="wss://example.test/hubs/user",
        on_user_account=observed.append,
        on_user_disconnect=lambda: invalidations.append(True),
    )

    result = asyncio.run(runner.probe_user_account_once(timeout_seconds=1))

    assert result == {"id": 101, "simulated": True}
    assert observed == [{"id": 101, "simulated": True}]
    assert invalidations == []
    assert any("SubscribeAccounts" in message for message in sent)
    assert not any(
        target in message
        for message in sent
        for target in ("SubscribePositions", "SubscribeOrders", "SubscribeTrades")
    )


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
