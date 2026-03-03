import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.db as db


class _ConnectionOK:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, _statement):
        return None


class _EngineOK:
    def connect(self):
        return _ConnectionOK()


class _EngineDown:
    def connect(self):
        raise RuntimeError("connection refused")


def test_guard_allows_cloud_database_url(monkeypatch):
    monkeypatch.setattr(db, "DATABASE_URL", "postgresql+psycopg://user:pass@db.example.com:5432/postgres")
    db.guard_against_local_database_url()


def test_guard_allows_local_database_url_when_reachable(monkeypatch):
    monkeypatch.setattr(db, "DATABASE_URL", "postgresql+psycopg://user:pass@127.0.0.1:5432/postgres")
    monkeypatch.setattr(db, "engine", _EngineOK())
    db.guard_against_local_database_url()


def test_guard_blocks_local_database_url_when_unreachable(monkeypatch):
    monkeypatch.setattr(db, "DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/postgres")
    monkeypatch.setattr(db, "engine", _EngineDown())

    with pytest.raises(RuntimeError, match="local DB mode"):
        db.guard_against_local_database_url()

