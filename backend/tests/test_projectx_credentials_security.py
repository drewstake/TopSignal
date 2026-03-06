import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.services.projectx_credentials import _decrypt, _encrypt


def test_local_database_allows_dev_fallback_credentials_key(monkeypatch):
    monkeypatch.delenv("CREDENTIALS_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("ALLOW_INSECURE_LOCAL_CREDENTIALS_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")

    encrypted = _encrypt("demo-secret")

    assert _decrypt(encrypted) == "demo-secret"


def test_remote_database_requires_explicit_credentials_encryption_key(monkeypatch):
    monkeypatch.delenv("CREDENTIALS_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("ALLOW_INSECURE_LOCAL_CREDENTIALS_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@db.example.com:5432/postgres")

    with pytest.raises(RuntimeError, match="CREDENTIALS_ENCRYPTION_KEY is required"):
        _encrypt("demo-secret")
