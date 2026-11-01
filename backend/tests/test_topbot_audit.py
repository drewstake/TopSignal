from types import SimpleNamespace
import os
import sqlite3
import pytest
os.environ.setdefault('DATABASE_URL','sqlite+pysqlite:///:memory:')
from app.services import topbot_audit as audit


def test_append_only_pagination_and_scope():
    for i in range(5): audit.record('owner', 1, 'decision', old_stop=i, new_stop=i+1)
    audit.record('owner', 2, 'other_account')
    audit.record('other_owner', 1, 'other_owner')
    first = audit.page('owner', 1, limit=2)
    second = audit.page('owner', 1, limit=2, before=first['next_before'])
    assert [r['seq'] for r in first['items']] == [5,4]
    assert [r['seq'] for r in second['items']] == [3,2]
    assert all(r['kind']=='decision' for r in audit.page('owner',1)['items'])
    with audit.connection('owner') as db:
        for sql in ('DELETE FROM events', "UPDATE events SET kind='changed'"):
            with pytest.raises(sqlite3.IntegrityError, match='append_only'): db.execute(sql)
    assert len(audit.page('owner',1)['items']) == 5


def test_request_is_durable_before_broker_call_and_ack_is_distinct():
    def modify(**kwargs):
        rows = audit.page('owner', 1)['items']
        assert rows[0]['kind']=='broker_request'
        assert rows[0]['details']['old_stop']==100
        return {'order_id':'stop', 'raw_payload':{'token':'must-not-be-logged'}}
    client = audit.AuditedClient(SimpleNamespace(modify_stop_order=modify),
                                dict(owner='owner',account_id=1,old_stop=100,new_stop=125,reason='Trailing'))
    client.modify_stop_order(account_id=1,order_id='stop',stop_price=125)
    rows = audit.page('owner',1)['items']
    assert [r['kind'] for r in rows]==['broker_acknowledged','broker_request']
    assert rows[0]['operation']==rows[1]['operation']
    assert 'must-not-be-logged' not in str(rows)


def test_timeout_is_unknown_not_success_and_no_secret_exception_text():
    def fail(**kwargs): raise TimeoutError('api_key=secret')
    client = audit.AuditedClient(SimpleNamespace(place_order=fail),dict(owner='owner',account_id=1))
    with pytest.raises(TimeoutError): client.place_order(account_id=1)
    rows = audit.page('owner',1)['items']
    assert rows[0]['kind']=='broker_outcome_unconfirmed'
    assert 'secret' not in str(rows)


def test_logging_failure_blocks_broker_mutation(monkeypatch):
    calls=[]
    client=audit.AuditedClient(SimpleNamespace(place_order=lambda **kw:calls.append(kw)),dict(owner='owner',account_id=1))
    monkeypatch.setattr(audit,'record',lambda *a,**kw:(_ for _ in ()).throw(OSError('disk unavailable')))
    with pytest.raises(OSError):client.place_order(account_id=1)
    assert not calls


def test_journal_survives_unrelated_cloud_transaction_rollback():
    cloud=sqlite3.connect(':memory:')
    cloud.execute('CREATE TABLE scratch(id INTEGER)')
    cloud.execute('INSERT INTO scratch VALUES(1)')
    audit.record('owner',1,'broker_request',operation='pending')
    cloud.rollback()
    assert audit.page('owner',1)['items'][0]['operation']=='pending'


def test_account_scope_cannot_change_mid_action():
    client=audit.AuditedClient(SimpleNamespace(cancel_order=lambda **kw:pytest.fail('wrong account')),
                               dict(owner='owner',account_id=1))
    with pytest.raises(ValueError,match='scope_mismatch'):client.cancel_order(account_id=2,order_id=1)


def test_audit_endpoint_checks_ownership_before_reading(monkeypatch):
    import app.main as main
    from fastapi import HTTPException
    monkeypatch.setattr(main,'get_authenticated_user_id',lambda:'owner')
    def denied(*args,**kwargs): raise LookupError('account_not_found')
    monkeypatch.setattr(main,'_require_owned_projectx_account',denied)
    monkeypatch.setattr(audit,'page',lambda *a,**kw:pytest.fail('unowned history must not be read'))
    with pytest.raises(HTTPException) as exc:main.get_topbot_audit(2,before=None,db=SimpleNamespace())
    assert exc.value.status_code==404
