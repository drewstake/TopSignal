from datetime import datetime, timezone
import json
import sqlite3

import pytest

from app.services import trade_progress_store as store
from app.services.trade_progress import ProgressEngine

T = datetime(2026, 10, 5, 14, tzinfo=timezone.utc).timestamp()
C = "CON.F.US.MNQ.Z26"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def pos(q=2, price=100, t=T, identity="p1", **extra):
    return dict(id=identity, accountId=1, contractId=C, creationTimestamp=iso(T),
                netQty=q, averagePrice=price, timestamp=iso(t), **extra)


def quote(price, t):
    return dict(contractId=C, bestBid=price-.25, bestAsk=price+.25, timestamp=iso(t))


@pytest.fixture
def local(monkeypatch, tmp_path):
    monkeypatch.setenv("TOPSIGNAL_DATABENTO_CACHE_DIR", str(tmp_path))
    return tmp_path


def ready(owner="owner", account=1):
    engine = ProgressEngine(owner, account, {"MNQ":2}, clock=lambda:T)
    engine.connection("market",True,T)
    engine.connection("user",True,T)
    engine.snapshot([],T,T)
    return engine


def test_status_distinguishes_flat_stale_and_scoped_positions(local, monkeypatch):
    monkeypatch.setattr(store, 'now', lambda: T)
    monkeypatch.setattr(store, 'verify_destination', lambda: dict(free_bytes=store.FLOOR, files={}))
    assert store.status('owner', 1)['recording_state'] == 'not_enrolled'
    with store.connect(write=True) as db:
        db.execute('INSERT INTO trade_progress_scopes VALUES(?,?,?,?,?)', ('owner',1,1,T,None))
    assert store.status('owner', 1)['recording_state'] == 'recording_flat'
    foreign = ready('other', 1)
    foreign.position(pos(), T)
    foreign.flush()
    assert store.status('owner', 1)['open_positions'] == 0
    engine = ready()
    engine.position(pos(), T)
    engine.flush()
    assert store.status('owner', 1)['recording_state'] == 'recording'
    monkeypatch.setattr(store, 'now', lambda: T+31)
    assert store.status('owner', 1)['recording_state'] == 'heartbeat_stale'


def test_future_clock_fill_is_preserved_with_timing_uncertainty(local):
    engine=ready()
    engine.fill(dict(id=99,accountId=1,contractId=C,creationTimestamp=iso(T+.02),
                     profitAndLoss=10,fees=.36,commissions=.25,size=1,price=105),T)
    engine.flush()
    with store.connect() as db:
        row=json.loads(db.execute('select payload from trade_progress_fills').fetchone()[0])
    assert row['ts']==T+.02
    assert row['clock_issue']=='provider_timestamp_after_receipt'


def test_reconnected_fill_clock_metadata_does_not_create_a_broker_correction(local):
    engine=ready()
    fill=dict(id=99,accountId=1,contractId=C,creationTimestamp=iso(T+.02),profitAndLoss=10,size=1,price=105)
    engine.fill(fill,T); engine.flush()
    engine.fill(fill,T+1); engine.flush()
    with store.connect() as db:
        assert db.execute('select count(*) from trade_progress_fill_revisions').fetchone()[0]==1
        head=json.loads(db.execute('select payload from trade_progress_fills').fetchone()[0])
    assert head['realized_dollars']==10 and not head.get('revision_conflict')
    assert head['clock_issue']=='provider_timestamp_after_receipt'


def test_policy_manages_only_its_enrollments_and_retains_audited_optouts(local, monkeypatch):
    monkeypatch.setattr(store,'verify_destination',lambda: {})
    store.enroll('owner',1,True)
    assert store.reconcile_policy('owner',[2],2)
    assert store.scopes()==[('owner',1),('owner',2)]
    assert store.reconcile_policy('owner',[3],2)
    assert store.scopes()==[('owner',1),('owner',3)]
    store.enroll('owner',3,False)
    assert store.reconcile_policy('owner',[3],2)
    assert store.scopes()==[('owner',1)]
    assert not store.reconcile_policy('owner',[4,5],2)
    assert store.scopes()==[('owner',1)]
    with store.connect() as db:
        assert db.execute('select count(*) from trade_progress_enrollment_changes where account=3').fetchone()[0]==2


def test_snapshot_can_replace_receipt_only_identity_without_false_continuity(local):
    engine=ready()
    engine.position(dict(accountId=1,contractId=C,netQty=1,averagePrice=100),T+1)
    first=engine.active[C]['id']
    engine.snapshot([pos(1)],T+2,T+3)
    assert engine.active[C]['id']!=first
    assert engine.active[C]['identity'] is not None
    assert 'lifecycle_identity_uncertain' in engine.dirty[first]['reasons']


def saved(engine):
    state = next(iter(engine.active.values())) if engine.active else next(iter(engine.dirty.values()))
    engine.flush()
    return store.timeline(engine.owner, engine.account, state["id"])


@pytest.mark.parametrize("q,expected", [(2,3),(-2,-3),(7,3)])
def test_signed_points_and_quantity_only_scales_dollars(local,q,expected):
    engine=ready()
    engine.position(pos(q),T)
    engine.quote(quote(103,T+3),T+3)
    engine.tick(T+10)
    sample=saved(engine)["samples"][-1]
    assert sample["points"] == expected
    assert sample["unrealized_dollars"] == expected*abs(q)*2
    assert sample["mark_timestamp"] == T+3
    assert sample["mark_basis"] == "bid_ask_midpoint"


def test_scaling_partial_exit_final_and_separate_realized(local):
    engine=ready()
    engine.position(pos(),T)
    engine.quote(quote(110,T+2),T+2)
    engine.position(pos(4,105,T+3),T+3)
    engine.quote(quote(112,T+4),T+4)
    engine.position(pos(1,105,T+5),T+5)
    engine.fill(dict(id=22,accountId=1,contractId=C,creationTimestamp=iso(T+5),profitAndLoss=42,fees=1,size=3,price=112),T+5)
    engine.position(pos(0,0,T+6),T+6)
    result=saved(engine)
    assert [s["kind"] for s in result["samples"]]==["entry","scale_in","partial_exit","exit"]
    scale, partial, exit_=result["samples"][1:]
    assert (scale["average_entry"],scale["quantity"],scale["points"]) == (105,4,5)
    assert (partial["points"],partial["unrealized_dollars"]) == (7,14)
    assert (exit_["quantity"],exit_["quantity_after"],exit_["average_entry"]) == (1,0,105)
    assert result["fills"][0]["realized_dollars"]==42
    assert result["position"]["best"]["points"]==10
    assert result["position"]["best_dollars"]["unrealized_dollars"]==56


def test_reversal_creates_two_positions_with_independent_basis(local):
    engine=ready()
    engine.position(pos(),T)
    first=engine.active[C]["id"]
    engine.quote(quote(110,T+2),T+2)
    engine.position(pos(-3,110,T+3),T+3)
    second=engine.active[C]["id"]
    engine.quote(quote(108,T+4),T+4)
    engine.tick(T+13)
    engine.flush()
    assert first!=second
    assert store.timeline("owner",1,first)["samples"][-1]["kind"]=="reversal_exit"
    other=store.timeline("owner",1,second)
    assert other["position"]["opened"]==T+3
    assert other["samples"][-1]["points"]==2
    assert other["samples"][-1]["unrealized_dollars"]==12


def test_cadence_and_brief_extreme_between_samples(local):
    engine=ready()
    engine.position(pos(),T)
    engine.quote(quote(120,T+.2),T+.2)
    engine.quote(quote(95,T+.3),T+.3)
    engine.quote(quote(101,T+.4),T+.4)
    engine.tick(T+.999)
    assert [s["kind"] for s in engine.samples]==["entry"]
    for sec in range(1,31):
        engine.tick(T+sec)
        engine.tick(T+sec+.05)  # Worker wakeups must not duplicate periodic samples.
    engine.position(pos(0,0,T+30.25),T+30.25)
    engine.tick(T+32)
    result=saved(engine)
    assert [s["ts"] for s in result["samples"] if s["kind"]=="periodic"]==[T+sec for sec in range(1,31)]
    assert result["position"]["best"]["ts"]==T+.2
    assert result["position"]["worst"]["points"]==-5
    assert result["samples"][-2]["points"]==1
    assert result["samples"][-1]["kind"]=="exit"
    assert not any(s["kind"]=="gap" for s in result["samples"])


def test_delayed_one_second_sampling_marks_gap_without_backfilling(local):
    engine=ready()
    engine.position(pos(),T)
    engine.quote(quote(101,T+1),T+1)
    engine.tick(T+1)
    engine.quote(quote(104,T+4),T+4)
    engine.tick(T+4)
    engine.tick(T+5)
    result=saved(engine)
    assert [s["ts"] for s in result["samples"] if s["kind"]=="periodic"]==[T+1,T+4,T+5]
    gap=next(s for s in result["samples"] if s["kind"]=="gap")
    assert (gap["gap_start"],gap["ts"],gap["issue"])==(T+1,T+4,"sampling_gap")


def test_missing_stale_and_unknown_point_value_are_null(local):
    engine=ready()
    engine.points={}
    engine.position(pos(),T)
    engine.tick(T+10)
    assert engine.samples[-1]["points"] is None
    engine.quote(quote(103,T+11),T+11)
    engine.tick(T+20)
    assert engine.samples[-1]["points"]==3
    assert engine.samples[-1]["unrealized_dollars"] is None
    engine.tick(T+50)
    assert engine.samples[-1]["issue"]=="stale_price"
    assert engine.samples[-1]["points"] is None


def test_duplicate_out_of_order_and_closed_replay(local):
    engine=ready()
    engine.position(pos(),T)
    engine.position(pos(),T+1)
    engine.position(pos(3,101,T+3),T+3)
    assert not engine.position(pos(7,101,T+2),T+4)
    assert len(engine.samples)==2
    engine.position(pos(0,0,T+5),T+5)
    engine.flush()
    engine=ready()
    assert not engine.position(pos(),T+10)
    assert not engine.active


def test_restart_same_identity_resumes_with_durable_gap(local):
    engine=ready()
    engine.position(pos(),T)
    engine.quote(quote(110,T+.3),T+.3)
    first=engine.active[C]["id"]
    engine.tick(T+1)
    engine.flush()
    engine=ProgressEngine("owner",1,{"MNQ":2},clock=lambda:T+40)
    engine.connection("market",True,T+40)
    engine.connection("user",True,T+40)
    engine.tick(T+40)
    assert engine.samples[-1]["points"] is None
    engine.snapshot([pos()],T+40,T+41)
    assert engine.active[C]["id"]==first
    engine.quote(quote(102,T+42),T+42)
    result=saved(engine)
    assert result["position"]["best"]["points"]==10
    assert "backend_restart" in result["position"]["reasons"]
    gaps=[s for s in result["samples"] if s["kind"]=="gap"]
    assert gaps[0]["gap_start"]==T+1
    assert not any(s["ts"]==T+20 for s in result["samples"])


def test_reconnect_flat_does_not_invent_exit_price(local):
    engine=ready()
    engine.position(pos(),T)
    engine.quote(quote(105,T+2),T+2)
    engine.connection("user",False,T+3)
    engine.snapshot([],T+20,T+21)
    result=saved(engine)
    assert result["samples"][-1]["kind"]=="exit_detected"
    assert result["samples"][-1]["points"] is None
    assert result["position"]["exit_time"] is None


def test_snapshot_cannot_overwrite_newer_live_event_or_resurrect_close(local):
    engine=ready()
    engine.position(pos(),T)
    engine.position(pos(3,101,T+5),T+5)
    engine.snapshot([pos()],T+4,T+6)
    assert engine.active[C]["quantity"]==3
    engine.position(pos(0,0,T+7),T+7)
    engine.snapshot([pos()],T+6,T+8)
    assert not engine.active


def test_undated_changes_are_receipt_time_and_late_entry_is_flagged(local):
    engine=ready()
    payload=pos()
    payload.pop("timestamp")
    engine.snapshot([payload],T+100,T+101)
    result=saved(engine)
    assert "recording_began_after_entry" in result["position"]["reasons"]
    assert "position_update_time_unavailable" in result["position"]["reasons"]
    assert result["samples"][0]["event_timestamp"] is None


def test_account_and_owner_isolation(local):
    engine=ready()
    wrong=pos(); wrong["accountId"]=2
    assert not engine.position(wrong,T)
    engine.position(pos(),T)
    result=saved(engine)
    identity=result["position"]["id"]
    assert store.timeline("other",1,identity) is None
    assert store.timeline("owner",2,identity) is None
    assert store.candidates("other",1,C,T,T+10)==[]


def test_fill_duplicates_corrections_and_voids(local):
    engine=ready()
    engine.position(pos(),T)
    fill=dict(id=1,accountId=1,contractId=C,creationTimestamp=iso(T+2),profitAndLoss=12)
    engine.fill(fill,T+2); engine.fill(fill,T+2)
    engine.flush()
    engine.fill(dict(fill,profitAndLoss=8),T+3)
    engine.fill(dict(fill,accountId=2,profitAndLoss=900),T+3)
    result=saved(engine)
    assert len(result["fills"])==1 and result["fills"][0]["realized_dollars"] is None
    assert result["fills"][0]["revision_conflict"]
    assert result["fills"][0]["reported_realized_dollars"]==8
    engine.fill(dict(fill,voided=True),T+4)
    assert saved(engine)["fills"][0]["voided"]


def test_destination_gate_checks_identity_and_actual_free_space(local,monkeypatch):
    with pytest.raises(ValueError): store.enroll("owner",1,True)
    report=store.disk_report()
    store.verify_and_register(expected_host=report["host"],expected_root=str(local),expected_drive=local.anchor)
    store.enroll("owner",1,True)
    assert store.scopes()==[("owner",1)]
    monkeypatch.setattr(store,"disk_report",lambda:report|{"free_bytes":store.FLOOR-1})
    with pytest.raises(OSError): store.verify_destination()
    monkeypatch.setattr(store,"disk_report",lambda:report|{"device":report["device"]+1})
    with pytest.raises(ValueError): store.verify_destination()


def test_no_cloud_route_and_sqlite_backup_includes_live_wal(local,tmp_path):
    from app.local_sync import eligible
    from app.services.historical_trade_excursions import recorder_lock
    from tools.backup_market_cache import backup,verify
    engine=ready(); engine.position(pos(),T); engine.quote(quote(103,T+1),T+1); engine.tick(T+10); result=saved(engine)
    with store.connect() as db:
        names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    assert all(name.startswith("trade_progress_") and not eligible(name) for name in names)
    # The feature has no table in ORM metadata; no outbox trigger can capture it.
    from app.db import Base
    assert not any(name.startswith("trade_progress_") for name in Base.metadata.tables)
    output=tmp_path.parent/(tmp_path.name+"-backup")
    with store.connect(write=True) as held, recorder_lock(store.location().with_suffix(".lock")) as acquired:
        assert acquired
        with recorder_lock(store.location().with_suffix(".lock")) as second:
            assert not second
        held.execute("INSERT OR REPLACE INTO trade_progress_meta VALUES('backup-test','committed-in-wal')")
        held.commit()
        assert store.location().with_name("progress.sqlite3-wal").exists()
        backup(local,output)
    manifest=verify(output)
    assert 'trade-progress-v1/progress.lock' not in manifest['files']
    name="trade-progress-v1/progress.sqlite3"
    assert manifest["files"][name]["sqlite"]
    with sqlite3.connect(output/name) as db:
        restored=json.loads(db.execute("SELECT state FROM trade_progress_positions").fetchone()[0])
        assert restored["id"]==result["position"]["id"]
        assert db.execute("SELECT value FROM trade_progress_meta WHERE key='backup-test'").fetchone()[0]=="committed-in-wal"


def test_pagination_is_scoped_and_lossless(local):
    engine=ready(); engine.position(pos(),T)
    for i in range(1,8):
        engine.quote(quote(100+i,T+i),T+i); engine.tick(T+i)
    result=saved(engine)
    first=store.timeline("owner",1,result["position"]["id"],limit=3)
    second=store.timeline("owner",1,result["position"]["id"],after=first["next_cursor"],limit=10)
    assert first["samples"]+second["samples"]==result["samples"]


def test_write_failure_is_atomic_and_recovery_flags_lost_tail(local):
    engine=ready(); engine.position(pos(),T); engine.flush()
    with store.connect(write=True) as db:
        db.execute("CREATE TRIGGER fail_sample BEFORE INSERT ON trade_progress_samples BEGIN SELECT RAISE(ABORT,'disk failure'); END")
    engine.quote(quote(120,T+3),T+3); engine.tick(T+10)
    with pytest.raises(sqlite3.Error): engine.flush()
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM trade_progress_samples").fetchone()[0]==1
        assert json.loads(db.execute("SELECT state FROM trade_progress_positions").fetchone()[0])["best"] is None
    recovered=ProgressEngine("owner",1,{"MNQ":2},clock=lambda:T+20)
    assert recovered.samples[0]["issue"]=="backend_restart"
    assert recovered.samples[0]["gap_start"]==T


def test_callbacks_are_bounded_nonblocking_and_connection_is_observed(local):
    from queue import Queue
    from app.services.trade_progress_recorder import AccountRecorder
    worker=AccountRecorder("owner",1,{"MNQ":2},lambda:None)
    worker.queue=Queue(maxsize=1)
    worker.tracker.ingest_position_event(pos())
    worker.tracker.ingest_market_event(quote(101,T+1))
    assert worker.overflow.is_set() and worker.queue.qsize()==1
    worker.queue.get_nowait()
    worker.runtime.runner._archive_connection_event("user","disconnected_test")
    kind,payload,_=worker.queue.get_nowait()
    assert kind=="connection" and payload==("user",False)
    worker.runtime.runner._dispatch_signalr_invocation("user",dict(target="GatewayUserTrade",arguments=[dict(id=1,accountId=1)]))
    assert worker.queue.get_nowait()[0]=="fill"


def test_same_owner_market_is_shared_but_positions_and_other_owners_are_isolated(local):
    from app.services.trade_progress_recorder import AccountRecorder,SharedRecorderMarket
    market=SharedRecorderMarket('owner',lambda:None)
    first=AccountRecorder('owner',1,{},lambda:None,market=market)
    second=AccountRecorder('owner',2,{},lambda:None,market=market)
    first.tracker.contracts=second.tracker.contracts=frozenset({C})
    market.add(first);market.add(second)
    assert market.active_contracts()=={C}
    assert not first.runtime.runner._market_hub_url and not second.runtime.runner._market_hub_url
    market.ingest_market_event(quote(101,T))
    assert first.queue.get_nowait()[0]=='quote' and second.queue.get_nowait()[0]=='quote'
    with pytest.raises(ValueError):market.add(AccountRecorder('other',1,{},lambda:None))
    first.tracker.ingest_position_event(pos())
    assert first.queue.qsize()==1 and second.queue.empty()


def test_midpoint_fallback_and_invalid_quote_do_not_invent_observations(local):
    engine=ready(); engine.position(pos(),T)
    engine.quote(dict(contractId=C,lastPrice=102,timestamp=iso(T+1)),T+1)
    engine.tick(T+10)
    assert engine.samples[-1]["mark_basis"]=="last_price"
    engine.quote(dict(contractId=C,lastPrice=999),T+11)
    engine.quote(quote(999,T+5),T+4)
    engine.quote(quote(999,T),T+12)
    assert engine.active[C]["best"]["points"]==2


def test_same_timestamp_price_spike_is_not_discarded(local):
    engine=ready(); engine.position(pos(),T)
    engine.quote(quote(102,T+1),T+1)
    engine.quote(quote(120,T+1),T+1)
    engine.quote(quote(101,T+2),T+2)
    assert engine.active[C]["best"]["points"]==20


def test_documented_position_shape_and_flat_notification_without_type(local):
    engine=ready()
    engine.position(dict(id=12,accountId=1,contractId=C,creationTimestamp=iso(T),type=2,size=3,averagePrice=100),T)
    engine.quote(quote(98,T+1),T+1)
    engine.tick(T+10)
    assert engine.samples[-1]["points"]==2
    engine.position(dict(id=12,accountId=1,contractId=C,creationTimestamp=iso(T),size=0),T+11)
    assert not engine.active
    assert engine.samples[-1]["kind"]=="exit"


def test_missing_identity_cannot_silently_join_across_reconnect(local):
    engine=ready()
    payload=pos(); payload.pop("id"); payload.pop("creationTimestamp")
    engine.position(payload,T)
    first=engine.active[C]["id"]
    engine.connection("user",False,T+2)
    engine.snapshot([payload],T+3,T+4)
    assert engine.active[C]["id"]!=first
    assert "lifecycle_identity_uncertain" in engine.dirty[first]["reasons"]


def test_mirror_rejects_feature_even_if_a_table_were_registered(local):
    from app.local_sync import _apply_row
    class ForbiddenTarget:
        def execute(self,*_args):
            pytest.fail("Feature data reached cloud SQL")
    with pytest.raises(ValueError,match="Unregistered mirror table"):
        _apply_row(ForbiddenTarget(),"trade_progress_samples","INSERT",{"id":1,"points":3},
                   {"trade_progress_samples":["id"]})


def test_replayed_legacy_fill_digest_does_not_manufacture_a_revision(local):
    from hashlib import sha256
    engine=ready()
    fill=dict(id=12,accountId=1,contractId=C,creationTimestamp=iso(T),size=1,price=100,profitAndLoss=10)
    engine.fill(fill,T+1);engine.flush()
    with store.connect(write=True) as db:
        row=db.execute('SELECT payload FROM trade_progress_fill_revisions').fetchone()
        legacy_digest=sha256(row['payload'].encode()).hexdigest()
        db.execute('UPDATE trade_progress_fill_revisions SET digest=?',(legacy_digest,))
    engine.fill(fill,T+2);engine.flush()
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM trade_progress_fill_revisions').fetchone()[0]==1
        payload=json.loads(db.execute('SELECT payload FROM trade_progress_fills').fetchone()[0])
        assert payload['realized_dollars']==10 and not payload.get('revision_conflict')


def test_writer_rechecks_runner_connection_after_queued_stale_state(local,monkeypatch):
    from app.services import trade_progress_recorder as runtime
    engine=ready(); engine.position(pos(),T)
    worker=runtime.AccountRecorder("owner",1,{"MNQ":2},lambda:None)
    worker.runtime.runner.stream_states={"market":"connected","user":"disconnected"}
    worker.submit("connection",("user",True))
    class OneIteration:
        stopped=False
        def is_set(self): return self.stopped
        def wait(self,_timeout): self.stopped=True
    worker.stop_event=OneIteration()
    monkeypatch.setattr(runtime,"ProgressEngine",lambda *_args:engine)
    monkeypatch.setattr(store,"verify_destination",lambda:{})
    worker.run()
    with store.connect() as db:
        state=json.loads(db.execute("SELECT state FROM trade_progress_positions").fetchone()[0])
        assert "user_disconnected" in state["reasons"]
        assert not engine.connected["user"]


def test_busy_writer_retries_same_batch_without_losing_or_duplicating_observations(local):
    import threading
    from app.services.trade_progress_recorder import AccountRecorder
    engine = ready()
    engine.position(pos(), T)
    engine.fill(dict(id=91, accountId=1, contractId=C, creationTimestamp=iso(T),
                     size=1, price=100, profitAndLoss=0), T)
    # A separate connection (like a maintenance process) owns the real SQLite
    # write lock longer than the existing one-second busy timeout.
    with store.connect(write=True):
        pass
    locked = threading.Event()
    def competing_writer():
        with sqlite3.connect(store.location()) as db:
            db.execute('BEGIN IMMEDIATE')
            locked.set()
            threading.Event().wait(1.3)
    thread = threading.Thread(target=competing_writer)
    thread.start()
    assert locked.wait(2)
    worker = AccountRecorder('owner', 1, {'MNQ': 2}, lambda: None)
    worker.flush(engine)
    thread.join(3)
    assert not thread.is_alive()
    assert worker.metrics['commit_busy_retries'] >= 1
    assert worker.metrics['discarded_after_write_failure'] == 0
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM trade_progress_samples').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM trade_progress_fills').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM trade_progress_fill_revisions').fetchone()[0] == 1
    assert not engine.samples and not engine.fills


def test_six_account_writers_serialize_and_preserve_scope(local, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from app.services.trade_progress_recorder import AccountRecorder
    engines = {account: ready(account=account) for account in range(1, 7)}
    for account, engine in engines.items():
        engine.position(pos() | {'accountId': account}, T)
    original_connect = sqlite3.connect
    class SlowCommit(sqlite3.Connection):
        def commit(self):
            threading.Event().wait(.22)
            return super().commit()
    def slow_connect(*args, **kwargs):
        return original_connect(*args, **kwargs, factory=SlowCommit)
    monkeypatch.setattr(sqlite3, 'connect', slow_connect)
    barrier = threading.Barrier(6)
    def capture(account):
        engine = engines[account]
        worker = AccountRecorder('owner', account, {'MNQ': 2}, lambda: None)
        barrier.wait(timeout=3)
        worker.flush(engine)
        return worker.metrics
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(capture, range(1, 7)))
    assert sum(row['commit_busy_retries'] for row in results) >= 1
    assert all(row['discarded_after_write_failure'] == 0 for row in results)
    with store.connect() as db:
        assert [r[0] for r in db.execute('SELECT account FROM trade_progress_positions ORDER BY account')] == list(range(1, 7))
        assert db.execute('SELECT count(*) FROM trade_progress_samples').fetchone()[0] == 6


@pytest.mark.parametrize('code, expected_calls', [(sqlite3.SQLITE_BUSY, 4), (sqlite3.SQLITE_IOERR, 1)])
def test_write_retry_is_bounded_and_never_retries_storage_failure(local, code, expected_calls):
    from app.services.trade_progress_recorder import AccountRecorder
    worker = AccountRecorder('owner', 1, {}, lambda: None)
    class BrokenEngine:
        calls = 0
        def flush(self):
            self.calls += 1
            exc = sqlite3.OperationalError('controlled failure')
            exc.sqlite_errorcode = code
            raise exc
    engine = BrokenEngine()
    with pytest.raises(sqlite3.OperationalError):
        worker.flush(engine)
    assert engine.calls == expected_calls
    assert worker.metrics['last_write_error']['sqlite_code'] == code
