"""Explicit local routing for replaceable market caches and research evidence.

Execution records never use this engine. There is no cloud fallback: a missing
or damaged local store must be repaired, not silently replaced by stale data.
Enable only after the migration tool's backup and restore gates have passed.
"""
from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import Session
from sqlalchemy.sql import visitors
from sqlalchemy.sql.schema import Table
from sqlalchemy.sql.elements import TextClause

RESEARCH_TABLES = frozenset({
    "market_observations", "projectx_market_candles", "decision_research_snapshots",
})


def enabled() -> bool:
    value = os.getenv("TOPSIGNAL_RESEARCH_STORAGE", "application").strip().lower()
    if value not in {"application", "local"}:
        raise RuntimeError("TOPSIGNAL_RESEARCH_STORAGE must be application or local")
    return value == "local"


def storage_path() -> Path:
    configured = os.getenv("TOPSIGNAL_DATABENTO_CACHE_DIR", "").strip()
    if not configured or not Path(configured).is_absolute():
        raise RuntimeError("Local research requires an absolute TOPSIGNAL_DATABENTO_CACHE_DIR")
    return Path(configured).resolve() / "research-store-v1" / "research.sqlite3"


def sqlite_engine(path: Path):
    engine = create_engine("sqlite:///" + path.as_posix(),
                           connect_args={"timeout": 10, "check_same_thread": False},
                           hide_parameters=True, pool_pre_ping=True)

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")

    return engine


@lru_cache(maxsize=4)
def open_store(path: Path):
    if not path.is_file():
        raise RuntimeError("Local research store missing; restore verified data before startup")
    engine = sqlite_engine(path)
    if not RESEARCH_TABLES.issubset(inspect(engine).get_table_names()):
        engine.dispose()
        raise RuntimeError("Local research schema incomplete")
    return engine


def initialize_store(path: Path):
    """Offline migration/test helper, never invoked by application startup."""
    from .models import ProjectXMarketCandle
    from .market_observation_models import DecisionResearchSnapshot, MarketObservation

    if path.exists():
        raise FileExistsError("Research destination already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = sqlite_engine(path)
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        for model in (ProjectXMarketCandle, MarketObservation, DecisionResearchSnapshot):
            model.__table__.create(conn)
        install_summary_indexes(conn)
    return engine


def install_summary_indexes(connection):
    """Explicit local migration; keep summary scans away from large JSON pages."""
    if connection.dialect.name != 'sqlite':
        raise RuntimeError('Research summary indexes are local SQLite only')
    connection.exec_driver_sql('''CREATE INDEX IF NOT EXISTS ix_local_observation_summary_v1
        ON market_observations(user_id,contract_id,received_at,event_type,side,size,price,bid,ask)''')
    connection.exec_driver_sql('''CREATE INDEX IF NOT EXISTS ix_local_decision_summary_v1
        ON decision_research_snapshots(user_id,account_id,outcome,snapshot_version,observed_at,action,score)''')
    connection.exec_driver_sql('''CREATE INDEX IF NOT EXISTS ix_local_observation_latest_spread_v1
        ON market_observations(user_id,contract_id,id DESC,received_at)
        WHERE bid IS NOT NULL AND ask IS NOT NULL''')


def validate_configuration():
    if enabled():
        path = storage_path()
        if not path.is_file():
            raise RuntimeError("Local research store unavailable")
        engine = open_store(path)
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")


class StorageSession(Session):
    def get_bind(self, mapper=None, *, clause=None, bind=None, **kwargs):
        if not enabled():
            return super().get_bind(mapper=mapper, clause=clause, bind=bind, **kwargs)
        tables = set()
        if mapper is not None:
            tables.add(inspect(mapper).local_table.name)
        if clause is not None:
            if isinstance(clause, TextClause):
                # Textual research SQL cannot safely participate in ORM routing.
                import re
                if any(re.search(r"\b" + name + r"\b", clause.text, re.I) for name in RESEARCH_TABLES):
                    raise RuntimeError("Research SQL must use mapped tables")
            tables.update(node.name for node in visitors.iterate(clause) if isinstance(node, Table))
        if tables & RESEARCH_TABLES:
            if tables - RESEARCH_TABLES:
                raise RuntimeError("Cross-store joins are forbidden")
            if bind is not None:
                raise RuntimeError("Explicit research bind override is forbidden")
            return open_store(storage_path())
        return super().get_bind(mapper=mapper, clause=clause, bind=bind, **kwargs)
