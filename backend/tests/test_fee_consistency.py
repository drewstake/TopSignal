from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.projectx_trades import _to_metric_sample
from app.services.projectx_metrics import compute_trade_summary


def event(**changes):
    return SimpleNamespace(trade_timestamp=datetime(2026, 10, 23, 15, tzinfo=timezone.utc),
        trade_date=None, entry_timestamp=None, order_id="fixture", symbol="MNQ",
        contract_id="CON.F.US.MNQ.Z26", side="SELL", price=20000,
        **{"pnl": 100., "fees": .36, "commissions": .50, "fee_scope": "per_side", "size": 1., **changes})


@pytest.mark.parametrize("scope,normalized_fee", [("per_side", .72), ("round_turn", .36)])
@pytest.mark.parametrize("commissions", [None, 0., .50])
@pytest.mark.parametrize("pnl", [100., 0., -50.])
def test_dashboard_normalizes_fees_and_net(scope, normalized_fee, commissions, pnl):
    row = event(pnl=pnl, commissions=commissions, fee_scope=scope)
    dashboard = compute_trade_summary([_to_metric_sample(row)])
    expected_fees = normalized_fee + (.50 if commissions is None else commissions)
    assert dashboard['fees'] == pytest.approx(expected_fees)
    assert dashboard['net_pnl'] == pytest.approx(pnl-expected_fees)


def test_split_closes_and_entry_event_do_not_double_count_commissions():
    rows = [event(pnl=None, fees=.36, commissions=None),
            event(pnl=40., size=1., fees=.36, commissions=.50),
            event(pnl=60., size=1., fees=.72, commissions=.50, fee_scope="round_turn")]
    dashboard = compute_trade_summary([_to_metric_sample(row) for row in rows])
    assert dashboard['trade_count'] == 2 and dashboard['fees'] == 2.44
    assert dashboard['net_pnl'] == 97.56


@pytest.mark.parametrize("timestamp,commission", [
    (datetime(2026, 4, 11, 23, 59, tzinfo=timezone.utc), 0.),
    (datetime(2026, 4, 12, 0, 0, tzinfo=timezone.utc), .50),
    (datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc), .50),
    (datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc), .50),
])
def test_fee_effective_boundary_and_dst_instants_match(timestamp, commission):
    row = event(commissions=None)
    row.trade_timestamp = timestamp
    assert compute_trade_summary([_to_metric_sample(row)])['fees'] == pytest.approx(.72+commission)
