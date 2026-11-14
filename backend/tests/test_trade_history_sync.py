from datetime import date, datetime, timedelta, timezone
import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Account, ProjectXTradeEvent
from app.services import trade_history_sync as sync
from app.services.projectx_trades import store_trade_events

NOW = datetime(2026, 11, 13, tzinfo=timezone.utc)


def event(identity='1', account=10, **changes):
    return dict(account_id=account, contract_id='CON.F.US.MNQ.Z26', symbol='MNQ',
                timestamp=NOW-timedelta(hours=1), source_trade_id=identity, order_id='same-order',
                side='SELL',size=1,price=100,fees=.36,pnl=10,raw_payload={'id':identity,'voided':False}, **changes)


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv('TOPSIGNAL_DATABENTO_CACHE_DIR',str(tmp_path))
    engine=create_engine('sqlite:///'+str(tmp_path/'execution.db'))
    Base.metadata.create_all(engine)
    sessions=sessionmaker(engine)
    with sessions() as db:
        db.add(Account(user_id='a',provider='projectx',external_id='10',trade_data_source='projectx'))
        db.commit()
    with sync.state(write=True):
        pass
    yield sessions
    engine.dispose()


def test_distinct_provider_legs_same_order_timestamp_and_owner_are_preserved(local):
    with local() as db:
        assert store_trade_events(db,[event('1'),event('2')],user_id='a')==2
        db.commit()
        assert store_trade_events(db,[event('1'),event('2')],user_id='a')==0
        assert store_trade_events(db,[event('1')],user_id='b')==1
        db.commit()
        assert db.query(ProjectXTradeEvent).count()==3


def test_sync_resumes_without_duplicates_and_preserves_corrections_voids(local):
    class Client:
        rows=[event()]
        def fetch_trade_history(self,*args,**kw): return self.rows
    client=Client()
    def run(): return sync.sync_day(local,client,'a',10,date(2026,11,12),now=NOW,wait=lambda _:None)
    assert run()['state']=='response_covered'
    assert run()['rows']==1
    client.rows=[dict(event(),pnl=None,voided=True,raw_payload={'id':'1','voided':True})]
    run()
    with local() as db:
        row=db.query(ProjectXTradeEvent).one()
        assert row.pnl is None and row.raw_payload['voided'] is True
    with sync.state() as db:
        assert db.execute('select count(*) from revisions').fetchone()[0]==2
        assert db.execute('select attempts from windows').fetchone()[0]==3
        previous=json.loads(db.execute('select payload from prior_heads').fetchone()[0])
        assert previous['raw_payload']['voided'] is False


def test_wrong_owner_prevents_provider_call(local):
    class Client:
        def fetch_trade_history(self,*args,**kw): raise AssertionError('must not call provider')
    with pytest.raises(ValueError):
        sync.sync_day(local,Client(),'b',10,date(2026,11,12),now=NOW)


def test_wrong_provider_scope_marks_partial_without_writes(local):
    class Client:
        def fetch_trade_history(self,*args,**kw): return [event(account=99)]
    assert sync.sync_day(local,Client(),'a',10,date(2026,11,12),now=NOW,wait=lambda _:None)['state']=='partial'
    with local() as db: assert db.query(ProjectXTradeEvent).count()==0


def test_dense_undocumented_paging_uses_time_windows_and_deduplicates_boundary():
    middle=NOW-timedelta(hours=12)
    rows=[dict(event(str(i)),timestamp=middle+timedelta(hours=i-1)) for i in range(3)]
    class Client:
        def fetch_trade_history(self,account,start,end,**kw):
            assert 'offset' not in kw and 'limit' not in kw
            return [r for r in rows if start<=r['timestamp']<=end]
    result=sync.fetch_window(Client(),10,NOW-timedelta(days=1),NOW,threshold=3,wait=lambda _:None)
    assert len(result)==3


def test_indivisible_timestamp_is_partial_not_complete():
    class Client:
        def fetch_trade_history(self,*args,**kw): return [event('1'),event('2')]
    with pytest.raises(sync.PartialHistory):
        sync.fetch_window(Client(),10,NOW-timedelta(microseconds=1),NOW,threshold=2,wait=lambda _:None)


def test_provisional_day_and_persisted_failure_backoff(local):
    class Client:
        def fetch_trade_history(self,*args,**kw): return []
    now=NOW+timedelta(hours=3)
    assert sync.sync_day(local,Client(),'a',10,now.date(),now=now,wait=lambda _:None)['state']=='provisional'
    assert now.date() not in sync.due_days('a',10,'2026-11-13',now)
    class Failed:
        def fetch_trade_history(self,*args,**kw): raise TimeoutError('private credential must not appear')
    result=sync.sync_day(local,Failed(),'a',10,now.date(),now=now,wait=lambda _:None)
    assert result['state']=='temporarily_inaccessible'
    with sync.state() as db:
        assert 'private' not in json.dumps([dict(r) for r in db.execute('select * from windows')])


def test_active_coverage_policy_discovers_accounts_preserves_optouts_and_caps_growth(local,monkeypatch):
    from app.services import trade_progress_store as progress
    monkeypatch.setattr(progress,'verify_destination',lambda: {})
    with progress.connect(write=True):pass
    progress.enroll('a',10,False)
    progress.enroll('b',11,False)
    sync.configure('a','2026-11-12',record_active=True,max_accounts=2)
    class Client:
        timeout_seconds=30
        rows=[dict(id=10,can_trade=True,is_visible=True),dict(id=11,can_trade=False,is_visible=True),
              dict(id=12,can_trade=False,is_visible=False)]
        def list_accounts(self,**kw):return self.rows
    client=Client()
    monkeypatch.setattr(sync,'due_days',lambda *args: [])
    worker=sync.HistorySynchronizer(local,lambda db,*,user_id:client)
    worker.cycle()
    with progress.connect() as db:
        assert {(r['owner'],r['account']):r['enabled'] for r in db.execute('select * from trade_progress_scopes')}=={('a',10):0,('a',11):1,('b',11):0}
    client.rows.append(dict(id=13,can_trade=True,is_visible=True))
    worker.cycle()
    with sync.state() as db:
        assert db.execute('select error from policies').fetchone()[0]=='capacity_review_required'
    with progress.connect() as db:
        assert db.execute('select count(*) from trade_progress_scopes where account=13').fetchone()[0]==0
