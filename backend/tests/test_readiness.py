import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.main as main_module
from app.bot_worker import BotWorkerRuntime, BotWorkerSettings


class _Result:
    def __init__(self, value=1):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self, *, migration_applied=True, fail=False):
        self.migration_applied = migration_applied
        self.fail = fail
        self.rolled_back = False
        self.params = []

    def execute(self, statement, _params=None):
        self.params.append(_params)
        if self.fail:
            raise RuntimeError("database unavailable")
        if "topsignal_schema_migrations" in str(statement):
            return _Result(1 if self.migration_applied else None)
        return _Result()

    def get_bind(self):
        return object()

    def connection(self):
        return object()

    def rollback(self):
        self.rolled_back = True


class _Inspector:
    def __init__(
        self,
        *,
        include_ledger=True,
        include_baseline=False,
        include_databento=True,
        missing_table=None,
    ):
        self.include_ledger = include_ledger
        self.include_baseline = include_baseline
        self.include_databento = include_databento
        self.missing_table = missing_table

    def get_table_names(self):
        tables = [
            "account_emergency_actions",
            "accounts",
            "bot_backtests",
            "bot_configs",
            "bot_order_attempts",
            "bot_runtime_leases",
            "expense_suppressions",
            "projectx_trade_events",
            "trade_import_batches",
            "trade_import_previews",
        ]
        if self.include_databento:
            tables.extend(
                [
                    "databento_import_batches",
                    "databento_import_files",
                    "databento_instruments",
                    "databento_ohlcv_1m",
                    "databento_roll_schedule",
                ]
            )
        if self.include_ledger:
            tables.append("topsignal_schema_migrations")
        if self.include_baseline:
            tables.append("topsignal_schema_baselines")
        return [table for table in tables if table != self.missing_table]

    def get_columns(self, table_name):
        if table_name == "account_emergency_actions":
            return [
                {"name": column_name, "nullable": column_name in {"result_payload", "completed_at"}}
                for column_name in {
                    "id",
                    "user_id",
                    "account_id",
                    "status",
                    "confirmed_flat",
                    "lease_owner_id",
                    "lease_expires_at",
                    "attempt_count",
                    "request_payload",
                    "result_payload",
                    "completed_at",
                }
            ]
        if table_name == "accounts":
            return [
                {"name": "balance", "nullable": True},
                {"name": "trade_data_source", "nullable": False},
                {"name": "provider_simulated", "nullable": True},
                {"name": "provider_classification_observed_at", "nullable": True},
            ]
        if table_name == "trade_import_batches":
            return [
                {"name": column_name, "nullable": False}
                for column_name in {
                    "user_id",
                    "account_id",
                    "account_row_id",
                    "account_external_id",
                    "source_file_name",
                    "file_sha256",
                    "total_rows",
                    "inserted_rows",
                    "duplicate_rows",
                    "imported_at",
                }
            ]
        if table_name == "expense_suppressions":
            return [
                {"name": column_name, "nullable": False}
                for column_name in {"user_id", "source", "account_id", "created_at"}
            ]
        if table_name == "trade_import_previews":
            return [
                {"name": column_name, "nullable": False}
                for column_name in {
                    "token_hash",
                    "user_id",
                    "account_id",
                    "account_row_id",
                    "account_external_id",
                    "normalized_manifest",
                    "dedupe_snapshot",
                    "status",
                    "expires_at",
                    "retention_until",
                    "import_batch_id",
                }
            ]
        if table_name == "projectx_trade_events":
            return [
                {"name": column_name, "nullable": column_name == "import_batch_id"}
                for column_name in {
                    "commissions",
                    "fee_scope",
                    "trade_date",
                    "entry_timestamp",
                    "entry_price",
                    "import_batch_id",
                    "account_row_id",
                    "account_external_id",
                }
            ]
        databento_columns = {
            "databento_import_batches": {"archive_sha256", "status", "manifest_json"},
            "databento_import_files": {"batch_id", "filename", "file_sha256", "status"},
            "databento_instruments": {
                "dataset",
                "instrument_id",
                "raw_symbol",
                "root_symbol",
                "definition_ts",
            },
            "databento_ohlcv_1m": {
                "dataset",
                "instrument_id",
                "ts_event",
                "trading_date",
                "open_nano",
                "high_nano",
                "low_nano",
                "close_nano",
                "volume",
                "source_file_sha256",
            },
            "databento_roll_schedule": {
                "root_symbol",
                "trading_date",
                "instrument_id",
                "decision_session_date",
                "current_volume",
                "candidate_volume",
                "policy_version",
            },
        }
        if table_name in databento_columns:
            return [
                {"name": column_name, "nullable": False}
                for column_name in databento_columns[table_name]
            ]
        return [
            {"name": "bot_config_id", "nullable": True},
            {"name": "execution_mode", "nullable": False},
            {"name": "correlation_id", "nullable": True},
            {"name": "idempotency_key", "nullable": True},
        ]

    def get_check_constraints(self, table_name):
        if table_name == "account_emergency_actions":
            return [
                {
                    "sqltext": (
                        "status in ('pending','confirmed_account_flat','unconfirmed') "
                        "and ((status = 'confirmed_account_flat' and confirmed_flat) "
                        "or (status <> 'confirmed_account_flat' and not confirmed_flat)) "
                        "and ((status = 'pending' and completed_at is null) "
                        "or (status <> 'pending' and completed_at is not null)) "
                        "and (status <> 'pending' or (lease_owner_id is not null "
                        "and lease_expires_at is not null)) and attempt_count >= 1"
                    )
                }
            ]
        if table_name == "bot_configs":
            return [{"sqltext": "order_size <= 10000 and order_size = trunc(order_size)"}]
        if table_name == "projectx_trade_events":
            return [
                {"sqltext": "size <= 10000 and size = trunc(size)"},
                {"sqltext": "import_batch_id is null or account_row_id is not null"},
            ]
        return [{"sqltext": "status in ('pending', 'submission_unknown')"}]

    def get_indexes(self, table_name):
        if table_name == "account_emergency_actions":
            return [
                {
                    "name": "uq_account_emergency_actions_one_pending",
                    "unique": True,
                }
            ]
        return []

    def get_foreign_keys(self, table_name):
        if table_name == "projectx_trade_events":
            return [
                {
                    "constrained_columns": [
                        "import_batch_id",
                        "user_id",
                        "account_id",
                        "account_row_id",
                        "account_external_id",
                    ],
                    "referred_table": "trade_import_batches",
                    "options": {"ondelete": "RESTRICT"},
                }
            ]
        return [
            {
                "constrained_columns": ["bot_config_id"],
                "options": {"ondelete": "SET NULL"},
            }
        ]


def _worker_runtime(*, enabled: bool) -> BotWorkerRuntime:
    return BotWorkerRuntime(
        session_factory=lambda: None,
        client_factory=lambda *_args, **_kwargs: None,
        settings=BotWorkerSettings(enabled=enabled),
    )


def test_worker_health_requires_enabled_runtime_for_production_supervisor(monkeypatch):
    runtime = _worker_runtime(enabled=False)
    monkeypatch.setattr(main_module, "_bot_worker_runtime", runtime)

    optional = main_module.worker_health(require_enabled=False)
    required = main_module.worker_health(require_enabled=True)

    assert optional == {"status": "disabled", "enabled": False, "healthy": True}
    assert required.status_code == 503
    assert json.loads(required.body) == {
        "status": "disabled",
        "enabled": False,
        "healthy": False,
    }


def test_worker_health_requires_live_task_and_fresh_runner_heartbeat(monkeypatch):
    runtime = _worker_runtime(enabled=True)
    monkeypatch.setattr(main_module, "_bot_worker_runtime", runtime)

    missing_task = main_module.worker_health(require_enabled=True)
    assert missing_task.status_code == 503

    runtime._runner_task = SimpleNamespace(done=lambda: False)
    runtime._touch_runner_heartbeat()
    assert main_module.worker_health(require_enabled=True) == {
        "status": "starting",
        "enabled": True,
        "healthy": True,
    }

    runtime._runner_task = SimpleNamespace(done=lambda: True)
    completed = main_module.worker_health(require_enabled=True)
    assert completed.status_code == 503

    runtime._runner_task = SimpleNamespace(done=lambda: False)
    runtime._touch_runner_heartbeat(
        now=datetime.now(timezone.utc) - timedelta(seconds=46)
    )
    stale = main_module.worker_health(require_enabled=True)
    assert stale.status_code == 503

    runtime._touch_runner_heartbeat()
    runtime._replace_snapshot(state="crashed", owns_lease=False)
    response = main_module.worker_health(require_enabled=True)
    assert response.status_code == 503
    assert json.loads(response.body)["status"] == "crashed"


def test_readiness_requires_current_migration_ledger(monkeypatch):
    monkeypatch.setattr(main_module, "inspect", lambda _bind: _Inspector())
    db = _Session(migration_applied=True)

    assert main_module.readiness(db=db) == {"status": "ready"}
    assert db.rolled_back is False
    assert {"version": "20260903_add_bot_runtime_lease.sql"} in db.params


def test_readiness_fails_closed_for_pending_migration(monkeypatch):
    monkeypatch.setattr(main_module, "inspect", lambda _bind: _Inspector())
    db = _Session(migration_applied=False)

    response = main_module.readiness(db=db)

    assert response.status_code == 503
    assert json.loads(response.body) == {
        "status": "not_ready", "reason": "schema_outdated", "failed_checks": []
    }
    assert db.rolled_back is True


def test_readiness_database_failure_is_actionable_without_leaking_secrets():
    class DisconnectedSession(_Session):
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("postgresql://secret-user:secret-password@private-host/db")

        def rollback(self):
            raise RuntimeError("rollback also failed with private query parameters")

    response = main_module.readiness(db=DisconnectedSession())
    assert response.status_code == 503
    assert json.loads(response.body) == {
        "status": "not_ready", "reason": "database_unavailable", "failed_checks": []
    }
    assert response.headers["retry-after"] == "5"
    assert response.headers["cache-control"] == "no-store"


def test_readiness_reports_runtime_failed_checks_without_account_data(monkeypatch):
    monkeypatch.setattr(main_module, "inspect", lambda _bind: _Inspector())
    monkeypatch.setattr(main_module, "_bot_worker_runtime", object())
    monkeypatch.setattr(
        main_module, "inspect_bot_runtime",
        lambda *_args, **_kwargs: SimpleNamespace(
            ready=False, checks={"lease_held": True, "provider_healthy": False, "orders_resolved": False},
            failed_checks=("orders_resolved", "provider_healthy"),
        ),
    )
    response = main_module.readiness(db=_Session())
    assert response.status_code == 503
    assert json.loads(response.body) == {
        "status": "not_ready", "reason": "bot_runtime_not_ready",
        "failed_checks": ["orders_resolved", "provider_healthy"],
    }


def test_readiness_refuses_missing_enabled_worker(monkeypatch):
    monkeypatch.setattr(main_module, "inspect", lambda _bind: _Inspector())
    monkeypatch.setattr(main_module, "_bot_worker_runtime", None)
    monkeypatch.setenv("TOPSIGNAL_BOT_WORKER_ENABLED", "true")
    response = main_module.readiness(db=_Session())
    assert response.status_code == 503
    assert json.loads(response.body)["reason"] == "worker_not_started"


def test_liveness_does_not_depend_on_provider_or_database():
    assert main_module.health() == {"status": "ok"}


def test_readiness_rejects_unversioned_pre_runner_schema(monkeypatch):
    monkeypatch.setattr(main_module, "inspect", lambda _bind: _Inspector(include_ledger=False))
    response = main_module.readiness(db=_Session())

    assert response.status_code == 503


def test_readiness_accepts_schema_without_databento_market_tables(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "inspect",
        lambda _bind: _Inspector(include_databento=False),
    )

    assert main_module.readiness(db=_Session()) == {"status": "ready"}


def test_readiness_still_requires_projectx_bot_persistence(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "inspect",
        lambda _bind: _Inspector(missing_table="bot_order_attempts"),
    )

    response = main_module.readiness(db=_Session())
    assert response.status_code == 503


def test_readiness_requires_account_emergency_action_invariants(monkeypatch):
    class MissingEmergencyInvariantInspector(_Inspector):
        def get_check_constraints(self, table_name):
            if table_name == "account_emergency_actions":
                return [{"sqltext": "status in ('pending','confirmed_account_flat','unconfirmed')"}]
            return super().get_check_constraints(table_name)

    monkeypatch.setattr(
        main_module,
        "inspect",
        lambda _bind: MissingEmergencyInvariantInspector(),
    )

    response = main_module.readiness(db=_Session())
    assert response.status_code == 503


def test_readiness_requires_expense_suppressions(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "inspect",
        lambda _bind: _Inspector(missing_table="expense_suppressions"),
    )

    response = main_module.readiness(db=_Session())
    assert response.status_code == 503


def test_readiness_requires_trade_import_schema(monkeypatch):
    class MissingTradeImportColumnInspector(_Inspector):
        def get_columns(self, table_name):
            columns = super().get_columns(table_name)
            if table_name == "projectx_trade_events":
                return [
                    column
                    for column in columns
                    if column["name"] != "import_batch_id"
                ]
            return columns

    monkeypatch.setattr(
        main_module,
        "inspect",
        lambda _bind: MissingTradeImportColumnInspector(),
    )

    response = main_module.readiness(db=_Session())
    assert response.status_code == 503


def test_readiness_accepts_validated_fresh_schema_baseline(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "inspect",
        lambda _bind: _Inspector(include_ledger=False, include_baseline=True),
    )
    db = _Session()

    assert main_module.readiness(db=db) == {"status": "ready"}
    assert {"version": "schema-20260905-v7"} in db.params
