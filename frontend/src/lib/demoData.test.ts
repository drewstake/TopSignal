import { describe, expect, it } from "vitest";

import { getDemoApiResponse } from "./demoData";
import {
  DEMO_AS_OF_DATE,
  DEMO_AS_OF_ISO,
  DEMO_AS_OF_LABEL,
  DEMO_SCENARIO_VERSION,
} from "./demoScenario";
import type {
  AccountInfo,
  AccountLastTradeInfo,
  AccountPnlCalendarDay,
  AccountSummary,
  AccountSummaryWithPointBases,
  AccountTrade,
  BehaviorMetrics,
  BotActivity,
  BotConfigListResponse,
  ExpenseListResponse,
  ExpenseTotals,
  FinancialSummary,
  JournalEntriesResponse,
  PayoutListResponse,
  ProjectXMarketCandle,
  StreakMetrics,
  SummaryMetrics,
  SymbolPnlPoint,
  TradeRecord,
} from "./types";

const PRIMARY_ACCOUNT_ID = 910001;
const FOLLOWER_ACCOUNT_ID = 910002;
const SWING_ACCOUNT_ID = 910003;
const PRACTICE_ACCOUNT_ID = 910004;
const ARCHIVED_ACCOUNT_ID = 910005;
const MISSING_ACCOUNT_ID = 910099;

function demo<T>(path: string, query?: Record<string, string | number | boolean | null | undefined>) {
  const response = getDemoApiResponse<T>(path, query);
  expect(response, `Expected Demo Mode to handle ${path}`).not.toBeNull();
  return response!.data;
}

function round(value: number) {
  return Math.round((value + Number.EPSILON) * 100) / 100;
}

function netPnl(trade: AccountTrade) {
  return round((trade.pnl ?? 0) - Math.abs(trade.fees));
}

function sum(values: number[]) {
  return values.reduce((total, value) => total + value, 0);
}

describe("demo scenario provenance and accounts", () => {
  it("publishes one stable, human-readable scenario clock", () => {
    expect(DEMO_AS_OF_DATE).toBe("2026-07-24");
    expect(DEMO_AS_OF_ISO.startsWith(DEMO_AS_OF_DATE)).toBe(true);
    expect(DEMO_AS_OF_LABEL).toBe("Jul 24, 2026");
    expect(DEMO_SCENARIO_VERSION).toContain("2026.07.24");
  });

  it("uses meaningful names and represents active, locked, missing, and archived states honestly", () => {
    const selectable = demo<AccountInfo[]>("/api/accounts", {
      show_inactive: false,
      show_missing: false,
      include_archived: false,
    });
    const managed = demo<AccountInfo[]>("/api/accounts", {
      show_inactive: true,
      show_missing: true,
      include_archived: true,
    });

    expect(selectable.map((account) => account.name)).toEqual([
      "Demo · 50K Main",
      "Demo · Copy Follower",
      "Demo · Practice Lab",
    ]);
    expect(managed).toHaveLength(6);
    expect(managed.find((account) => account.id === SWING_ACCOUNT_ID)?.account_state).toBe("LOCKED_OUT");
    expect(managed.find((account) => account.id === MISSING_ACCOUNT_ID)?.account_state).toBe("MISSING");
    expect(managed.find((account) => account.id === ARCHIVED_ACCOUNT_ID)?.is_archived).toBe(true);
    expect(managed.every((account) => account.name.startsWith("Demo · "))).toBe(true);
  });

  it("derives balances and last-trade metadata from each account's own ledger", () => {
    const accounts = demo<AccountInfo[]>("/api/accounts", {
      show_inactive: true,
      show_missing: true,
      include_archived: true,
    });
    const openingBalances = new Map([
      [PRIMARY_ACCOUNT_ID, 50_000],
      [FOLLOWER_ACCOUNT_ID, 50_000],
      [SWING_ACCOUNT_ID, 100_000],
      [PRACTICE_ACCOUNT_ID, 50_000],
    ]);

    for (const [accountId, openingBalance] of openingBalances) {
      const trades = demo<AccountTrade[]>(`/api/accounts/${accountId}/trades`, { limit: 10_000 });
      const account = accounts.find((candidate) => candidate.id === accountId);
      const lastTrade = demo<AccountLastTradeInfo>(`/api/accounts/${accountId}/last-trade`);
      const expectedLastTradeAt = trades.map((trade) => trade.timestamp).sort().at(-1);

      expect(account?.balance).toBeCloseTo(openingBalance + sum(trades.map(netPnl)), 2);
      expect(account?.last_trade_at).toBe(expectedLastTradeAt);
      expect(lastTrade.last_trade_at).toBe(expectedLastTradeAt);
      expect(lastTrade.source).toContain(DEMO_SCENARIO_VERSION);
      expect(trades.every((trade) => trade.timestamp <= DEMO_AS_OF_ISO)).toBe(true);
    }
  });

  it("returns real empty behavior for archived, missing, and unknown accounts without primary fallback", () => {
    for (const accountId of [ARCHIVED_ACCOUNT_ID, MISSING_ACCOUNT_ID, 999999]) {
      const trades = demo<AccountTrade[]>(`/api/accounts/${accountId}/trades`, { limit: 100 });
      const summary = demo<AccountSummary>(`/api/accounts/${accountId}/summary`);
      const calendar = demo<AccountPnlCalendarDay[]>(`/api/accounts/${accountId}/pnl-calendar`);
      const lastTrade = demo<AccountLastTradeInfo>(`/api/accounts/${accountId}/last-trade`);

      expect(trades).toEqual([]);
      expect(calendar).toEqual([]);
      expect(summary.trade_count).toBe(0);
      expect(summary.net_pnl).toBe(0);
      expect(lastTrade.account_id).toBe(accountId);
      expect(lastTrade.last_trade_at).toBeNull();
    }
  });
});

describe("demo trading ledger", () => {
  it("uses production gross-before-fees semantics and reconciles summary and calendar totals", () => {
    for (const accountId of [PRIMARY_ACCOUNT_ID, FOLLOWER_ACCOUNT_ID, SWING_ACCOUNT_ID, PRACTICE_ACCOUNT_ID]) {
      const trades = demo<AccountTrade[]>(`/api/accounts/${accountId}/trades`, { limit: 10_000 });
      const summary = demo<AccountSummary>(`/api/accounts/${accountId}/summary`);
      const calendar = demo<AccountPnlCalendarDay[]>(`/api/accounts/${accountId}/pnl-calendar`);
      const gross = round(sum(trades.map((trade) => trade.pnl ?? 0)));
      const fees = round(sum(trades.map((trade) => Math.abs(trade.fees))));
      const net = round(sum(trades.map(netPnl)));

      expect(summary.realized_pnl).toBeCloseTo(gross, 2);
      expect(summary.gross_pnl).toBeCloseTo(gross, 2);
      expect(summary.fees).toBeCloseTo(fees, 2);
      expect(summary.net_pnl).toBeCloseTo(net, 2);
      expect(round(summary.gross_pnl - summary.fees)).toBeCloseTo(summary.net_pnl, 2);
      expect(sum(calendar.map((day) => day.trade_count))).toBe(trades.length);
      expect(round(sum(calendar.map((day) => day.gross_pnl)))).toBeCloseTo(gross, 2);
      expect(round(sum(calendar.map((day) => day.fees)))).toBeCloseTo(fees, 2);
      expect(round(sum(calendar.map((day) => day.net_pnl)))).toBeCloseTo(net, 2);
      expect(summary.execution_count).toBe(trades.length);
      expect(summary.half_turn_count).toBe(new Set(trades.map((trade) => trade.order_id)).size);
      expect(trades.every((trade) => (trade.mfe ?? 0) >= 0 && (trade.mae ?? 0) <= 0)).toBe(true);
    }
  });

  it("keeps the main, follower, swing, and practice stories behaviorally distinct", () => {
    const primary = demo<AccountTrade[]>(`/api/accounts/${PRIMARY_ACCOUNT_ID}/trades`, { limit: 10_000 });
    const follower = demo<AccountTrade[]>(`/api/accounts/${FOLLOWER_ACCOUNT_ID}/trades`, { limit: 10_000 });
    const swing = demo<AccountTrade[]>(`/api/accounts/${SWING_ACCOUNT_ID}/trades`, { limit: 10_000 });
    const practice = demo<AccountTrade[]>(`/api/accounts/${PRACTICE_ACCOUNT_ID}/trades`, { limit: 10_000 });

    expect(primary.length).toBeGreaterThan(follower.length);
    expect(Math.max(...primary.map((trade) => trade.duration_minutes ?? 0))).toBeLessThan(60);
    expect(Math.min(...swing.map((trade) => trade.duration_minutes ?? 0))).toBeGreaterThan(1_000);
    expect(new Set(swing.map((trade) => trade.symbol))).toEqual(new Set(["MES", "MGC"]));
    expect(Math.max(...follower.map((trade) => trade.size))).toBeLessThan(Math.max(...primary.map((trade) => trade.size)));
    expect(new Set(practice.map((trade) => trade.symbol))).toEqual(new Set(["MES", "MGC", "MNQ"]));
    expect(Math.max(...practice.map((trade) => trade.size))).toBe(2);
    expect(follower[0].source_trade_id).not.toBe(primary[0].source_trade_id);
    expect(follower.some((trade) => !primary.some((candidate) => candidate.entry_price === trade.entry_price))).toBe(true);
  });

  it("computes point payoff, median sizing, tail risk, and MFE on the correct basis", () => {
    const trades = demo<AccountTrade[]>(`/api/accounts/${PRIMARY_ACCOUNT_ID}/trades`, { limit: 10_000 });
    const response = demo<AccountSummaryWithPointBases>(
      `/api/accounts/${PRIMARY_ACCOUNT_ID}/summary-with-point-bases`,
      { points_basis: "MNQ" },
    );
    const mnq = trades.filter((trade) => trade.symbol === "MNQ");
    const winningPoints = mnq.map((trade) => netPnl(trade) / (trade.size * 2)).filter((value) => value > 0);
    const losingPoints = mnq.map((trade) => netPnl(trade) / (trade.size * 2)).filter((value) => value < 0).map(Math.abs);
    const sizes = trades.map((trade) => trade.size).sort((left, right) => left - right);
    const expectedMedian = (sizes[sizes.length / 2 - 1] + sizes[sizes.length / 2]) / 2;
    const sortedNet = trades.map(netPnl).sort((left, right) => left - right);
    const worstCount = Math.max(1, Math.ceil(sortedNet.length * 0.05));

    expect(response.summary.pointsBasisUsed).toBe("MNQ");
    expect(response.point_payoff_by_basis.MNQ.avgPointGain).toBeCloseTo(sum(winningPoints) / winningPoints.length, 4);
    expect(response.point_payoff_by_basis.MNQ.avgPointLoss).toBeCloseTo(sum(losingPoints) / losingPoints.length, 4);
    expect(response.point_payoff_by_basis.NQ).toEqual({ avgPointGain: null, avgPointLoss: null });
    expect(response.summary.medianPositionSize).toBe(expectedMedian);
    expect(response.summary.tail_risk_5pct).toBeCloseTo(sum(sortedNet.slice(0, worstCount)) / worstCount, 2);
    expect(trades.some((trade) => (trade.mfe ?? 0) > Math.abs((trade.exit_price ?? 0) - (trade.entry_price ?? 0)))).toBe(true);
  });
});

describe("demo journals and financial story", () => {
  it("derives every journal snapshot from that account's trades on the journal day", () => {
    const allBodies = new Set<string>();
    const allMoods = new Set<string>();
    for (const accountId of [PRIMARY_ACCOUNT_ID, FOLLOWER_ACCOUNT_ID, SWING_ACCOUNT_ID, PRACTICE_ACCOUNT_ID]) {
      const entries = demo<JournalEntriesResponse>(`/api/accounts/${accountId}/journal`, { limit: 100 }).items;
      const trades = demo<AccountTrade[]>(`/api/accounts/${accountId}/trades`, { limit: 10_000 });
      expect(entries.length).toBeGreaterThan(1);

      for (const entry of entries) {
        const dayTrades = trades.filter((trade) => trade.timestamp.slice(0, 10) === entry.entry_date);
        const stats = entry.stats_json;
        expect(stats).not.toBeNull();
        expect(stats?.trade_count).toBe(dayTrades.length);
        expect(stats?.gross).toBeCloseTo(sum(dayTrades.map((trade) => trade.pnl ?? 0)), 2);
        expect(stats?.total_fees).toBeCloseTo(sum(dayTrades.map((trade) => trade.fees)), 2);
        expect(stats?.net).toBeCloseTo(sum(dayTrades.map(netPnl)), 2);
        expect(stats?.net_realized_pnl).toBe(stats?.net);
        allBodies.add(entry.body);
        allMoods.add(entry.mood);
      }
    }
    expect(allBodies.size).toBe(12);
    expect(allMoods).toEqual(new Set(["Confident", "Focused", "Frustrated", "Neutral"]));
    expect(demo<JournalEntriesResponse>(`/api/accounts/${MISSING_ACCOUNT_ID}/journal`).items).toEqual([]);
  });

  it("reconciles expense lists, semantic ranges, payouts, and the financial summary", () => {
    const expenses = demo<ExpenseListResponse>("/api/expenses", { limit: 100 }).items;
    const payouts = demo<PayoutListResponse>("/api/payouts", { limit: 100 }).items;
    const totals = demo<ExpenseTotals>("/api/expenses/totals", { range: "all_time" });
    const week = demo<ExpenseTotals>("/api/expenses/totals", { range: "week" });
    const financial = demo<FinancialSummary>("/api/expenses/financial-summary", { as_of_date: DEMO_AS_OF_DATE });
    const mainFinancial = demo<FinancialSummary>("/api/expenses/financial-summary", {
      as_of_date: DEMO_AS_OF_DATE,
      account_id: PRIMARY_ACCOUNT_ID,
    });
    const mainExpenses = expenses.filter((expense) => expense.account_id === PRIMARY_ACCOUNT_ID);

    expect(totals.total_amount_cents).toBe(sum(expenses.map((expense) => expense.amount_cents)));
    expect(sum(Object.values(totals.by_category).map((category) => category.amount_cents))).toBe(totals.total_amount_cents);
    expect(week.start_date).toBe("2026-07-20");
    expect(week.total_amount_cents).toBe(2_500);
    expect(financial.expense_totals.total_amount_cents).toBe(totals.total_amount_cents);
    expect(financial.payout_totals.total_amount_cents).toBe(sum(payouts.map((payout) => payout.amount_cents)));
    expect(financial.first_cash_flow_date).toBe("2026-06-26");
    expect(financial.spend_since_last_payout.last_payout_date).toBe("2026-07-17");
    expect(financial.spend_since_last_payout.total_amount_cents).toBe(2_500);
    expect(financial.ranges.find((range) => range.key === "all_time")?.expense_totals.total_amount_cents)
      .toBe(totals.total_amount_cents);
    expect(mainFinancial.expense_totals.total_amount_cents).toBe(sum(mainExpenses.map((expense) => expense.amount_cents)));
    expect(mainFinancial.payout_totals.total_amount_cents).toBe(financial.payout_totals.total_amount_cents);
    expect(payouts.every((payout) => ![0, 6].includes(new Date(`${payout.payout_date}T12:00:00Z`).getUTCDay()))).toBe(true);
  });
});

describe("demo bot and market data", () => {
  it("scopes bot configuration and its complete dry-run lifecycle to the selected account", () => {
    const primary = demo<BotConfigListResponse>("/api/bots", { account_id: PRIMARY_ACCOUNT_ID });
    const follower = demo<BotConfigListResponse>("/api/bots", { account_id: FOLLOWER_ACCOUNT_ID });
    const missing = demo<BotConfigListResponse>("/api/bots", { account_id: MISSING_ACCOUNT_ID });
    expect(primary.total).toBe(1);
    expect(follower.total).toBe(1);
    expect(primary.items[0].id).not.toBe(follower.items[0].id);
    expect(missing).toEqual({ items: [], total: 0 });

    const activity = demo<BotActivity>(`/api/bots/${follower.items[0].id}/activity`);
    expect(activity.config.account_id).toBe(FOLLOWER_ACCOUNT_ID);
    expect(activity.runs.every((row) => row.account_id === FOLLOWER_ACCOUNT_ID)).toBe(true);
    expect(activity.decisions.every((row) => row.account_id === FOLLOWER_ACCOUNT_ID)).toBe(true);
    expect(activity.order_attempts.every((row) => row.account_id === FOLLOWER_ACCOUNT_ID)).toBe(true);
    expect(activity.risk_events.every((row) => row.account_id === FOLLOWER_ACCOUNT_ID)).toBe(true);
    expect(activity.order_attempts.map((row) => row.side)).toEqual(["SELL", "BUY"]);
    expect(activity.order_attempts.every((row) => row.status === "dry_run" && row.provider_order_id === null)).toBe(true);
    expect(activity.runs[0].started_at).toBe("2026-07-24T13:30:00.000Z");
    expect(activity.runs[0].stopped_at).toBe("2026-07-24T19:45:00.000Z");
    expect(activity.config.trading_start_time).toBe("09:30");
    expect(activity.config.trading_end_time).toBe("15:45");
  });

  it("honors candle symbol, contract, range, timeframe, limit, freshness, and partial-bar parameters", () => {
    const baseQuery = {
      contract_id: "CON.F.US.MNQ.U26",
      symbol: "MNQ",
      start: "2026-07-24T13:30:00.000Z",
      end: "2026-07-24T15:00:00.000Z",
      unit: "minute",
      unit_number: 5,
      limit: 12,
    };
    const complete = demo<ProjectXMarketCandle[]>("/api/projectx/candles", {
      ...baseQuery,
      include_partial_bar: false,
    });
    const withPartial = demo<ProjectXMarketCandle[]>("/api/projectx/candles", {
      ...baseQuery,
      include_partial_bar: true,
    });

    expect(complete).toHaveLength(12);
    expect(complete.every((candle) => candle.symbol === "MNQ" && candle.contract_id === baseQuery.contract_id)).toBe(true);
    expect(complete.every((candle) => candle.timestamp >= baseQuery.start && candle.timestamp <= baseQuery.end)).toBe(true);
    expect(complete.every((candle) => !candle.is_partial)).toBe(true);
    expect(complete.every((candle) => Date.parse(candle.fetched_at ?? "") >= Date.parse(candle.timestamp))).toBe(true);
    expect(complete.slice(1).every((candle, index) => Date.parse(candle.timestamp) - Date.parse(complete[index].timestamp) === 300_000)).toBe(true);
    expect(withPartial.at(-1)?.timestamp).toBe("2026-07-24T15:00:00.000Z");
    expect(withPartial.at(-1)?.is_partial).toBe(true);
  });

  it("aligns month candles to calendar boundaries and never substitutes MNQ for unsupported requests", () => {
    const monthly = demo<ProjectXMarketCandle[]>("/api/projectx/candles", {
      contract_id: "CON.F.US.MES.U26",
      symbol: "MES",
      start: "2026-04-01T00:00:00.000Z",
      end: DEMO_AS_OF_ISO,
      unit: "month",
      unit_number: 1,
      limit: 6,
      include_partial_bar: true,
    });
    const unsupportedSymbol = demo<ProjectXMarketCandle[]>("/api/projectx/candles", { symbol: "CL" });
    const unsupportedContract = demo<ProjectXMarketCandle[]>("/api/projectx/candles", {
      contract_id: "CON.F.US.CL.Q26",
    });
    const mismatched = demo<ProjectXMarketCandle[]>("/api/projectx/candles", {
      contract_id: "CON.F.US.MES.U26",
      symbol: "MNQ",
    });

    expect(monthly.map((candle) => candle.timestamp.slice(0, 10))).toEqual([
      "2026-04-01",
      "2026-05-01",
      "2026-06-01",
      "2026-07-01",
    ]);
    expect(monthly.at(-1)?.is_partial).toBe(true);
    expect(monthly.every((candle) => candle.symbol === "MES" && candle.open < 7_000)).toBe(true);
    expect(unsupportedSymbol).toEqual([]);
    expect(unsupportedContract).toEqual([]);
    expect(mismatched).toEqual([]);
  });

  it.each([
    { unit: "second", unitNumber: 30, start: "2026-07-24T13:59:00.000Z", end: "2026-07-24T14:03:15.000Z" },
    { unit: "minute", unitNumber: 15, start: "2026-07-24T13:00:00.000Z", end: "2026-07-24T16:00:00.000Z" },
    { unit: "hour", unitNumber: 1, start: "2026-07-20T12:00:00.000Z", end: "2026-07-24T20:00:00.000Z" },
    { unit: "day", unitNumber: 1, start: "2026-07-01T00:00:00.000Z", end: DEMO_AS_OF_ISO },
    { unit: "week", unitNumber: 1, start: "2026-04-01T00:00:00.000Z", end: DEMO_AS_OF_ISO },
    { unit: "month", unitNumber: 1, start: "2026-01-01T00:00:00.000Z", end: DEMO_AS_OF_ISO },
  ] as const)("honors $unit candle windows", ({ unit, unitNumber, start, end }) => {
    const candles = demo<ProjectXMarketCandle[]>("/api/projectx/candles", {
      contract_id: "CON.F.US.MGC.Q26",
      symbol: "MGC",
      start,
      end,
      unit,
      unit_number: unitNumber,
      limit: 4,
      include_partial_bar: true,
    });

    expect(candles.length).toBeGreaterThan(0);
    expect(candles.length).toBeLessThanOrEqual(4);
    expect(candles.every((candle) => candle.unit === unit && candle.unit_number === unitNumber)).toBe(true);
    expect(candles.every((candle) => candle.timestamp >= start && candle.timestamp <= end)).toBe(true);
    expect(candles.every((candle) => candle.symbol === "MGC" && candle.contract_id === "CON.F.US.MGC.Q26")).toBe(true);
  });
});

describe("legacy demo metrics", () => {
  it("remains account-scoped and reconciles rule-break, symbol, streak, and summary totals", () => {
    const accountSummary = demo<AccountSummary>(`/api/accounts/${FOLLOWER_ACCOUNT_ID}/summary`);
    const summary = demo<SummaryMetrics>("/metrics/summary", { account_id: FOLLOWER_ACCOUNT_ID });
    const symbols = demo<SymbolPnlPoint[]>("/metrics/pnl-by-symbol", { account_id: FOLLOWER_ACCOUNT_ID });
    const streaks = demo<StreakMetrics>("/metrics/streaks", { account_id: PRIMARY_ACCOUNT_ID });
    const behavior = demo<BehaviorMetrics>("/metrics/behavior", { account_id: PRIMARY_ACCOUNT_ID });
    const trades = demo<TradeRecord[]>("/trades", { account_id: PRIMARY_ACCOUNT_ID });
    const primaryNet = trades.map((trade) => round((trade.pnl ?? 0) - Math.abs(trade.fees ?? 0)));
    const ruleBreakNet = trades
      .filter((trade) => trade.is_rule_break)
      .map((trade) => round((trade.pnl ?? 0) - Math.abs(trade.fees ?? 0)));

    expect(summary.net_pnl).toBe(accountSummary.net_pnl);
    expect(round(sum(symbols.map((row) => row.pnl)))).toBe(accountSummary.net_pnl);
    expect(behavior.rule_break_count).toBe(trades.filter((trade) => trade.is_rule_break).length);
    expect(behavior.rule_break_pnl).toBeCloseTo(sum(ruleBreakNet), 2);
    expect(round(behavior.rule_break_pnl + behavior.rule_following_pnl)).toBeCloseTo(sum(primaryNet), 2);
    expect(streaks.pnl_after_losses.map((bucket) => bucket.loss_streak)).toEqual([1, 2, 3]);
    expect(streaks.longest_loss_streak).toBeGreaterThan(0);

    const unknown = demo<SummaryMetrics>("/metrics/summary", { account_id: 999999 });
    expect(unknown.trade_count).toBe(0);
    expect(unknown.net_pnl).toBe(0);
  });
});
