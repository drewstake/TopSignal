import asyncio
import json
import os
from datetime import datetime
from unittest.mock import AsyncMock
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.services.projectx_market_price import ProjectXMarketPriceSession, stream_market_prices
from app.services.projectx_order_book import ProjectXOrderBookRegistry


CONTRACT = "CON.F.US.MNQ.U26"
NEXT_CONTRACT = "CON.F.US.MNQ.Z26"


class IdlePriceSession(ProjectXMarketPriceSession):
    def __init__(self, **kwargs):
        super().__init__(now=lambda: datetime.fromisoformat("2026-09-08T14:00:00+00:00"), **kwargs)

    async def _run_connection_loop(self):
        await asyncio.Event().wait()


def trade(price=20000.25, timestamp="2026-09-08T14:00:00Z", contract=CONTRACT):
    return {"type": 1, "target": "GatewayTrade", "arguments": [contract, {
        "symbolId": "F.US.MNQ", "price": price, "timestamp": timestamp, "volume": 2,
    }]}


def test_prices_are_scoped_to_user_and_exact_contract_with_shared_viewer_cleanup():
    async def scenario():
        registry = ProjectXOrderBookRegistry(session_factory=IdlePriceSession)
        first = await registry.subscribe(user_id="one", client=object(), contract_id=CONTRACT)
        second = await registry.subscribe(user_id="one", client=object(), contract_id=CONTRACT)
        other_user = await registry.subscribe(user_id="two", client=object(), contract_id=CONTRACT)
        other_expiry = await registry.subscribe(user_id="one", client=object(), contract_id=NEXT_CONTRACT)
        assert first.session is second.session
        assert first.session is not other_user.session
        await first.session.process_signalr_frame(trade())
        assert (await first.queue.get())["data"]["price"] == 20000.25
        assert (await second.queue.get())["data"]["contract_id"] == CONTRACT
        assert other_user.queue.empty() and other_expiry.queue.empty()
        await first.close()
        await second.session.process_signalr_frame(trade(price=20001))
        assert (await second.queue.get())["data"]["price"] == 20001
        await second.close()
        await other_expiry.close()
        assert "one" not in registry._sessions
        assert first.session._runner_task is None
        await other_user.close()
        assert registry._sessions == {}
        await registry.close()
    asyncio.run(scenario())


def test_invalid_stale_duplicate_and_old_connection_ticks_are_ignored():
    async def scenario():
        session = IdlePriceSession(client=object(), subscriber_queue_size=8)
        subscription = await session.subscribe(CONTRACT)
        await session.process_signalr_frame(trade())
        await subscription.queue.get()
        for frame in [trade(), trade(price="NaN"), trade(price=-1), trade(timestamp="bad"),
                      trade(timestamp="2026-09-08T13:59:59Z"), trade(contract=NEXT_CONTRACT)]:
            await session.process_signalr_frame(frame)
        await session.process_signalr_frame(trade(price=20001), connection_generation=99)
        assert subscription.queue.empty()
        # Queue overflow must preserve the newest price, not emit a depth snapshot.
        for price in range(20001, 20021):
            await session.process_signalr_frame(trade(price=price))
        events = []
        while not subscription.queue.empty():
            events.append(subscription.queue.get_nowait())
        assert len(events) <= 8
        assert all(event["event"] == "price" for event in events)
        assert events[-1]["data"]["price"] == 20020
        await subscription.close()
    asyncio.run(scenario())


def test_provider_uses_only_contract_trades_and_handles_subscription_acknowledgement():
    async def scenario():
        session = IdlePriceSession(client=object())
        session.set_observation_user("one")
        subscription = await session.subscribe(CONTRACT)
        # Keep another viewer active so removing this contract sends unsubscribe.
        other = await session.subscribe(NEXT_CONTRACT)
        socket = AsyncMock()
        generation = await session._activate_connection(socket)
        await session._subscribe_provider_if_active(CONTRACT)
        invocation = json.loads(socket.send.call_args.args[0].rstrip("\x1e"))
        assert invocation["target"] == "SubscribeContractTrades"
        assert invocation["arguments"] == [CONTRACT]
        assert socket.send.call_count == 1
        await session.process_signalr_frame({"type": 3, "invocationId": invocation["invocationId"]},
                                           connection_generation=generation)
        assert (await subscription.queue.get())["data"]["state"] == "connected"
        await subscription.close()
        assert json.loads(socket.send.call_args.args[0].rstrip("\x1e"))["target"] == "UnsubscribeContractTrades"
        await other.close()
    asyncio.run(scenario())


def test_sse_coalesces_prices_and_releases_subscription_on_disconnect():
    async def scenario():
        session = IdlePriceSession(client=object())
        subscription = await session.subscribe(CONTRACT)
        request = AsyncMock()
        request.is_disconnected.return_value = False
        stream = stream_market_prices(request, subscription, interval_seconds=0)
        assert (await anext(stream))["event"] == "state"
        await session.process_signalr_frame(trade(price=20000))
        await session.process_signalr_frame(trade(price=20001))
        assert (await anext(stream))["data"]["price"] == 20001
        await stream.aclose()
        assert await session.is_idle()
        assert session._runner_task is None
    asyncio.run(scenario())


def test_sse_cancellation_while_waiting_releases_subscription():
    async def scenario():
        session = IdlePriceSession(client=object())
        subscription = await session.subscribe(CONTRACT)
        request = AsyncMock()
        request.is_disconnected.return_value = False
        stream = stream_market_prices(request, subscription, interval_seconds=0)
        await anext(stream)
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        pending.cancel()
        try:
            await pending
        except asyncio.CancelledError:
            pass
        assert await session.is_idle()
        assert session._runner_task is None
    asyncio.run(scenario())


def test_route_resolves_user_credentials_and_releases_database_before_streaming(monkeypatch):
    import app.main as main

    async def scenario():
        scope = {"open": False}
        client = object()

        class DatabaseScope:
            def __enter__(self):
                scope["open"] = True
                return self

            def __exit__(self, *args):
                scope["open"] = False

        def resolve_client(db, *, user_id):
            assert scope["open"] and user_id == "viewer"
            return client

        session = IdlePriceSession(client=client)
        subscription = await session.subscribe(CONTRACT)

        async def subscribe(**kwargs):
            assert not scope["open"]
            assert kwargs == {"user_id": "viewer", "client": client, "contract_id": CONTRACT}
            return subscription

        monkeypatch.setattr(main, "get_authenticated_user_id", lambda: "viewer")
        monkeypatch.setattr(main, "SessionLocal", DatabaseScope)
        monkeypatch.setattr(main, "_projectx_client_for_user", resolve_client)
        monkeypatch.setattr(main, "_market_price_registry", SimpleNamespace(subscribe=subscribe))
        monkeypatch.setattr(main, "_streaming_runtime", None)
        request = AsyncMock()
        request.is_disconnected.return_value = False
        response = await main.stream_projectx_market_price(request, contract_id=CONTRACT, symbol="MNQ", throttle_ms=250)
        assert response.media_type == "text/event-stream"
        assert "event: state" in await anext(response.body_iterator)
        await response.body_iterator.aclose()
        assert await session.is_idle()
        assert session._runner_task is None
    asyncio.run(scenario())


_RS = "\x1e"


class _StubClient:
    def __init__(self):
        self.invalidated_tokens = []

    def get_access_token(self):
        return "server-only-projectx-token"

    def invalidate_access_token(self, token):
        self.invalidated_tokens.append(token)


class _FakeWebSocket:
    def __init__(self):
        self.sent = []
        self.incoming = asyncio.Queue()
        self.incoming.put_nowait("{}" + _RS)  # SignalR handshake acknowledgement

    async def send(self, message):
        self.sent.append(message)

    async def recv(self):
        return await self.__anext__()

    async def close(self):
        await self.incoming.put(StopAsyncIteration)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.incoming.get()
        if item is StopAsyncIteration:
            raise StopAsyncIteration
        return item


class _FakeConnector:
    def __init__(self):
        self.websockets = []

    def __call__(self, url, **_kwargs):
        websocket = _FakeWebSocket()
        self.websockets.append(websocket)

        class Context:
            async def __aenter__(self):
                return websocket

            async def __aexit__(self, *_args):
                await websocket.close()
                return False

        return Context()


async def _wait_until(predicate, *, timeout=1.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition was not reached before timeout")


def _subscribed(websocket):
    return any("SubscribeContractTrades" in raw for raw in websocket.sent)


def test_trade_delivering_connections_reset_reconnect_backoff_and_keep_credentials(monkeypatch):
    """A price hub that delivered trades was healthy; its next drop must retry promptly."""
    sleep = asyncio.sleep
    delays = []

    async def record_sleep(delay):
        if asyncio.current_task().get_name() == "projectx-market-depth":
            delays.append(delay)
        await sleep(delay)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)

    async def scenario():
        client = _StubClient()
        connector = _FakeConnector()
        session = ProjectXMarketPriceSession(
            client=client, connect_factory=connector,
            reconnect_base_seconds=0.01, reconnect_max_seconds=0.08,
            now=lambda: datetime.fromisoformat("2026-09-08T14:00:00+00:00"),
        )
        subscription = await session.subscribe(CONTRACT)
        try:
            for index in range(5):
                await _wait_until(lambda: len(connector.websockets) > index)
                websocket = connector.websockets[index]
                await _wait_until(lambda: _subscribed(websocket))
                await websocket.incoming.put(json.dumps(trade(
                    price=20000 + index, timestamp=f"2026-09-08T14:00:0{index}Z",
                )) + _RS)
                await _wait_until(lambda: any(
                    event.get("event") == "price" and event["data"]["price"] == 20000 + index
                    for event in list(subscription.queue._queue)
                ))
                await websocket.incoming.put(StopAsyncIteration)
            await _wait_until(lambda: len(delays) == 5)
            assert delays == [0.01] * 5
            assert client.invalidated_tokens == []
        finally:
            await subscription.close()
            await session.close()

    asyncio.run(scenario())


def test_long_quiet_connection_resets_backoff_without_trades(monkeypatch):
    """Overnight tape can be silent; a connection that stayed up was still healthy."""
    sleep = asyncio.sleep
    delays = []

    async def record_sleep(delay):
        if asyncio.current_task().get_name() == "projectx-market-depth":
            delays.append(delay)
        await sleep(delay)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)

    async def scenario():
        connector = _FakeConnector()
        session = ProjectXMarketPriceSession(
            client=_StubClient(), connect_factory=connector,
            reconnect_base_seconds=0.01, reconnect_max_seconds=0.08, stable_connection_seconds=0.05,
            now=lambda: datetime.fromisoformat("2026-09-08T14:00:00+00:00"),
        )
        subscription = await session.subscribe(CONTRACT)
        try:
            # Two brief, data-less connections escalate the delay ...
            for index in range(2):
                await _wait_until(lambda: len(connector.websockets) > index)
                await _wait_until(lambda: _subscribed(connector.websockets[index]))
                await connector.websockets[index].incoming.put(StopAsyncIteration)
            await _wait_until(lambda: len(delays) == 2)
            assert delays == [0.01, 0.02]
            # ... while a quiet connection that stayed up restarts the ladder.
            await _wait_until(lambda: len(connector.websockets) > 2)
            await _wait_until(lambda: _subscribed(connector.websockets[2]))
            await sleep(0.08)
            await connector.websockets[2].incoming.put(StopAsyncIteration)
            await _wait_until(lambda: len(delays) == 3)
            assert delays[-1] == 0.01
        finally:
            await subscription.close()
            await session.close()

    asyncio.run(scenario())


def test_stream_heartbeats_report_upstream_state_while_market_is_quiet():
    async def scenario():
        session = IdlePriceSession(client=object())
        subscription = await session.subscribe(CONTRACT)
        socket = AsyncMock()
        await session._activate_connection(socket)
        session._connected_since = asyncio.get_running_loop().time()
        session._last_upstream_frame_at = session._connected_since
        await session._broadcast_state("connected")
        request = AsyncMock()
        request.is_disconnected.return_value = False
        stream = stream_market_prices(request, subscription, interval_seconds=0, heartbeat_seconds=0.05)
        assert (await anext(stream))["event"] == "state"
        assert (await anext(stream))["data"]["state"] == "connected"  # queued broadcast
        heartbeat = await asyncio.wait_for(anext(stream), timeout=1)
        assert heartbeat["event"] == "heartbeat"
        data = heartbeat["data"]
        assert data["contract_id"] == CONTRACT and data["state"] == "connected"
        assert isinstance(data["upstream_idle_ms"], int) and data["upstream_idle_ms"] >= 0
        assert isinstance(data["connected_for_ms"], int)
        assert datetime.fromisoformat(data["server_time"]).tzinfo is not None
        # Heartbeats keep their cadence while prices flow; they never carry prices.
        await session.process_signalr_frame(trade(price=20002))
        assert (await anext(stream))["data"]["price"] == 20002
        assert "price" not in heartbeat["data"]
        await stream.aclose()
    asyncio.run(scenario())


def test_resubscribing_viewer_gets_the_real_last_trade_only_on_an_unbroken_connection():
    async def scenario():
        session = IdlePriceSession(client=object())
        first = await session.subscribe(CONTRACT)
        await session._activate_connection(AsyncMock())
        await session.process_signalr_frame(trade(price=20003, timestamp="2026-09-08T13:59:58Z"))
        second = await session.subscribe(CONTRACT)
        request = AsyncMock()
        request.is_disconnected.return_value = False
        stream = stream_market_prices(request, second, interval_seconds=0, heartbeat_seconds=60)
        assert (await anext(stream))["event"] == "state"
        replay = await anext(stream)
        # Provider timestamp is preserved; the viewer judges its age itself.
        assert replay["data"] == {"contract_id": CONTRACT, "symbol": "F.US.MNQ", "price": 20003,
                                  "timestamp": "2026-09-08T13:59:58+00:00"}
        await stream.aclose()
        # A provider reconnect invalidates the remembered trade.
        await session._clear_books_for_reconnect()
        assert session.last_price_event(CONTRACT) is None
        await first.close()
    asyncio.run(scenario())


def test_registry_grace_reuses_the_live_provider_session_after_a_brief_viewer_gap():
    async def scenario():
        registry = ProjectXOrderBookRegistry(session_factory=IdlePriceSession, disconnect_grace_seconds=0.2)
        first = await registry.subscribe(user_id="one", client=object(), contract_id=CONTRACT)
        session, runner = first.session, first.session._runner_task
        await first.close()  # Tab hidden / reload: the last viewer leaves.
        assert not await session.is_idle() and registry._sessions["one"] is session
        second = await registry.subscribe(user_id="one", client=object(), contract_id=CONTRACT)
        assert second.session is session and session._runner_task is runner
        await second.close()
        await _wait_until(lambda: "one" not in registry._sessions)  # Grace expiry releases it.
        assert await session.is_idle()
        await registry.close()
    asyncio.run(scenario())


def test_feed_state_logs_distinguish_price_and_trade_hubs(caplog):
    from app.services.projectx_market_trades import ProjectXMarketTradesSession

    async def scenario():
        for factory in (ProjectXMarketPriceSession, ProjectXMarketTradesSession):
            class Idle(factory):
                async def _run_connection_loop(self):
                    await asyncio.Event().wait()
            session = Idle(client=object(), now=lambda: datetime.fromisoformat("2026-09-08T14:00:00+00:00"))
            session.set_observation_user("viewer")
            subscription = await session.subscribe(CONTRACT)
            await session._broadcast_state("connected")
            await subscription.close()
    with caplog.at_level("INFO"):
        asyncio.run(scenario())
    kinds = [json.loads(record.getMessage())["stream_kind"] for record in caplog.records
             if "projectx_feed_state" in record.getMessage()]
    assert "price" in kinds and "trades" in kinds


def test_event_loop_monitor_reports_backend_stalls(caplog):
    import time
    from app.services.event_loop_monitor import monitor_event_loop_lag

    async def scenario():
        task = asyncio.create_task(monitor_event_loop_lag(
            interval_seconds=0.01, stall_threshold_seconds=0.05, report_interval_seconds=60,
        ))
        await asyncio.sleep(0.02)
        time.sleep(0.12)  # A synchronous call blocking the loop.
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    with caplog.at_level("WARNING"):
        asyncio.run(scenario())
    reports = [json.loads(record.getMessage()) for record in caplog.records
               if "event_loop_stall" in record.getMessage()]
    assert len(reports) == 1 and reports[0]["worst_lag_ms"] >= 50
