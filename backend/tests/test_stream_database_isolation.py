"""Slow credential DB reads must not monopolize the async server loop."""
import asyncio
import os
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
import app.main as main


@pytest.mark.parametrize("route", [main.stream_projectx_market_price, main.stream_projectx_market_depth])
def test_slow_database_keeps_event_loop_responsive_and_session_scoped(monkeypatch, route):
    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()
    thread_ids = []
    client = object()
    class Database:
        def __enter__(self):
            thread_ids.append(threading.get_ident())
            return self
        def __exit__(self, *args):
            thread_ids.append(threading.get_ident())
            closed.set()
    def credentials(db, *, user_id):
        assert user_id == "viewer"
        entered.set()
        assert release.wait(2), "event loop was blocked by the synchronous DB wait"
        return client
    async def subscribe(**kwargs):
        assert closed.is_set()
        assert kwargs["client"] is client and kwargs["user_id"] == "viewer"
        return SimpleNamespace(initial_events=[], queue=asyncio.Queue(), close=AsyncMock())
    monkeypatch.setattr(main, "SessionLocal", Database)
    monkeypatch.setattr(main, "_projectx_client_for_user", credentials)
    monkeypatch.setattr(main, "get_authenticated_user_id", lambda: "viewer")
    monkeypatch.setattr(main, "_market_price_registry", SimpleNamespace(subscribe=subscribe))
    monkeypatch.setattr(main, "_order_book_registry", SimpleNamespace(subscribe=subscribe))
    async def scenario():
        kwargs = {"contract_id": "fixture"}
        if route is main.stream_projectx_market_price:
            kwargs.update(symbol="MNQ", throttle_ms=250)
        pending = asyncio.create_task(route(AsyncMock(), **kwargs))
        try:
            for _ in range(100):
                if entered.is_set(): break
                await asyncio.sleep(0.005)
            assert entered.is_set()
            assert not pending.done()
            assert threading.get_ident() != thread_ids[0]
        finally:
            release.set()
        response = await asyncio.wait_for(pending, 1)
        assert response.media_type == "text/event-stream"
        assert closed.is_set() and thread_ids[0] == thread_ids[1]
    asyncio.run(scenario())
