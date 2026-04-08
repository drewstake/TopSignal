import { describe, expect, it } from "vitest";

import type { AccountSummary, AccountTrade, JournalEntry } from "../../lib/types";
import { buildJournalCopyText, buildJournalCopyTradeStats, getCopyEntry } from "./journalCopy";

const sampleEntry: JournalEntry = {
  id: 17,
  account_id: 13001,
  entry_date: "2026-03-06",
  title: "Stayed patient through the chop",
  mood: "Focused",
  tags: ["discipline", "risk", "patience"],
  body: "Today I followed my plan well.\n![Journal image](journal-image://12)\nStayed patient after the open.",
  version: 3,
  stats_source: "trade_snapshot",
  stats_json: {
    snapshot_version: 2,
    trade_count: 3,
    total_pnl: 426.62,
    total_fees: 6.12,
    win_rate: 66.67,
    avg_win: 257.75,
    avg_loss: -95,
    largest_win: 305.5,
    largest_loss: -95,
    gross: 426.62,
    net: 420.5,
    net_realized_pnl: 420.5,
  },
  stats_pulled_at: "2026-03-06T21:00:00.000Z",
  is_archived: false,
  created_at: "2026-03-06T20:00:00.000Z",
  updated_at: "2026-03-06T21:00:00.000Z",
};

const sampleSummary: AccountSummary = {
  realized_pnl: 426.62,
  gross_pnl: 426.62,
  fees: 6.12,
  net_pnl: 420.5,
  win_rate: 66.67,
  win_count: 2,
  loss_count: 1,
  breakeven_count: 0,
  profit_factor: 1.85,
  avg_win: 257.75,
  avg_loss: -95,
  avg_win_duration_minutes: 0,
  avg_loss_duration_minutes: 0,
  expectancy_per_trade: 140.17,
  tail_risk_5pct: 0,
  max_drawdown: 0,
  average_drawdown: 0,
  risk_drawdown_score: 0,
  max_drawdown_length_hours: 0,
  recovery_time_hours: 0,
  average_recovery_length_hours: 0,
  trade_count: 3,
  half_turn_count: 3,
  execution_count: 3,
  day_win_rate: 100,
  green_days: 1,
  red_days: 0,
  flat_days: 0,
  avg_trades_per_day: 3,
  active_days: 1,
  efficiency_per_hour: 0,
  profit_per_day: 420.5,
  averagePositionSize: 1,
  medianPositionSize: 1,
  tradeCountUsedForSizingStats: 3,
  avgPointGain: null,
  avgPointLoss: null,
  pointsBasisUsed: "auto",
  sizingBenchmark: {
    benchmarkMode: "fixed_average_size",
    benchmarkSizeUsed: 1,
    benchmarkGrossPnl: 415.4,
    benchmarkNetPnl: 405.2,
    benchmarkDiff: 15.3,
    benchmarkRatio: 1.0378,
    benchmarkLabel: "In Line With Benchmark",
  },
};

const sampleTrades: AccountTrade[] = [
  {
    id: 1,
    account_id: 13001,
    contract_id: "MNQM6",
    symbol: "MNQ",
    side: "BUY",
    size: 1,
    price: 0,
    timestamp: "2026-03-06T14:00:00.000Z",
    entry_time: "2026-03-06T13:45:00.000Z",
    exit_time: "2026-03-06T14:00:00.000Z",
    duration_minutes: 15,
    entry_price: 0,
    exit_price: 0,
    fees: 1.02,
    pnl: 211.02,
    order_id: "a",
    source_trade_id: "a",
  },
  {
    id: 2,
    account_id: 13001,
    contract_id: "MNQM6",
    symbol: "MNQ",
    side: "SELL",
    size: 1,
    price: 0,
    timestamp: "2026-03-06T15:00:00.000Z",
    entry_time: "2026-03-06T14:40:00.000Z",
    exit_time: "2026-03-06T15:00:00.000Z",
    duration_minutes: 20,
    entry_price: 0,
    exit_price: 0,
    fees: 2.04,
    pnl: 307.54,
    order_id: "b",
    source_trade_id: "b",
  },
  {
    id: 3,
    account_id: 13001,
    contract_id: "MESM6",
    symbol: "MES",
    side: "BUY",
    size: 1,
    price: 0,
    timestamp: "2026-03-06T15:30:00.000Z",
    entry_time: "2026-03-06T15:20:00.000Z",
    exit_time: "2026-03-06T15:30:00.000Z",
    duration_minutes: 10,
    entry_price: 0,
    exit_price: 0,
    fees: 2.04,
    pnl: -92.96,
    order_id: "c",
    source_trade_id: "c",
  },
];

describe("getCopyEntry", () => {
  it("returns the selected entry when it is visible", () => {
    const secondEntry = { ...sampleEntry, id: 18, entry_date: "2026-03-05" };

    expect(getCopyEntry([sampleEntry, secondEntry], 18)).toEqual(secondEntry);
  });

  it("falls back to the first visible entry when no selection exists", () => {
    expect(getCopyEntry([sampleEntry], null)).toEqual(sampleEntry);
  });
});

describe("buildJournalCopyTradeStats", () => {
  it("combines summary and trade data into a compact trade stats block", () => {
    expect(
      buildJournalCopyTradeStats({
        entry: sampleEntry,
        summary: sampleSummary,
        trades: sampleTrades,
      }),
    ).toEqual({
      netPnl: 420.5,
      tradeCount: 3,
      winRate: 66.67,
      profitFactor: 1.85,
      expectancy: 140.17,
      bestTrade: 305.5,
      worstTrade: -95,
      symbols: ["MNQ", "MES"],
    });
  });

  it("omits fetched trade-only fields when the trade rows are incomplete for the day", () => {
    expect(
      buildJournalCopyTradeStats({
        entry: sampleEntry,
        summary: {
          ...sampleSummary,
          trade_count: 5,
        },
        trades: sampleTrades,
      }),
    ).toEqual({
      netPnl: 420.5,
      tradeCount: 5,
      winRate: 66.67,
      profitFactor: 1.85,
      expectancy: 140.17,
      bestTrade: 305.5,
      worstTrade: -95,
    });
  });

  it("uses legacy snapshot payloads when live summary data is unavailable", () => {
    expect(
      buildJournalCopyTradeStats({
        entry: {
          ...sampleEntry,
          stats_json: {
            ...sampleEntry.stats_json!,
            snapshot_version: 1,
          },
        },
      }),
    ).toEqual({
      netPnl: 420.5,
      tradeCount: 3,
      winRate: 66.67,
      bestTrade: 305.5,
      worstTrade: -95,
    });
  });
});

describe("buildJournalCopyText", () => {
  it("formats the journal copy payload for ChatGPT-friendly plain text", () => {
    const tradeStatsByDate = new Map([
      [
        sampleEntry.entry_date,
        buildJournalCopyTradeStats({
          entry: sampleEntry,
          summary: sampleSummary,
          trades: sampleTrades,
        }),
      ],
    ]);

    expect(buildJournalCopyText([sampleEntry], tradeStatsByDate)).toBe(`==================================================
Journal Entry
Date: 2026-03-06
Title: Stayed patient through the chop
Mood: Focused
Tags: discipline, risk, patience

Trade Stats:
Net PnL: +$420.50
Trades: 3
Win Rate: 66.7%
Profit Factor: 1.85
Expectancy: +$140.17
Best Trade: +$305.50
Worst Trade: -$95.00
Symbols: MNQ, MES

Notes:
Today I followed my plan well.
Stayed patient after the open.
==================================================`);
  });

  it("omits empty sections when no notes, tags, or trade stats exist", () => {
    const minimalEntry: JournalEntry = {
      ...sampleEntry,
      title: "Quick review",
      tags: [],
      body: "",
      stats_json: null,
    };

    expect(buildJournalCopyText([minimalEntry])).toBe(`==================================================
Journal Entry
Date: 2026-03-06
Title: Quick review
Mood: Focused
==================================================`);
  });
});
