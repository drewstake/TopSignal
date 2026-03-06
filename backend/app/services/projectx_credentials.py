from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from ..models import ProviderCredential

_PROJECTX_PROVIDER = "projectx"


@dataclass(frozen=True)
class ProjectXCredentials:
    username: str
    api_key: str


def upsert_projectx_credentials(
    db: Session,
    *,
    user_id: str,
    username: str,
    api_key: str,
) -> None:
    normalized_username = _normalize_secret(username, field_name="username")
    normalized_api_key = _normalize_secret(api_key, field_name="api_key")

    row = (
        db.query(ProviderCredential)
        .filter(ProviderCredential.user_id == user_id)
        .filter(ProviderCredential.provider == _PROJECTX_PROVIDER)
        .one_or_none()
    )
    encrypted_username = _encrypt(normalized_username)
    encrypted_api_key = _encrypt(normalized_api_key)

    if row is None:
        row = ProviderCredential(
            user_id=user_id,
            provider=_PROJECTX_PROVIDER,
            username_encrypted=encrypted_username,
            api_key_encrypted=encrypted_api_key,
        )
        db.add(row)
    else:
        row.username_encrypted = encrypted_username
        row.api_key_encrypted = encrypted_api_key

    db.commit()


def get_projectx_credentials(db: Session, *, user_id: str) -> ProjectXCredentials | None:
    row = (
        db.query(ProviderCredential)
        .filter(ProviderCredential.user_id == user_id)
        .filter(ProviderCredential.provider == _PROJECTX_PROVIDER)
        .one_or_none()
    )
    if row is None:
        return None

    username = _decrypt(row.username_encrypted)
    api_key = _decrypt(row.api_key_encrypted)
    return ProjectXCredentials(username=username, api_key=api_key)


def has_projectx_credentials(db: Session, *, user_id: str) -> bool:
    existing = (
        db.query(ProviderCredential.id)
        .filter(ProviderCredential.user_id == user_id)
        .filter(ProviderCredential.provider == _PROJECTX_PROVIDER)
        .first()
    )
    return existing is not None


def delete_projectx_credentials(db: Session, *, user_id: str) -> bool:
    row = (
        db.query(ProviderCredential)
        .filter(ProviderCredential.user_id == user_id)
        .filter(ProviderCredential.provider == _PROJECTX_PROVIDER)
        .one_or_none()
    )
    if row is None:
        return False

    db.delete(row)
    db.commit()
    return True


def _normalize_secret(value: str, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def _decrypt(value: str) -> str:
    return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")


def _fernet() -> Fernet:
    key = os.getenv("CREDENTIALS_ENCRYPTION_KEY")
    if key:
        normalized_key = key.strip().encode("utf-8")
        return Fernet(normalized_key)

    if not _allow_insecure_local_credentials_key():
        raise RuntimeError("CREDENTIALS_ENCRYPTION_KEY is required for non-local credential storage")

    # Local/dev fallback so existing tests and localhost setups still run.
    digest = hashlib.sha256(b"topsignal-local-dev-credentials-key").digest()
    fallback = base64.urlsafe_b64encode(digest)
    return Fernet(fallback)


def _allow_insecure_local_credentials_key() -> bool:
    raw = os.getenv("ALLOW_INSECURE_LOCAL_CREDENTIALS_KEY")
    if raw is not None:
        return _read_bool_env(raw, False)

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        return True
    if database_url.startswith("sqlite"):
        return True

    parsed = urlparse(database_url)
    return (parsed.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}


def _read_bool_env(raw: str, default: bool) -> bool:
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default
