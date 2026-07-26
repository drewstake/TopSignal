from __future__ import annotations

import io
import os

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.main as main_module
from app.db import Base
from app.models import Account, ProjectXTradeEvent, TradeImportBatch, TradeImportPreview
from app.projectx_schemas import TopstepTradeImportStatusIn

from test_topstep_trade_imports import ACCOUNT_ID, USER_ID, _csv_bytes, _trade_row


@pytest.fixture()
def route_db(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Account.__table__,
            TradeImportBatch.__table__,
            TradeImportPreview.__table__,
            ProjectXTradeEvent.__table__,
        ],
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    session.add(
        Account(
            id=9901,
            user_id=USER_ID,
            provider="projectx",
            external_id=str(ACCOUNT_ID),
            name="Topstep Live",
            trade_data_source="csv_import",
            account_state="ACTIVE",
        )
    )
    session.commit()
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: USER_ID)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_preview_confirm_and_status_routes_use_one_upload_and_persist_outcome(route_db):
    upload = UploadFile(
        filename="topstep.csv",
        file=io.BytesIO(_csv_bytes([_trade_row()])),
    )

    preview = main_module.preview_topstep_trade_import(
        account_id=ACCOUNT_ID,
        file=upload,
        db=route_db,
    )
    assert upload.file.closed is True
    assert preview["new_rows"] == 1
    assert preview["conflict_rows"] == 0

    confirmed = main_module.confirm_topstep_trade_import(
        account_id=ACCOUNT_ID,
        preview_token=preview["preview_token"],
        db=route_db,
    )
    recovered = main_module.get_topstep_trade_import_status(
        account_id=ACCOUNT_ID,
        payload=TopstepTradeImportStatusIn(preview_token=preview["preview_token"]),
        db=route_db,
    )

    assert confirmed["inserted_rows"] == 1
    assert recovered["status"] == "committed"
    assert recovered["result"] == confirmed
    assert route_db.query(TradeImportBatch).one().account_row_id == 9901
    assert route_db.query(ProjectXTradeEvent).one().account_row_id == 9901


def test_status_route_maps_unknown_account_scoped_token_to_not_found(route_db):
    with pytest.raises(HTTPException) as exc_info:
        main_module.get_topstep_trade_import_status(
            account_id=ACCOUNT_ID,
            payload=TopstepTradeImportStatusIn(preview_token="x" * 43),
            db=route_db,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "preview_not_found"


def test_status_route_keeps_preview_token_out_of_url_paths():
    matching_routes = [
        route
        for route in main_module.app.routes
        if getattr(route, "path", "").startswith("/api/accounts/{account_id}/trade-imports/status")
    ]

    assert len(matching_routes) == 1
    assert matching_routes[0].path == "/api/accounts/{account_id}/trade-imports/status"
    assert matching_routes[0].methods == {"POST"}
    assert "preview_token" not in matching_routes[0].path
