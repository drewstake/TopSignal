import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Account
from app.services.local_account_exclusions import (
    excluded_local_projectx_accounts,
    local_account_exclusions_path,
)
from app.services.projectx_accounts import sync_projectx_accounts


USER_A = "00000000-0000-0000-0000-000000000000"
USER_B = "00000000-0000-0000-0000-000000000001"


def test_deleted_local_account_stays_deleted_across_refresh_and_restart(tmp_path):
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'workspace.sqlite3').as_posix()}"
    provider_rows = [
        {"id": 123, "name": "Removed", "can_trade": False, "is_visible": True},
        {"id": 456, "name": "Keep", "can_trade": True, "is_visible": True},
    ]
    engine = create_engine(database_url)
    Account.__table__.create(engine)
    with Session(engine) as db:
        path = local_account_exclusions_path(db)
        path.write_text(json.dumps({"version": 1, "users": {USER_A: ["123"]}}), encoding="utf-8")
        sync_projectx_accounts(db, provider_rows, user_id=USER_A)
        sync_projectx_accounts(db, provider_rows, user_id=USER_B)
        db.commit()
        assert [a.external_id for a in db.query(Account).filter_by(user_id=USER_A)] == ["456"]
        assert {a.external_id for a in db.query(Account).filter_by(user_id=USER_B)} == {"123", "456"}
    engine.dispose()

    restarted_engine = create_engine(database_url)
    with Session(restarted_engine) as db:
        sync_projectx_accounts(db, provider_rows, user_id=USER_A)
        db.commit()
        assert [a.external_id for a in db.query(Account).filter_by(user_id=USER_A)] == ["456"]
    restarted_engine.dispose()


def test_exclusions_belong_to_one_database(tmp_path):
    engines = [create_engine(f"sqlite+pysqlite:///{(tmp_path / name).as_posix()}") for name in ("one.sqlite3", "two.sqlite3")]
    try:
        with Session(engines[0]) as first, Session(engines[1]) as second:
            local_account_exclusions_path(first).write_text(json.dumps({"version": 1, "users": {USER_A: ["123"]}}), encoding="utf-8")
            assert excluded_local_projectx_accounts(first, user_id=USER_A) == {"123"}
            assert excluded_local_projectx_accounts(second, user_id=USER_A) == set()
    finally:
        for engine in engines:
            engine.dispose()


@pytest.mark.parametrize("content", ["{", "[]", '{"version":1,"users":[]}', '{"version":1,"users":{"' + USER_A + '":["invalid"]}}'])
def test_invalid_exclusions_stop_import_instead_of_recreating_deleted_accounts(tmp_path, content):
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'workspace.sqlite3').as_posix()}")
    try:
        with Session(engine) as db:
            local_account_exclusions_path(db).write_text(content, encoding="utf-8")
            with pytest.raises(ValueError, match="Invalid local ProjectX"):
                excluded_local_projectx_accounts(db, user_id=USER_A)
    finally:
        engine.dispose()
