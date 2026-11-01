import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json

import pytest

from app.services import historical_trade_excursions as history
from app.services.projectx_hubs import ProjectXHubRunner
from app.services.streaming_pnl_tracker import StreamingPnlTracker

OPEN = datetime(2026, 10, 5, 14, tzinfo=timezone.utc)


def trade(**kwargs):
    return dict(id=1, contract_id="CON.F.US.MNQ.Z26", symbol="MNQ", side="SELL", size=2,
                entry_time=OPEN, entry_price=100, exit_time=OPEN+timedelta(minutes=2), exit_price=102,
                legs=[(OPEN, 100, 2)]) | kwargs


def bars():
    return [dict(timestamp=OPEN, low=98, high=103),
            dict(timestamp=OPEN+timedelta(minutes=1), low=99, high=105)]


@pytest.mark.parametrize("side,expected", [("SELL", (-8, 20)), ("BUY", (-20, 8))])
def test_closing_side_direction_and_dollar_size(side, expected):
    result = history.calculate(trade(side=side), bars(), point_value=2)
    assert (result["mae"], result["mfe"]) == expected
    assert result["excursion_source"] == "historical_1m_estimate"


def test_partial_exit_uses_closed_quantity_not_entire_position():
    result = history.calculate(trade(size=1, legs=[(OPEN, 100, 1)]), bars(), point_value=2)
    assert (result["mae"], result["mfe"]) == (-4, 10)


def test_scale_in_only_exposes_each_leg_after_its_entry():
    result = history.calculate(trade(legs=[(OPEN, 100, 1), (OPEN+timedelta(minutes=1), 104, 1)]),
                               bars(), point_value=2)
    assert (result["mae"], result["mfe"]) == (-12, 12)


def test_exit_boundary_excludes_following_candle_and_prices_before_entry():
    extra = [dict(timestamp=OPEN-timedelta(minutes=1), low=1, high=999), *bars(),
             dict(timestamp=OPEN+timedelta(minutes=2), low=1, high=999)]
    assert history.calculate(trade(), extra, point_value=2)["mfe"] == 20


def test_same_minute_trade_is_explicitly_estimated():
    result = history.calculate(trade(entry_time=OPEN+timedelta(seconds=20),
        exit_time=OPEN+timedelta(seconds=40), legs=[(OPEN+timedelta(seconds=20), 100, 2)]), bars(), point_value=2)
    assert result["mfe"] == 12
    assert "outside the trade" in result["excursion_note"]


@pytest.mark.parametrize("changes,prices", [({}, []), ({}, bars()[:1]),
    ({"legs": []}, bars()), ({"size": 3}, bars()), ({"side": "unknown"}, bars()),
    ({}, [dict(timestamp=OPEN, low=float("nan"), high=103), bars()[1]]),
    ({}, [dict(bars()[0], is_partial=True), bars()[1]])])
def test_missing_or_invalid_evidence_never_produces_zero_estimates(changes, prices):
    assert "mae" not in history.calculate(trade(**changes), prices, point_value=2)


@pytest.fixture
def local(monkeypatch, tmp_path):
    monkeypatch.setenv("TOPSIGNAL_DATABENTO_CACHE_DIR", str(tmp_path))


def test_cache_is_scoped_and_invalidated_when_fills_change(local):
    original = trade()
    lifecycle = SimpleNamespace(entry_legs=original["legs"])
    prepared = history.inputs(original, lifecycle)
    result = history.calculate(original, bars(), point_value=2)
    with history.connect() as db:
        db.execute("INSERT INTO results VALUES(?,?,?,?,?,?)", ("owner", 1, 1, history.fingerprint(prepared), history.encoded(result), None))
    def read(owner="owner", account=1, **changes):
        value = original | changes
        return history.attach_cached(owner, account, [value], lifecycles={1: lifecycle})[0]
    assert read()["mfe"] == 20
    assert "mfe" not in read(owner="other")
    assert "mfe" not in read(account=2)
    assert "mfe" not in read(exit_price=104)
    assert read(mae=-1, mfe=1, excursion_source="observed_stream")["mfe"] == 1


def test_single_recorder_lock_is_released(local):
    with history.recorder_lock() as first:
        assert first
        with history.recorder_lock() as second:
            assert not second
    with history.recorder_lock() as third:
        assert third


def test_live_positions_subscribe_and_record_without_bot_or_cloud_writes(local):
    tracker = StreamingPnlTracker(owner_user_id="owner", owner_account_id=1,
        point_value_by_symbol={"MNQ": 2}, on_lifecycle_closed=history.save_observation)
    runner = ProjectXHubRunner(tracker=tracker, client_factory=lambda: None, user_id="owner", account_id=1,
                              market_hub_url="", user_hub_url="", track_positions=True)
    def position(size, at, account=1):
        runner._dispatch_payload("user", dict(accountId=account, contractId="CON.F.US.MNQ.Z26", type=1,
                                             size=size, averagePrice=100, timestamp=at.isoformat()))
    position(2, OPEN, account=2)
    assert not tracker.active_contracts()
    position(2, OPEN)
    sent = []
    async def scenario():
        class Socket:
            async def send(self, message):
                sent.append(json.loads(message.rstrip("\x1e")))
        task = asyncio.create_task(runner._track_position_contracts(Socket()))
        await asyncio.sleep(.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
    assert sent == [{"type": 1, "target": "SubscribeContractQuotes", "arguments": ["CON.F.US.MNQ.Z26"]}]
    for seconds, price in ((10, 98), (20, 105)):
        runner._dispatch_frame("market", {"type": 1, "target": "GatewayQuote", "arguments": ["CON.F.US.MNQ.Z26",
            {"lastPrice": price, "timestamp": (OPEN+timedelta(seconds=seconds)).isoformat()}]})
    position(0, OPEN+timedelta(minutes=2))
    result = history.attach_cached("owner", 1, [trade()], lifecycles={1: SimpleNamespace(entry_legs=trade()["legs"])})[0]
    assert (result["mae"], result["mfe"], result["excursion_source"]) == (-8, 20, "observed_stream")


def test_disconnect_discards_incomplete_position():
    completed = []
    tracker = StreamingPnlTracker(owner_user_id="owner", owner_account_id=1,
        point_value_by_symbol={"MNQ": 2}, on_lifecycle_closed=completed.append)
    tracker.ingest_position_event(dict(accountId=1, contractId="MNQ", type=1, size=1, averagePrice=100, timestamp=OPEN))
    tracker.reset_observations()
    tracker.ingest_position_event(dict(accountId=1, contractId="MNQ", type=1, size=0, timestamp=OPEN+timedelta(minutes=2)))
    assert not completed and not tracker.active_contracts()


def test_backfill_is_idempotent_scoped_and_picks_up_future_closed_trades(local):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    from app.models import Account, ProjectXTradeEvent
    owner = "00000000-0000-0000-0000-000000000001"
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Account.__table__, ProjectXTradeEvent.__table__])
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        db.add(Account(user_id=owner, provider="projectx", external_id="1"))
        db.add(ProjectXTradeEvent(user_id=owner, account_id=1, contract_id="CON.F.US.MNQ.Z26", symbol="MNQ",
            side="BUY", size=2, price=100, trade_timestamp=OPEN, fees=0, pnl=None, order_id="open"))
        db.add(ProjectXTradeEvent(user_id=owner, account_id=1, contract_id="CON.F.US.MNQ.Z26", symbol="MNQ",
            side="SELL", size=1, price=102, trade_timestamp=OPEN+timedelta(minutes=2), fees=0, pnl=4, order_id="close"))
        db.commit()
    calls = []
    class Client:
        def retrieve_bars(self, **kwargs):
            calls.append(kwargs)
            return bars()
    try:
        first = history.backfill(owner, 1, client=Client(), session_factory=sessions)
        assert first == dict(calculated=1, cached=0, unavailable=0)
        assert calls[0]["contract_id"] == "CON.F.US.MNQ.Z26" and calls[0]["unit"] == 2
        second = history.backfill(owner, 1, client=Client(), session_factory=sessions)
        assert second["cached"] == 1 and len(calls) == 1
        with pytest.raises(ValueError, match="ownership"):
            history.backfill("00000000-0000-0000-0000-000000000002", 1, client=Client(), session_factory=sessions)
        assert len(calls) == 1
        with sessions() as db:
            db.add(ProjectXTradeEvent(user_id=owner, account_id=1, contract_id="CON.F.US.MNQ.Z26", symbol="MNQ",
                side="SELL", size=1, price=103, trade_timestamp=OPEN+timedelta(minutes=2, seconds=1),
                fees=0, pnl=6, order_id="later-close"))
            db.commit()
        # A missing minute on the later close must remain unavailable, without
        # corrupting the prior successfully calculated partial exit.
        later = history.backfill(owner, 1, client=Client(), session_factory=sessions)
        assert later == dict(calculated=0, cached=1, unavailable=1)
        assert len(calls) == 2
    finally:
        engine.dispose()
