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
        "averagePositionSize": 0.0,
        "medianPositionSize": 0.0,
        "tradeCountUsedForSizingStats": 0,
        "avgPointGain": None,
        "avgPointLoss": None,
        "pointsBasisUsed": "auto",
        "sizingBenchmark": {
            "benchmarkMode": "fixed_average_size",
            "benchmarkSizeUsed": 0.0,
            "benchmarkGrossPnl": 0.0,
            "benchmarkNetPnl": 0.0,
            "benchmarkDiff": 0.0,
            "benchmarkRatio": None,
            "benchmarkLabel": "In Line With Benchmark",
        },
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


def test_compute_trade_summary_calculates_position_size_stats_from_closed_trades():
    samples = [
        TradeMetricSample(timestamp=_dt(9, 0), pnl=120.0, fees=4.0, size=2.0, symbol="MNQ"),
        TradeMetricSample(timestamp=_dt(9, 5), pnl=-30.0, fees=2.0, size=4.0, symbol="MNQ"),
        TradeMetricSample(timestamp=_dt(9, 10), pnl=80.0, fees=2.0, size=8.0, symbol="MNQ"),
        TradeMetricSample(timestamp=_dt(9, 15), pnl=150.0, fees=3.0, size=12.0, symbol="MNQ"),
        TradeMetricSample(timestamp=_dt(9, 20), pnl=None, fees=1.0, size=25.0, symbol="MNQ"),
    ]

    summary = compute_trade_summary(samples)

    assert summary["averagePositionSize"] == 6.5
    assert summary["medianPositionSize"] == 6.0
    assert summary["tradeCountUsedForSizingStats"] == 4


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
    assert summary["averagePositionSize"] == 0.0
    assert summary["medianPositionSize"] == 0.0
    assert summary["tradeCountUsedForSizingStats"] == 0


def test_compute_daily_pnl_calendar_groups_by_new_york_trading_day():
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


def test_compute_daily_pnl_calendar_rolls_sunday_trades_into_monday_bucket():
    samples = [
        TradeMetricSample(timestamp=datetime(2026, 3, 1, 23, 50, tzinfo=timezone.utc), pnl=40.0, fees=1.0),
        TradeMetricSample(timestamp=datetime(2026, 3, 2, 0, 9, tzinfo=timezone.utc), pnl=60.0, fees=1.5),
    ]

    calendar = compute_daily_pnl_calendar(samples)

    assert calendar == [
        {
            "date": "2026-03-02",
            "trade_count": 2,
            "gross_pnl": 100.0,
            "fees": 2.5,
            "net_pnl": 97.5,
        }
    ]


def test_compute_daily_pnl_calendar_rolls_after_6pm_et_to_next_day():
    samples = [
        # 5:59 PM ET Monday -> Monday trading day bucket.
        TradeMetricSample(timestamp=datetime(2026, 3, 2, 22, 59, tzinfo=timezone.utc), pnl=10.0, fees=1.0),
        # 6:00 PM ET Monday -> Tuesday trading day bucket.
        TradeMetricSample(timestamp=datetime(2026, 3, 2, 23, 0, tzinfo=timezone.utc), pnl=20.0, fees=1.0),
        # Reported case (trade id 2211911285): Monday 6:09 PM ET -> Tuesday bucket.
        TradeMetricSample(timestamp=datetime(2026, 3, 2, 23, 9, tzinfo=timezone.utc), pnl=30.0, fees=1.0),
    ]

    calendar = compute_daily_pnl_calendar(samples)

    assert calendar == [
        {
            "date": "2026-03-02",
            "trade_count": 1,
            "gross_pnl": 10.0,
            "fees": 1.0,
            "net_pnl": 9.0,
        },
        {
            "date": "2026-03-03",
            "trade_count": 2,
            "gross_pnl": 50.0,
            "fees": 2.0,
            "net_pnl": 48.0,
        },
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


def test_compute_trade_summary_daily_stats_use_new_york_day_boundary_across_midnight_utc():
    samples = [
        TradeMetricSample(timestamp=datetime(2026, 3, 1, 23, 50, tzinfo=timezone.utc), pnl=100.0, fees=0.0),
        TradeMetricSample(timestamp=datetime(2026, 3, 2, 0, 9, tzinfo=timezone.utc), pnl=-50.0, fees=0.0),
    ]

    summary = compute_trade_summary(samples)

    assert summary["active_days"] == 1
    assert summary["green_days"] == 1
    assert summary["red_days"] == 0
    assert summary["flat_days"] == 0
    assert summary["day_win_rate"] == 100.0
    assert summary["avg_trades_per_day"] == 2.0
    assert summary["profit_per_day"] == 50.0


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


def test_compute_trade_summary_points_basis_auto_uses_trade_symbol_point_value():
    samples = [
        TradeMetricSample(timestamp=_dt(9, 0), pnl=5.0, fees=1.0, symbol="MNQ", size=1.0),
        TradeMetricSample(timestamp=_dt(9, 1), pnl=-6.0, fees=0.0, symbol="MES", size=2.0),
    ]

    summary = compute_trade_summary(samples)

    # MNQ point value = 0.5 / 0.25 = 2 -> 4/2 = 2.0 points
    # MES point value = 1.25 / 0.25 = 5 -> -6/(2*5) = -0.6 points
    assert summary["avgPointGain"] == 2.0
    assert summary["avgPointLoss"] == 0.6
    assert summary["pointsBasisUsed"] == "auto"


def test_compute_trade_summary_points_basis_filters_to_requested_symbol():
    samples = [
        TradeMetricSample(timestamp=_dt(9, 0), pnl=10.0, fees=0.0, symbol="MNQ", size=1.0),
        TradeMetricSample(timestamp=_dt(9, 1), pnl=-20.0, fees=0.0, symbol="MES", size=2.0),
        TradeMetricSample(timestamp=_dt(9, 2), pnl=15.0, fees=0.0, symbol="MES", size=1.0),
    ]

    summary = compute_trade_summary(samples, points_basis="MES")

    # MES basis uses only MES trades:
    # Gain: 15/(1*5) = 3.0 points
    # Loss: abs(-20/(2*5)) = 2.0 points
    assert summary["avgPointGain"] == 3.0
    assert summary["avgPointLoss"] == 2.0
    assert summary["pointsBasisUsed"] == "MES"


def test_compute_trade_summary_uses_average_position_size_for_mnq_benchmark():
    samples = [
        TradeMetricSample(timestamp=_dt(9, 0), pnl=32.0, fees=8.0, symbol="MNQ", size=4.0),
        TradeMetricSample(timestamp=_dt(9, 1), pnl=48.0, fees=16.0, symbol="MNQ", size=8.0),
    ]

    summary = compute_trade_summary(samples)
    benchmark = summary["sizingBenchmark"]

    assert benchmark["benchmarkMode"] == "fixed_average_size"
    assert benchmark["benchmarkSizeUsed"] == 6.0
    assert benchmark["benchmarkGrossPnl"] == 84.0
    assert benchmark["benchmarkNetPnl"] == 60.0
    assert benchmark["benchmarkDiff"] == -4.0
    assert benchmark["benchmarkRatio"] == 0.9333
    assert benchmark["benchmarkLabel"] == "In Line With Benchmark"


def test_compute_trade_summary_recalculates_benchmark_fees_for_average_size():
    samples = [
        TradeMetricSample(timestamp=_dt(9, 0), pnl=20.0, fees=4.0, symbol="MNQ", size=2.0),
        TradeMetricSample(timestamp=_dt(9, 1), pnl=80.0, fees=8.0, symbol="MNQ", size=8.0),
    ]

    summary = compute_trade_summary(samples)
    benchmark = summary["sizingBenchmark"]

    assert benchmark["benchmarkSizeUsed"] == 5.0
    assert benchmark["benchmarkGrossPnl"] == 100.0
    assert benchmark["benchmarkNetPnl"] == 85.0
    assert benchmark["benchmarkDiff"] == 3.0
    assert benchmark["benchmarkRatio"] == 1.0353
    assert benchmark["benchmarkLabel"] == "In Line With Benchmark"


def test_compute_trade_summary_assigns_positive_benchmark_ratio_labels():
    samples = [
        TradeMetricSample(timestamp=_dt(9, 0), pnl=16.0, fees=4.0, symbol="MNQ", size=2.0),
        TradeMetricSample(timestamp=_dt(9, 1), pnl=120.0, fees=20.0, symbol="MNQ", size=10.0),
    ]

    summary = compute_trade_summary(samples)
    benchmark = summary["sizingBenchmark"]

    assert benchmark["benchmarkSizeUsed"] == 6.0
    assert benchmark["benchmarkNetPnl"] == 96.0
    assert benchmark["benchmarkRatio"] == 1.1667
    assert benchmark["benchmarkLabel"] == "Above Benchmark"


def test_compute_trade_summary_uses_diff_fallback_when_benchmark_is_near_zero():
    samples = [
        TradeMetricSample(timestamp=_dt(9, 0), pnl=16.0, fees=8.0, symbol="MNQ", size=4.0),
        TradeMetricSample(timestamp=_dt(9, 1), pnl=0.0, fees=4.8, symbol="MNQ", size=6.0),
    ]

    summary = compute_trade_summary(samples)
    benchmark = summary["sizingBenchmark"]

    assert benchmark["benchmarkSizeUsed"] == 5.0
    assert benchmark["benchmarkNetPnl"] == 6.0
    assert benchmark["benchmarkRatio"] is None
    assert benchmark["benchmarkDiff"] == -2.8
    assert benchmark["benchmarkLabel"] == "In Line With Benchmark"


def test_compute_trade_summary_uses_diff_fallback_when_benchmark_is_negative():
    samples = [
        TradeMetricSample(timestamp=_dt(9, 0), pnl=100.0, fees=10.0, symbol="MNQ", size=10.0),
        TradeMetricSample(timestamp=_dt(9, 1), pnl=-50.0, fees=2.0, symbol="MNQ", size=1.0),
    ]

    summary = compute_trade_summary(samples)
    benchmark = summary["sizingBenchmark"]

    assert benchmark["benchmarkSizeUsed"] == 5.5
    assert benchmark["benchmarkNetPnl"] == -236.5
    assert benchmark["benchmarkRatio"] is None
    assert benchmark["benchmarkDiff"] == 274.5
    assert benchmark["benchmarkLabel"] == "Far Above Benchmark"


def test_compute_trade_summary_combines_average_size_benchmarks_in_mixed_periods():
    samples = [
        TradeMetricSample(timestamp=_dt(9, 0), pnl=20.0, fees=4.0, symbol="MES", size=2.0),
        TradeMetricSample(timestamp=_dt(9, 1), pnl=32.0, fees=8.0, symbol="MNQ", size=4.0),
    ]

    summary = compute_trade_summary(samples)
    benchmark = summary["sizingBenchmark"]

    assert benchmark["benchmarkSizeUsed"] == 3.0
    assert benchmark["benchmarkGrossPnl"] == 54.0
    assert benchmark["benchmarkNetPnl"] == 42.0
    assert benchmark["benchmarkDiff"] == -2.0
    assert benchmark["benchmarkRatio"] == 0.9524
    assert benchmark["benchmarkLabel"] == "In Line With Benchmark"


def test_compute_trade_summary_handles_missing_or_invalid_average_position_size_for_benchmark():
    samples = [
        TradeMetricSample(timestamp=_dt(9, 0), pnl=100.0, fees=10.0, symbol="MNQ", size=0.0),
        TradeMetricSample(timestamp=_dt(9, 1), pnl=50.0, fees=5.0, symbol="MNQ", size=None),
    ]

    summary = compute_trade_summary(samples)
    benchmark = summary["sizingBenchmark"]

    assert summary["averagePositionSize"] == 0.0
    assert benchmark["benchmarkMode"] == "fixed_average_size"
    assert benchmark["benchmarkSizeUsed"] == 0.0
    assert benchmark["benchmarkGrossPnl"] == 0.0
    assert benchmark["benchmarkNetPnl"] == 0.0
    assert benchmark["benchmarkDiff"] == 135.0
    assert benchmark["benchmarkRatio"] is None
    assert benchmark["benchmarkLabel"] == "Above Benchmark"


def test_compute_trade_summary_adds_topstep_micro_commission_after_april_12_2026():
    samples = [
        TradeMetricSample(
            timestamp=datetime(2026, 4, 13, 9, 0, tzinfo=timezone.utc),
            pnl=100.0,
            fees=0.74,
            symbol="MNQ",
            size=1.0,
        ),
    ]

    summary = compute_trade_summary(samples)

    assert summary["fees"] == 1.24
    assert summary["net_pnl"] == 98.76


def test_compute_trade_summary_adds_topstep_non_micro_commission_after_april_12_2026():
    samples = [
        TradeMetricSample(
            timestamp=datetime(2026, 4, 13, 9, 0, tzinfo=timezone.utc),
            pnl=100.0,
            fees=2.8,
            symbol="NQ",
            size=1.0,
        ),
    ]

    summary = compute_trade_summary(samples)

    assert summary["fees"] == 3.8
    assert summary["net_pnl"] == 96.2


def test_compute_trade_summary_scales_topstep_commission_by_contract_size():
    samples = [
        TradeMetricSample(
            timestamp=datetime(2026, 4, 13, 9, 0, tzinfo=timezone.utc),
            pnl=110.0,
            fees=7.4,
            symbol="MNQ",
            size=10.0,
        ),
    ]

    summary = compute_trade_summary(samples)

    assert summary["fees"] == 12.4
    assert summary["net_pnl"] == 97.6
