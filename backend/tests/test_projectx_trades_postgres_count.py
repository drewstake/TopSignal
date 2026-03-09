from datetime import datetime, timezone

from app.models import ProjectXTradeEvent
from app.services import projectx_trades


class _FakeScalarResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return list(self._values)


class _FakeExecuteResult:
    rowcount = -1

    def __init__(self, inserted_ids):
        self._inserted_ids = inserted_ids

    def scalars(self):
        return _FakeScalarResult(self._inserted_ids)


class _FakeInsertStatement:
    def __init__(self):
        self.values_payload = None
        self.returning_column = None

    def values(self, values):
        self.values_payload = values
        return self

    def on_conflict_do_nothing(self):
        return self

    def returning(self, column):
        self.returning_column = column
        return self


class _FakeSession:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids
        self.executed_stmt = None

    def execute(self, stmt):
        self.executed_stmt = stmt
        return _FakeExecuteResult(self.inserted_ids)


def test_store_trade_events_postgres_counts_returned_ids_when_rowcount_is_unknown(monkeypatch):
    fake_stmt = _FakeInsertStatement()
    fake_db = _FakeSession(inserted_ids=[101, 102])
    event_timestamp = datetime(2026, 3, 9, 14, 30, tzinfo=timezone.utc)

    monkeypatch.setattr(projectx_trades, "pg_insert", lambda model: fake_stmt)

    inserted_count = projectx_trades._store_trade_events_postgres(
        fake_db,
        [
            {
                "account_id": 8801,
                "contract_id": "CON.F.US.MNQ.H26",
                "symbol": "MNQ",
                "side": "BUY",
                "size": 1.0,
                "price": 20500.0,
                "timestamp": event_timestamp,
                "fees": 1.4,
                "pnl": 45.0,
                "order_id": "ORD-8801-1",
                "source_trade_id": "SRC-8801-1",
                "status": "FILLED",
                "raw_payload": {"id": "SRC-8801-1"},
            },
            {
                "account_id": 8801,
                "contract_id": "CON.F.US.MNQ.H26",
                "symbol": "MNQ",
                "side": "SELL",
                "size": 1.0,
                "price": 20510.0,
                "timestamp": event_timestamp,
                "fees": 1.4,
                "pnl": 55.0,
                "order_id": "ORD-8801-2",
                "source_trade_id": "SRC-8801-2",
                "status": "FILLED",
                "raw_payload": {"id": "SRC-8801-2"},
            },
        ],
        user_id="user-1",
    )

    assert inserted_count == 2
    assert fake_db.executed_stmt is fake_stmt
    assert fake_stmt.returning_column is ProjectXTradeEvent.id
    assert fake_stmt.values_payload is not None
