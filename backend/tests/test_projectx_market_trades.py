import asyncio
import json
import os
from datetime import datetime
from unittest.mock import AsyncMock
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
from app.services.projectx_market_trades import ProjectXMarketTradesSession, stream_market_trades
from app.services.projectx_order_book import ProjectXOrderBookRegistry

CONTRACT = "CON.F.US.MNQ.Z26"


class IdleTradesSession(ProjectXMarketTradesSession):
    def __init__(self, **kwargs):
        super().__init__(now=lambda: datetime.fromisoformat("2026-11-03T15:00:00+00:00"), **kwargs)

    async def _run_connection_loop(self):
        await asyncio.Event().wait()


def print_frame(entries=None, contract=CONTRACT):
    return {"type": 1, "target": "GatewayTrade", "arguments": [contract, entries or {
        "price": 20000, "volume": 2, "type": 0, "timestamp": "2026-11-03T15:00:00Z"}]}


def test_prints_preserve_equal_trades_volume_sides_and_only_deduplicate_native_ids():
    async def scenario():
        session = IdleTradesSession(client=object())
        sub = await session.subscribe(CONTRACT)
        base = print_frame()["arguments"][1]
        rows = [base, base, {**base, "type": 1}, {**base, "type": 99}, {**base, "type": True},
                {**base, "tradeId": "one"}, {**base, "tradeId": "one"}, {**base, "volume": 0},
                {**base, "price": "NaN"}, {**base, "timestamp": "2026-11-03T15:00:00"}]
        await session.process_signalr_frame(print_frame(rows))
        events = [sub.queue.get_nowait()["data"] for _ in range(sub.queue.qsize())]
        assert len(events) == 6
        assert [e["side"] for e in events] == ["buy", "buy", "sell", "unknown", "unknown", "buy"]
        assert sum(e["volume"] for e in events) == 12
        assert len({e["id"] for e in events}) == 6
        await sub.close()
    asyncio.run(scenario())


def test_user_contract_isolation_old_generation_and_shared_viewer_cleanup():
    async def scenario():
        registry = ProjectXOrderBookRegistry(session_factory=IdleTradesSession)
        a = await registry.subscribe(user_id="a", client=object(), contract_id=CONTRACT)
        a2 = await registry.subscribe(user_id="a", client=object(), contract_id=CONTRACT)
        b = await registry.subscribe(user_id="b", client=object(), contract_id=CONTRACT)
        expiry = await registry.subscribe(user_id="a", client=object(), contract_id="OTHER")
        await a.session.process_signalr_frame(print_frame(), connection_generation=99)
        assert a.queue.empty()
        await a.session.process_signalr_frame(print_frame())
        assert a.queue.qsize() == a2.queue.qsize() == 1
        assert b.queue.empty() and expiry.queue.empty()
        await a.close()
        assert not await a2.session.is_idle()
        await a2.close()
        await expiry.close()
        await b.close()
        assert not registry._sessions
    asyncio.run(scenario())


def test_bounded_queue_reports_every_dropped_print():
    async def scenario():
        session = IdleTradesSession(client=object(), subscriber_queue_size=8)
        sub = await session.subscribe(CONTRACT)
        for _ in range(30):
            await session.process_signalr_frame(print_frame())
        events = [sub.queue.get_nowait() for _ in range(sub.queue.qsize())]
        assert len(events) <= 8
        assert sum(e.get("data", {}).get("dropped", 0) for e in events) + sum(e["event"] == "trade" for e in events) == 30
        assert not any(e["event"] in {"snapshot", "price"} for e in events)
        await sub.close()
    asyncio.run(scenario())


def test_batching_preserves_all_prints_and_cancellation_releases_session():
    async def scenario():
        session = IdleTradesSession(client=object())
        sub = await session.subscribe(CONTRACT)
        request = AsyncMock()
        request.is_disconnected.return_value = False
        stream = stream_market_trades(request, sub, interval_seconds=0)
        assert (await anext(stream))["event"] == "state"
        for _ in range(300):
            await session.process_signalr_frame(print_frame())
        first, second = await anext(stream), await anext(stream)
        assert len(first["data"]["trades"]) == 256
        assert len(second["data"]["trades"]) == 44
        assert first["data"]["dropped"] == second["data"]["dropped"] == 0
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        pending.cancel()
        try:
            await pending
        except asyncio.CancelledError:
            pass
        assert await session.is_idle() and session._runner_task is None
    asyncio.run(scenario())


def test_only_trades_are_subscribed_and_research_capture_is_disabled(monkeypatch):
    async def scenario():
        session = IdleTradesSession(client=object())
        session.set_observation_user("a")
        monkeypatch.setenv("TOPSIGNAL_RAW_MARKET_CAPTURE_ENABLED", "true")
        sub = await session.subscribe(CONTRACT)
        socket = AsyncMock()
        generation = await session._activate_connection(socket)
        await session._subscribe_provider_if_active(CONTRACT)
        assert socket.send.call_count == 1
        payload = json.loads(socket.send.call_args.args[0].rstrip("\x1e"))
        assert payload["target"] == "SubscribeContractTrades"
        await session.process_signalr_frame({"type": 3, "invocationId": payload["invocationId"]}, connection_generation=generation)
        assert sub.queue.get_nowait()["data"]["state"] == "connected"
        await sub.close()
    asyncio.run(scenario())


def test_authenticated_route_releases_db_scope_before_streaming(monkeypatch):
    import app.main as main

    async def scenario():
        client = object()
        session = IdleTradesSession(client=client)
        sub = await session.subscribe(CONTRACT)
        resolved = []
        def resolve(user_id):
            resolved.append(user_id)
            return client
        async def subscribe(**kwargs):
            assert resolved == ["viewer"]
            assert kwargs == {"user_id": "viewer", "client": client, "contract_id": CONTRACT}
            return sub
        monkeypatch.setattr(main, "get_authenticated_user_id", lambda: "viewer")
        monkeypatch.setattr(main, "_stream_client_for_user", resolve)
        monkeypatch.setattr(main, "_market_trades_registry", SimpleNamespace(subscribe=subscribe))
        request = AsyncMock()
        request.is_disconnected.return_value = False
        response = await main.stream_projectx_market_trades(request, contract_id=CONTRACT)
        assert "event: state" in await anext(response.body_iterator)
        await response.body_iterator.aclose()
        assert await session.is_idle()
    asyncio.run(scenario())
