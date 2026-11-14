from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.models import (
    Account,
    JournalEntry,
    ProjectXTradeDaySync,
    ProjectXTradeEvent,
    TradeImportBatch,
)
from app.services import projectx_trades as projectx_trades_module
from app.services.trading_day import trading_day_bounds_utc


_POSTGRES_URL = os.getenv("TOPSIGNAL_TEST_POSTGRES_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="TOPSIGNAL_TEST_POSTGRES_URL is required for PostgreSQL concurrency tests",
)


@pytest.fixture(scope="module")
def postgres_engine():
    engine = create_engine(_POSTGRES_URL, pool_pre_ping=True)
    try:
        if engine.dialect.name != "postgresql":
            pytest.fail("TOPSIGNAL_TEST_POSTGRES_URL must use PostgreSQL")
        with engine.connect() as connection:
            missing_tables = [
                table_name
                for table_name in (
                    "projectx_trade_day_syncs",
                    "projectx_trade_events",
                    "journal_entries",
                )
                if connection.execute(
                    text("select to_regclass(:table_name)"),
                    {"table_name": f"public.{table_name}"},
                ).scalar_one()
                is None
            ]
        if missing_tables:
            pytest.fail(
                "PostgreSQL concurrency tests require the migrated schema; "
                f"missing tables: {', '.join(missing_tables)}"
            )
        yield engine
    finally:
        engine.dispose()


def _wait_for_blocker(
    engine,
    *,
    waiting_pid: int,
    blocking_pid: int,
    timeout_seconds: float = 5.0,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    with engine.connect() as observer:
        while time.monotonic() < deadline:
            blockers = observer.execute(
                text("select pg_blocking_pids(:waiting_pid)"),
                {"waiting_pid": waiting_pid},
            ).scalar_one()
            if blocking_pid in blockers:
                return True
            time.sleep(0.025)
    return False


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def test_provider_leg_identity_survives_same_execution_and_correction(postgres_engine):
    """Exercise the migrated partial index on real PostgreSQL, not SQLite."""
    sessions=sessionmaker(bind=postgres_engine)
    owner=str(uuid4());account=24_000_000_000+(uuid4().int%4_000_000_000)
    stamp=datetime(2036,3,6,15,tzinfo=timezone.utc)
    base=dict(account_id=account,contract_id='CON.F.US.MNQ.M36',symbol='MNQ',side='SELL',size=1,
              price=20000,timestamp=stamp,fees=.36,pnl=10,order_id='same-order',raw_payload={})
    try:
        with sessions() as db:
            assert projectx_trades_module.store_trade_events(db,[dict(base,source_trade_id='one'),dict(base,source_trade_id='two')],user_id=owner)==2
            db.commit()
        with sessions() as db:
            projectx_trades_module.store_trade_events(db,[dict(base,source_trade_id='one',pnl=11,correction=True)],user_id=owner)
            db.commit()
            rows=db.query(ProjectXTradeEvent).filter_by(user_id=owner).order_by(ProjectXTradeEvent.source_trade_id).all()
            assert len(rows)==2 and [float(r.pnl) for r in rows]==[11,10]
    finally:
        with postgres_engine.begin() as db:
            db.execute(delete(ProjectXTradeEvent).where(ProjectXTradeEvent.user_id==owner))


def test_trade_day_partial_writer_waits_for_complete_and_cannot_downgrade(
    postgres_engine,
):
    session_factory = sessionmaker(
        bind=postgres_engine,
        autoflush=False,
    )
    user_id = str(uuid4())
    account_id = 1_000_000_000 + (uuid4().int % 8_000_000_000)
    trade_day = date(2036, 3, 2)
    window_start, window_end = trading_day_bounds_utc(trade_day)
    initial_synced_at = datetime(2036, 3, 2, 20, 0, tzinfo=timezone.utc)
    complete_synced_at = datetime(2036, 3, 2, 20, 5, tzinfo=timezone.utc)
    losing_partial_synced_at = datetime(2036, 3, 2, 20, 10, tzinfo=timezone.utc)

    try:
        with session_factory() as setup:
            setup.add(
                ProjectXTradeDaySync(
                    user_id=user_id,
                    account_id=account_id,
                    trade_date=trade_day,
                    window_start=window_start,
                    window_end=window_end,
                    sync_status="partial",
                    last_synced_at=initial_synced_at,
                    row_count=1,
                    updated_at=initial_synced_at,
                )
            )
            setup.commit()

        partial_started = threading.Event()
        partial_pid: dict[str, int] = {}

        def write_partial() -> None:
            with session_factory() as partial:
                partial.execute(text("set local lock_timeout = '10s'"))
                partial_pid["value"] = int(
                    partial.execute(text("select pg_backend_pid()"))
                    .scalar_one()
                )
                partial_started.set()
                projectx_trades_module._upsert_trade_day_sync(
                    partial,
                    user_id=user_id,
                    account_id=account_id,
                    trade_day=trade_day,
                    window_start=window_start,
                    window_end=window_end,
                    sync_status="partial",
                    last_synced_at=losing_partial_synced_at,
                    row_count=2,
                )
                partial.commit()

        with session_factory() as complete:
            complete_pid = int(
                complete.execute(text("select pg_backend_pid()"))
                .scalar_one()
            )
            projectx_trades_module._upsert_trade_day_sync(
                complete,
                user_id=user_id,
                account_id=account_id,
                trade_day=trade_day,
                window_start=window_start,
                window_end=window_end,
                sync_status="complete",
                last_synced_at=complete_synced_at,
                row_count=11,
            )
            complete.flush()

            with ThreadPoolExecutor(max_workers=1) as executor:
                partial_future = executor.submit(write_partial)
                assert partial_started.wait(timeout=5)
                was_blocked = _wait_for_blocker(
                    postgres_engine,
                    waiting_pid=partial_pid["value"],
                    blocking_pid=complete_pid,
                )
                complete.commit()
                partial_future.result(timeout=10)

        assert was_blocked, "the PARTIAL writer never waited on the COMPLETE row lock"
        with session_factory() as verifier:
            marker = (
                verifier.query(ProjectXTradeDaySync)
                .filter(ProjectXTradeDaySync.user_id == user_id)
                .filter(ProjectXTradeDaySync.account_id == account_id)
                .filter(ProjectXTradeDaySync.trade_date == trade_day)
                .one()
            )
            assert marker.sync_status == "complete"
            assert marker.row_count == 11
            assert _as_utc(marker.last_synced_at) == complete_synced_at
            assert _as_utc(marker.updated_at) == complete_synced_at
            assert _as_utc(marker.window_start) == window_start
            assert _as_utc(marker.window_end) == window_end
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                delete(ProjectXTradeDaySync).where(
                    ProjectXTradeDaySync.user_id == user_id
                )
            )






def test_trade_event_lock_reload_preserves_concurrent_completed_pnl(postgres_engine):
    session_factory = sessionmaker(bind=postgres_engine, autoflush=False)
    user_id = str(uuid4())
    account_id = 24_000_000_000 + (uuid4().int % 4_000_000_000)
    timestamp = datetime(2036, 3, 6, 15, 0, tzinfo=timezone.utc)
    source_trade_id = f"pg-completed-race-{uuid4().hex}"

    stale_event = {
        "account_id": account_id,
        "contract_id": "CON.F.US.MNQ.M36",
        "symbol": "MNQ",
        "side": "BUY",
        "size": 1,
        "price": 20_000,
        "timestamp": timestamp,
        "fees": 0,
        "pnl": None,
        "order_id": f"order-{source_trade_id}",
        "source_trade_id": source_trade_id,
        "status": "PENDING",
        "raw_payload": {"state": "stale"},
    }
    provider_started = threading.Event()
    provider_pid: dict[str, int] = {}

    try:
        with session_factory() as setup:
            setup.add(
                ProjectXTradeEvent(
                    user_id=user_id,
                    account_id=account_id,
                    contract_id="CON.F.US.MNQ.M36",
                    symbol="MNQ",
                    side="BUY",
                    size=1,
                    price=20_000,
                    trade_timestamp=timestamp,
                    fees=0,
                    pnl=None,
                    order_id=f"order-{source_trade_id}",
                    source_trade_id=source_trade_id,
                    status="PENDING",
                    raw_payload={"state": "initial"},
                )
            )
            setup.commit()

        def write_stale_provider() -> None:
            with session_factory() as provider:
                provider.execute(text("set local lock_timeout = '10s'"))
                provider_pid["value"] = int(
                    provider.execute(text("select pg_backend_pid()")).scalar_one()
                )
                provider_started.set()
                projectx_trades_module.store_trade_events(
                    provider,
                    [stale_event],
                    user_id=user_id,
                )
                provider.commit()

        with session_factory() as completed_writer:
            completed_pid = int(
                completed_writer.execute(text("select pg_backend_pid()")).scalar_one()
            )
            row = (
                completed_writer.query(ProjectXTradeEvent)
                .filter(ProjectXTradeEvent.user_id == user_id)
                .filter(ProjectXTradeEvent.source_trade_id == source_trade_id)
                .with_for_update()
                .one()
            )
            row.pnl = 125
            row.fees = 2.5
            row.status = "FILLED"
            row.raw_payload = {"state": "completed"}
            completed_writer.flush()

            with ThreadPoolExecutor(max_workers=1) as executor:
                provider_future = executor.submit(write_stale_provider)
                assert provider_started.wait(timeout=5)
                was_blocked = _wait_for_blocker(
                    postgres_engine,
                    waiting_pid=provider_pid["value"],
                    blocking_pid=completed_pid,
                )
                completed_writer.commit()
                provider_future.result(timeout=10)

        assert was_blocked, "stale provider writer never waited on the completed row"
        with session_factory() as verifier:
            saved = (
                verifier.query(ProjectXTradeEvent)
                .filter(ProjectXTradeEvent.user_id == user_id)
                .filter(ProjectXTradeEvent.source_trade_id == source_trade_id)
                .one()
            )
            assert float(saved.pnl) == 125.0
            assert float(saved.fees) == 2.5
            assert saved.status == "FILLED"
            assert saved.raw_payload == {"state": "completed"}
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                delete(ProjectXTradeEvent).where(ProjectXTradeEvent.user_id == user_id)
            )


@pytest.mark.parametrize("import_locks_first", [False, True])
def test_import_provenance_wins_provider_race_in_both_lock_orders(
    postgres_engine,
    import_locks_first,
):
    session_factory = sessionmaker(bind=postgres_engine, autoflush=False)
    user_id = str(uuid4())
    account_id = 28_000_000_000 + (uuid4().int % 4_000_000_000)
    timestamp = datetime(2036, 3, 7, 15, 0, tzinfo=timezone.utc)
    source_trade_id = f"pg-import-race-{uuid4().hex}"
    account_row_id = 0
    import_batch_id = 0
    event_id = 0
    provider_event = {
        "account_id": account_id,
        "contract_id": "PROVIDER-CONTRACT",
        "symbol": "ES",
        "side": "SELL",
        "size": 9,
        "price": 1,
        "timestamp": timestamp,
        "fees": 99,
        "pnl": -999,
        "order_id": f"order-{source_trade_id}",
        "source_trade_id": source_trade_id,
        "status": "FILLED",
        "raw_payload": {"source": "projectx"},
    }

    def apply_audited_import(row: ProjectXTradeEvent) -> None:
        row.account_row_id = account_row_id
        row.account_external_id = str(account_id)
        row.import_batch_id = import_batch_id
        row.contract_id = "AUDITED-CONTRACT"
        row.symbol = "MNQ"
        row.side = "BUY"
        row.size = 1
        row.price = 20_000
        row.fees = 2.22
        row.commissions = 1.5
        row.fee_scope = "round_turn"
        row.pnl = 202.5
        row.trade_date = date(2036, 3, 7)
        row.status = "IMPORTED"
        row.raw_payload = {"source": "audited-import"}

    try:
        with session_factory() as setup:
            account = Account(
                user_id=user_id,
                provider="projectx",
                external_id=str(account_id),
                name="Concurrency fixture",
            )
            setup.add(account)
            setup.flush()
            account_row_id = int(account.id)
            batch = TradeImportBatch(
                user_id=user_id,
                account_id=account_id,
                account_row_id=account_row_id,
                account_external_id=str(account_id),
                source_file_name="race.csv",
                file_sha256=uuid4().hex * 2,
                total_rows=1,
                inserted_rows=1,
                duplicate_rows=0,
            )
            setup.add(batch)
            setup.flush()
            import_batch_id = int(batch.id)
            event = ProjectXTradeEvent(
                user_id=user_id,
                account_id=account_id,
                contract_id="INITIAL-CONTRACT",
                symbol="MNQ",
                side="BUY",
                size=1,
                price=20_000,
                trade_timestamp=timestamp,
                fees=0,
                pnl=None,
                order_id=f"order-{source_trade_id}",
                source_trade_id=source_trade_id,
                status="PENDING",
                raw_payload={"source": "initial"},
            )
            setup.add(event)
            setup.commit()
            event_id = int(event.id)

        waiting_started = threading.Event()
        waiting_pid: dict[str, int] = {}

        if import_locks_first:
            def run_provider() -> None:
                with session_factory() as provider:
                    provider.execute(text("set local lock_timeout = '10s'"))
                    waiting_pid["value"] = int(
                        provider.execute(text("select pg_backend_pid()")).scalar_one()
                    )
                    waiting_started.set()
                    projectx_trades_module.store_trade_events(
                        provider,
                        [provider_event],
                        user_id=user_id,
                    )
                    provider.commit()

            with session_factory() as importer:
                blocker_pid = int(
                    importer.execute(text("select pg_backend_pid()")).scalar_one()
                )
                row = (
                    importer.query(ProjectXTradeEvent)
                    .filter(ProjectXTradeEvent.id == event_id)
                    .with_for_update()
                    .one()
                )
                apply_audited_import(row)
                importer.flush()
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(run_provider)
                    assert waiting_started.wait(timeout=5)
                    was_blocked = _wait_for_blocker(
                        postgres_engine,
                        waiting_pid=waiting_pid["value"],
                        blocking_pid=blocker_pid,
                    )
                    importer.commit()
                    future.result(timeout=10)
        else:
            def run_import() -> None:
                with session_factory() as importer:
                    importer.execute(text("set local lock_timeout = '10s'"))
                    waiting_pid["value"] = int(
                        importer.execute(text("select pg_backend_pid()")).scalar_one()
                    )
                    waiting_started.set()
                    row = (
                        importer.query(ProjectXTradeEvent)
                        .filter(ProjectXTradeEvent.id == event_id)
                        .with_for_update()
                        .one()
                    )
                    apply_audited_import(row)
                    importer.commit()

            with session_factory() as provider:
                blocker_pid = int(
                    provider.execute(text("select pg_backend_pid()")).scalar_one()
                )
                projectx_trades_module.store_trade_events(
                    provider,
                    [provider_event],
                    user_id=user_id,
                )
                provider.flush()
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(run_import)
                    assert waiting_started.wait(timeout=5)
                    was_blocked = _wait_for_blocker(
                        postgres_engine,
                        waiting_pid=waiting_pid["value"],
                        blocking_pid=blocker_pid,
                    )
                    provider.commit()
                    future.result(timeout=10)

        assert was_blocked, "second writer never waited on the trade row lock"
        with session_factory() as verifier:
            saved = verifier.query(ProjectXTradeEvent).filter(ProjectXTradeEvent.id == event_id).one()
            assert int(saved.import_batch_id) == import_batch_id
            assert int(saved.account_row_id) == account_row_id
            assert saved.account_external_id == str(account_id)
            assert saved.contract_id == "AUDITED-CONTRACT"
            assert float(saved.pnl) == 202.5
            assert float(saved.fees) == 2.22
            assert float(saved.commissions) == 1.5
            assert saved.status == "IMPORTED"
            assert saved.raw_payload == {"source": "audited-import"}
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                delete(ProjectXTradeEvent).where(ProjectXTradeEvent.user_id == user_id)
            )
            connection.execute(
                delete(TradeImportBatch).where(TradeImportBatch.user_id == user_id)
            )
            connection.execute(delete(Account).where(Account.user_id == user_id))
