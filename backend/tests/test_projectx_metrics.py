from datetime import datetime, timezone

from app.services.projectx_metrics import TradeMetricSample, compute_daily_pnl_calendar, compute_trade_summary


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 2, 20, hour, minute, tzinfo=timezone.utc)


def test_compute_trade_summary_empty_payload():
    summary = compute_trade_summary([])

    assert summary == {
        "realized_pnl": 0.0,
        "gross_pnl": 0.0,
        "fees": 0.0,
        "net_pnl": 0.0,
        "win_rate": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "max_drawdown": 0.0,
        "trade_count": 0,
    }


def test_compute_trade_summary_with_mixed_results_and_fees():
    samples = [
        TradeMetricSample(timestamp=_dt(9, 0), pnl=100.0, fees=5.0),
        TradeMetricSample(timestamp=_dt(9, 15), pnl=-40.0, fees=2.0),
        TradeMetricSample(timestamp=_dt(9, 30), pnl=None, fees=1.5),
        TradeMetricSample(timestamp=_dt(9, 45), pnl=60.0, fees=3.5),
    ]

    summary = compute_trade_summary(samples)

    assert summary["trade_count"] == 4
    assert summary["realized_pnl"] == 120.0
    assert summary["gross_pnl"] == 120.0
    assert summary["fees"] == 10.5
    assert summary["net_pnl"] == 109.5
    assert summary["win_rate"] == 66.67
    assert summary["avg_win"] == 80.0
    assert summary["avg_loss"] == -40.0
    assert summary["max_drawdown"] == -42.0


def test_compute_trade_summary_drawdown_uses_net_values_in_order():
    samples = [
        TradeMetricSample(timestamp=_dt(10, 0), pnl=50.0, fees=0.0),
        TradeMetricSample(timestamp=_dt(10, 1), pnl=-30.0, fees=0.0),
        TradeMetricSample(timestamp=_dt(10, 2), pnl=-25.0, fees=0.0),
        TradeMetricSample(timestamp=_dt(10, 3), pnl=10.0, fees=0.0),
    ]

    summary = compute_trade_summary(samples)

    # Equity path: 50 -> 20 -> -5 -> 5, max drawdown from peak 50 down to -5 = -55.
    assert summary["max_drawdown"] == -55.0


def test_compute_trade_summary_treats_missing_pnl_as_zero_realized():
    samples = [
        TradeMetricSample(timestamp=_dt(11, 0), pnl=None, fees=0.0, symbol="NQ", side="BUY", size=1.0, price=100.0),
        TradeMetricSample(timestamp=_dt(11, 1), pnl=None, fees=0.0, symbol="NQ", side="SELL", size=1.0, price=105.0),
        TradeMetricSample(timestamp=_dt(11, 2), pnl=None, fees=0.0, symbol="NQ", side="SELL", size=1.0, price=103.0),
        TradeMetricSample(timestamp=_dt(11, 3), pnl=None, fees=0.0, symbol="NQ", side="BUY", size=1.0, price=100.0),
    ]

    summary = compute_trade_summary(samples)

    assert summary["realized_pnl"] == 0.0
    assert summary["net_pnl"] == 0.0
    assert summary["win_rate"] == 0.0
    assert summary["trade_count"] == 4


def test_compute_daily_pnl_calendar_groups_by_utc_date():
    samples = [
        TradeMetricSample(timestamp=_dt(9, 0), pnl=100.0, fees=5.0),
        TradeMetricSample(timestamp=_dt(9, 15), pnl=-30.0, fees=2.5),
        TradeMetricSample(timestamp=_dt(16, 0), pnl=40.0, fees=1.0),
    ]

    calendar = compute_daily_pnl_calendar(samples)

    assert calendar == [
        {
            "date": "2026-02-20",
            "trade_count": 3,
            "gross_pnl": 110.0,
            "fees": 8.5,
            "net_pnl": 101.5,
        }
    ]


def test_compute_daily_pnl_calendar_ignores_rows_without_broker_pnl():
    samples = [
        TradeMetricSample(timestamp=_dt(11, 0), pnl=None, fees=1.5, symbol="NQ", side="BUY", size=1.0, price=100.0),
        TradeMetricSample(timestamp=_dt(11, 1), pnl=None, fees=1.25, symbol="NQ", side="SELL", size=1.0, price=105.0),
        TradeMetricSample(timestamp=_dt(11, 2), pnl=None, fees=0.5, symbol="NQ", side="SELL", size=1.0, price=103.0),
        TradeMetricSample(timestamp=_dt(11, 3), pnl=None, fees=0.75, symbol="NQ", side="BUY", size=1.0, price=100.0),
    ]

    calendar = compute_daily_pnl_calendar(samples)

    assert calendar == []


def test_compute_trade_summary_ignores_open_leg_fees_for_net_pnl():
    samples = [
        TradeMetricSample(timestamp=_dt(12, 0), pnl=4508.5, fees=63.64),
        TradeMetricSample(timestamp=_dt(12, 1), pnl=None, fees=32.93),
    ]

    summary = compute_trade_summary(samples)

    assert summary["realized_pnl"] == 4508.5
    assert summary["fees"] == 63.64
    assert summary["net_pnl"] == 4444.86


def test_compute_trade_summary_does_not_infer_pnl_from_missing_rows_when_broker_pnl_exists():
    samples = [
        TradeMetricSample(timestamp=_dt(12, 0), pnl=None, fees=0.0, symbol="NQ", side="BUY", size=1.0, price=100.0),
        TradeMetricSample(timestamp=_dt(12, 1), pnl=50.0, fees=0.0, symbol="NQ", side="SELL", size=1.0, price=105.0),
        TradeMetricSample(timestamp=_dt(12, 2), pnl=None, fees=0.0, symbol="NQ", side="SELL", size=1.0, price=110.0),
        TradeMetricSample(timestamp=_dt(12, 3), pnl=20.0, fees=0.0, symbol="NQ", side="BUY", size=1.0, price=108.0),
    ]

    summary = compute_trade_summary(samples)

    assert summary["realized_pnl"] == 70.0
    assert summary["net_pnl"] == 70.0
    assert summary["win_rate"] == 100.0
