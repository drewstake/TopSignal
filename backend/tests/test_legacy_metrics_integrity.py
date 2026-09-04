import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.models import Account, Trade
from app.services import metrics


def test_incomplete_legacy_metrics_has_safe_actionable_http_response():
    import asyncio
    import json
    import app.main as main

    handler = main.app.exception_handlers[metrics.IncompleteTradePnlError]
    response = asyncio.run(handler(None, metrics.IncompleteTradePnlError("private account detail")))
    assert response.status_code == 503
    assert json.loads(response.body)["detail"]["code"] == "metrics_pnl_incomplete"
    assert b"private account detail" not in response.body


USER_A = "00000000-0000-0000-0000-000000000001"
USER_B = "00000000-0000-0000-0000-000000000002"
READERS = [
    metrics.get_summary_metrics, metrics.get_pnl_by_hour, metrics.get_pnl_by_day,
    metrics.get_pnl_by_symbol, metrics.get_streak_metrics, metrics.get_behavior_metrics,
]


@pytest.fixture
def db_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            Account(id=1, user_id=USER_A, provider="manual", external_id="1", name="Local"),
            Account(id=2, user_id=USER_B, provider="manual", external_id="2", name="Other"),
        ])
        session.commit()
        yield session
    engine.dispose()


def _trade(db, *, pnl, account_id=1, user_id=USER_A, closed=True, fees=2):
    opened = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)  # Sunday
    row = Trade(
        user_id=user_id, account_id=account_id, symbol="MNQ", side="LONG",
        opened_at=opened, closed_at=opened + timedelta(minutes=10) if closed else None,
        qty=2, entry_price=100, exit_price=110 if closed else None,
        pnl=pnl, fees=fees, is_rule_break=False,
    )
    db.add(row)
    db.commit()
    return row


@pytest.mark.parametrize("reader", READERS)
def test_closed_trade_missing_authoritative_pnl_blocks_every_metrics_view(db_session, reader):
    _trade(db_session, pnl=100)
    _trade(db_session, pnl=None)
    with pytest.raises(metrics.IncompleteTradePnlError, match="authoritative P&L"):
        reader(db_session, account_id=1, user_id=USER_A)


def test_authoritative_pnl_is_consistent_across_summary_and_aggregates(db_session):
    # Explicit dollar P&L differs from qty * raw price delta; never guess the multiplier.
    _trade(db_session, pnl=40)
    _trade(db_session, pnl=-15)
    summary = metrics.get_summary_metrics(db_session, 1, USER_A)
    assert summary["net_pnl"] == 21
    assert summary["trade_count"] == 2
    for reader in [metrics.get_pnl_by_hour, metrics.get_pnl_by_day, metrics.get_pnl_by_symbol]:
        rows = reader(db_session, 1, USER_A)
        assert sum(row["pnl"] for row in rows) == summary["net_pnl"]
        assert sum(row["trade_count"] for row in rows) == 2
    sunday = next(row for row in metrics.get_pnl_by_day(db_session, 1, USER_A) if row["day_of_week"] == 7)
    assert sunday["day_label"] == "Sun"
    assert sunday["pnl"] == 21


@pytest.mark.parametrize("reader", READERS)
def test_unknown_pnl_is_scoped_and_does_not_block_open_trades(db_session, reader):
    _trade(db_session, pnl=40)
    _trade(db_session, pnl=None, closed=False)
    _trade(db_session, pnl=None, account_id=2, user_id=USER_B)
    reader(db_session, account_id=1, user_id=USER_A)


@pytest.mark.parametrize("pnl, fees", [(float("nan"), 0), (float("inf"), 0), (1, float("inf"))])
def test_nonfinite_pnl_or_fees_cannot_be_published(pnl, fees):
    with pytest.raises(metrics.IncompleteTradePnlError, match="finite"):
        metrics._trade_net_pnl(SimpleNamespace(pnl=pnl, fees=fees))


def test_zero_authoritative_pnl_is_valid_and_fees_are_consistent(db_session):
    _trade(db_session, pnl=0, fees=None)
    assert metrics.get_summary_metrics(db_session, 1, USER_A)["net_pnl"] == 0
    assert metrics.get_pnl_by_symbol(db_session, 1, USER_A)[0]["pnl"] == 0
