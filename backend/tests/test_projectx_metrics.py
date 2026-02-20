from datetime import datetime, timezone

from app.services.projectx_metrics import TradeMetricSample, compute_trade_summary


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
    assert summary["fees"] == 12.0
    assert summary["net_pnl"] == 108.0
    assert summary["win_rate"] == 66.67
    assert summary["avg_win"] == 80.0
    assert summary["avg_loss"] == -40.0
    assert summary["max_drawdown"] == -43.5


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


def test_compute_trade_summary_fifo_fallback_when_pnl_is_missing():
    samples = [
        TradeMetricSample(timestamp=_dt(11, 0), pnl=None, fees=0.0, symbol="NQ", side="BUY", size=1.0, price=100.0),
        TradeMetricSample(timestamp=_dt(11, 1), pnl=None, fees=0.0, symbol="NQ", side="SELL", size=1.0, price=105.0),
        TradeMetricSample(timestamp=_dt(11, 2), pnl=None, fees=0.0, symbol="NQ", side="SELL", size=1.0, price=103.0),
        TradeMetricSample(timestamp=_dt(11, 3), pnl=None, fees=0.0, symbol="NQ", side="BUY", size=1.0, price=100.0),
    ]

    summary = compute_trade_summary(samples)

    # FIFO realized values by event: 0, +5, 0, +3
    assert summary["realized_pnl"] == 8.0
    assert summary["net_pnl"] == 8.0
    assert summary["win_rate"] == 100.0
    assert summary["trade_count"] == 4
