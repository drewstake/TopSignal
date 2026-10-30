from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import json
import sqlite3

import pytest

from app.services import market_archive as a
from app.services import market_archive_collector as collector
from app.services.market_archive_events import event_record

CONTRACT = 'CON.F.US.MNQ.Z26'
OTHER = 'CON.F.US.MNQ.H27'
OWNER = 'archive-test-owner'
UTC = timezone.utc


def bar(at, close=101):
    return dict(timestamp=at, open=100., high=max(102., close), low=99., close=float(close), volume=10., is_partial=False)


def fill_day(archive, day, *, missing=(), contract=CONTRACT, now=None):
    expected = a.expected_minutes(day)
    now = now or (expected[-1] + timedelta(minutes=3))
    with archive.transaction() as db:
        for i, at in enumerate(expected):
            if i not in missing:
                a.put_bar(db, a.native_bar(bar(at), contract), a.stamp(now))
        return a.refresh_day(db, contract, day, now, a.stamp(now))


def prepared(at, ident=1):
    rows = []
    for i in range(25):
        row = a.native_bar(bar(at-timedelta(minutes=5*(25-i))), CONTRACT, 5)
        rows.append({**row, 'fetched_at': a.stamp(at), 'first_fetched_at': a.stamp(at), 'provider_revision_hash': str(i)})
    return dict(owner=OWNER, decision_id=ident, at=a.stamp(at), config=32, contract=CONTRACT,
                correlation_id='correlation-'+str(ident), forecast={}, action='HOLD', candles=rows)


def test_dedup_corrections_and_value_reversion_are_immutable(tmp_path):
    archive = a.Archive(OWNER, tmp_path)
    at = datetime(2026, 10, 28, 14, tzinfo=UTC)
    first = a.native_bar(bar(at), CONTRACT)
    corrected = a.native_bar(bar(at, 103), CONTRACT)
    with archive.transaction() as db:
        initial = a.put_bar(db, first, a.stamp(at+timedelta(minutes=1)))
        assert a.put_bar(db, first, a.stamp(at+timedelta(minutes=2))) == initial
        amended = a.put_bar(db, corrected, a.stamp(at+timedelta(minutes=3)))
        assert amended != initial
        a.put_bar(db, first, a.stamp(at+timedelta(minutes=4)))
    with archive.reader() as db:
        assert db.execute('SELECT count(*) FROM bars').fetchone()[0] == 2
        assert [r[0] for r in db.execute('SELECT id FROM revisions ORDER BY seq')] == [initial, amended, initial]


def test_exact_hold_reconstruction_survives_correction_retry_restart(tmp_path):
    archive = a.Archive(OWNER, tmp_path)
    at = datetime(2026, 10, 28, 14, tzinfo=UTC)
    original = prepared(at)
    archive.decision(original)
    assert len(original['candles']) == 25 and 'fetched_at' in original['candles'][0]
    changed = prepared(at)
    changed['candles'][-1]['close'] = 99.
    archive.decision(changed)  # same committed identity wins first write
    with archive.transaction() as db:
        a.put_bar(db, a.native_bar(bar(at-timedelta(minutes=5), 104), CONTRACT, 5), a.stamp(at+timedelta(minutes=1)))
    recovered = a.Archive(OWNER, tmp_path).reconstruct(1)
    assert recovered['candles'] == original['candles']
    assert recovered['action'] == 'HOLD'
    assert recovered['execution_commit_confirmed_at'] is None


def test_atomic_batch_rollback_and_unknown_schema(tmp_path):
    archive = a.Archive(OWNER, tmp_path)
    with pytest.raises(RuntimeError):
        with archive.transaction() as db:
            a.put_bar(db, a.native_bar(bar(datetime(2026, 10, 28, tzinfo=UTC)), CONTRACT), a.stamp(datetime.now(UTC)))
            raise RuntimeError('crash before commit')
    with archive.reader() as db:
        assert db.execute('SELECT count(*) FROM bars').fetchone()[0] == 0
    with sqlite3.connect(archive.path) as db:
        db.execute('PRAGMA user_version=99')
    with pytest.raises(ValueError, match='schema'):
        with archive.transaction():
            pass


@pytest.mark.parametrize('day,minutes', [(date(2026, 10, 28), 1380), (date(2026, 11, 26), 1140),
                                      (date(2026, 11, 27), 1155), (date(2026, 11, 1), 0), (date(2026, 12, 25), 0)])
def test_session_boundaries_holidays_and_closures(day, minutes):
    assert len(a.expected_minutes(day)) == minutes


def test_dst_session_open_is_exchange_local_not_fixed_utc():
    assert a.expected_minutes(date(2026, 10, 30))[0].hour == 22
    assert a.expected_minutes(date(2026, 11, 2))[0].hour == 23


def test_all_aggregations_complete_only_and_separate_native(tmp_path):
    archive = a.Archive(OWNER, tmp_path)
    day = date(2026, 10, 28)
    coverage = fill_day(archive, day, missing=(2,))
    assert coverage['missing'] == 1 and coverage['scheduled_closed_minutes'] == 60
    with archive.reader() as db:
        assert db.execute('SELECT count(*) FROM heads WHERE stream=?', (a.stream_key(CONTRACT, 1440, 'derived_1m'),)).fetchone()[0] == 0
    times = a.expected_minutes(day)
    now = times[-1]+timedelta(minutes=3)
    with archive.transaction() as db:
        a.put_bar(db, a.native_bar(bar(times[2]), CONTRACT), a.stamp(now))
        a.put_bar(db, a.native_bar(bar(times[0], 110), CONTRACT, 5), a.stamp(now))
        coverage = a.refresh_day(db, CONTRACT, day, now, a.stamp(now))
    assert coverage['missing'] == 0
    with archive.reader() as db:
        for frame, count in ((5, 276), (15, 92), (60, 23), (240, 6), (1440, 1)):
            rows = [a.unpack(r[0]) for r in db.execute('SELECT b.payload FROM heads h JOIN bars b ON b.id=h.id WHERE h.stream=? ORDER BY h.at', (a.stream_key(CONTRACT, frame, 'derived_1m'),))]
            assert len(rows) == count
            assert sum(r['volume'] for r in rows) == 13800
            assert all(r['open'] == 100 and r['close'] == 101 for r in rows)
        assert rows[0]['timestamp'] == a.stamp(times[0])
        assert rows[0]['expected_minutes'] == 1380


def test_contracts_never_splice(tmp_path):
    archive = a.Archive(OWNER, tmp_path)
    fill_day(archive, date(2026, 10, 28), missing=range(690, 1380))
    fill_day(archive, date(2026, 10, 28), contract=OTHER, missing=range(690))
    with archive.reader() as db:
        for contract in (CONTRACT, OTHER):
            assert db.execute('SELECT count(*) FROM heads WHERE stream=?', (a.stream_key(contract, 1440, 'derived_1m'),)).fetchone()[0] == 0


class Provider:
    def __init__(self, missing=None):
        self.calls = []
        self.missing = missing
    def retrieve_bars(self, **kwargs):
        self.calls.append(kwargs)
        at = kwargs['start']
        rows = []
        while at < kwargs['end']:
            if a.futures_session_is_open(at, symbol='MNQ') and at != self.missing:
                rows.append(bar(at))
            at += a.MINUTE
        return rows
    def search_orders(self, **kwargs):
        return []
    def fetch_trade_history(self, *args, **kwargs):
        return []


def test_bounded_gap_recovery_overlap_and_restart(tmp_path):
    now = datetime(2026, 10, 28, 14, tzinfo=UTC)
    archive = a.Archive(OWNER, tmp_path)
    start = a.trading_day_bounds_utc(date(2026, 10, 28))[0]
    missing = start+timedelta(minutes=2)
    with archive.transaction() as db:
        a.set_state(db, 'cursor:'+CONTRACT, a.stamp(start))
    provider = Provider(missing)
    collector.collect(OWNER, 1, 32, CONTRACT, provider, now=now, root=tmp_path)
    assert len(provider.calls) == 2
    assert all(c['limit'] == 1500 and c['end']-c['start'] <= timedelta(days=1) for c in provider.calls)
    before = a.status(OWNER, root=tmp_path)
    assert before['coverage'][0]['missing'] == 1
    provider.missing = None
    collector.collect(OWNER, 1, 32, CONTRACT, provider, now=now, root=tmp_path)
    after = a.status(OWNER, root=tmp_path)
    assert after['coverage'][0]['missing'] == 0
    assert after['coverage'][0]['attempts'] == 1
    count = after['counts']['bars']
    collector.collect(OWNER, 1, 32, CONTRACT, provider, now=now, root=tmp_path)
    assert a.status(OWNER, root=tmp_path)['counts']['bars'] == count
    collector.collect(OWNER, 1, 32, OTHER, provider, now=now, root=tmp_path)
    with archive.reader() as db:
        assert db.execute("SELECT count(*) FROM events WHERE kind='contract_roll'").fetchone()[0] == 1


def test_unrecoverable_gap_is_bounded_and_never_fabricated(tmp_path):
    now = datetime(2026, 10, 28, 21, 5, tzinfo=UTC)
    start = a.trading_day_bounds_utc(date(2026, 10, 28))[0]
    provider = Provider(start+2*a.MINUTE)
    archive = a.Archive(OWNER, tmp_path)
    with archive.transaction() as db:
        a.set_state(db, 'cursor:'+CONTRACT, a.stamp(start))
    for i in range(6):
        collector.collect(OWNER, 1, 32, CONTRACT, provider, now=now+timedelta(minutes=16*i), root=tmp_path)
    row = next(r for r in a.status(OWNER, root=tmp_path)['coverage'] if r['day'] == '2026-10-28')
    assert row['attempts'] == 3 and row['missing'] == 1


def test_broker_event_retry_dedup_correction_and_quote_identity(tmp_path):
    now = datetime.now(UTC)
    archive = a.Archive(OWNER, tmp_path)
    payload = dict(id=123, accountId=1, contractId=CONTRACT, price=20000, orderId=456, voided=False, fees=1.2)
    event = event_record(1, 'GatewayUserTrade', payload, now)
    archive.events([event, {**event, 'received_at': a.stamp(now+timedelta(seconds=1))}])
    archive.events([event_record(1, 'GatewayUserTrade', {**payload, 'voided': True}, now)])
    quote = event_record(1, 'GatewayQuote', {'contractId': CONTRACT, 'bestBid': 100, 'bestAsk': 101, 'timestamp': a.stamp(now)}, now)
    assert quote['quality'] == 'paired_bbo'
    archive.events([quote])
    with archive.reader() as db:
        assert dict(db.execute('SELECT kind,count(*) FROM events GROUP BY kind')) == {'GatewayUserTrade': 2, 'bbo_sample': 1}
    assert 'broker_reported' in event['execution_evidence']


def test_failure_does_not_raise_into_trading_and_is_visible(tmp_path, monkeypatch):
    monkeypatch.setenv('TOPSIGNAL_DATABENTO_CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(a.Archive, 'decision', lambda *args: (_ for _ in ()).throw(OSError('disk full')))
    a.record_decision(prepared(datetime(2026, 10, 28, 14, tzinfo=UTC)))
    assert a.status(OWNER, root=tmp_path)['errors']['last_error']['operation'] == 'decision_write'


def test_recovery_is_fenced_when_lease_lost(tmp_path):
    provider = Provider()
    collector.collect(OWNER, 1, 32, CONTRACT, provider, now=datetime(2026, 10, 28, 14, tzinfo=UTC), root=tmp_path, allowed=lambda: False)
    assert provider.calls == []


def test_commit_recovery_only_confirms_matching_committed_identity(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.models import BotDecision
    monkeypatch.setenv('TOPSIGNAL_MARKET_ARCHIVE_ENABLED', 'true')
    monkeypatch.setenv('TOPSIGNAL_DATABENTO_CACHE_DIR', str(tmp_path))
    archive = a.Archive(OWNER, tmp_path)
    now = datetime(2026, 10, 28, 14, tzinfo=UTC)
    for ident in (1, 2, 3):
        archive.decision(prepared(now, ident))
    engine = create_engine('sqlite://')
    BotDecision.__table__.create(engine)
    with Session(engine) as db:
        for ident, correlation in ((1, 'correlation-1'), (2, 'different-evaluation')):
            db.add(BotDecision(id=ident, user_id=OWNER, bot_config_id=32, account_id=1, contract_id=CONTRACT,
                               decision_type='signal', action='HOLD', reason='test', correlation_id=correlation))
        db.commit()
        a.recover_decision_commits(db, OWNER)
    assert archive.reconstruct(1)['execution_commit_confirmed_at']
    assert archive.reconstruct(2)['execution_commit_confirmed_at'] is None
    assert archive.reconstruct(3)['execution_commit_confirmed_at'] is None
    engine.dispose()


def test_existing_hub_dispatch_captures_quotes_orders_fills_without_position_confusion():
    from app.services.projectx_hubs import ProjectXHubRunner
    tracker = SimpleNamespace(ingest_position_event=lambda *args, **kwargs: pytest.fail('Order treated as a position'))
    events = []
    runner = ProjectXHubRunner(tracker=tracker, client_factory=lambda: None, user_id=OWNER, account_id=1,
                              archive_contracts=(CONTRACT,), on_archive_event=lambda k, p: events.append((k, p)))
    for kind, arguments in [('GatewayQuote', [CONTRACT, {'bestBid': 100, 'bestAsk': 101}]),
                            ('GatewayUserOrder', [{'accountId': 1, 'contractId': CONTRACT, 'id': 2}]),
                            ('GatewayUserTrade', [{'accountId': 1, 'contractId': CONTRACT, 'id': 3}])]:
        runner._dispatch_frame('market' if kind == 'GatewayQuote' else 'user', {'type': 1, 'target': kind, 'arguments': arguments})
    assert [k for k, _ in events] == ['GatewayQuote', 'GatewayUserOrder', 'GatewayUserTrade']
    assert events[0][1]['contractId'] == CONTRACT


def test_collector_has_no_cloud_persistence_and_keeps_sqlite_guard():
    from pathlib import Path
    source = Path(collector.__file__).read_text()
    assert 'store_market_candles' not in source and 'SessionLocal' not in source
    from app.services import databento_ingestion
    assert 'sqlite' in Path(databento_ingestion.__file__).read_text()
