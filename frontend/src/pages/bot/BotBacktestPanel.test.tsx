import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { BotBacktestBreakdown, BotBacktestResult } from "../../lib/types";
import { BacktestResults } from "./BotBacktestPanel";

const breakdown: BotBacktestBreakdown = {
  trade_count: 1,
  winning_trades: 1,
  losing_trades: 0,
  win_rate: 100,
  gross_pnl: 50,
  net_pnl: 47.6,
  profit_factor: null,
  expectancy: 47.6,
  average_win: 47.6,
  average_loss: 0,
  payoff_ratio: null,
};

const result: BotBacktestResult = {
  id: 17,
  bot_config_id: 4,
  engine_version: "1.0.0",
  input_fingerprint: "test-fingerprint",
  created_at: "2026-02-02T18:00:00Z",
  range: { start: "2026-01-01T00:00:00Z", end: "2026-01-31T23:59:59Z", bar_count: 500 },
  config_snapshot: { strategy_type: "sma_cross", fast_period: 9, slow_period: 21 },
  assumptions: {
    fill_model: "next_bar_open",
    signal_timing: "closed bars only",
    event_order: "resting brackets then fills then intrabar brackets",
    same_bar_exit_rule: "stop_first",
    bracket_rule: "whole-tick distances anchored to fill",
    gap_rule: "stops fill at the gap open",
    final_position_handling: "force_close",
    position_rule: "target direction",
    session_rule: "configured limits",
    commission_rule: "per side",
    slippage_rule: "adverse",
    pnl_rule: "tick aware",
    metric_basis: "net trade P&L",
    market_data: "stored closed candles",
    live_order_routing: "disabled",
    timezone: "America/New_York",
    commission_per_contract: 1.2,
    slippage_ticks: 1,
    tick_size: 0.25,
    tick_value: 0.5,
    engine_version: "1.0.0",
    configured_execution_mode_was_ignored: "dry_run",
  },
  metrics: {
    gross_pnl: 50,
    net_pnl: 47.6,
    total_commission: 2.4,
    trade_count: 1,
    winning_trades: 1,
    losing_trades: 0,
    win_rate: 100,
    profit_factor: null,
    expectancy: 47.6,
    average_win: 47.6,
    average_loss: 0,
    payoff_ratio: null,
    max_drawdown_dollars: 10,
    max_drawdown_percent: 0.02,
    average_mae: -10,
    average_mfe: 65,
    max_consecutive_wins: 1,
    max_consecutive_losses: 0,
    exposure_percent: 4.5,
    long: breakdown,
    short: { ...breakdown, trade_count: 0, winning_trades: 0, win_rate: 0, gross_pnl: 0, net_pnl: 0, expectancy: 0, average_win: 0 },
  },
  equity_curve: [
    { timestamp: "2026-01-02T14:30:00Z", equity: 50_000, realized_pnl: 0, unrealized_pnl: 0 },
    { timestamp: "2026-01-02T15:00:00Z", equity: 50_047.6, realized_pnl: 47.6, unrealized_pnl: 0 },
  ],
  drawdown_series: [
    { timestamp: "2026-01-02T14:30:00Z", equity: 50_000, drawdown_dollars: 0, drawdown_percent: 0 },
    { timestamp: "2026-01-02T15:00:00Z", equity: 50_047.6, drawdown_dollars: 0, drawdown_percent: 0 },
  ],
  daily_results: [
    { period: "2026-01-02", gross_pnl: 50, net_pnl: 47.6, commission: 2.4, trade_count: 1, wins: 1, losses: 0 },
  ],
  monthly_results: [
    { period: "2026-01", gross_pnl: 50, net_pnl: 47.6, commission: 2.4, trade_count: 1, wins: 1, losses: 0 },
  ],
  trades: [
    {
      id: 1,
      side: "long",
      quantity: 1,
      signal_timestamp: "2026-01-02T14:30:00Z",
      entry_timestamp: "2026-01-02T14:35:00Z",
      entry_price: 20_000.25,
      exit_timestamp: "2026-01-02T15:00:00Z",
      exit_price: 20_025.25,
      exit_reason: "take_profit",
      gross_pnl: 50,
      commission: 2.4,
      net_pnl: 47.6,
      mae: -10,
      mfe: 65,
      bars_held: 5,
    },
  ],
  warnings: ["Only one trade occurred; summary statistics are not robust."],
};

describe("BacktestResults", () => {
  it("renders metrics, assumptions, warnings, chart, and trade ledger", () => {
    const markup = renderToStaticMarkup(<BacktestResults result={result} />);

    expect(markup).toContain("Net P&amp;L");
    expect(markup).toContain("$47.60");
    expect(markup).toContain("Sample-quality warnings");
    expect(markup).toContain("Only one trade occurred");
    expect(markup).toContain("Backtest equity and drawdown chart");
    expect(markup).toContain("next bar open");
    expect(markup).toContain("External routing disabled");
    expect(markup).toContain("Trade ledger");
    expect(markup).toContain("take profit");
  });
});
