import hashlib
import hmac
import logging
import os
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.main as main_module
from app.auth import AuthenticatedUser, bind_authenticated_user, reset_authenticated_user
from app.db import Base
from app.main import get_projectx_credentials_status, list_projectx_accounts
from app.models import Account, ProjectXTradeEvent, ProviderCredential
from app.services.projectx_client import (
    PROJECTX_ERROR_AUTH_FAILED,
    PROJECTX_ERROR_NETWORK,
    ProjectXClient,
    ProjectXClientError,
)
from app.services.projectx_credentials import (
    ProjectXCredentialsEncryptionKeyInvalid,
    upsert_projectx_credentials,
)


CURRENT_USER_ID = "11111111-1111-1111-1111-111111111111"
OTHER_USER_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Account.__table__,
            ProjectXTradeEvent.__table__,
            ProviderCredential.__table__,
        ],
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(
            bind=engine,
            tables=[
                ProviderCredential.__table__,
                ProjectXTradeEvent.__table__,
                Account.__table__,
            ],
        )
        engine.dispose()


@pytest.fixture(autouse=True)
def authenticated_user_and_runtime(monkeypatch):
    auth_token = bind_authenticated_user(
        AuthenticatedUser(user_id=CURRENT_USER_ID, email=None, claims={}),
    )
    monkeypatch.delenv("ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS", raising=False)
    monkeypatch.delenv("ALLOW_INSECURE_LOCAL_CREDENTIALS_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("PROJECTX_API_BASE_URL", "https://example.test")
    try:
        yield
    finally:
        reset_authenticated_user(auth_token)


def _fingerprint(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def test_credentials_status_separates_configured_decryptable_and_authenticated(
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(
        main_module.ProjectXClient,
        "get_access_token",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("credential status must not contact ProjectX")
        ),
    )

    assert get_projectx_credentials_status(db=db_session) == {
        "configured": False,
        "decryptable": False,
        "status": "not_configured",
        "error_code": "projectx_credentials_not_configured",
    }

    first_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("CREDENTIALS_ENCRYPTION_KEY", first_key)
    upsert_projectx_credentials(
        db_session,
        user_id=CURRENT_USER_ID,
        username="stored-user-fixture",
        api_key="stored-key-fixture",
    )

    assert get_projectx_credentials_status(db=db_session) == {
        "configured": True,
        "decryptable": True,
        "status": "ready",
        "error_code": None,
    }

    monkeypatch.setenv(
        "CREDENTIALS_ENCRYPTION_KEY",
        Fernet.generate_key().decode("ascii"),
    )
    assert get_projectx_credentials_status(db=db_session) == {
        "configured": True,
        "decryptable": False,
        "status": "unavailable",
        "error_code": "projectx_credentials_unavailable",
    }


def test_malformed_encryption_key_is_safe_and_does_not_overwrite_stored_row(
    db_session,
    monkeypatch,
):
    monkeypatch.setenv(
        "CREDENTIALS_ENCRYPTION_KEY",
        Fernet.generate_key().decode("ascii"),
    )
    upsert_projectx_credentials(
        db_session,
        user_id=CURRENT_USER_ID,
        username="original-user-fixture",
        api_key="original-key-fixture",
    )
    stored = db_session.query(ProviderCredential).one()
    original_username_fingerprint = _fingerprint(stored.username_encrypted)
    original_api_key_fingerprint = _fingerprint(stored.api_key_encrypted)

    monkeypatch.setenv("CREDENTIALS_ENCRYPTION_KEY", "malformed-key-fixture")
    with pytest.raises(ProjectXCredentialsEncryptionKeyInvalid):
        upsert_projectx_credentials(
            db_session,
            user_id=CURRENT_USER_ID,
            username="replacement-user-fixture",
            api_key="replacement-key-fixture",
        )

    db_session.expire_all()
    unchanged = db_session.query(ProviderCredential).one()
    assert hmac.compare_digest(
        original_username_fingerprint,
        _fingerprint(unchanged.username_encrypted),
    )
    assert hmac.compare_digest(
        original_api_key_fingerprint,
        _fingerprint(unchanged.api_key_encrypted),
    )
    assert get_projectx_credentials_status(db=db_session)["error_code"] == (
        "projectx_credentials_unavailable"
    )


def test_signed_in_users_decrypted_credential_drives_successful_account_refresh(
    db_session,
    monkeypatch,
):
    monkeypatch.setenv("PROJECTX_ACCOUNT_STALE_AFTER_SECONDS", "900")
    encryption_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("CREDENTIALS_ENCRYPTION_KEY", encryption_key)
    upsert_projectx_credentials(
        db_session,
        user_id=CURRENT_USER_ID,
        username="current-user-fixture",
        api_key="current-key-fixture",
    )
    upsert_projectx_credentials(
        db_session,
        user_id=OTHER_USER_ID,
        username="other-user-fixture",
        api_key="other-key-fixture",
    )

    old_seen_at = datetime.now(timezone.utc) - timedelta(days=1)
    db_session.add_all(
        [
            Account(
                user_id=CURRENT_USER_ID,
                provider="projectx",
                external_id="7401",
                name="Old Name",
                balance=100,
                account_state="LOCKED_OUT",
                can_trade=False,
                is_visible=False,
                last_seen_at=old_seen_at,
                is_main=True,
            ),
            Account(
                user_id=OTHER_USER_ID,
                provider="projectx",
                external_id="7401",
                name="Other User Name",
                balance=200,
                account_state="ACTIVE",
                can_trade=True,
                is_visible=True,
                last_seen_at=old_seen_at,
                is_main=True,
            ),
        ]
    )
    db_session.commit()

    expected_username_fingerprint = _fingerprint("current-user-fixture")
    expected_api_key_fingerprint = _fingerprint("current-key-fixture")
    credential_checks: list[bool] = []

    def list_accounts(client, *, only_active_accounts=True):
        credential_checks.extend(
            [
                hmac.compare_digest(
                    expected_username_fingerprint,
                    _fingerprint(client.username),
                ),
                hmac.compare_digest(
                    expected_api_key_fingerprint,
                    _fingerprint(client.api_key),
                ),
            ]
        )
        assert only_active_accounts is False
        return [
            {
                "id": 7401,
                "name": "Refreshed Account",
                "balance": 50000,
                "can_trade": True,
                "is_visible": True,
            }
        ]

    monkeypatch.setattr(
        main_module.ProjectXClient,
        "from_env",
        lambda: (_ for _ in ()).throw(
            AssertionError("a stored per-user credential must take precedence")
        ),
    )
    monkeypatch.setattr(main_module.ProjectXClient, "list_accounts", list_accounts)

    payload = list_projectx_accounts(db=db_session)

    assert credential_checks == [True, True]
    assert payload[0]["name"] == "Refreshed Account"
    assert payload[0]["balance"] == pytest.approx(50000)
    assert payload[0]["can_trade"] is True
    assert payload[0]["is_visible"] is True
    assert payload[0]["provider_sync_status"] == "provider_fresh"
    assert payload[0]["provider_sync_error_code"] is None
    assert payload[0]["provider_data_stale"] is False
    assert payload[0]["provider_data_stale_at"] == (
        main_module._as_utc(payload[0]["last_seen_at"]) + timedelta(seconds=900)
    )
    assert payload[0]["provider_last_successful_refresh_at"] == payload[0]["last_seen_at"]

    current_row = (
        db_session.query(Account)
        .filter(Account.user_id == CURRENT_USER_ID)
        .one()
    )
    other_row = (
        db_session.query(Account)
        .filter(Account.user_id == OTHER_USER_ID)
        .one()
    )
    assert current_row.name == "Refreshed Account"
    assert float(current_row.balance) == pytest.approx(50000)
    assert current_row.can_trade is True
    assert current_row.is_visible is True
    assert main_module._as_utc(current_row.last_seen_at) > old_seen_at
    assert other_row.name == "Other User Name"
    assert float(other_row.balance) == pytest.approx(200)
    assert main_module._as_utc(other_row.last_seen_at) == old_seen_at


def test_cache_only_snapshot_uses_age_and_never_marks_csv_provider_stale(
    db_session,
    monkeypatch,
):
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            Account(
                user_id=CURRENT_USER_ID,
                provider="projectx",
                external_id="7501",
                name="Recent Cache",
                trade_data_source="projectx",
                account_state="ACTIVE",
                last_seen_at=now - timedelta(seconds=30),
                is_main=True,
            ),
            Account(
                user_id=CURRENT_USER_ID,
                provider="projectx",
                external_id="7502",
                name="Old Cache",
                trade_data_source="projectx",
                account_state="ACTIVE",
                last_seen_at=now - timedelta(minutes=30),
            ),
            Account(
                user_id=CURRENT_USER_ID,
                provider="projectx",
                external_id="7503",
                name="Live CSV",
                trade_data_source="csv_import",
                account_state="ACTIVE",
                last_seen_at=now - timedelta(days=10),
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setenv("PROJECTX_ACCOUNT_STALE_AFTER_SECONDS", "900")
    monkeypatch.setattr(
        main_module,
        "_projectx_client_for_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cache-only account reads must not contact ProjectX")
        ),
    )

    payload = list_projectx_accounts(
        show_inactive=True,
        refresh_provider=False,
        db=db_session,
    )
    by_id = {row["id"]: row for row in payload}

    assert by_id[7501]["provider_sync_status"] == "cache_fresh"
    assert by_id[7501]["provider_data_stale"] is False
    assert by_id[7501]["provider_data_stale_at"] == (
        main_module._as_utc(by_id[7501]["last_seen_at"]) + timedelta(seconds=900)
    )
    assert by_id[7502]["provider_sync_status"] == "cache_stale"
    assert by_id[7502]["provider_data_stale"] is True
    assert by_id[7502]["provider_data_stale_at"] == (
        main_module._as_utc(by_id[7502]["last_seen_at"]) + timedelta(seconds=900)
    )
    assert by_id[7503]["provider_sync_status"] == "not_applicable"
    assert by_id[7503]["provider_data_stale"] is False
    assert by_id[7503]["provider_data_stale_at"] is None
    assert by_id[7503]["provider_sync_error_code"] is None
    assert by_id[7503]["provider_last_successful_refresh_at"] is None


def test_snapshot_staleness_boolean_and_deadline_share_the_exact_threshold(monkeypatch):
    monkeypatch.setenv("PROJECTX_ACCOUNT_STALE_AFTER_SECONDS", "900")
    last_seen_at = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    expected_stale_at = last_seen_at + timedelta(seconds=900)

    is_stale_at_deadline, stale_at = main_module._projectx_account_snapshot_freshness(
        last_seen_at,
        now_utc=expected_stale_at,
    )
    is_stale_after_deadline, repeated_stale_at = (
        main_module._projectx_account_snapshot_freshness(
            last_seen_at,
            now_utc=expected_stale_at + timedelta(microseconds=1),
        )
    )

    assert stale_at == expected_stale_at
    assert repeated_stale_at == expected_stale_at
    assert is_stale_at_deadline is True
    assert is_stale_after_deadline is True


def test_provider_outage_uses_cached_rows_with_safe_actionable_metadata(
    db_session,
    monkeypatch,
    caplog,
):
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            Account(
                user_id=CURRENT_USER_ID,
                provider="projectx",
                external_id="7601",
                name="Recent Cache",
                trade_data_source="projectx",
                account_state="ACTIVE",
                last_seen_at=now - timedelta(seconds=30),
                is_main=True,
            ),
            Account(
                user_id=CURRENT_USER_ID,
                provider="projectx",
                external_id="7602",
                name="Old Cache",
                trade_data_source="projectx",
                account_state="ACTIVE",
                last_seen_at=now - timedelta(minutes=30),
            ),
            Account(
                user_id=CURRENT_USER_ID,
                provider="projectx",
                external_id="7603",
                name="Live CSV",
                trade_data_source="csv_import",
                account_state="ACTIVE",
                last_seen_at=now - timedelta(days=10),
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setenv("PROJECTX_ACCOUNT_STALE_AFTER_SECONDS", "900")
    raw_provider_marker = "raw-provider-detail-must-not-surface"
    monkeypatch.setattr(
        main_module,
        "_projectx_client_for_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ProjectXClientError(
                raw_provider_marker,
                status_code=503,
                reason_code=PROJECTX_ERROR_NETWORK,
            )
        ),
    )
    caplog.set_level(logging.WARNING, logger=main_module.logger.name)

    payload = list_projectx_accounts(show_inactive=True, db=db_session)
    by_id = {row["id"]: row for row in payload}

    assert by_id[7601]["provider_sync_status"] == "cached_fallback"
    assert by_id[7601]["provider_data_stale"] is False
    assert by_id[7601]["provider_data_stale_at"] == (
        main_module._as_utc(by_id[7601]["last_seen_at"]) + timedelta(seconds=900)
    )
    assert by_id[7601]["provider_sync_error_code"] == "projectx_network_error"
    assert by_id[7601]["provider_sync_error_message"] == (
        "ProjectX is temporarily unreachable. Try refreshing accounts again."
    )
    assert by_id[7601]["provider_last_successful_refresh_at"] == by_id[7601]["last_seen_at"]
    assert by_id[7602]["provider_sync_status"] == "cached_fallback"
    assert by_id[7602]["provider_data_stale"] is True
    assert by_id[7602]["provider_data_stale_at"] == (
        main_module._as_utc(by_id[7602]["last_seen_at"]) + timedelta(seconds=900)
    )
    assert by_id[7603]["provider_sync_status"] == "not_applicable"
    assert by_id[7603]["provider_data_stale"] is False
    assert by_id[7603]["provider_data_stale_at"] is None
    assert by_id[7603]["provider_sync_error_code"] is None
    assert "projectx_network_error" in caplog.text
    assert "projectx_cached_fallback_used" in caplog.text
    assert raw_provider_marker not in caplog.text


def test_no_cache_auth_failure_returns_structured_safe_error(
    db_session,
    monkeypatch,
    caplog,
):
    raw_provider_marker = "raw-auth-provider-detail-must-not-surface"
    monkeypatch.setattr(
        main_module,
        "_projectx_client_for_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ProjectXClientError(
                raw_provider_marker,
                status_code=401,
                reason_code=PROJECTX_ERROR_AUTH_FAILED,
            )
        ),
    )
    caplog.set_level(logging.WARNING, logger=main_module.logger.name)

    with pytest.raises(HTTPException) as exc_info:
        list_projectx_accounts(db=db_session)

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == {
        "code": "projectx_auth_failed",
        "message": "ProjectX rejected the stored credential. Reconnect ProjectX and try again.",
    }
    assert raw_provider_marker not in str(exc_info.value.detail)
    assert raw_provider_marker not in caplog.text


def test_provider_failure_does_not_return_silent_200_when_cached_rows_are_filtered(
    db_session,
    monkeypatch,
):
    db_session.add(
        Account(
            user_id=CURRENT_USER_ID,
            provider="projectx",
            external_id="7651",
            name="Hidden Cache",
            trade_data_source="projectx",
            account_state="HIDDEN",
            last_seen_at=datetime.now(timezone.utc) - timedelta(minutes=30),
            is_main=False,
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        main_module,
        "_projectx_client_for_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ProjectXClientError(
                "provider detail",
                status_code=503,
                reason_code=PROJECTX_ERROR_NETWORK,
            )
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        list_projectx_accounts(db=db_session)

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail["code"] == "projectx_network_error"


def test_successful_refresh_marks_only_returned_accounts_provider_fresh(
    db_session,
    monkeypatch,
):
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            Account(
                user_id=CURRENT_USER_ID,
                provider="projectx",
                external_id="7701",
                name="Returned",
                account_state="ACTIVE",
                last_seen_at=now - timedelta(minutes=10),
                is_main=True,
            ),
            Account(
                user_id=CURRENT_USER_ID,
                provider="projectx",
                external_id="7702",
                name="Temporarily Omitted",
                account_state="ACTIVE",
                last_seen_at=now - timedelta(seconds=30),
            ),
        ]
    )
    db_session.commit()

    class StubClient:
        def list_accounts(self, *, only_active_accounts=True):
            assert only_active_accounts is False
            return [
                {
                    "id": 7701,
                    "name": "Returned",
                    "balance": 50000,
                    "can_trade": True,
                    "is_visible": True,
                }
            ]

    monkeypatch.setenv("PROJECTX_ACCOUNT_STALE_AFTER_SECONDS", "900")
    monkeypatch.setattr(
        main_module,
        "_projectx_client_for_user",
        lambda *_args, **_kwargs: StubClient(),
    )

    payload = list_projectx_accounts(show_inactive=True, db=db_session)
    by_id = {row["id"]: row for row in payload}

    assert by_id[7701]["provider_sync_status"] == "provider_fresh"
    assert by_id[7702]["provider_sync_status"] == "cache_fresh"
    assert by_id[7702]["provider_data_stale"] is False


def test_token_cache_index_is_a_non_reversible_credential_fingerprint():
    client = ProjectXClient(
        base_url="https://example.test",
        username="cache-user-fixture",
        api_key="cache-key-fixture",
    )

    cache_key = client._token_cache_key()

    assert len(cache_key) == 64
    assert cache_key.isalnum()
    assert "cache-user-fixture" not in cache_key
    assert "cache-key-fixture" not in cache_key
