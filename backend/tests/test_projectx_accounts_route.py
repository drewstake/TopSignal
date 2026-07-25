import io
import logging
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.main as main_module
from app.db import Base
from app.main import (
    confirm_topstep_trade_import,
    create_topstep_live_import_target,
    get_projectx_account_last_trade,
    list_projectx_accounts,
    preview_topstep_trade_import,
    rename_projectx_account,
    set_projectx_main_account,
    update_projectx_account_trade_data_source,
)
from app.models import Account, ProjectXTradeEvent, ProviderCredential
from app.projectx_schemas import (
    ProjectXAccountRenameIn,
    ProjectXAccountTradeDataSourceIn,
    TopstepLiveAccountCreateIn,
)


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[Account.__table__, ProjectXTradeEvent.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXTradeEvent.__table__, Account.__table__])
        engine.dispose()


@pytest.fixture(autouse=True)
def allow_legacy_env_projectx_credentials(monkeypatch):
    monkeypatch.setenv("ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS", "true")
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:54321")


def test_accounts_route_default_view_shows_active_plus_main_with_state_sync(db_session, monkeypatch):
    class StubClient:
        def list_accounts(self, *, only_active_accounts=True):
            assert only_active_accounts is False
            return [
                {"id": 7001, "name": "Alpha", "balance": 25000.0, "can_trade": False, "is_visible": True},
                {"id": 7002, "name": "Bravo", "balance": 50000.0, "can_trade": True, "is_visible": True},
            ]

    monkeypatch.setattr(main_module.ProjectXClient, "from_env", lambda: StubClient())

    db_session.add(
        Account(
            provider="projectx",
            external_id="7999",
            name="Main Legacy",
            account_state="ACTIVE",
            is_main=True,
            first_seen_at=datetime.now(timezone.utc) - timedelta(days=7),
            last_seen_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
    )
    db_session.commit()

    payload = list_projectx_accounts(show_inactive=False, show_missing=False, db=db_session)
    by_id = {int(account["id"]): account for account in payload}

    assert sorted(by_id.keys()) == [7002, 7999]
    assert by_id[7002]["account_state"] == "ACTIVE"
    assert by_id[7002]["is_main"] is False
    assert by_id[7999]["account_state"] == "MISSING"
    assert by_id[7999]["is_main"] is True

    locked_out_row = (
        db_session.query(Account)
        .filter(Account.provider == "projectx")
        .filter(Account.external_id == "7001")
        .one()
    )
    assert locked_out_row.account_state == "LOCKED_OUT"
    assert locked_out_row.first_seen_at is not None
    assert locked_out_row.last_seen_at is not None


def test_accounts_route_filters_inactive_and_missing_states(db_session, monkeypatch):
    class StubClient:
        def list_accounts(self, *, only_active_accounts=True):
            assert only_active_accounts is False
            return [
                {"id": 7101, "name": "Active", "balance": 10000.0, "can_trade": True, "is_visible": True},
                {"id": 7102, "name": "Locked", "balance": 20000.0, "can_trade": False, "is_visible": True},
                {"id": 7103, "name": "Hidden", "balance": 30000.0, "can_trade": True, "is_visible": False},
            ]

    monkeypatch.setattr(main_module.ProjectXClient, "from_env", lambda: StubClient())
    db_session.add(
        Account(
            provider="projectx",
            external_id="7199",
            name="Missing",
            account_state="ACTIVE",
            first_seen_at=datetime.now(timezone.utc) - timedelta(days=3),
            last_seen_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
    )
    db_session.commit()

    with_inactive = list_projectx_accounts(show_inactive=True, show_missing=False, db=db_session)
    ids_with_inactive = sorted(int(row["id"]) for row in with_inactive)
    assert ids_with_inactive == [7101, 7102, 7103]

    with_missing = list_projectx_accounts(show_inactive=False, show_missing=True, db=db_session)
    ids_with_missing = sorted(int(row["id"]) for row in with_missing)
    assert ids_with_missing == [7101, 7199]

    by_id = {int(row["id"]): row for row in with_missing}
    assert by_id[7199]["account_state"] == "MISSING"


def test_accounts_route_returns_provider_and_custom_display_names(db_session, monkeypatch):
    class StubClient:
        def list_accounts(self, *, only_active_accounts=True):
            assert only_active_accounts is False
            return [
                {
                    "id": 7301,
                    "name": "50KTC-7301",
                    "balance": 12500.0,
                    "can_trade": True,
                    "is_visible": True,
                }
            ]

    monkeypatch.setattr(main_module.ProjectXClient, "from_env", lambda: StubClient())
    db_session.add(
        Account(
            provider="projectx",
            external_id="7301",
            name="Old Provider Name",
            display_name="Personal Account",
            account_state="ACTIVE",
        )
    )
    db_session.commit()

    payload = list_projectx_accounts(show_inactive=False, show_missing=False, db=db_session)

    assert payload == [
        {
            "id": 7301,
            "name": "Personal Account",
            "provider_name": "50KTC-7301",
            "custom_display_name": "Personal Account",
            "balance": 12500.0,
            "status": "ACTIVE",
            "account_state": "ACTIVE",
            "is_main": False,
            "can_trade": True,
                "is_visible": True,
                "last_trade_at": None,
                "last_seen_at": payload[0]["last_seen_at"],
                "provider_data_stale": False,
                "trade_data_source": "projectx",
            }
        ]
    assert isinstance(payload[0]["last_seen_at"], datetime)


def test_accounts_route_normalizes_provider_ids_when_attaching_provider_fields(db_session, monkeypatch):
    class StubClient:
        def list_accounts(self, *, only_active_accounts=True):
            assert only_active_accounts is False
            return [
                {
                    "id": "007304",
                    "name": "50KTC-7304",
                    "balance": 12500.0,
                    "can_trade": True,
                    "is_visible": True,
                }
            ]

    monkeypatch.setattr(main_module.ProjectXClient, "from_env", lambda: StubClient())

    payload = list_projectx_accounts(show_inactive=False, show_missing=False, db=db_session)

    assert payload == [
        {
            "id": 7304,
            "name": "50KTC-7304",
            "provider_name": "50KTC-7304",
            "custom_display_name": None,
            "balance": 12500.0,
            "status": "ACTIVE",
            "account_state": "ACTIVE",
            "is_main": False,
            "can_trade": True,
                "is_visible": True,
                "last_trade_at": None,
                "last_seen_at": payload[0]["last_seen_at"],
                "provider_data_stale": False,
                "trade_data_source": "projectx",
            }
        ]
    assert isinstance(payload[0]["last_seen_at"], datetime)

    row = (
        db_session.query(Account)
        .filter(Account.provider == "projectx")
        .filter(Account.external_id == "7304")
        .one()
    )
    assert row.name == "50KTC-7304"


def test_create_live_import_target_is_local_missing_and_idempotent(db_session):
    payload = TopstepLiveAccountCreateIn(
        account_id=88001,
        name="Topstep Live Funded",
    )

    first = create_topstep_live_import_target(payload=payload, db=db_session)
    second = create_topstep_live_import_target(payload=payload, db=db_session)

    assert first == second
    assert first["id"] == 88001
    assert first["name"] == "Topstep Live Funded"
    assert first["account_state"] == "ACTIVE"
    assert first["can_trade"] is None
    assert first["is_main"] is True
    assert first["provider_data_stale"] is False
    assert first["trade_data_source"] == "csv_import"
    assert db_session.query(Account).count() == 1

    row = db_session.query(Account).one()
    assert row.external_id == "88001"
    assert row.last_seen_at is None
    assert row.last_missing_at is None


def test_create_live_import_target_keeps_existing_csv_account_unchanged(db_session):
    last_missing_at = datetime.now(timezone.utc) - timedelta(days=1)
    db_session.add(
        Account(
            provider="projectx",
            external_id="88003",
            name="Topstep Live Existing",
            trade_data_source="csv_import",
            account_state="MISSING",
            last_missing_at=last_missing_at,
            is_main=True,
        )
    )
    db_session.commit()

    payload = create_topstep_live_import_target(
        payload=TopstepLiveAccountCreateIn(
            account_id=88003,
            name="Replacement Name Must Not Win",
        ),
        db=db_session,
    )

    assert payload["trade_data_source"] == "csv_import"
    assert payload["name"] == "Topstep Live Existing"
    assert db_session.query(Account).count() == 1
    row = db_session.query(Account).one()
    assert row.account_state == "MISSING"
    assert row.last_missing_at == last_missing_at.replace(tzinfo=None)


def test_create_live_import_target_rejects_existing_projectx_account(db_session):
    db_session.add(
        Account(
            provider="projectx",
            external_id="88004",
            name="EXPRESS-V2-DLL-192577-19008334",
            trade_data_source="projectx",
            account_state="ACTIVE",
            is_main=True,
        )
    )
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        create_topstep_live_import_target(
            payload=TopstepLiveAccountCreateIn(
                account_id=88004,
                name="Topstep Live Funded",
            ),
            db=db_session,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "code": "account_trade_data_source_conflict",
        "message": (
            "Account 88004 already uses projectx; cross-source conversion to "
            "csv_import is not allowed. ProjectX and Live CSV accounts must remain separate."
        ),
        "account_id": 88004,
        "current_trade_data_source": "projectx",
        "requested_trade_data_source": "csv_import",
    }
    row = db_session.query(Account).one()
    assert row.name == "EXPRESS-V2-DLL-192577-19008334"
    assert row.trade_data_source == "projectx"


def test_create_live_import_target_rejects_unsafe_name(db_session):
    with pytest.raises(HTTPException) as exc_info:
        create_topstep_live_import_target(
            payload=TopstepLiveAccountCreateIn(
                account_id=88002,
                name="Unsafe\x00Name",
            ),
            db=db_session,
        )

    assert exc_info.value.status_code == 400
    assert db_session.query(Account).count() == 0


def test_accounts_route_skips_projectx_when_every_account_uses_csv_import(
    db_session,
    monkeypatch,
):
    db_session.add(
        Account(
            provider="projectx",
            external_id="88010",
            name="Topstep Live",
            trade_data_source="csv_import",
            account_state="ACTIVE",
            is_main=False,
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        main_module,
        "_projectx_client_for_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("CSV-only account lists must not load ProjectX credentials")
        ),
    )

    payload = list_projectx_accounts(
        show_inactive=False,
        show_missing=False,
        db=db_session,
    )

    assert [row["id"] for row in payload] == [88010]
    assert payload[0]["trade_data_source"] == "csv_import"
    assert payload[0]["provider_data_stale"] is False


def test_accounts_route_local_snapshot_skips_projectx_for_mixed_accounts(
    db_session,
    monkeypatch,
):
    db_session.add_all(
        [
            Account(
                provider="projectx",
                external_id="88020",
                name="Topstep Live Funded",
                trade_data_source="csv_import",
                account_state="ACTIVE",
                is_main=True,
            ),
            Account(
                provider="projectx",
                external_id="88021",
                name="Express Account",
                trade_data_source="projectx",
                account_state="ACTIVE",
                balance=50000,
                can_trade=True,
                is_visible=True,
                is_main=False,
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(
        main_module,
        "_projectx_client_for_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("A local account snapshot must not contact ProjectX")
        ),
    )

    payload = list_projectx_accounts(
        show_inactive=True,
        show_missing=False,
        refresh_provider=False,
        db=db_session,
    )
    by_id = {row["id"]: row for row in payload}

    assert list(by_id) == [88020, 88021]
    assert by_id[88020]["is_main"] is True
    assert by_id[88020]["trade_data_source"] == "csv_import"
    assert by_id[88020]["provider_data_stale"] is False
    assert by_id[88021]["trade_data_source"] == "projectx"
    assert by_id[88021]["provider_data_stale"] is True
    assert by_id[88021]["balance"] == 50000


def test_provider_sync_skips_csv_id_collision_and_missing_transition(
    db_session,
    monkeypatch,
):
    old_seen_at = datetime.now(timezone.utc) - timedelta(days=2)
    db_session.add_all(
        [
            Account(
                provider="projectx",
                external_id="88011",
                name="Local Live Name",
                balance=43210,
                trade_data_source="csv_import",
                account_state="ACTIVE",
                last_seen_at=old_seen_at,
                is_main=False,
            ),
            Account(
                provider="projectx",
                external_id="88012",
                name="Provider Account",
                trade_data_source="projectx",
                account_state="ACTIVE",
                last_seen_at=old_seen_at,
                is_main=True,
            ),
            Account(
                provider="projectx",
                external_id="88016",
                name="Local Live Omitted By Provider",
                trade_data_source="csv_import",
                account_state="ACTIVE",
                last_seen_at=old_seen_at,
                is_main=False,
            ),
        ]
    )
    db_session.commit()

    class StubClient:
        def list_accounts(self, *, only_active_accounts=True):
            assert only_active_accounts is False
            return [
                {
                    "id": 88011,
                    "name": "Provider Must Not Win",
                    "balance": 99999,
                    "can_trade": False,
                    "is_visible": False,
                },
                {
                    "id": 88012,
                    "name": "Provider Account",
                    "balance": 50000,
                    "can_trade": True,
                    "is_visible": True,
                },
            ]

    monkeypatch.setattr(
        main_module,
        "_projectx_client_for_user",
        lambda *_args, **_kwargs: StubClient(),
    )

    payload = list_projectx_accounts(
        show_inactive=True,
        show_missing=True,
        db=db_session,
    )
    by_id = {row["id"]: row for row in payload}

    assert db_session.query(Account).count() == 3
    assert by_id[88011]["name"] == "Local Live Name"
    assert by_id[88011]["balance"] is None
    assert by_id[88011]["can_trade"] is None
    assert by_id[88011]["last_seen_at"] is None
    assert by_id[88011]["account_state"] == "ACTIVE"
    assert by_id[88011]["provider_data_stale"] is False
    assert by_id[88011]["trade_data_source"] == "csv_import"
    assert by_id[88016]["account_state"] == "ACTIVE"
    assert by_id[88016]["provider_data_stale"] is False

    stored_csv_account = (
        db_session.query(Account)
        .filter(Account.external_id == "88011")
        .one()
    )
    assert stored_csv_account.name == "Local Live Name"
    assert float(stored_csv_account.balance) == pytest.approx(43210)
    assert stored_csv_account.account_state == "ACTIVE"


def test_trade_import_routes_reject_projectx_accounts_before_reading_files(
    db_session,
):
    db_session.add(
        Account(
            provider="projectx",
            external_id="88017",
            name="Provider Account",
            trade_data_source="projectx",
            account_state="ACTIVE",
        )
    )
    db_session.commit()

    preview_file = UploadFile(
        filename="trades.csv",
        file=io.BytesIO(b"must not be parsed"),
    )
    with pytest.raises(HTTPException) as preview_exc:
        preview_topstep_trade_import(
            account_id=88017,
            file=preview_file,
            db=db_session,
        )
    assert preview_exc.value.status_code == 409
    assert preview_exc.value.detail == "trade_import_requires_csv_import_account"
    assert preview_file.file.tell() == 0
    preview_file.file.close()

    confirm_file = UploadFile(
        filename="trades.csv",
        file=io.BytesIO(b"must not be parsed"),
    )
    with pytest.raises(HTTPException) as confirm_exc:
        confirm_topstep_trade_import(
            account_id=88017,
            file=confirm_file,
            preview_sha256="0" * 64,
            db=db_session,
        )
    assert confirm_exc.value.status_code == 409
    assert confirm_exc.value.detail == "trade_import_requires_csv_import_account"
    assert confirm_file.file.tell() == 0
    confirm_file.file.close()


def test_mixed_account_provider_outage_marks_only_projectx_rows_stale(
    db_session,
    monkeypatch,
):
    db_session.add_all(
        [
            Account(
                provider="projectx",
                external_id="88014",
                name="Local Live",
                trade_data_source="csv_import",
                account_state="ACTIVE",
                is_main=False,
            ),
            Account(
                provider="projectx",
                external_id="88015",
                name="API Account",
                trade_data_source="projectx",
                account_state="ACTIVE",
                is_main=True,
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(
        main_module,
        "_projectx_client_for_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            main_module.ProjectXClientError(
                "provider unavailable",
                status_code=503,
            )
        ),
    )

    payload = list_projectx_accounts(
        show_inactive=True,
        show_missing=True,
        db=db_session,
    )
    by_id = {row["id"]: row for row in payload}

    assert by_id[88014]["provider_data_stale"] is False
    assert by_id[88015]["provider_data_stale"] is True


def test_trade_data_source_patch_is_idempotent_for_same_source(db_session):
    csv_missing_at = datetime.now(timezone.utc) - timedelta(days=1)
    db_session.add_all(
        [
            Account(
                provider="projectx",
                external_id="88013",
                name="Paused XFA",
                trade_data_source="projectx",
                account_state="MISSING",
                last_missing_at=datetime.now(timezone.utc),
                is_main=True,
            ),
            Account(
                provider="projectx",
                external_id="88018",
                name="Topstep Live",
                trade_data_source="csv_import",
                account_state="MISSING",
                last_missing_at=csv_missing_at,
                is_main=False,
            ),
        ]
    )
    db_session.commit()

    projectx_payload = update_projectx_account_trade_data_source(
        account_id=88013,
        payload=ProjectXAccountTradeDataSourceIn(
            trade_data_source="projectx",
        ),
        db=db_session,
    )
    csv_payload = update_projectx_account_trade_data_source(
        account_id=88018,
        payload=ProjectXAccountTradeDataSourceIn(
            trade_data_source="csv_import",
        ),
        db=db_session,
    )

    assert projectx_payload["id"] == 88013
    assert projectx_payload["trade_data_source"] == "projectx"
    assert projectx_payload["provider_data_stale"] is True
    assert projectx_payload["account_state"] == "MISSING"
    assert csv_payload["id"] == 88018
    assert csv_payload["trade_data_source"] == "csv_import"
    assert csv_payload["provider_data_stale"] is False
    assert csv_payload["account_state"] == "MISSING"

    rows = {
        row.external_id: row
        for row in db_session.query(Account).all()
    }
    assert rows["88013"].trade_data_source == "projectx"
    assert rows["88018"].trade_data_source == "csv_import"
    assert rows["88018"].last_missing_at == csv_missing_at.replace(tzinfo=None)


@pytest.mark.parametrize(
    ("account_id", "current_source", "requested_source"),
    [
        (88019, "projectx", "csv_import"),
        (88020, "csv_import", "projectx"),
    ],
)
def test_trade_data_source_patch_rejects_cross_source_conversion(
    db_session,
    account_id,
    current_source,
    requested_source,
):
    db_session.add(
        Account(
            provider="projectx",
            external_id=str(account_id),
            name="Express Account" if current_source == "projectx" else "Topstep Live",
            trade_data_source=current_source,
            account_state="ACTIVE",
            is_main=True,
        )
    )
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        update_projectx_account_trade_data_source(
            account_id=account_id,
            payload=ProjectXAccountTradeDataSourceIn(
                trade_data_source=requested_source,
            ),
            db=db_session,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "code": "account_trade_data_source_conflict",
        "message": (
            f"Account {account_id} already uses {current_source}; "
            f"cross-source conversion to {requested_source} is not allowed. "
            "ProjectX and Live CSV accounts must remain separate."
        ),
        "account_id": account_id,
        "current_trade_data_source": current_source,
        "requested_trade_data_source": requested_source,
    }
    assert db_session.query(Account).one().trade_data_source == current_source


def test_set_main_account_endpoint_keeps_single_main_flag(db_session):
    db_session.add_all(
        [
            Account(provider="projectx", external_id="7201", name="One", account_state="ACTIVE", is_main=False),
            Account(provider="projectx", external_id="7202", name="Two", account_state="ACTIVE", is_main=False),
        ]
    )
    db_session.commit()

    first = set_projectx_main_account(account_id=7201, db=db_session)
    second = set_projectx_main_account(account_id=7202, db=db_session)

    assert first == {"account_id": 7201, "is_main": True}
    assert second == {"account_id": 7202, "is_main": True}

    rows = (
        db_session.query(Account)
        .filter(Account.provider == "projectx")
        .order_by(Account.external_id.asc())
        .all()
    )
    assert [bool(row.is_main) for row in rows] == [False, True]


def test_set_main_account_endpoint_is_idempotent_for_existing_main(db_session):
    db_session.add_all(
        [
            Account(provider="projectx", external_id="7201", name="One", account_state="ACTIVE", is_main=True),
            Account(provider="projectx", external_id="7202", name="Two", account_state="ACTIVE", is_main=False),
        ]
    )
    db_session.commit()

    payload = set_projectx_main_account(account_id=7201, db=db_session)

    assert payload == {"account_id": 7201, "is_main": True}
    rows = (
        db_session.query(Account)
        .filter(Account.provider == "projectx")
        .order_by(Account.external_id.asc())
        .all()
    )
    assert [bool(row.is_main) for row in rows] == [True, False]


def test_set_main_account_endpoint_rejects_unknown_accounts(db_session):
    with pytest.raises(HTTPException) as exc_info:
        set_projectx_main_account(account_id=7299, db=db_session)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Account not found."
    assert db_session.query(Account).count() == 0


def test_rename_account_endpoint_trims_and_persists_custom_display_name(db_session):
    db_session.add(
        Account(
            provider="projectx",
            external_id="7302",
            name="Provider Alpha",
            account_state="ACTIVE",
        )
    )
    db_session.commit()

    payload = rename_projectx_account(
        account_id=7302,
        payload=ProjectXAccountRenameIn(display_name="  My Alpha  "),
        db=db_session,
    )

    assert payload == {
        "account_id": 7302,
        "name": "My Alpha",
        "provider_name": "Provider Alpha",
        "custom_display_name": "My Alpha",
    }

    row = (
        db_session.query(Account)
        .filter(Account.provider == "projectx")
        .filter(Account.external_id == "7302")
        .one()
    )
    assert row.name == "Provider Alpha"
    assert row.display_name == "My Alpha"


def test_rename_account_endpoint_rejects_blank_names(db_session):
    db_session.add(
        Account(
            provider="projectx",
            external_id="7303",
            name="Provider Bravo",
            display_name="Keep Me",
            account_state="ACTIVE",
        )
    )
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        rename_projectx_account(
            account_id=7303,
            payload=ProjectXAccountRenameIn(display_name="   "),
            db=db_session,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Account name cannot be empty."

    row = (
        db_session.query(Account)
        .filter(Account.provider == "projectx")
        .filter(Account.external_id == "7303")
        .one()
    )
    assert row.name == "Provider Bravo"
    assert row.display_name == "Keep Me"


def test_rename_account_endpoint_rejects_overlong_names(db_session):
    db_session.add(
        Account(
            provider="projectx",
            external_id="7304",
            name="Provider Charlie",
            display_name="Keep Me",
            account_state="ACTIVE",
        )
    )
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        rename_projectx_account(
            account_id=7304,
            payload=ProjectXAccountRenameIn(display_name="A" * 121),
            db=db_session,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Account name must be 120 characters or fewer."

    row = (
        db_session.query(Account)
        .filter(Account.provider == "projectx")
        .filter(Account.external_id == "7304")
        .one()
    )
    assert row.display_name == "Keep Me"


def test_rename_account_endpoint_rejects_control_characters(db_session):
    db_session.add(
        Account(
            provider="projectx",
            external_id="7305",
            name="Provider Delta",
            display_name="Keep Me",
            account_state="ACTIVE",
        )
    )
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        rename_projectx_account(
            account_id=7305,
            payload=ProjectXAccountRenameIn(display_name="Bad\nName"),
            db=db_session,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Account name cannot contain control characters."

    row = (
        db_session.query(Account)
        .filter(Account.provider == "projectx")
        .filter(Account.external_id == "7305")
        .one()
    )
    assert row.display_name == "Keep Me"


def test_account_last_trade_endpoint_returns_local_value_without_provider_call(db_session, monkeypatch):
    class StubClient:
        def fetch_last_trade_timestamp(self, account_id, *, lookback_days):
            raise AssertionError("provider call should not happen when local timestamp exists")

    monkeypatch.setattr(main_module.ProjectXClient, "from_env", lambda: StubClient())

    db_session.add(
        ProjectXTradeEvent(
            id=10,
            account_id=7010,
            contract_id="CON.F.US.MNQ.H26",
            symbol="MNQ",
            side="BUY",
            size=1.0,
            price=20500.0,
            trade_timestamp=datetime(2026, 2, 4, 9, 15, tzinfo=timezone.utc),
            fees=1.2,
            order_id="LOCAL-1",
        )
    )
    db_session.commit()

    payload = get_projectx_account_last_trade(account_id=7010, refresh=False, db=db_session)
    assert payload["last_trade_at"] == datetime(2026, 2, 4, 9, 15, tzinfo=timezone.utc)
    assert payload["source"] == "local"


def test_account_last_trade_endpoint_uses_provider_when_local_missing(db_session, monkeypatch):
    class StubClient:
        def __init__(self):
            self.calls = []

        def fetch_last_trade_timestamp(self, account_id, *, lookback_days):
            self.calls.append((account_id, lookback_days))
            if account_id == 7020:
                return datetime(2026, 1, 29, 17, 5, tzinfo=timezone.utc)
            return None

    client = StubClient()
    monkeypatch.setattr(main_module.ProjectXClient, "from_env", lambda: client)

    payload = get_projectx_account_last_trade(account_id=7020, refresh=False, db=db_session)
    assert payload["last_trade_at"] == datetime(2026, 1, 29, 17, 5, tzinfo=timezone.utc)
    assert payload["source"] == "provider"
    assert client.calls == [(7020, 3650)]


def test_csv_import_last_trade_refresh_stays_local(db_session, monkeypatch):
    db_session.add_all(
        [
            Account(
                provider="projectx",
                external_id="7021",
                name="Topstep Live",
                trade_data_source="csv_import",
                account_state="ACTIVE",
            ),
            ProjectXTradeEvent(
                id=11,
                account_id=7021,
                contract_id="CON.F.US.MNQ.H26",
                symbol="MNQ",
                side="BUY",
                size=1.0,
                price=20500.0,
                trade_timestamp=datetime(2026, 2, 5, 9, 15, tzinfo=timezone.utc),
                fees=1.2,
                order_id="LOCAL-CSV-1",
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(
        main_module,
        "_projectx_client_for_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("CSV last-trade reads must stay local")
        ),
    )

    payload = get_projectx_account_last_trade(
        account_id=7021,
        refresh=True,
        db=db_session,
    )

    assert payload["last_trade_at"] == datetime(
        2026,
        2,
        5,
        9,
        15,
        tzinfo=timezone.utc,
    )
    assert payload["source"] == "local"


def test_authenticated_mode_does_not_fall_back_to_shared_env_credentials(db_session, monkeypatch):
    Base.metadata.create_all(bind=db_session.bind, tables=[ProviderCredential.__table__])
    monkeypatch.delenv("ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS", raising=False)
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://app.example.com")
    monkeypatch.setenv("ALLOWED_ORIGIN_REGEX", "")
    monkeypatch.setattr(
        main_module.ProjectXClient,
        "from_env",
        lambda: (_ for _ in ()).throw(AssertionError("shared env credentials should not be used")),
    )

    with pytest.raises(HTTPException) as exc_info:
        list_projectx_accounts(show_inactive=False, show_missing=False, db=db_session)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "projectx_credentials_not_configured"


def test_local_only_origins_do_not_enable_shared_credentials_in_cloud_runtime(db_session, monkeypatch):
    Base.metadata.create_all(bind=db_session.bind, tables=[ProviderCredential.__table__])
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:5173")
    monkeypatch.setenv("ALLOWED_ORIGIN_REGEX", main_module._LOCAL_ORIGIN_REGEX)
    monkeypatch.setattr(
        main_module.ProjectXClient,
        "from_env",
        lambda: (_ for _ in ()).throw(AssertionError("shared env credentials should not be used")),
    )

    with pytest.raises(HTTPException) as exc_info:
        list_projectx_accounts(show_inactive=False, show_missing=False, db=db_session)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "projectx_credentials_not_configured"


def test_accounts_route_serves_last_known_local_balance_during_provider_outage(db_session, monkeypatch):
    db_session.add(
        Account(
            provider="projectx",
            external_id="7102",
            name="Cached Account",
            balance=12345.67,
            account_state="ACTIVE",
            is_main=True,
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        main_module.ProjectXClient,
        "from_env",
        lambda: (_ for _ in ()).throw(
            main_module.ProjectXClientError("provider unavailable", status_code=503)
        ),
    )

    payload = list_projectx_accounts(show_inactive=False, show_missing=False, db=db_session)

    assert [int(row["id"]) for row in payload] == [7102]
    assert payload[0]["balance"] == pytest.approx(12345.67)
    assert payload[0]["provider_data_stale"] is True


def test_accounts_route_preserves_unknown_balance_during_provider_outage(db_session, monkeypatch):
    db_session.add(
        Account(
            provider="projectx",
            external_id="7103",
            name="Never Synced",
            balance=None,
            account_state="ACTIVE",
            is_main=True,
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        main_module.ProjectXClient,
        "from_env",
        lambda: (_ for _ in ()).throw(
            main_module.ProjectXClientError("provider unavailable", status_code=503)
        ),
    )

    payload = list_projectx_accounts(show_inactive=False, show_missing=False, db=db_session)

    assert payload[0]["balance"] is None
    assert payload[0]["provider_data_stale"] is True


def test_account_sync_does_not_erase_last_known_balance_when_provider_omits_it(db_session, monkeypatch):
    class StubClient:
        def list_accounts(self, *, only_active_accounts=True):
            assert only_active_accounts is False
            return [
                {
                    "id": 7104,
                    "name": "Partial Provider Account",
                    "balance": None,
                    "can_trade": True,
                    "is_visible": True,
                }
            ]

    db_session.add(
        Account(
            provider="projectx",
            external_id="7104",
            name="Cached Account",
            balance=45678.9,
            account_state="ACTIVE",
            is_main=True,
        )
    )
    db_session.commit()
    monkeypatch.setattr(main_module.ProjectXClient, "from_env", lambda: StubClient())

    payload = list_projectx_accounts(show_inactive=False, show_missing=False, db=db_session)

    assert payload[0]["balance"] == pytest.approx(45678.9)
    stored_balance = db_session.query(Account).filter(Account.external_id == "7104").one().balance
    assert float(stored_balance) == pytest.approx(45678.9)


def test_accounts_route_falls_back_to_env_credentials_when_stored_credentials_are_unavailable_in_local_dev(
    db_session, monkeypatch, caplog
):
    Base.metadata.create_all(bind=db_session.bind, tables=[ProviderCredential.__table__])
    monkeypatch.delenv("CREDENTIALS_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("ALLOW_INSECURE_LOCAL_CREDENTIALS_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@db.example.com:5432/postgres")

    db_session.add(
        ProviderCredential(
            user_id="00000000-0000-0000-0000-000000000000",
            provider="projectx",
            username_encrypted="unreadable",
            api_key_encrypted="unreadable",
        )
    )
    db_session.commit()

    class StubClient:
        def list_accounts(self, *, only_active_accounts=True):
            assert only_active_accounts is False
            return [{"id": 7101, "name": "Active", "balance": 10000.0, "can_trade": True, "is_visible": True}]

    monkeypatch.setattr(main_module.ProjectXClient, "from_env", lambda: StubClient())
    caplog.set_level(logging.WARNING, logger=main_module.logger.name)

    payload = list_projectx_accounts(show_inactive=False, show_missing=False, db=db_session)

    assert [int(row["id"]) for row in payload] == [7101]
    assert "falling back to env credentials" not in caplog.text


def test_authenticated_mode_returns_500_when_stored_credentials_are_unavailable_without_env_fallback(
    db_session, monkeypatch
):
    Base.metadata.create_all(bind=db_session.bind, tables=[ProviderCredential.__table__])
    monkeypatch.delenv("ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS", raising=False)
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("CREDENTIALS_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("ALLOW_INSECURE_LOCAL_CREDENTIALS_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@db.example.com:5432/postgres")
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://app.example.com")
    monkeypatch.setenv("ALLOWED_ORIGIN_REGEX", "")
    monkeypatch.setattr(
        main_module.ProjectXClient,
        "from_env",
        lambda: (_ for _ in ()).throw(AssertionError("shared env credentials should not be used")),
    )

    db_session.add(
        ProviderCredential(
            user_id="00000000-0000-0000-0000-000000000000",
            provider="projectx",
            username_encrypted="unreadable",
            api_key_encrypted="unreadable",
        )
    )
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        list_projectx_accounts(show_inactive=False, show_missing=False, db=db_session)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "projectx_credentials_unavailable"
