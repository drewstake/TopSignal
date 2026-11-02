import os
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
from datetime import datetime, timedelta, timezone
from fastapi import Response
from app.auth import AuthenticatedUser, bind_authenticated_user, reset_authenticated_user
from app.services.topbot_depth import DepthFeed, DepthState
from app import main

CONTRACT = "CON.F.US.MNQ.Z26"


def test_observation_is_owner_scoped_read_only_and_preserves_partial_totals():
    now = datetime.now(timezone.utc)
    feed = DepthFeed()
    state = DepthState(CONTRACT, .1)
    state.apply({"event": "snapshot", "data": {"contract_id": CONTRACT, "sequence": 1,
        "timestamp": now.isoformat(), "bids": [{"price": 20000-i*.25,"size":12} for i in range(100)],
        "asks": [{"price": 20000.25+i*.25,"size":10} for i in range(100)]}}, now)
    feed.streams[("owner-a", CONTRACT)] = {"state": state, "touched": 123}
    result = feed.observation("owner-a", CONTRACT, now=now)
    assert result["depth"]["imbalance"] == (1200-1000)/(1200+1000)
    assert result["threshold"] == .1
    assert feed.streams[("owner-a", CONTRACT)]["touched"] == 123
    assert feed.observation("owner-b", CONTRACT, now=now)["depth"]["ready"] is False
    assert feed.observation("owner-b", CONTRACT, now=now)["threshold"] is None
    assert len(feed.streams) == 1
    state.asks.pop(min(state.asks))
    partial = feed.observation("owner-a", CONTRACT, now=now)["depth"]
    assert partial["ask_levels"] == 99 and partial["ask_total"] == 990 and partial["ready"]
    stale = feed.observation("owner-a", CONTRACT, now=now+timedelta(seconds=3))["depth"]
    assert not stale["ready"] and "imbalance" not in stale


def test_endpoint_uses_authenticated_owner_without_database_dependency(monkeypatch):
    import app.services.topbot_depth as module
    calls = []
    monkeypatch.setattr(module.feed, "observation", lambda owner, contract: calls.append((owner, contract)) or {"depth": {"ready": False}})
    token = bind_authenticated_user(AuthenticatedUser("owner-a", None, {}))
    try:
        response = Response()
        assert main.get_topbot_depth(response, CONTRACT) == {"depth": {"ready": False}}
        assert calls == [("owner-a", CONTRACT)]
        assert response.headers["cache-control"] == "no-store"
        route = next(r for r in main.app.routes if getattr(r, "path", None) == "/api/topbot/depth")
        assert route.dependant.dependencies == []  # No get_db / broker client / statistics query.
        assert main._is_authenticated_route(route.path)
    finally:
        reset_authenticated_user(token)
