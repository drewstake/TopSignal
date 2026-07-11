import json
import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.main as main_module


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
        tables = ["accounts", "bot_backtests", "bot_configs", "bot_order_attempts"]
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
        if table_name == "accounts":
            return [{"name": "balance", "nullable": True}]
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
        if table_name == "bot_configs":
            return [{"sqltext": "order_size <= 10000 and order_size = trunc(order_size)"}]
        return [{"sqltext": "status in ('pending', 'submission_unknown')"}]

    def get_foreign_keys(self, _table_name):
        return [
            {
                "constrained_columns": ["bot_config_id"],
                "options": {"ondelete": "SET NULL"},
            }
        ]


def test_readiness_requires_current_migration_ledger(monkeypatch):
    monkeypatch.setattr(main_module, "inspect", lambda _bind: _Inspector())
    db = _Session(migration_applied=True)

    assert main_module.readiness(db=db) == {"status": "ready"}
    assert db.rolled_back is False
    assert {"version": "20260711_seed_nq_es_instrument_metadata.sql"} in db.params


def test_readiness_fails_closed_for_pending_migration(monkeypatch):
    monkeypatch.setattr(main_module, "inspect", lambda _bind: _Inspector())
    db = _Session(migration_applied=False)

    response = main_module.readiness(db=db)

    assert response.status_code == 503
    assert json.loads(response.body) == {"status": "not_ready"}
    assert db.rolled_back is True


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


def test_readiness_accepts_validated_fresh_schema_baseline(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "inspect",
        lambda _bind: _Inspector(include_ledger=False, include_baseline=True),
    )
    db = _Session()

    assert main_module.readiness(db=db) == {"status": "ready"}
    assert {"version": "schema-20260711-v2"} in db.params
