from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.topbot_runner import cost_summary, statistics_item


def test_runner_costs_match_journal_and_preserve_broker_fees():
    result = cost_summary(.72, 6, datetime(2026, 11, 3, tzinfo=timezone.utc), "CON.F.US.MNQ.Z26")
    assert result["broker_fees_usd"] == .72
    assert result["commissions_usd"] == .5
    assert result["fees_usd"] == 1.22
    assert result["net_pnl_usd"] == pytest.approx(10.78)
    explicit = cost_summary(.72, 6, datetime(2026, 11, 3, tzinfo=timezone.utc), "CON.F.US.MNQ.Z26", 0)
    assert explicit["fees_usd"] == .72


def test_historical_view_does_not_rewrite_or_double_charge():
    runner = dict(stats_status="complete", fees_usd=.72, gross_points=6,
                  net_pnl_usd=11.28, exit_at="2026-11-03T20:50:00+00:00")
    row = SimpleNamespace(id=1, side="BUY", contract_id="CON.F.US.MNQ.Z26", raw_request={"timeExit": {"runner": runner}})
    before = deepcopy(row.raw_request)
    view = statistics_item(row)
    assert view["net_pnl_usd"] == pytest.approx(10.78)
    assert row.raw_request == before
    row.raw_request = {"timeExit": {"runner": view}}
    assert statistics_item(row)["net_pnl_usd"] == pytest.approx(10.78)


def test_commission_effective_date_and_unknown_fill():
    assert cost_summary(.72, 6, datetime(2026, 4, 11, tzinfo=timezone.utc), "MNQ")["fees_usd"] == .72
    row = SimpleNamespace(id=1, side="BUY", contract_id="MNQ", raw_request={"timeExit": {"runner": {"stats_status": "awaiting_fill"}}})
    assert "net_pnl_usd" not in statistics_item(row)
