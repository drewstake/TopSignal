import os
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from datetime import datetime, timezone
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.main as main
from app.db import Base
from app.models import Account, ProjectXTradeEvent
from app.services import trade_progress_store as store
from app.services.trade_progress import ProgressEngine

OWNER="11111111-1111-1111-1111-111111111111"
OTHER="22222222-2222-2222-2222-222222222222"


@pytest.fixture
def db(monkeypatch,tmp_path):
    monkeypatch.setenv("TOPSIGNAL_DATABENTO_CACHE_DIR",str(tmp_path))
    monkeypatch.setattr(main,"get_authenticated_user_id",lambda:OWNER)
    engine=create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine,tables=[Account.__table__,ProjectXTradeEvent.__table__])
    with Session(engine) as session:
        session.add_all([Account(user_id=OWNER,provider="projectx",external_id="1",name="Mine"),
                         Account(user_id=OTHER,provider="projectx",external_id="2",name="Theirs")])
        session.add_all([ProjectXTradeEvent(id=1,user_id=OWNER,account_id=1,contract_id="MNQ",side="SELL",size=1,
                            price=101,trade_timestamp=datetime.now(timezone.utc),fees=0,pnl=2,order_id="1"),
                         ProjectXTradeEvent(id=2,user_id=OTHER,account_id=2,contract_id="MNQ",side="SELL",size=1,
                            price=101,trade_timestamp=datetime.now(timezone.utc),fees=0,pnl=2,order_id="2")])
        session.commit()
        yield session
    engine.dispose()


def test_all_routes_require_existing_authentication(monkeypatch):
    monkeypatch.setattr(main,"auth_required",lambda:True)
    client=TestClient(main.app)
    try:
        for path in ("status", "positions/unknown"):
            assert client.get("/api/accounts/1/trade-progress/"+path).status_code==401
        assert client.get("/api/accounts/1/trades/1/progress").status_code==401
        assert client.put("/api/accounts/1/trade-progress/enrollment?enabled=true").status_code==401
    finally:
        client.close()


@pytest.mark.parametrize("operation", [
    lambda db: main.get_trade_progress_status(2,db),
    lambda db: main.set_trade_progress_enrollment(2,True,db),
    lambda db: main.get_trade_progress(2,2,db),
    lambda db: main.get_trade_progress_timeline(2,"unknown",0,db),
])
def test_ownership_required_before_local_access(db,operation,monkeypatch):
    monkeypatch.setattr(store,"connect",lambda **kwargs: pytest.fail("local store touched before ownership check"))
    with pytest.raises(HTTPException) as error: operation(db)
    assert error.value.status_code==404


def test_trade_id_cannot_cross_accounts(db):
    with pytest.raises(HTTPException) as error: main.get_trade_progress(1,2,db)
    assert error.value.status_code==404
    result=main.get_trade_progress(1,1,db)
    assert result["matches"]==[] and not result["status"]["enrolled"]


def test_same_external_account_number_does_not_leak_other_owner_position(db):
    recorder=ProgressEngine(OTHER,1,{"MNQ":2})
    recorder.position(dict(id=1,accountId=1,contractId="MNQ",netQty=1,avgPrice=100),store.now())
    identity=recorder.active["MNQ"]["id"]
    recorder.flush()
    with pytest.raises(HTTPException) as error: main.get_trade_progress_timeline(1,identity,0,db)
    assert error.value.status_code==404


def test_enrollment_requires_verified_disk_and_stays_local(db):
    with pytest.raises(HTTPException) as error: main.set_trade_progress_enrollment(1,True,db)
    assert error.value.status_code==409
    report=store.disk_report()
    store.verify_and_register(expected_host=report["host"],expected_root=report["root"],expected_drive=report["drive"])
    status=main.set_trade_progress_enrollment(1,True,db)
    assert status["enrolled"]
    assert status["sample_seconds"]==1
    assert not db.dirty and not db.new
    assert not main.set_trade_progress_enrollment(1,False,db)["enrolled"]
