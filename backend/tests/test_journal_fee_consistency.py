from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect

from app.models import ProjectXTradeEvent
from app.services import journal
from app.services.projectx_trades import _to_metric_sample
from app.services.projectx_metrics import compute_trade_summary
from test_journal_service import db_session


def event(**changes):
    return SimpleNamespace(trade_timestamp=datetime(2026, 10, 23, 15, tzinfo=timezone.utc),
        trade_date=None, entry_timestamp=None, order_id="fixture", symbol="MNQ",
        contract_id="CON.F.US.MNQ.Z26", side="SELL", price=20000,
        **{"pnl": 100., "fees": .36, "commissions": .50, "fee_scope": "per_side", "size": 1., **changes})


@pytest.mark.parametrize("scope,normalized_fee", [("per_side", .72), ("round_turn", .36)])
@pytest.mark.parametrize("commissions", [None, 0., .50])
@pytest.mark.parametrize("pnl", [100., 0., -50.])
def test_journal_and_dashboard_agree_on_fees_and_net(scope, normalized_fee, commissions, pnl):
    row = event(pnl=pnl, commissions=commissions, fee_scope=scope)
    snapshot = journal._compute_trade_stats_snapshot([row])
    dashboard = compute_trade_summary([_to_metric_sample(row)])
    expected_fees = normalized_fee + (.50 if commissions is None else commissions)
    assert snapshot["total_fees"] == pytest.approx(expected_fees)
    assert snapshot["net"] == pytest.approx(pnl-expected_fees)
    assert snapshot["net"] == dashboard["net_pnl"]


def test_split_closes_and_entry_event_do_not_double_count_commissions():
    rows = [event(pnl=None, fees=.36, commissions=None),
            event(pnl=40., size=1., fees=.36, commissions=.50),
            event(pnl=60., size=1., fees=.72, commissions=.50, fee_scope="round_turn")]
    snapshot = journal._compute_trade_stats_snapshot(rows)
    dashboard = compute_trade_summary([_to_metric_sample(row) for row in rows])
    assert snapshot["trade_count"] == 2
    assert snapshot["total_fees"] == 2.44
    assert snapshot["net"] == dashboard["net_pnl"] == 97.56


@pytest.mark.parametrize("timestamp,commission", [
    (datetime(2026, 4, 11, 23, 59, tzinfo=timezone.utc), 0.),
    (datetime(2026, 4, 12, 0, 0, tzinfo=timezone.utc), .50),
    (datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc), .50),
    (datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc), .50),
])
def test_fee_effective_boundary_and_dst_instants_match(timestamp, commission):
    row = event(commissions=None)
    row.trade_timestamp = timestamp
    snapshot = journal._compute_trade_stats_snapshot([row])
    assert snapshot["net"] == compute_trade_summary([_to_metric_sample(row)])["net_pnl"]
    assert snapshot["total_fees"] == pytest.approx(.72 + commission)


def test_explicit_pull_loads_fee_scope_and_only_updates_requested_snapshot(db_session, monkeypatch):
    entry, _ = journal.create_journal_entry(db_session, user_id="owner", account_id=999,
        entry_date=date(2026, 10, 23), title="Fixture", mood="NEUTRAL", tags=[], body="")
    old = {"snapshot_version": 2, "net": 99.14}
    entry.stats_json = old
    row = event()
    db_session.add(ProjectXTradeEvent(user_id="owner", account_id=999,
        source_trade_id="fixture", raw_payload={}, **vars(row)))
    db_session.commit()
    db_session.refresh(entry)
    assert entry.stats_json == old  # Existing stored results are not auto-rewritten.
    original = journal._compute_trade_stats_snapshot
    def check_loaded(rows, **kwargs):
        rows = list(rows)
        assert len(rows) == 1
        assert "fee_scope" not in inspect(rows[0]).unloaded
        return original(rows, **kwargs)
    monkeypatch.setattr(journal, "_compute_trade_stats_snapshot", check_loaded)
    journal.pull_journal_entry_trade_stats(db_session, user_id="owner", account_id=999, entry_id=entry.id,
                                          entry_date=date(2026, 10, 23))
    db_session.refresh(entry)
    assert entry.stats_json["net"] == 98.78
