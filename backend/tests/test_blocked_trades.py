from contextlib import closing
from datetime import UTC, datetime, timedelta
import time

import pytest

from app.models import Account, BotConfig, BotDecision, BotOrderAttempt, BotRun
from app.services import blocked_replay, blocked_trades, bot_service, topbot_profit_guard as guard
from app.services.topbot_depth import DepthState, feed
from app.services.topbot_evidence import Capture
from app.services.topbot_strategy import DEFAULTS
from test_bot_execution_safety import RecordingClient, open_exchange_session
from test_bot_worker import _runtime, _seed_run, session_factory

OWNER, ACCOUNT, CONTRACT = "user-a", 9001, "CON.F.US.MNQ.H26"


@pytest.fixture
def setup(session_factory, tmp_path, monkeypatch, open_exchange_session):
    now = datetime.now(UTC)
    monkeypatch.setenv("TOPSIGNAL_DATABENTO_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(blocked_trades, "FREE_FLOOR", 0)
    monkeypatch.delenv("TOPSIGNAL_TOPBOT_EVIDENCE_DIR", raising=False)
    for name in ("TOPSIGNAL_BOT_WORKER_ENABLED","TOPSIGNAL_LIVE_EXECUTION_ENABLED","TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION"):
        monkeypatch.setenv(name, "true")
    monkeypatch.setattr(bot_service, "running_under_tests", lambda: False)
    monkeypatch.setattr("app.services.topbot_session.futures_session_is_open", lambda *a, **kw: True)
    monkeypatch.setattr("app.services.topbot_session.flat_deadline", lambda *a, **kw: now+timedelta(hours=2))
    _seed_run(session_factory, account_id=ACCOUNT, dry_run=False)
    with session_factory() as db:
        account = db.query(Account).one()
        account.provider_simulated = True
        account.provider_classification_observed_at = now
        config = db.get(BotConfig, 1)
        config.strategy_type = "topbot_adaptive"
        config.strategy_params = dict(DEFAULTS, entry_contracts=2, exit_mode="fixed", take_profit_points=1,
                                      imbalance_direction="following", daily_pnl_limit=800, daily_loss_limit=1200)
        config.max_daily_loss = 1200
        config.order_size = config.max_contracts = config.max_open_position = 2
        config.max_trades_per_day = 10
        config.symbol = "MNQ"
        config.cooldown_seconds = 0
        db.commit()
    client = RecordingClient()
    runtime = _runtime(session_factory)
    assert runtime._acquire_or_renew_lease()
    monkeypatch.setattr(runtime, "_client_for_user", lambda owner: client)
    monkeypatch.setattr(feed, "ensure", lambda *a, **kw: None)
    capture = Capture(OWNER, CONTRACT, free_floor=0)
    book = DepthState(CONTRACT)
    observed = {}
    def quote(at, bid, *, gap=False):
        seq = capture.index+1
        event = {"event": "snapshot", "data": dict(contract_id=CONTRACT, sequence=seq+(1 if gap else 0),
                 timestamp=at.isoformat(), bids=[dict(price=bid, size=9)],
                 asks=[dict(price=bid+.25, size=1),dict(price=bid+.5,size=2)])}
        book.apply(event,at)
        ref = capture.append(event,at,book.transitions)
        observed.update(book.snapshot(at), evidence=ref, checked_at=at.isoformat())
    for second in range(31):
        quote(now-timedelta(seconds=30-second),100)
    monkeypatch.setattr(feed,"snapshot",lambda *a, **kw: dict(observed))
    def tick():
        return runtime._process_run(run_id=1, now=now, startup_recovery=False,
                                    blocked_accounts=set(), probe_provider=False, probed_users=set())
    def replay(active=None):
        end = time.monotonic()+5
        while capture.durable_index != capture.index and time.monotonic()<end:
            time.sleep(.01)
        assert capture.durable_index == capture.index
        with closing(blocked_trades.connect()) as db:
            blocked_replay.cycle(db, {} if active is None else active, now)
        return blocked_trades.report(OWNER,ACCOUNT,1)
    try:
        yield now, client, quote, tick, replay, observed
    finally:
        capture.close()
        assert not client.place_order_calls and not client.cancel_order_calls and not client.close_position_calls


@pytest.mark.parametrize("outcome", ["TP","SL","unresolved","silence","halt"])
def test_latched_1200_stop_flat_restart_and_recorded_exit(setup, session_factory, outcome):
    now, client, quote, tick, replay, observed = setup
    triggered = guard.observe(OWNER,ACCOUNT,-1335.30,limit=(800,1200),now=now-timedelta(seconds=32))
    assert guard.observe(OWNER,ACCOUNT,-798.74,limit=(800,1200),now=now)["trigger"] == triggered["trigger"]
    assert client.search_open_positions(account_id=ACCOUNT) == []
    assert not tick()["evaluated"]
    tick()
    report = replay()
    assert report["counts"]["open"] == 1 and report["denominator"] == 0
    trade, = report["trades"]
    assert (trade["reason"],trade["entry"],trade["sl"],trade["tp"],trade["quantity"]) == (
        "daily_loss_limit_reached",100.375,75.375,101.5,2)
    # The pending episode is replayed from its original capture by a fresh reader.
    with session_factory() as db:
        config = db.get(BotConfig,1)
        config.strategy_params = dict(config.strategy_params, take_profit_points=90)
        db.commit()
    quote(now+timedelta(seconds=.30),101.5)  # A target touch alone cannot fill.
    if outcome == "SL":
        quote(now+timedelta(seconds=.40),75)
        quote(now+timedelta(seconds=.70),76)
    elif outcome == "silence":
        quote(now+timedelta(seconds=4),102)
    else:
        if outcome == "halt":
            blocked_trades.record_halt(OWNER, ACCOUNT, 1, dict(contract_id=CONTRACT,
                                       rejected_at=(now+timedelta(seconds=.4)).isoformat()))
        quote(now+timedelta(seconds=.45),102,gap=outcome=="unresolved")
    result = replay()
    t, = result["trades"]
    expected = outcome if outcome in ("TP","SL") else "unresolved"
    assert t["id"] == trade["id"] and t["status"] == expected
    assert t["net"] == {"TP":2.06, "SL":-100.94, "unresolved":None}[expected]
    assert result["denominator"] == int(expected!="unresolved")
    with session_factory() as db:
        assert db.query(BotOrderAttempt).count() == db.query(BotDecision).count() == 0
    assert guard.read(OWNER,ACCOUNT)["trigger"] == "daily_loss_limit_reached"
    assert not blocked_trades.report(OWNER,ACCOUNT,2)["trades"]
    assert not blocked_trades.report("other",ACCOUNT,1)["trades"]


@pytest.mark.parametrize("block", ["manual","halt","feed","locked","closed"])
def test_no_admission_under_other_blocks(setup, session_factory, monkeypatch, block):
    now,client,_,tick,_,observed = setup
    guard.observe(OWNER,ACCOUNT,810,limit=(800,1200),now=now-timedelta(seconds=32))
    with session_factory() as db:
        run = db.get(BotRun,1)
        if block=="manual":
            run.status="stopped"
        if block=="halt":
            run.raw_state = dict(run.raw_state,instrument_pause={"contract_id":CONTRACT})
        db.commit()
    if block=="feed":
        observed["ready"]=False
    if block=="locked":
        client.account_can_trade=False
    if block=="closed":
        monkeypatch.setattr("app.services.topbot_session.futures_session_is_open",lambda *a, **kw: False)
    tick()
    assert not blocked_trades.path().exists()


@pytest.mark.parametrize("budget", [True,False])
def test_budget_rejection_and_profit_latch(setup, session_factory, monkeypatch, budget):
    now,_,_,tick,replay,_ = setup
    if budget:
        poll = guard.poll
        monkeypatch.setattr(guard, "poll", lambda *a, **kw: poll(*a, **kw, now=now))
        monkeypatch.setattr(bot_service,"_fetch_account_daily_pnl",lambda *a, **kw: (-1190,0,None))
    else:
        guard.observe(OWNER,ACCOUNT,810,limit=(800,1200),now=now-timedelta(seconds=32))
    processed = tick()
    with session_factory() as db:
        from app.models import BotRiskEvent
        assert blocked_trades.path().exists(), (processed, [r.code for r in db.query(BotRiskEvent)],
                                               [(d.action,d.reason,d.raw_payload.get("entry_checks",{}).get("phase")) for d in db.query(BotDecision)])
    report = replay()
    t, = report["trades"]
    assert t["reason"] == (blocked_trades.BUDGET if budget else "daily_profit_target_reached")
    with session_factory() as db:
        assert db.query(BotOrderAttempt).count() == int(budget)
        if budget:
            assert db.query(BotOrderAttempt).one().status == "blocked"


def test_overlapping_episodes_and_durable_dedup(setup):
    now,_,quote,tick,replay,_ = setup
    guard.observe(OWNER,ACCOUNT,-1200,limit=(800,1200),now=now-timedelta(seconds=32))
    tick()
    with closing(blocked_trades.connect()) as db:
        row = db.execute("SELECT * FROM signals").fetchone()
        fields = [row[k] for k in ("owner","account","config","contract","episode","at","session","reason","side","quantity","snapshot")]
    blocked_trades.enqueue(fields)
    fields[4] = (now-timedelta(seconds=29)).isoformat()
    fields[5] = (now+timedelta(seconds=.1)).isoformat()
    blocked_trades.enqueue(fields)
    assert replay()["counts"]["open"] == 1
    quote(now+timedelta(seconds=.8),102)
    replay()
    assert len(replay()["trades"]) == 1
    with closing(blocked_trades.connect()) as db:
        assert [r[0] for r in db.execute("SELECT status FROM signals ORDER BY id")] == ["TP","skipped"]


@pytest.mark.parametrize("mode", ["trailing", "session"])
def test_configured_other_exits_continue_after_block_clears(setup, session_factory, monkeypatch, mode):
    now,_,quote,tick,replay,_ = setup
    if mode == "session":
        monkeypatch.setattr("app.services.topbot_session.flat_deadline",
                            lambda *a, **kw: now+timedelta(minutes=2,seconds=1))
    else:
        with session_factory() as db:
            config = db.get(BotConfig,1)
            config.strategy_params = dict(DEFAULTS, imbalance_direction="following", daily_loss_limit=1200)
            config.order_size = config.max_contracts = config.max_open_position = 1
            db.commit()
    guard.observe(OWNER,ACCOUNT,-1200,limit=(800,1200),now=now-timedelta(seconds=32))
    tick()
    assert replay()["counts"]["open"] == 1
    with session_factory() as db:
        db.get(BotConfig,1).enabled = False
        db.commit()
    if mode == "session":
        quote(now+timedelta(seconds=1.25),100)
    else:
        for second in range(1,6):
            quote(now+timedelta(seconds=second),135)
        quote(now+timedelta(seconds=5.3),109)
        quote(now+timedelta(seconds=5.6),108)
    result = replay()
    row, = result["trades"]
    assert row["status"] == "other"
    assert row["detail"] == ("Session close" if mode == "session" else "Trailing stop")
    assert (result["wins"],result["denominator"]) == (int(mode=="trailing"),1)
