// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, RouterProvider, createMemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { accountsApi } from "../../lib/api";
import type {
  AccountInfo,
  AccountPnlCalendarDay,
  AccountSummary,
  AccountTrade,
  JournalEntry,
  TradeImportConfirmResult,
  TradeImportPreview,
  TradeImportPreviewTrade,
} from "../../lib/types";
import { JournalPage } from "../journal/JournalPage";
import { TradesPage } from "../trades/TradesPage";
import { DashboardPage } from "./DashboardPage";

const LIVE_ACCOUNT_ID = 88_001;
const TRADE_DAY = "2026-07-24";

const liveAccount: AccountInfo = {
  id: LIVE_ACCOUNT_ID,
  name: "Live Funded 88-001",
  provider_name: "Live Funded 88-001",
  custom_display_name: null,
  trade_data_source: "csv_import",
  balance: null,
  provider_data_stale: false,
  last_seen_at: null,
  status: "ACTIVE",
  account_state: "ACTIVE",
  is_main: true,
  is_archived: false,
  can_trade: null,
  is_visible: true,
  last_trade_at: null,
};

const emptySummary: AccountSummary = {
  realized_pnl: 0,
  gross_pnl: 0,
  fees: 0,
  net_pnl: 0,
  win_rate: 0,
  win_count: 0,
  loss_count: 0,
  breakeven_count: 0,
  profit_factor: 0,
  avg_win: 0,
  avg_loss: 0,
  avg_win_duration_minutes: 0,
  avg_loss_duration_minutes: 0,
  expectancy_per_trade: 0,
  tail_risk_5pct: 0,
  max_drawdown: 0,
  average_drawdown: 0,
  risk_drawdown_score: 0,
  max_drawdown_length_hours: 0,
  recovery_time_hours: 0,
  average_recovery_length_hours: 0,
  trade_count: 0,
  half_turn_count: 0,
  execution_count: 0,
  day_win_rate: 0,
  green_days: 0,
  red_days: 0,
  flat_days: 0,
  avg_trades_per_day: 0,
  active_days: 0,
  efficiency_per_hour: 0,
  profit_per_day: 0,
  averagePositionSize: 0,
  medianPositionSize: 0,
  tradeCountUsedForSizingStats: 0,
  avgPointGain: null,
  avgPointLoss: null,
  pointsBasisUsed: "auto",
  sizingBenchmark: {
    benchmarkMode: "fixed_average_size",
    benchmarkSizeUsed: 0,
    benchmarkGrossPnl: 0,
    benchmarkNetPnl: 0,
    benchmarkDiff: 0,
    benchmarkRatio: null,
    benchmarkLabel: "In Line With Benchmark",
  },
};

const importedTrades: AccountTrade[] = [
  {
    id: 101,
    account_id: LIVE_ACCOUNT_ID,
    contract_id: "CON.F.US.MNQ.U26",
    symbol: "MNQ",
    side: "SHORT",
    size: 2,
    price: 30_100.25,
    timestamp: "2026-07-24T14:35:45Z",
    entry_time: "2026-07-24T14:34:00Z",
    exit_time: "2026-07-24T14:35:45Z",
    duration_minutes: 1.75,
    entry_price: 30_150.25,
    exit_price: 30_100.25,
    fees: 2,
    non_commission_fees: 1,
    commissions: 1,
    pnl: 98,
    order_id: "ORDER-FLOW-1001",
    source_trade_id: "FLOW-1001",
  },
  {
    id: 102,
    account_id: LIVE_ACCOUNT_ID,
    contract_id: "CON.F.US.MNQ.U26",
    symbol: "MNQ",
    side: "LONG",
    size: 1,
    price: 30_120.5,
    timestamp: "2026-07-24T15:08:30Z",
    entry_time: "2026-07-24T15:04:00Z",
    exit_time: "2026-07-24T15:08:30Z",
    duration_minutes: 4.5,
    entry_price: 30_100.5,
    exit_price: 30_120.5,
    fees: 1,
    non_commission_fees: 0.5,
    commissions: 0.5,
    pnl: 39,
    order_id: "ORDER-FLOW-1002",
    source_trade_id: "FLOW-1002",
  },
  {
    id: 103,
    account_id: LIVE_ACCOUNT_ID,
    contract_id: "CON.F.US.MNQ.U26",
    symbol: "MNQ",
    side: "LONG",
    size: 1,
    price: 30_080,
    timestamp: "2026-07-24T16:02:00Z",
    entry_time: "2026-07-24T15:58:00Z",
    exit_time: "2026-07-24T16:02:00Z",
    duration_minutes: 4,
    entry_price: 30_100,
    exit_price: 30_080,
    fees: 1,
    non_commission_fees: 0.5,
    commissions: 0.5,
    pnl: -41,
    order_id: "ORDER-FLOW-1003",
    source_trade_id: "FLOW-1003",
  },
];

interface LocalReadState {
  trades: AccountTrade[];
  summary: AccountSummary;
  calendar: AccountPnlCalendarDay[];
  journal: JournalEntry[];
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve;
  });
  return { promise, resolve };
}

function summaryFor(trades: readonly AccountTrade[]): AccountSummary {
  const netPnl = trades.reduce((total, trade) => total + (trade.pnl ?? 0), 0);
  const fees = trades.reduce((total, trade) => total + trade.fees, 0);
  const wins = trades.filter((trade) => (trade.pnl ?? 0) > 0);
  const losses = trades.filter((trade) => (trade.pnl ?? 0) < 0);
  const sizes = trades.map((trade) => Math.abs(trade.size));
  return {
    ...emptySummary,
    realized_pnl: netPnl,
    gross_pnl: netPnl + fees,
    fees,
    net_pnl: netPnl,
    win_rate: trades.length === 0 ? 0 : (wins.length / trades.length) * 100,
    win_count: wins.length,
    loss_count: losses.length,
    profit_factor:
      losses.length === 0
        ? wins.reduce((total, trade) => total + (trade.pnl ?? 0), 0)
        : wins.reduce((total, trade) => total + (trade.pnl ?? 0), 0) /
          Math.abs(losses.reduce((total, trade) => total + (trade.pnl ?? 0), 0)),
    avg_win: wins.length === 0 ? 0 : wins.reduce((total, trade) => total + (trade.pnl ?? 0), 0) / wins.length,
    avg_loss:
      losses.length === 0 ? 0 : losses.reduce((total, trade) => total + (trade.pnl ?? 0), 0) / losses.length,
    expectancy_per_trade: trades.length === 0 ? 0 : netPnl / trades.length,
    trade_count: trades.length,
    half_turn_count: trades.reduce((total, trade) => total + Math.abs(trade.size), 0),
    execution_count: trades.length * 2,
    day_win_rate: trades.length === 0 ? 0 : 100,
    green_days: trades.length === 0 ? 0 : 1,
    avg_trades_per_day: trades.length,
    active_days: trades.length === 0 ? 0 : 1,
    profit_per_day: netPnl,
    averagePositionSize: sizes.length === 0 ? 0 : sizes.reduce((total, value) => total + value, 0) / sizes.length,
    medianPositionSize: sizes.length === 0 ? 0 : [...sizes].sort((left, right) => left - right)[Math.floor(sizes.length / 2)],
    tradeCountUsedForSizingStats: trades.length,
  };
}

function refreshDerivedState(state: LocalReadState) {
  state.summary = summaryFor(state.trades);
  state.calendar = state.trades.length
    ? [
        {
          date: TRADE_DAY,
          trade_count: state.trades.length,
          win_count: state.summary.win_count,
          loss_count: state.summary.loss_count,
          breakeven_count: state.summary.breakeven_count,
          gross_pnl: state.summary.gross_pnl,
          non_commission_fees: state.summary.fees - state.trades.reduce((total, trade) => total + (trade.commissions ?? 0), 0),
          commissions: state.trades.reduce((total, trade) => total + (trade.commissions ?? 0), 0),
          fees: state.summary.fees,
          net_pnl: state.summary.net_pnl,
        },
      ]
    : [];
}

function withPulledTradeStats(entry: JournalEntry, state: LocalReadState): JournalEntry {
  return {
    ...entry,
    stats_source: "account_trades",
    stats_json: {
      snapshot_version: 1,
      trade_count: state.trades.length,
      total_pnl: state.summary.net_pnl,
      total_fees: state.summary.fees,
      win_rate: state.summary.win_rate,
      avg_win: state.summary.avg_win,
      avg_loss: state.summary.avg_loss,
      largest_win: Math.max(...state.trades.map((trade) => trade.pnl ?? 0)),
      largest_loss: Math.min(...state.trades.map((trade) => trade.pnl ?? 0)),
      largest_position_size: Math.max(...state.trades.map((trade) => Math.abs(trade.size))),
      gross: state.summary.gross_pnl,
      net: state.summary.net_pnl,
      net_realized_pnl: state.summary.net_pnl,
    },
    stats_pulled_at: "2026-07-24T16:05:00Z",
    updated_at: "2026-07-24T16:05:00Z",
  };
}

function previewTrade(trade: AccountTrade, rowNumber: number, status: TradeImportPreviewTrade["status"]): TradeImportPreviewTrade {
  return {
    row_number: rowNumber,
    source_trade_id: trade.source_trade_id ?? trade.order_id,
    contract_name: trade.contract_id,
    symbol: trade.symbol,
    entered_at: trade.entry_time ?? trade.timestamp,
    exited_at: trade.exit_time ?? trade.timestamp,
    entry_price: trade.entry_price ?? trade.price,
    exit_price: trade.exit_price ?? trade.price,
    fees: trade.non_commission_fees ?? trade.fees,
    commissions: trade.commissions ?? 0,
    gross_pnl: (trade.pnl ?? 0) + trade.fees,
    net_pnl: trade.pnl ?? 0,
    size: trade.size,
    direction: trade.side,
    trade_day: TRADE_DAY,
    duration: trade.duration_minutes == null ? null : `${trade.duration_minutes} minutes`,
    status,
  };
}

function makePreview(
  sourceFileName: string,
  token: string,
  rows: TradeImportPreviewTrade[],
): TradeImportPreview {
  const newRows = rows.filter((row) => row.status === "new");
  return {
    preview_token: token,
    expires_at: "2099-07-25T18:00:00Z",
    source_file_name: sourceFileName,
    file_sha256: `sha-${sourceFileName}`,
    total_rows: rows.length,
    new_rows: newRows.length,
    duplicate_rows: rows.filter((row) => row.status === "duplicate").length,
    conflict_rows: rows.filter((row) => row.status === "conflict").length,
    summary: {
      gross_pnl: newRows.reduce((total, row) => total + row.gross_pnl, 0),
      fees: newRows.reduce((total, row) => total + row.fees, 0),
      commissions: newRows.reduce((total, row) => total + row.commissions, 0),
      net_pnl: newRows.reduce((total, row) => total + row.net_pnl, 0),
      wins: newRows.filter((row) => row.net_pnl > 0).length,
      losses: newRows.filter((row) => row.net_pnl < 0).length,
      breakeven: newRows.filter((row) => row.net_pnl === 0).length,
    },
    trades: rows,
  };
}

function installStatefulAccountsApi() {
  const state: LocalReadState = {
    trades: [],
    summary: { ...emptySummary },
    calendar: [],
    journal: [],
  };
  const tokenRows = new Map<string, AccountTrade[]>();
  const summaryReadTradeCounts: number[] = [];
  let previewSequence = 0;

  vi.spyOn(accountsApi, "getSelectableAccountsLocalFirst").mockResolvedValue([liveAccount]);
  const getSummaryWithPointBases = vi.spyOn(accountsApi, "getSummaryWithPointBases").mockImplementation(async () => {
    summaryReadTradeCounts.push(state.summary.trade_count);
    return {
      summary: state.summary,
      point_payoff_by_basis: {
        MNQ: { avgPointGain: null, avgPointLoss: null },
        MES: { avgPointGain: null, avgPointLoss: null },
        NQ: { avgPointGain: null, avgPointLoss: null },
        ES: { avgPointGain: null, avgPointLoss: null },
        MGC: { avgPointGain: null, avgPointLoss: null },
        SIL: { avgPointGain: null, avgPointLoss: null },
      },
    };
  });
  vi.spyOn(accountsApi, "getSummary").mockImplementation(async () => state.summary);
  const getTrades = vi.spyOn(accountsApi, "getTrades").mockImplementation(async () => [...state.trades]);
  const getPnlCalendar = vi.spyOn(accountsApi, "getPnlCalendar").mockImplementation(async () => [...state.calendar]);
  vi.spyOn(accountsApi, "getJournalDays").mockImplementation(async () => ({
    days: state.journal.map((entry) => entry.entry_date),
  }));
  vi.spyOn(accountsApi, "getJournalEntries").mockImplementation(async () => ({
    items: [...state.journal],
    total: state.journal.length,
  }));
  vi.spyOn(accountsApi, "listJournalImages").mockResolvedValue([]);
  const createJournalEntry = vi.spyOn(accountsApi, "createJournalEntry").mockImplementation(async (_accountId, input) => {
    const existing = state.journal.find((entry) => entry.entry_date === input.entry_date);
    if (existing) {
      return { ...existing, already_existed: true };
    }
    const created: JournalEntry = {
      id: 501,
      account_id: LIVE_ACCOUNT_ID,
      entry_date: input.entry_date,
      title: input.title,
      mood: input.mood,
      tags: input.tags,
      body: input.body,
      version: 1,
      stats_source: null,
      stats_json: null,
      stats_pulled_at: null,
      is_archived: false,
      created_at: "2026-07-24T16:05:00Z",
      updated_at: "2026-07-24T16:05:00Z",
    };
    state.journal = [created];
    return { ...created, already_existed: false };
  });
  const pullJournalTradeStats = vi.spyOn(accountsApi, "pullJournalTradeStats").mockImplementation(async (_accountId, entryId) => {
    const existing = state.journal.find((entry) => entry.id === entryId);
    if (!existing) {
      throw new Error("Journal entry not found");
    }
    const updated = withPulledTradeStats(existing, state);
    state.journal = state.journal.map((entry) => (entry.id === entryId ? updated : entry));
    return updated;
  });

  const previewTradeImport = vi.spyOn(accountsApi, "previewTradeImport").mockImplementation(async (_accountId, file) => {
    previewSequence += 1;
    const token = `${file.name}-${previewSequence}`;
    const storedIds = new Set(state.trades.map((trade) => trade.source_trade_id));
    let sourceRows: AccountTrade[];
    if (file.name === "overlap.csv") {
      sourceRows = [importedTrades[1], importedTrades[2]];
    } else if (file.name === "conflict.csv") {
      const conflictRow = previewTrade(importedTrades[0], 2, "conflict");
      conflictRow.net_pnl += 500;
      conflictRow.conflict = {
        identity_kind: "source_trade_id",
        identity_value: "FLOW-1001",
        reason: "stored_trade_mismatch",
        stored_event_id: importedTrades[0].id,
        differences: [{ field: "net_pnl", stored: importedTrades[0].pnl, incoming: conflictRow.net_pnl }],
      };
      return makePreview(file.name, token, [conflictRow]);
    } else {
      sourceRows = importedTrades.slice(0, 2);
    }

    const rows = sourceRows.map((trade, index) =>
      previewTrade(trade, index + 2, storedIds.has(trade.source_trade_id) ? "duplicate" : "new"),
    );
    tokenRows.set(
      token,
      sourceRows.filter((trade) => !storedIds.has(trade.source_trade_id)),
    );
    return makePreview(file.name, token, rows);
  });

  const confirmTradeImport = vi.spyOn(accountsApi, "confirmTradeImport").mockImplementation(async (_accountId, token) => {
    const newRows = tokenRows.get(token);
    if (!newRows) {
      throw new Error("Unknown or non-confirmable preview token");
    }
    const storedIds = new Set(state.trades.map((trade) => trade.source_trade_id));
    const inserted = newRows.filter((trade) => !storedIds.has(trade.source_trade_id));
    state.trades = [...state.trades, ...inserted];
    refreshDerivedState(state);
    const sourceFileName = token.slice(0, token.lastIndexOf("-"));
    const result: TradeImportConfirmResult = {
      import_id: confirmTradeImport.mock.calls.length,
      source_file_name: sourceFileName,
      imported_at: "2026-07-24T16:05:00Z",
      total_rows: sourceFileName === "overlap.csv" ? 2 : inserted.length,
      inserted_rows: inserted.length,
      duplicate_rows: sourceFileName === "overlap.csv" ? 1 : 0,
    };
    return result;
  });

  return {
    state,
    summaryReadTradeCounts,
    getSummaryWithPointBases,
    getTrades,
    getPnlCalendar,
    createJournalEntry,
    pullJournalTradeStats,
    previewTradeImport,
    confirmTradeImport,
  };
}

function mountDailyFlow() {
  const api = installStatefulAccountsApi();
  const router = createMemoryRouter(
    [
      {
        path: "/",
        element: (
          <Outlet
            context={{
              accounts: [liveAccount],
              accountsLoading: false,
              accountsError: null,
              selectedAccountId: LIVE_ACCOUNT_ID,
            }}
          />
        ),
        children: [
          { path: "dashboard", element: <DashboardPage /> },
          { path: "trades", element: <TradesPage /> },
          { path: "journal", element: <JournalPage /> },
        ],
      },
    ],
    { initialEntries: [`/dashboard?account=${LIVE_ACCOUNT_ID}`] },
  );
  render(<RouterProvider router={router} />);
  return { ...api, router };
}

function importFile(fileName: string) {
  const input = document.querySelector<HTMLInputElement>('input[type="file"]');
  if (!input) {
    throw new Error("Expected the production TradeImportPanel file input");
  }
  const file = new File(["Id,PnL\nFLOW,98"], fileName, { type: "text/csv" });
  fireEvent.change(input, { target: { files: [file] } });
  return file;
}

async function waitForImportReady() {
  await waitFor(() => {
    const input = document.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();
    expect(input?.disabled).toBe(false);
  });
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  window.sessionStorage.clear();
  vi.restoreAllMocks();
});

describe("Live CSV daily-flow production page bridge", () => {
  it("shows a dedicated loading view instead of empty dashboard metrics while accounts resolve", async () => {
    installStatefulAccountsApi();
    const pendingAccounts = deferred<AccountInfo[]>();
    vi.mocked(accountsApi.getSelectableAccountsLocalFirst).mockReturnValueOnce(pendingAccounts.promise);
    const router = createMemoryRouter(
      [{ path: "/dashboard", element: <DashboardPage /> }],
      { initialEntries: [`/dashboard?account=${LIVE_ACCOUNT_ID}`] },
    );

    render(<RouterProvider router={router} />);

    const loadingView = screen.getByRole("status");
    expect(loadingView.getAttribute("aria-busy")).toBe("true");
    expect(screen.getByText("Loading dashboard...")).not.toBeNull();
    expect(document.querySelector('input[type="file"]')).toBeNull();
    expect(screen.queryByText("Performance")).toBeNull();
    expect(screen.queryByText("No trades available.")).toBeNull();

    await act(async () => {
      pendingAccounts.resolve([liveAccount]);
      await pendingAccounts.promise;
    });

    await waitForImportReady();
    expect(screen.queryByText("Loading dashboard...")).toBeNull();
  });

  it("commits through Dashboard, reloads its local reads, and exposes imported trades plus date-scoped Journal stats", async () => {
    const user = userEvent.setup();
    const {
      router,
      state,
      summaryReadTradeCounts,
      getSummaryWithPointBases,
      getTrades,
      getPnlCalendar,
      createJournalEntry,
      pullJournalTradeStats,
    } = mountDailyFlow();

    await waitForImportReady();
    await waitFor(() => expect(summaryReadTradeCounts).toContain(0));
    const initialSummaryReads = getSummaryWithPointBases.mock.calls.length;
    const initialTradeReads = getTrades.mock.calls.length;
    const initialCalendarReads = getPnlCalendar.mock.calls.length;

    importFile("fresh.csv");
    await user.click(await screen.findByRole("button", { name: "Confirm Import (2)" }));

    await screen.findByText(/Imported 2 trades from fresh\.csv/);
    await waitFor(() => expect(summaryReadTradeCounts.at(-1)).toBe(2));
    expect(getSummaryWithPointBases.mock.calls.length).toBeGreaterThan(initialSummaryReads);
    expect(getTrades.mock.calls.length).toBeGreaterThan(initialTradeReads);
    expect(getPnlCalendar.mock.calls.length).toBeGreaterThan(initialCalendarReads);
    expect(state.calendar).toEqual([expect.objectContaining({ date: TRADE_DAY, trade_count: 2, net_pnl: 137 })]);
    expect(await screen.findAllByText("FLOW-1001")).not.toHaveLength(0);

    await act(async () => {
      await router.navigate(`/trades?account=${LIVE_ACCOUNT_ID}`);
    });
    expect(await screen.findAllByText("FLOW-1001")).not.toHaveLength(0);
    expect(screen.getAllByText("MNQ")).not.toHaveLength(0);
    expect(screen.getByText(/Live CSV account: trade metrics use imported Topstep history/)).not.toBeNull();

    await act(async () => {
      await router.navigate(`/dashboard?account=${LIVE_ACCOUNT_ID}`);
    });
    const importedDay = await screen.findByRole("button", {
      name: /Jul 24, 2026\..*2 total trades\./,
    });
    await user.click(importedDay);
    await user.click(await screen.findByRole("button", { name: "Add Journal Entry" }));

    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/journal");
      expect(router.state.location.search).toContain(`account=${LIVE_ACCOUNT_ID}`);
      expect(router.state.location.search).toContain(`date=${TRADE_DAY}`);
    });
    expect(createJournalEntry).toHaveBeenCalledWith(LIVE_ACCOUNT_ID, {
      entry_date: TRADE_DAY,
      title: "New Entry",
      mood: "Neutral",
      tags: [],
      body: "",
    });
    expect(await screen.findByDisplayValue("New Entry")).not.toBeNull();
    await waitFor(() => expect(pullJournalTradeStats).toHaveBeenCalledWith(LIVE_ACCOUNT_ID, 501));
    await waitFor(() => expect(screen.getAllByText("2 trades")).not.toHaveLength(0));
  });

  it("keeps duplicates compact, stores only the new overlap row, and blocks a conflicting identity", async () => {
    const user = userEvent.setup();
    const { state, confirmTradeImport } = mountDailyFlow();

    await waitForImportReady();
    importFile("fresh.csv");
    await user.click(await screen.findByRole("button", { name: "Confirm Import (2)" }));
    await screen.findByText(/Imported 2 trades from fresh\.csv/);
    await user.click(screen.getByRole("button", { name: "Close" }));

    importFile("fresh.csv");
    await screen.findByText("Duplicate file");
    expect(screen.queryByText("Review parsed trades")).toBeNull();
    expect(screen.queryByRole("button", { name: /Confirm Import/ })).toBeNull();
    expect(confirmTradeImport).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Close" }));

    importFile("overlap.csv");
    expect(await screen.findByText("1 duplicate")).not.toBeNull();
    await user.click(screen.getByRole("button", { name: "Confirm Import (1)" }));
    await screen.findByText(/Imported 1 trade from overlap\.csv; skipped 1 duplicate/);
    expect(confirmTradeImport).toHaveBeenCalledTimes(2);
    expect(state.trades.map((trade) => trade.source_trade_id)).toEqual(["FLOW-1001", "FLOW-1002", "FLOW-1003"]);
    expect(state.summary.trade_count).toBe(3);
    await user.click(screen.getByRole("button", { name: "Close" }));

    importFile("conflict.csv");
    await screen.findByText("Confirmation is blocked while identity conflicts remain.");
    expect(screen.queryByRole("button", { name: /Confirm Import/ })).toBeNull();
    expect(confirmTradeImport).toHaveBeenCalledTimes(2);
    expect(state.trades).toHaveLength(3);
  });
});
