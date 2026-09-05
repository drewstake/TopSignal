import asyncio
import json
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.market_observation_models import DecisionResearchSnapshot, MarketObservation
from app.models import Account, BotDecision, BotOrderAttempt, ProjectXMarketCandle, ProjectXTradeEvent
from app.services import market_observations as market
from app.services.decision_research import evaluate_pending, execution_summary, label_barriers, research_status, stage_decision_snapshot, stage_routing_disposition
from app.services.projectx_order_book import MarketByPriceBook, ProjectXMarketDepthSession, _ContractChannel

USER = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"
CONTRACT = "CON.F.US.MNQ.U26"
NOW = datetime.now(timezone.utc).replace(second=15, microsecond=0)


def request(app, path):
    url = urlsplit(path)
    events = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(event):
        events.append(event)
    scope = dict(type="http", asgi={"version": "3.0"}, http_version="1.1", method="GET", scheme="http",
        path=url.path, raw_path=url.path.encode(), query_string=url.query.encode(), root_path="", headers=[],
        client=("127.0.0.1", 1), server=("testserver", 80))
    asyncio.run(app(scope, receive, send))
    status = next(item["status"] for item in events if item["type"] == "http.response.start")
    body = b"".join(item.get("body", b"") for item in events if item["type"] == "http.response.body")
    return SimpleNamespace(status_code=status, json=lambda: json.loads(body))


@pytest.fixture
def database(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    tables = [MarketObservation.__table__, DecisionResearchSnapshot.__table__, Account.__table__, BotDecision.__table__,
              BotOrderAttempt.__table__, ProjectXTradeEvent.__table__, ProjectXMarketCandle.__table__]
    Base.metadata.create_all(engine, tables=tables)
    factory = sessionmaker(bind=engine)
    recorder = market.ObservationWriter(factory, auto_start=False, capacity=4, record_cap=3, retention_days=3)
    monkeypatch.setattr(market, "writer", recorder)
    monkeypatch.setenv("TOPSIGNAL_MARKET_CAPTURE_ENABLED", "true")
    yield factory, recorder
    engine.dispose()


def depth(second=0, *, kind=2, price=100, volume=5):
    return {"type": kind, "timestamp": (NOW + timedelta(seconds=second)).isoformat(), "price": price, "volume": volume}


def stage(db, *, action="BUY", decision_id=1, observed=NOW):
    signal = SimpleNamespace(raw_payload={"entry_price": 100, "stop_loss": 95, "take_profit": 105, "api_key": "must-not-store"})
    decision = SimpleNamespace(id=decision_id, user_id=USER, account_id=123, contract_id=CONTRACT, action=action,
        reason="example", price=100, candle_timestamp=observed - timedelta(minutes=5))
    config = SimpleNamespace(id=9, strategy_type="test", strategy_params={"threshold": 3})
    analysis = {"trade_evaluation": {"total_score": 70}, "features": {"atr": 2}}
    stage_decision_snapshot(db, decision=decision, config=config, signal=signal, analysis=analysis, observed_at=observed)
    return signal, analysis


def candle(minute, *, opening=100, high=102, low=98, close=100, partial=False):
    return SimpleNamespace(candle_timestamp=NOW.replace(second=0) + timedelta(minutes=minute), open_price=opening,
        high_price=high, low_price=low, close_price=close, is_partial=partial)


def plan(direction="long"):
    return SimpleNamespace(observed_at=NOW, direction=direction, stop_loss=95 if direction == "long" else 105,
                           take_profit=105 if direction == "long" else 95)


def test_capture_deduplicates_depth_without_inventing_trade_volume(database):
    factory, recorder = database
    assert market.capture_depth(user_id=USER, contract_id=CONTRACT, entry=depth())
    assert not market.capture_depth(user_id=USER, contract_id=CONTRACT, entry=depth())
    assert not market.capture_depth(user_id=USER, contract_id=CONTRACT, entry=depth(kind=5))
    assert not market.capture_depth(user_id=USER, contract_id=CONTRACT, entry=depth(kind=11))
    recorder.flush()
    with factory() as db:
        row = db.query(MarketObservation).one()
        assert row.event_type == "depth" and row.size == 5
        status = market.observation_status(db, user_id=USER)
        assert status["profile"]["trade_count"] == 0
        assert status["profile"]["delta"] is None
        assert status["counts"]["depth"] == 1


def test_prints_without_ids_are_not_falsely_deduplicated_and_delta_unknown(database):
    factory, recorder = database
    payload = {"timestamp": NOW.isoformat(), "price": 100, "volume": 2, "type": 0}
    assert market.capture_trade(user_id=USER, contract_id=CONTRACT, payload=payload, received_at=NOW)
    assert market.capture_trade(user_id=USER, contract_id=CONTRACT, payload=payload, received_at=NOW)
    recorder.flush()
    with factory() as db:
        status = market.observation_status(db, user_id=USER)
        assert status["profile"]["total_volume"] == 4
        assert status["profile"]["classification_coverage"] == 0
        assert status["profile"]["delta"] is None
        assert db.query(MarketObservation).first().details["provider_trade_log_type"] == 0


def test_real_id_dedup_and_explicit_classification_coverage(database):
    factory, recorder = database
    payload = {"id": "t1", "timestamp": NOW.isoformat(), "price": 100, "volume": 2, "aggressorSide": "buy"}
    assert market.capture_trade(user_id=USER, contract_id=CONTRACT, payload=payload)
    assert not market.capture_trade(user_id=USER, contract_id=CONTRACT, payload=payload)
    recorder.flush()
    with factory() as db:
        status = market.observation_status(db, user_id=USER)
        assert status["profile"]["classification_coverage"] == 1
        assert status["profile"]["delta"] == 2


def test_queue_and_retention_are_bounded_and_owner_isolated(database):
    factory, recorder = database
    for index in range(4):
        assert market.capture_depth(user_id=USER, contract_id=CONTRACT, entry=depth(index))
    assert not market.capture_depth(user_id=USER, contract_id=CONTRACT, entry=depth(5))
    assert recorder.stats(USER)["dropped"] == 1
    assert recorder.stats(OTHER)["dropped"] == 0
    recorder.flush()
    with factory() as db:
        assert db.query(MarketObservation).count() == 3
        assert market.observation_status(db, user_id=OTHER)["event_count"] == 0
        assert market.observation_status(db, user_id=OTHER)["queued"] == 0


def test_database_failure_is_contained(database):
    _, _ = database
    def broken_factory():
        raise RuntimeError("private database details")
    recorder = market.ObservationWriter(broken_factory, auto_start=False)
    assert recorder.enqueue("market", {"user_id": USER, "fingerprint": "x"})
    recorder.flush()
    assert recorder.stats(USER)["write_errors"] == 1
    assert recorder.queue.empty()


def test_snapshot_waits_for_commit_and_copies_original_features(database):
    factory, recorder = database
    with factory() as db:
        signal, analysis = stage(db)
        signal.raw_payload["entry_price"] = 999
        analysis["features"]["atr"] = 999
        assert recorder.queue.empty()
        db.commit()
    recorder.flush()
    with factory() as db:
        row = db.query(DecisionResearchSnapshot).one()
        assert row.entry_price == 100
        assert row.snapshot["analysis"]["features"]["atr"] == 2
        assert "api_key" not in row.snapshot["signal"]["payload"]
        assert row.snapshot_hash == market.digest(row.snapshot)


def test_rollback_drops_snapshot_and_hold_has_no_trade_label(database):
    factory, recorder = database
    with factory() as db:
        stage(db)
        db.rollback()
    assert recorder.queue.empty()
    with factory() as db:
        stage(db, action="HOLD", decision_id=2)
        db.commit()
    recorder.flush()
    with factory() as db:
        row = db.query(DecisionResearchSnapshot).one()
        assert row.action == "HOLD" and row.outcome == "no_geometry"


def test_snapshot_is_first_write_immutable(database):
    factory, recorder = database
    with factory() as db:
        stage(db)
        db.commit()
    recorder.flush()
    recorder._seen.clear()
    with factory() as db:
        stage(db, action="HOLD")
        db.commit()
    recorder.flush()
    with factory() as db:
        assert db.query(DecisionResearchSnapshot).one().action == "BUY"


def test_nested_commit_never_publishes_before_outer_commit(database):
    factory, recorder = database
    with factory() as db:
        with db.begin_nested():
            stage(db)
        assert recorder.queue.empty()
        db.rollback()
    assert recorder.queue.empty()


def test_order_claim_savepoint_rollback_keeps_outer_snapshot_and_discards_inner(database):
    factory, recorder = database
    with factory() as db:
        with db.begin():
            stage(db, decision_id=1)
            try:
                with db.begin_nested():
                    stage(db, decision_id=2)
                    raise RuntimeError("idempotency claim rolled back")
            except RuntimeError:
                pass
            decision = SimpleNamespace(id=1, user_id=USER, action="NONE", reason="duplicate")
            stage_routing_disposition(db, decision=decision, evaluation_status="duplicate_skipped")
            assert recorder.queue.empty()
    recorder.flush()
    with factory() as db:
        row = db.query(DecisionResearchSnapshot).one()
        assert row.decision_id == 1
        assert row.routing["status"] == "duplicate_skipped"


def test_final_routing_is_separate_from_predecision_signal_and_commit_bound(database):
    factory, recorder = database
    with factory() as db:
        stage(db)
        db.commit()
    recorder.flush()
    with factory() as db:
        original = db.query(DecisionResearchSnapshot).one().snapshot_hash
        decision = SimpleNamespace(id=1, user_id=USER, action="RISK_REJECT", reason="daily loss limit")
        stage_routing_disposition(db, decision=decision, evaluation_status="risk_rejected", risk_events=[SimpleNamespace(code="daily_loss")])
        assert recorder.queue.empty()
        db.commit()
    recorder.flush()
    with factory() as db:
        row = db.query(DecisionResearchSnapshot).one()
        assert row.action == "BUY" and row.snapshot_hash == original
        assert row.routing["status"] == "risk_rejected"
        assert row.routing["risk_codes"] == ["daily_loss"]


def test_writer_shutdown_drains_and_then_refuses_more(database):
    factory, recorder = database
    assert market.capture_depth(user_id=USER, contract_id=CONTRACT, entry=depth())
    assert recorder.shutdown(timeout_seconds=3)
    with factory() as db:
        assert db.query(MarketObservation).count() == 1
    assert not market.capture_depth(user_id=USER, contract_id=CONTRACT, entry=depth(1))


def test_label_ignores_decision_minute_partial_and_future_bars():
    assert label_barriers(plan(), [candle(0, high=200), candle(1, high=200, partial=True), candle(2, high=200)], now=NOW + timedelta(minutes=1)) is None


@pytest.mark.parametrize("direction,high,low,expected", [("long", 106, 99, "target"), ("long", 101, 94, "stop"),
                                                        ("short", 106, 99, "stop"), ("short", 101, 94, "target")])
def test_label_known_barrier(direction, high, low, expected):
    result = label_barriers(plan(direction), [candle(1, high=high, low=low)], now=NOW + timedelta(minutes=2))
    assert result["outcome"] == expected


def test_label_ambiguous_same_minute_and_missing_minute():
    now = NOW + timedelta(minutes=4)
    assert label_barriers(plan(), [candle(1, high=106, low=94)], now=now)["outcome"] == "ambiguous"
    assert label_barriers(plan(), [candle(2, high=106)], now=now)["outcome"] == "gap"
    assert label_barriers(plan(), [candle(1, high=106), candle(1, low=94)], now=now)["outcome"] == "ambiguous"


def test_opening_gap_has_known_order_and_missing_entire_horizon_is_not_loss():
    result = label_barriers(plan(), [candle(1, opening=106, high=108, low=94)], now=NOW + timedelta(minutes=2))
    assert result["outcome"] == "target"
    assert label_barriers(plan(), [], now=NOW + timedelta(hours=2))["outcome"] == "gap"


def test_evaluator_uses_same_owner_contract_closed_one_minute_only(database):
    factory, recorder = database
    with factory() as db:
        stage(db)
        db.commit()
    recorder.flush()
    with factory() as db:
        for user, unit, high in [(OTHER, "minute", 200), (USER, "hour", 200), (USER, "minute", 103)]:
            db.add(ProjectXMarketCandle(user_id=user, contract_id=CONTRACT, live=False, unit=unit, unit_number=1,
                candle_timestamp=candle(1).candle_timestamp, open_price=100, high_price=high, low_price=98, close_price=100, volume=1, is_partial=False))
        db.add(ProjectXMarketCandle(user_id=USER, contract_id=CONTRACT, live=True, unit="minute", unit_number=1,
            candle_timestamp=candle(1).candle_timestamp, open_price=100, high_price=200, low_price=98, close_price=100, volume=1, is_partial=False))
        db.commit()
        result = evaluate_pending(db, user_id=USER, account_id=123, now=NOW + timedelta(minutes=2))
        assert result["updated"] == 0 and result["downloads"] == 0
        assert db.query(DecisionResearchSnapshot).one().outcome == "pending"


def test_router_fails_closed_and_detail_enforces_owner_and_account(database, monkeypatch):
    from fastapi import FastAPI
    from app.db import get_db
    from app import market_observation_routes as routes
    factory, recorder = database
    app = FastAPI()
    app.include_router(routes.router)
    def dependency():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = dependency
    monkeypatch.setattr(routes, "auth_required", lambda: True)
    monkeypatch.setattr(routes, "get_authenticated_user", lambda: None)
    assert request(app, "/api/market-observations/status").status_code == 401
    app.dependency_overrides[routes.observation_user_id] = lambda: USER
    with factory() as db:
        db.add(Account(user_id=USER, provider="projectx", external_id="123"))
        stage(db)
        db.commit()
    recorder.flush()
    response = request(app, "/api/decision-research/1?account_id=123")
    assert response.status_code == 200
    assert response.json()["snapshot"]["analysis"]["features"]["atr"] == 2
    assert request(app, "/api/decision-research/1?account_id=999").status_code == 404
    app.dependency_overrides[routes.observation_user_id] = lambda: OTHER
    assert request(app, "/api/decision-research/1?account_id=123").status_code == 404


def test_bbo_uses_only_fresh_observed_sides():
    book = MarketByPriceBook(CONTRACT)
    book.apply(depth(kind=4, price=100))
    assert book.capture_bbo() == {}
    book.apply(depth(kind=3, price=100.25))
    assert book.capture_bbo()["asks"][0]["price"] == 100.25
    book.apply(depth(4, kind=4, price=100))
    assert book.capture_bbo() == {}


def test_transport_capture_error_does_not_change_book_or_trade_routing(monkeypatch):
    monkeypatch.setattr(market, "capture_depth", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("storage down")))
    async def run():
        session = ProjectXMarketDepthSession(client=SimpleNamespace(), now=lambda: datetime(2026, 7, 10, 14, tzinfo=timezone.utc))
        session.set_observation_user(USER)
        session._channels[CONTRACT] = _ContractChannel(book=MarketByPriceBook(CONTRACT))
        await session._apply_depth_entry(CONTRACT, depth())
        assert session._channels[CONTRACT].book.snapshot()["bids"][0]["size"] == 5
    asyncio.run(run())


def test_provider_reset_preserves_identical_rebuilt_observations(database, monkeypatch):
    factory, _ = database
    recorder = market.ObservationWriter(factory, auto_start=False, capacity=16, record_cap=20)
    monkeypatch.setattr(market, "writer", recorder)
    async def run():
        session = ProjectXMarketDepthSession(client=SimpleNamespace(), now=lambda: datetime(2026, 7, 10, 14, tzinfo=timezone.utc))
        session.set_observation_user(USER)
        session._channels[CONTRACT] = _ContractChannel(book=MarketByPriceBook(CONTRACT))
        repeated_level = depth(1)
        repeated_reset = {"timestamp": NOW.isoformat(), "type": 6}
        await session._apply_depth_entry(CONTRACT, repeated_level)
        for _ in range(2):
            await session._apply_depth_entry(CONTRACT, repeated_reset)
            await session._apply_depth_entry(CONTRACT, repeated_level)
    asyncio.run(run())
    recorder.flush()
    with factory() as db:
        rows = db.query(MarketObservation).order_by(MarketObservation.id).all()
        assert [row.event_type for row in rows] == ["depth", "reset", "depth", "reset", "depth"]
        assert len({row.details["source_epoch"] for row in rows}) == 3


def test_execution_only_exact_owned_provider_id_matches_and_no_invented_latency(database):
    factory, _ = database
    with factory() as db:
        decision = BotDecision(id=1, user_id=USER, bot_config_id=1, account_id=123, contract_id=CONTRACT,
            action="BUY", decision_type="signal", reason="test", price=100)
        db.add(decision)
        db.add(BotOrderAttempt(user_id=USER, bot_decision_id=1, account_id=123, contract_id=CONTRACT,
            provider_order_id="o1", side="BUY", size=1, execution_mode="live", status="submitted"))
        for index, (owner, account, contract, order_id, price) in enumerate([(USER, 123, CONTRACT, "o1", 101), (OTHER, 123, CONTRACT, "o1", 999),
            (USER, 124, CONTRACT, "o1", 999), (USER, 123, "OTHER", "o1", 999), (USER, 123, CONTRACT, "unmatched", 999)]):
            db.add(ProjectXTradeEvent(user_id=owner, account_id=account, contract_id=contract, order_id=order_id,
                side="BUY", size=1, price=price, trade_timestamp=NOW + timedelta(seconds=index), fee_scope="per_side", source_trade_id=f"{owner}-{account}-{contract}-{order_id}"))
        db.commit()
        result = execution_summary(db, user_id=USER, account_id=123)
        assert result["matched_fill_count"] == 1
        assert result["mean_signed_price_difference"] == 1
        assert result["latency_ms"] is None
