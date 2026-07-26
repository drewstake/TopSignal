import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.main import create_topstep_live_import_target, set_projectx_main_account
from app.models import Account
from app.projectx_schemas import TopstepLiveAccountCreateIn


USER_ID = "00000000-0000-0000-0000-000000000000"


def _session_factory(tmp_path):
    database_path = tmp_path / "account-concurrency.sqlite3"
    engine = create_engine(
        f"sqlite+pysqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(bind=engine, tables=[Account.__table__])
    return engine, sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_parallel_live_creation_serializes_first_main_assignment(tmp_path):
    engine, SessionLocal = _session_factory(tmp_path)
    start = Barrier(2)

    def create(account_id: int) -> None:
        with SessionLocal() as db:
            start.wait(timeout=5)
            result = create_topstep_live_import_target(
                TopstepLiveAccountCreateIn(
                    account_id=account_id,
                    name=f"Live {account_id}",
                ),
                db=db,
            )
            assert result["id"] == account_id

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(create, account_id) for account_id in (88001, 88002)]
            for future in futures:
                future.result(timeout=10)

        with SessionLocal() as db:
            rows = db.query(Account).order_by(Account.external_id.asc()).all()
            assert [row.external_id for row in rows] == ["88001", "88002"]
            assert sum(bool(row.is_main) for row in rows) == 1
    finally:
        Base.metadata.drop_all(bind=engine, tables=[Account.__table__])
        engine.dispose()


def test_simultaneous_set_main_requests_leave_exactly_one_main(tmp_path):
    engine, SessionLocal = _session_factory(tmp_path)
    with SessionLocal() as db:
        db.add_all(
            [
                Account(
                    user_id=USER_ID,
                    provider="projectx",
                    external_id=str(account_id),
                    trade_data_source="csv_import",
                    name=f"Live {account_id}",
                    account_state="ACTIVE",
                    is_main=account_id == 88001,
                )
                for account_id in (88001, 88002, 88003)
            ]
        )
        db.commit()

    start = Barrier(2)

    def set_main(account_id: int) -> None:
        with SessionLocal() as db:
            start.wait(timeout=5)
            assert set_projectx_main_account(account_id=account_id, db=db) == {
                "account_id": account_id,
                "is_main": True,
            }

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(set_main, account_id) for account_id in (88002, 88003)]
            for future in futures:
                future.result(timeout=10)

        with SessionLocal() as db:
            rows = db.query(Account).order_by(Account.external_id.asc()).all()
            main_rows = [row for row in rows if row.is_main]
            assert len(main_rows) == 1
            assert int(main_rows[0].external_id) in {88002, 88003}
    finally:
        Base.metadata.drop_all(bind=engine, tables=[Account.__table__])
        engine.dispose()
