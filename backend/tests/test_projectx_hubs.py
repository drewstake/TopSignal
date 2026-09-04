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


@pytest.mark.parametrize("url", ["ws://example.test/hub", "https://user:secret@example.test/hub", "wss://example.test/hub?token=secret"])
def test_hub_refuses_insecure_or_credential_bearing_endpoint(url):
    with pytest.raises(Exception, match="endpoint must use TLS"):
        ProjectXHubRunner(tracker=StreamingPnlTracker(), client_factory=_unused_client_factory,
                          market_hub_url=url, user_hub_url="")


def test_hub_connector_disables_redirects(monkeypatch):
    from types import SimpleNamespace

    connection = SimpleNamespace(process_redirect=lambda _exc: "wss://other.example.test")
    monkeypatch.setattr(projectx_hubs_module.websockets, "connect", lambda *_args, **_kwargs: connection)
    result = projectx_hubs_module._open_hub("wss://example.test/hub")
    redirect_failure = RuntimeError("fixture redirect")
    assert result.process_redirect(redirect_failure) is redirect_failure


@pytest.mark.parametrize("account_id", [101.5, True, float("inf"), float("nan")])
def test_account_classification_never_coerces_fractional_or_invalid_account_id(account_id):
    observed = []
    runner = ProjectXHubRunner(
        tracker=StreamingPnlTracker(), client_factory=_unused_client_factory,
        user_id="fixture", account_id=101, market_hub_url="", on_user_account=observed.append,
    )
    runner._dispatch_user_account({"id": account_id, "simulated": True})
    assert observed == []


def test_flapping_hub_uses_exponential_backoff_after_successful_handshake(monkeypatch):
    class Client:
        def get_access_token(self):
            return "fixture-token"

    class Websocket:
        async def send(self, _message):
            pass

        async def recv(self):
            return "{}\x1e"

    class Connection:
        async def __aenter__(self):
            return Websocket()

        async def __aexit__(self, *_args):
            return False

    delays = []

    async def disconnected(*_args):
        raise ConnectionError("fixture flapping")

    async def backoff(delay):
        delays.append(delay)
        if len(delays) == 3:
            raise asyncio.CancelledError()

    runner = ProjectXHubRunner(tracker=StreamingPnlTracker(), client_factory=Client,
                              market_hub_url="wss://example.test/hub", user_hub_url="")
    monkeypatch.setattr(projectx_hubs_module, "_open_hub", lambda *_args, **_kwargs: Connection())
    monkeypatch.setattr(projectx_hubs_module.asyncio, "sleep", backoff)
    monkeypatch.setattr(runner, "_receive_messages", disconnected)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runner._consume_hub("market", "wss://example.test/hub"))
    assert delays == [1, 2, 4]


def test_cancelling_hub_reaps_receiver_and_refresh_tasks(monkeypatch):
    class Client:
        def get_access_token(self):
            return "fixture-token"

    class Websocket:
        async def send(self, _message):
            pass

        async def recv(self):
            return "{}\x1e"

    class Connection:
        async def __aenter__(self):
            return Websocket()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(projectx_hubs_module.websockets, "connect", lambda *_args, **_kwargs: Connection())
    runner = ProjectXHubRunner(
        tracker=StreamingPnlTracker(), client_factory=Client,
        user_id="fixture", account_id=101, market_hub_url="",
        on_user_account=lambda _payload: None,
    )

    async def exercise():
        entered = [asyncio.Event(), asyncio.Event()]
        exited = []

        async def child(index):
            entered[index].set()
            try:
                await asyncio.Event().wait()
            finally:
                exited.append(index)

        monkeypatch.setattr(runner, "_receive_messages", lambda *_args: child(0))
        monkeypatch.setattr(runner, "_refresh_user_account_loop", lambda *_args: child(1))
        parent = asyncio.create_task(runner._consume_hub("user", "wss://example.test/hub"))
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), timeout=2)
        parent.cancel()
        await asyncio.gather(parent, return_exceptions=True)
        assert sorted(exited) == [0, 1]
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(exercise())


def test_failing_hub_reaps_its_sibling_consumer(monkeypatch):
    runner = ProjectXHubRunner(
        tracker=StreamingPnlTracker(), client_factory=_unused_client_factory,
        user_id="fixture", account_id=101,
        market_hub_url="wss://example.test/market", user_hub_url="wss://example.test/user",
    )

    async def exercise():
        started = asyncio.Event()
        exited = []

        async def consume(kind, _url):
            if kind == "market":
                await started.wait()
                raise RuntimeError("fixture failure")
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                exited.append(True)

        monkeypatch.setattr(runner, "_consume_hub", consume)
        with pytest.raises(RuntimeError, match="fixture failure"):
            await runner.run_forever()
        assert exited == [True]
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(exercise())


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
