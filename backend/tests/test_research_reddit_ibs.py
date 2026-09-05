from datetime import date, timedelta

import pytest

from tools.research_reddit_ibs import Bar, Costs, backtest, entry_signal, parse_yahoo, planned_quantity


ZERO = Costs("zero", 0, 0)


def example_bars():
    start = date(2020, 1, 1)
    bars = [Bar(start+timedelta(days=i), 100, 101, 99, 100) for i in range(25)]
    bars.extend([
        Bar(start+timedelta(days=25), 99, 100, 93, 93.5),  # IBS signal
        Bar(start+timedelta(days=26), 96, 99, 95, 98),  # buy next open; no exit
        Bar(start+timedelta(days=27), 98, 101, 97, 100),  # exit signal > prior high99
        Bar(start+timedelta(days=28), 102, 103, 100, 101),  # sell next open102
    ])
    return bars


def test_signals_fill_next_open_and_close_sizing_cannot_see_future():
    bars = example_bars()
    result = backtest(bars, bars[25].day, bars[-1].day, ZERO)
    trade = result["trades"][0]
    assert trade["entry_signal_date"] == bars[25].day
    assert trade["entry_date"] == bars[26].day
    assert trade["entry_fill"] == 96
    assert trade["exit_signal_date"] == bars[27].day
    assert trade["exit_fill"] == 102
    assert trade["quantity"] == planned_quantity(100000, 93.5, ZERO)
    assert result["summary"]["net_pnl"] == pytest.approx(6*trade["quantity"])
    assert entry_signal(bars, 25) == entry_signal(bars[:26], 25)
    assert not entry_signal(bars, 24)


def test_dividends_paid_once_to_previous_close_owner_even_when_exiting_open():
    bars = example_bars()
    bars[26] = Bar(bars[26].day, 96, 99, 95, 98, dividend=1)
    bars[28] = Bar(bars[28].day, 102, 103, 100, 101, dividend=2)
    result = backtest(bars, bars[25].day, bars[-1].day, ZERO)
    t = result["trades"][0]
    assert t["dividends"] == 2*t["quantity"]  # no entitlement on entry ex-date
    assert result["summary"]["cash_dividends"] == t["dividends"]
    assert t["net_pnl"] == 8*t["quantity"]


def test_costs_are_adverse_both_sides_and_reconcile():
    bars = example_bars()
    costs = Costs("test", .001, .002)
    result = backtest(bars, bars[25].day, bars[-1].day, costs)
    t = result["trades"][0]
    assert t["entry_fill"] == pytest.approx(96*1.002)
    assert t["exit_fill"] == pytest.approx(102*.998)
    net = t["gross_price_pnl"]+t["dividends"]-t["entry_slippage"]-t["exit_slippage"]-t["entry_fee"]-t["exit_fee"]
    assert result["summary"]["net_pnl"] == pytest.approx(net)
    assert result["equity"][1]["cash"] >= 0


def test_gap_rejects_unaffordable_order_instead_of_future_price_position_sizing():
    bars = example_bars()
    bars[26] = Bar(bars[26].day, 150, 151, 149, 150)
    result = backtest(bars, bars[25].day, bars[-1].day, ZERO)
    assert result["summary"]["cancelled_unaffordable_entries"] == 1
    assert not result["trades"]


def test_final_open_position_is_liquidated_and_terminal_signal_is_not_filled():
    bars = example_bars()[:27]
    result = backtest(bars, bars[25].day, bars[-1].day, ZERO)
    t = result["trades"][0]
    assert t["exit_reason"] == "sample_end_close_liquidation"
    assert t["exit_fill"] == 98
    assert t["exit_signal_date"] is None
    result = backtest(bars[:26], bars[25].day, bars[25].day, ZERO)
    assert not result["trades"]
    assert result["summary"]["unexecuted_final_signal"]["action"] == "buy"


def test_reused_period_starts_flat_even_if_preperiod_signal_exists():
    bars = example_bars()
    result = backtest(bars, bars[26].day, bars[-1].day, ZERO)
    assert not result["trades"]
    assert result["summary"]["ending_equity"] == 100000


def test_buy_hold_uses_same_costs_and_dividend_accounting():
    bars = example_bars()
    result = backtest(bars, bars[25].day, bars[-1].day, ZERO, buy_hold=True)
    t = result["trades"][0]
    assert t["quantity"] == 950  # prior close100, 95% of100k
    assert t["entry_date"] == bars[25].day
    assert t["entry_fill"] == 99
    assert t["exit_fill"] == 101
    assert result["summary"]["net_pnl"] == 1900


def test_yahoo_parser_rejects_splits_and_missing_quotes():
    payload = {"chart": {"result": [{"meta": {"symbol": "SPY", "exchangeTimezoneName": "America/New_York"},
                                      "events": {"splits": {"a": {"date": 1}}}}], "error": None}}
    with pytest.raises(ValueError, match="Split event"):
        parse_yahoo(payload, "SPY")
