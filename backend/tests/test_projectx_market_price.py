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
