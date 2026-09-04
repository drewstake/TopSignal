import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.models import Account
from app.services.projectx_accounts import (
    invalidate_projectx_account_classification,
    persist_projectx_account_classification,
    sync_projectx_accounts,
)
import app.services.projectx_hubs as projectx_hubs_module
import app.services.projectx_streaming_runtime as streaming_runtime_module


def _factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _seed(db):
    db.add(
        Account(
            user_id="user-a",
            provider="projectx",
            external_id="101",
            trade_data_source="projectx",
            account_state="ACTIVE",
        )
    )
    db.commit()


def test_gateway_classification_persists_false_without_name_inference():
    engine, factory = _factory()
    observed_at = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
    try:
        with factory() as db:
            _seed(db)
            row = persist_projectx_account_classification(
                db,
                user_id="user-a",
                account_id=101,
                simulated=False,
                observed_at=observed_at,
            )
            db.commit()
            assert row.provider_simulated is False
            assert row.provider_classification_observed_at.replace(tzinfo=timezone.utc) == observed_at
    finally:
        engine.dispose()


def test_disconnect_immediately_invalidates_classification_freshness():
    engine, factory = _factory()
    try:
        with factory() as db:
            _seed(db)
            persist_projectx_account_classification(
                db,
                user_id="user-a",
                account_id=101,
                simulated=True,
            )
            db.commit()
            invalidate_projectx_account_classification(
                db,
                user_id="user-a",
                account_id=101,
            )
            db.commit()
            row = db.query(Account).one()
            assert row.provider_simulated is True
            assert row.provider_classification_observed_at is None
    finally:
        engine.dispose()


def test_rest_account_sync_only_updates_classification_when_boolean_present():
    engine, factory = _factory()
    observed_at = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
    try:
        with factory() as db:
            sync_projectx_accounts(
                db,
                [
                    {
                        "id": 101,
                        "name": "Account 101",
                        "can_trade": True,
                        "is_visible": True,
                        "simulated": True,
                    }
                ],
                user_id="user-a",
                now_utc=observed_at,
            )
            db.commit()
            row = db.query(Account).one()
            assert row.provider_simulated is True
            assert row.provider_classification_observed_at is not None

            sync_projectx_accounts(
                db,
                [
                    {
                        "id": 101,
                        "name": "Account 101",
                        "can_trade": True,
                        "is_visible": True,
                    }
                ],
                user_id="user-a",
                now_utc=observed_at,
            )
            db.commit()
            row = db.query(Account).one()
            assert row.provider_simulated is True
            assert row.provider_classification_observed_at is not None
    finally:
        engine.dispose()


def test_one_shot_refresh_persists_fresh_classification_after_socket_cleanup(monkeypatch):
    engine, factory = _factory()
    sent = []

    class Client:
        timeout_seconds = 1

        def get_access_token(self):
            return "fixture-token"

    class Websocket:
        def __init__(self):
            self.messages = [
                "{}\x1e",
                (
                    '{"type":1,"target":"GatewayUserAccount",'
                    '"arguments":[{"id":101,"simulated":true}]}\x1e'
                ),
            ]

        async def send(self, message):
            sent.append(message)

        async def recv(self):
            return self.messages.pop(0)

    websocket = Websocket()

    class Connection:
        async def __aenter__(self):
            return websocket

        async def __aexit__(self, *_args):
            return False

    try:
        with factory() as db:
            _seed(db)
            persist_projectx_account_classification(
                db,
                user_id="user-a",
                account_id=101,
                simulated=False,
                observed_at=datetime.now(timezone.utc) - timedelta(minutes=4),
            )
            db.commit()
        monkeypatch.setattr(streaming_runtime_module, "SessionLocal", factory)
        monkeypatch.setattr(
            projectx_hubs_module.websockets,
            "connect",
            lambda *_args, **_kwargs: Connection(),
        )

        result = streaming_runtime_module.refresh_projectx_account_classification_once(
            user_id="user-a",
            account_id=101,
            client_factory=Client,
            timeout_seconds=1,
        )

        assert result.provider_simulated is True
        assert result.source == "projectx_user_hub"
        with factory() as db:
            row = db.query(Account).one()
            assert row.provider_simulated is True
            assert row.provider_classification_observed_at is not None
            persisted_at = row.provider_classification_observed_at.replace(tzinfo=timezone.utc)
            assert persisted_at == result.provider_classification_observed_at
            assert persisted_at > datetime.now(timezone.utc) - timedelta(seconds=5)
        assert any("SubscribeAccounts" in message for message in sent)
    finally:
        engine.dispose()


def test_failed_one_shot_refresh_invalidates_cached_freshness(monkeypatch):
    engine, factory = _factory()

    class TimedOutRunner:
        def __init__(self, **_kwargs):
            pass

        async def probe_user_account_once(self, *, timeout_seconds):
            del timeout_seconds
            raise asyncio.TimeoutError()

    try:
        with factory() as db:
            _seed(db)
            persist_projectx_account_classification(
                db,
                user_id="user-a",
                account_id=101,
                simulated=True,
            )
            db.commit()
        monkeypatch.setattr(streaming_runtime_module, "SessionLocal", factory)
        monkeypatch.setattr(streaming_runtime_module, "ProjectXHubRunner", TimedOutRunner)

        with pytest.raises(
            streaming_runtime_module.ProjectXAccountClassificationProbeTimeout
        ):
            streaming_runtime_module.refresh_projectx_account_classification_once(
                user_id="user-a",
                account_id=101,
                client_factory=lambda: None,
                timeout_seconds=1,
            )

        with factory() as db:
            row = db.query(Account).one()
            assert row.provider_simulated is True
            assert row.provider_classification_observed_at is None
    finally:
        engine.dispose()
