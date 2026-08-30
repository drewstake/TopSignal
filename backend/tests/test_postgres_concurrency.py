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
from app.services import journal as journal_module
from app.services import projectx_trades as projectx_trades_module
from app.services.journal import (
    VersionConflictError,
    create_journal_entry,
    merge_journal_entries,
    pull_journal_entry_trade_stats,
    update_journal_entry,
)
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


def test_journal_merge_rejects_autosave_committed_after_its_snapshot(
    postgres_engine,
    monkeypatch: pytest.MonkeyPatch,
):
    session_factory = sessionmaker(
        bind=postgres_engine,
        autoflush=False,
    )
    user_id = str(uuid4())
    source_account_id = 10_000_000_000 + (uuid4().int % 4_000_000_000)
    destination_account_id = source_account_id + 4_000_000_000
    entry_date = date(2036, 3, 3)
    snapshot_taken = threading.Event()
    allow_merge_lock = threading.Event()
    original_lock = journal_module._lock_journal_merge_rows

    def lock_after_autosave(db, **kwargs):
        snapshot_taken.set()
        if not allow_merge_lock.wait(timeout=10):
            raise AssertionError("timed out waiting for the concurrent autosave")
        return original_lock(db, **kwargs)

    monkeypatch.setattr(
        journal_module,
        "_lock_journal_merge_rows",
        lock_after_autosave,
    )

    try:
        with session_factory() as setup:
            source, _ = create_journal_entry(
                setup,
                user_id=user_id,
                account_id=source_account_id,
                entry_date=entry_date,
                title="Reviewed source",
                mood="Focused",
                tags=["merge"],
                body="merge overwrite body",
            )
            destination, _ = create_journal_entry(
                setup,
                user_id=user_id,
                account_id=destination_account_id,
                entry_date=entry_date,
                title="Destination draft",
                mood="Neutral",
                tags=[],
                body="original destination body",
            )
            source_id = int(source.id)
            destination_id = int(destination.id)

        def run_merge() -> tuple[str, int, str]:
            with session_factory() as merge_session:
                try:
                    merge_journal_entries(
                        merge_session,
                        user_id=user_id,
                        from_account_id=source_account_id,
                        to_account_id=destination_account_id,
                        on_conflict="overwrite",
                        include_images=False,
                    )
                except VersionConflictError as exc:
                    return (
                        "conflict",
                        int(exc.server_row.version),
                        str(exc.server_row.body),
                    )
            return ("merged", -1, "")

        with ThreadPoolExecutor(max_workers=1) as executor:
            merge_future = executor.submit(run_merge)
            assert snapshot_taken.wait(timeout=5)

            with session_factory() as autosave:
                saved = update_journal_entry(
                    autosave,
                    user_id=user_id,
                    account_id=destination_account_id,
                    entry_id=destination_id,
                    version=1,
                    body="concurrent autosave body",
                )
                assert int(saved.version) == 2

            allow_merge_lock.set()
            merge_result = merge_future.result(timeout=10)

        assert merge_result == ("conflict", 2, "concurrent autosave body")
        with session_factory() as verifier:
            destination = (
                verifier.query(JournalEntry)
                .filter(JournalEntry.id == destination_id)
                .one()
            )
            source = verifier.query(JournalEntry).filter(JournalEntry.id == source_id).one()
            assert destination.body == "concurrent autosave body"
            assert int(destination.version) == 2
            assert source.body == "merge overwrite body"
            assert int(source.version) == 1
    finally:
        allow_merge_lock.set()
        with postgres_engine.begin() as connection:
            connection.execute(
                delete(JournalEntry).where(JournalEntry.user_id == user_id)
            )


def test_journal_stats_pull_rejects_autosave_after_snapshot_before_final_lock(
    postgres_engine,
    monkeypatch: pytest.MonkeyPatch,
):
    session_factory = sessionmaker(
        bind=postgres_engine,
        autoflush=False,
    )
    user_id = str(uuid4())
    account_id = 20_000_000_000 + (uuid4().int % 4_000_000_000)
    original_date = date(2036, 3, 4)
    concurrent_date = date(2036, 3, 5)
    original_window_start, _ = trading_day_bounds_utc(original_date)
    source_trade_id = f"pg-stats-race-{uuid4().hex}"
    primary = session_factory()
    original_get_entry = journal_module._get_entry_for_account
    injected_autosaves = 0
    autosave_result: dict[str, object] = {}

    try:
        entry, _ = create_journal_entry(
            primary,
            user_id=user_id,
            account_id=account_id,
            entry_date=original_date,
            title="PostgreSQL stats race",
            mood="Neutral",
            tags=[],
            body="original body",
        )
        entry_id = int(entry.id)
        primary.add(
            ProjectXTradeEvent(
                user_id=user_id,
                account_id=account_id,
                contract_id="CON.F.US.MNQ.M36",
                symbol="MNQ",
                side="BUY",
                size=1,
                price=20_000,
                trade_timestamp=original_window_start + timedelta(hours=1),
                fees=1,
                pnl=101,
                order_id=f"order-{source_trade_id}",
                source_trade_id=source_trade_id,
                status="FILLED",
                raw_payload={"test": "postgres-stats-race"},
            )
        )
        primary.commit()

        def get_entry_after_autosave(db, **kwargs):
            nonlocal injected_autosaves
            if (
                db is primary
                and kwargs.get("for_update")
                and injected_autosaves == 0
            ):
                # This is the pull helper's final lock, reached only after its
                # old-date trade queries and stats snapshot have completed.
                injected_autosaves += 1
                with session_factory() as concurrent:
                    saved = update_journal_entry(
                        concurrent,
                        user_id=user_id,
                        account_id=account_id,
                        entry_id=entry_id,
                        version=1,
                        entry_date=concurrent_date,
                        body="concurrent autosave body",
                    )
                    autosave_result.update(
                        version=int(saved.version),
                        entry_date=saved.entry_date,
                        body=str(saved.body),
                    )
            return original_get_entry(db, **kwargs)

        monkeypatch.setattr(
            journal_module,
            "_get_entry_for_account",
            get_entry_after_autosave,
        )

        with pytest.raises(VersionConflictError) as exc_info:
            pull_journal_entry_trade_stats(
                primary,
                user_id=user_id,
                account_id=account_id,
                entry_id=entry_id,
            )

        assert injected_autosaves == 1
        assert autosave_result == {
            "version": 2,
            "entry_date": concurrent_date,
            "body": "concurrent autosave body",
        }
        assert int(exc_info.value.server_row.version) == 2
        assert exc_info.value.server_row.entry_date == concurrent_date
        assert exc_info.value.server_row.body == "concurrent autosave body"

        with session_factory() as verifier:
            saved = (
                verifier.query(JournalEntry)
                .filter(JournalEntry.id == entry_id)
                .one()
            )
            assert saved.entry_date == concurrent_date
            assert saved.body == "concurrent autosave body"
            assert int(saved.version) == 2
            assert saved.stats_source is None
            assert saved.stats_json is None
            assert saved.stats_pulled_at is None
    finally:
        primary.close()
        with postgres_engine.begin() as connection:
            connection.execute(
                delete(ProjectXTradeEvent).where(
                    ProjectXTradeEvent.user_id == user_id
                )
            )
            connection.execute(
                delete(JournalEntry).where(JournalEntry.user_id == user_id)
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
