from datetime import datetime, timezone

from app.services.projectx_metrics import TradeMetricSample, compute_daily_pnl_calendar, compute_trade_summary


def _dt(hour: int, minute: int = 0, *, day: int = 20) -> datetime:
    return datetime(2026, 2, day, hour, minute, tzinfo=timezone.utc)


def test_compute_trade_summary_empty_payload():
    summary = compute_trade_summary([])

    assert summary == {
        "realized_pnl": 0.0,
        "gross_pnl": 0.0,
        "fees": 0.0,
        "net_pnl": 0.0,
        "win_rate": 0.0,
        "win_count": 0,
        "loss_count": 0,
        "breakeven_count": 0,
        "profit_factor": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "avg_win_duration_minutes": 0.0,
        "avg_loss_duration_minutes": 0.0,
        "expectancy_per_trade": 0.0,
        "tail_risk_5pct": 0.0,
        "max_drawdown": 0.0,
        "average_drawdown": 0.0,
        "risk_drawdown_score": 0.0,
        "max_drawdown_length_hours": 0.0,
        "recovery_time_hours": 0.0,
        "average_recovery_length_hours": 0.0,
        "trade_count": 0,
        "half_turn_count": 0,
        "execution_count": 0,
        "day_win_rate": 0.0,
        "green_days": 0,
        "red_days": 0,
        "flat_days": 0,
        "avg_trades_per_day": 0.0,
        "active_days": 0,
        "efficiency_per_hour": 0.0,
        "profit_per_day": 0.0,
    }


def test_compute_trade_summary_with_mixed_results_and_fees():
    samples = [
        TradeMetricSample(timestamp=_dt(9, 0), pnl=100.0, fees=5.0),
        TradeMetricSample(timestamp=_dt(9, 15), pnl=-40.0, fees=2.0),
        TradeMetricSample(timestamp=_dt(9, 30), pnl=None, fees=1.5),
        TradeMetricSample(timestamp=_dt(9, 45), pnl=60.0, fees=3.5),
    ]

    summary = compute_trade_summary(samples)

    assert summary["trade_count"] == 3
    assert summary["execution_count"] == 4
    assert summary["half_turn_count"] == 4
    assert summary["realized_pnl"] == 120.0
    assert summary["gross_pnl"] == 120.0
    assert summary["fees"] == 10.5
    assert summary["net_pnl"] == 109.5
    assert summary["win_rate"] == 66.67
    assert summary["win_count"] == 2
    assert summary["loss_count"] == 1
    assert summary["breakeven_count"] == 0
    assert summary["profit_factor"] == 4.0
    assert summary["avg_win"] == 75.75
    assert summary["avg_loss"] == -42.0
    assert summary["avg_win_duration_minutes"] == 0.0
    assert summary["avg_loss_duration_minutes"] == 0.0
    assert summary["expectancy_per_trade"] == 36.5
    assert summary["tail_risk_5pct"] == -42.0
    assert summary["max_drawdown"] == -42.0
    assert summary["average_drawdown"] == -42.0
    assert summary["risk_drawdown_score"] == 44.21
    assert summary["max_drawdown_length_hours"] == 0.5
    assert summary["recovery_time_hours"] == 0.5
    assert summary["average_recovery_length_hours"] == 0.5
    assert summary["day_win_rate"] == 100.0
    assert summary["green_days"] == 1
    assert summary["red_days"] == 0
    assert summary["flat_days"] == 0
    assert summary["avg_trades_per_day"] == 3.0
    assert summary["active_days"] == 1
    assert summary["efficiency_per_hour"] == 146.0
    assert summary["profit_per_day"] == 109.5


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
    assert summary["trade_count"] == 0
    assert summary["execution_count"] == 4
    assert summary["half_turn_count"] == 4
    assert summary["day_win_rate"] == 0.0
    assert summary["green_days"] == 0
    assert summary["red_days"] == 0
    assert summary["flat_days"] == 1
    assert summary["active_days"] == 1


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
    assert summary["trade_count"] == 1


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
    assert summary["trade_count"] == 2


def test_compute_trade_summary_distinguishes_half_turns_and_executions():
    samples = [
        TradeMetricSample(timestamp=_dt(9, 0), pnl=None, fees=0.0, order_id="A"),
        TradeMetricSample(timestamp=_dt(9, 1), pnl=None, fees=0.0, order_id="A"),
        TradeMetricSample(timestamp=_dt(9, 2), pnl=50.0, fees=2.0, order_id="B"),
    ]

    summary = compute_trade_summary(samples)

    assert summary["execution_count"] == 3
    assert summary["half_turn_count"] == 2
    assert summary["trade_count"] == 1


def test_compute_trade_summary_day_win_rate_counts_green_red_and_flat_days():
    samples = [
        TradeMetricSample(timestamp=_dt(9, 0, day=20), pnl=100.0, fees=0.0),
        TradeMetricSample(timestamp=_dt(9, 0, day=21), pnl=-50.0, fees=0.0),
        TradeMetricSample(timestamp=_dt(9, 0, day=22), pnl=None, fees=0.0),
    ]

    summary = compute_trade_summary(samples)

    assert summary["day_win_rate"] == 33.33
    assert summary["green_days"] == 1
    assert summary["red_days"] == 1
    assert summary["flat_days"] == 1
    assert summary["active_days"] == 3
    assert summary["avg_trades_per_day"] == 0.67
    assert summary["profit_per_day"] == 16.67


def test_compute_trade_summary_calculates_avg_win_and_loss_durations():
    samples = [
        TradeMetricSample(timestamp=_dt(9, 0), pnl=None, fees=0.0, symbol="NQ", side="BUY", size=1.0),
        TradeMetricSample(timestamp=_dt(9, 10), pnl=100.0, fees=0.0, symbol="NQ", side="SELL", size=1.0),
        TradeMetricSample(timestamp=_dt(9, 20), pnl=None, fees=0.0, symbol="NQ", side="SELL", size=1.0),
        TradeMetricSample(timestamp=_dt(9, 32), pnl=-80.0, fees=0.0, symbol="NQ", side="BUY", size=1.0),
    ]

    summary = compute_trade_summary(samples)

    assert summary["trade_count"] == 2
    assert summary["avg_win_duration_minutes"] == 10.0
    assert summary["avg_loss_duration_minutes"] == 12.0


def test_compute_trade_summary_duration_matching_uses_most_recent_open_lot():
    samples = [
        TradeMetricSample(timestamp=_dt(9, 0), pnl=None, fees=0.0, symbol="NQ", side="SELL", size=1.0),
        TradeMetricSample(timestamp=_dt(10, 0), pnl=None, fees=0.0, symbol="NQ", side="SELL", size=1.0),
        TradeMetricSample(timestamp=_dt(10, 1), pnl=50.0, fees=0.0, symbol="NQ", side="BUY", size=1.0),
    ]

    summary = compute_trade_summary(samples)

    # LIFO matching closes the most recent lot first: 10:00 -> 10:01 = 1 minute.
    assert summary["avg_win_duration_minutes"] == 1.0
