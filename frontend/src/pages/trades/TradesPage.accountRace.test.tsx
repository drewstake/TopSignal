// @vitest-environment jsdom

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, RouterProvider, createMemoryRouter, useSearchParams } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { accountsApi } from "../../lib/api";
import { ACCOUNT_QUERY_PARAM, parseAccountId } from "../../lib/accountSelection";
import type { AccountInfo, AccountSummary } from "../../lib/types";
import { TradesPage } from "./TradesPage";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve;
  });
  return { promise, resolve };
}

function account(id: number, source: AccountInfo["trade_data_source"], isMain: boolean): AccountInfo {
  return {
    id,
    name: source === "csv_import" ? "Live Funded" : "Express Funded",
    provider_name: source === "csv_import" ? "Live Funded" : "Express Funded",
    custom_display_name: null,
    trade_data_source: source,
    balance: 50_000,
    provider_data_stale: false,
    last_seen_at: null,
    status: "ACTIVE",
    account_state: "ACTIVE",
    is_main: isMain,
    is_archived: false,
    can_trade: true,
    is_visible: true,
    last_trade_at: null,
  };
}

function TestAppShellOutlet({
  accounts,
  accountsLoading = false,
  accountsError = null,
}: {
  accounts: AccountInfo[];
  accountsLoading?: boolean;
  accountsError?: string | null;
}) {
  const [searchParams] = useSearchParams();
  const queryAccountId = parseAccountId(searchParams.get(ACCOUNT_QUERY_PARAM));
  const selectedAccountId =
    accounts.find((candidate) => candidate.id === queryAccountId)?.id ??
    accounts.find((candidate) => candidate.is_main)?.id ??
    accounts[0]?.id ??
    null;
  return (
    <Outlet
      context={{
        accounts,
        accountsLoading,
        accountsError,
        selectedAccountId,
      }}
    />
  );
}

function createTradesRouter({
  accounts,
  accountsLoading = false,
  accountsError = null,
  initialEntry = "/?account=7101",
}: {
  accounts: AccountInfo[];
  accountsLoading?: boolean;
  accountsError?: string | null;
  initialEntry?: string;
}) {
  return createMemoryRouter(
    [
      {
        path: "/",
        element: (
          <TestAppShellOutlet
            accounts={accounts}
            accountsLoading={accountsLoading}
            accountsError={accountsError}
          />
        ),
        children: [{ index: true, element: <TradesPage /> }],
      },
    ],
    { initialEntries: [initialEntry] },
  );
}

const summary: AccountSummary = {
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

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("TradesPage account-switch races", () => {
  it("keeps account and first trade-data loading behind a skeleton", async () => {
    const express = account(7101, "projectx", true);
    const pendingSummary = deferred<AccountSummary>();
    const pendingTrades = deferred<[]>();
    vi.spyOn(accountsApi, "getSummary").mockReturnValue(pendingSummary.promise);
    vi.spyOn(accountsApi, "getTrades").mockReturnValue(pendingTrades.promise);
    const getAccounts = vi.spyOn(accountsApi, "getSelectableAccountsLocalFirst");
    const router = createTradesRouter({ accounts: [express] });

    render(<RouterProvider router={router} />);

    expect(screen.getByRole("status").textContent).toContain("Loading the active account and trade history.");
    expect(screen.queryByText("No account")).toBeNull();
    expect(screen.queryByText("No trades match your filters.")).toBeNull();
    expect(getAccounts).not.toHaveBeenCalled();

    await act(async () => {
      pendingSummary.resolve(summary);
      pendingTrades.resolve([]);
      await Promise.all([pendingSummary.promise, pendingTrades.promise]);
    });

    expect(await screen.findByText("No trades match your filters.")).not.toBeNull();
    expect(screen.queryByText("Loading the active account and trade history.")).toBeNull();
  });

  it("distinguishes shell loading, account errors, and a genuine no-account state", () => {
    const loadingRouter = createTradesRouter({ accounts: [], accountsLoading: true, initialEntry: "/" });
    const loadingRender = render(<RouterProvider router={loadingRouter} />);
    expect(screen.getByRole("status").textContent).toContain("Loading the active account and trade history.");
    expect(screen.queryByText("No active account selected.")).toBeNull();
    loadingRender.unmount();

    const errorRouter = createTradesRouter({ accounts: [], accountsError: "Saved accounts unavailable", initialEntry: "/" });
    const errorRender = render(<RouterProvider router={errorRouter} />);
    expect(screen.getByRole("alert").textContent).toContain("Saved accounts unavailable");
    errorRender.unmount();

    const emptyRouter = createTradesRouter({ accounts: [], initialEntry: "/" });
    render(<RouterProvider router={emptyRouter} />);
    expect(screen.getByText("No active account selected.")).not.toBeNull();
  });

  it("requires a fresh data scope when switching Express to Live and back to Express", async () => {
    const express = account(7101, "projectx", true);
    const live = account(88001, "csv_import", false);
    const liveSummary = deferred<AccountSummary>();
    const liveTrades = deferred<[]>();
    const returningExpressSummary = deferred<AccountSummary>();
    const returningExpressTrades = deferred<[]>();
    let expressSummaryReads = 0;
    let expressTradeReads = 0;

    const getSummary = vi.spyOn(accountsApi, "getSummary").mockImplementation((accountId) => {
      if (accountId === express.id) {
        expressSummaryReads += 1;
        return expressSummaryReads === 1 ? Promise.resolve(summary) : returningExpressSummary.promise;
      }
      return liveSummary.promise;
    });
    const getTrades = vi.spyOn(accountsApi, "getTrades").mockImplementation((accountId) => {
      if (accountId === express.id) {
        expressTradeReads += 1;
        return expressTradeReads === 1 ? Promise.resolve([]) : returningExpressTrades.promise;
      }
      return liveTrades.promise;
    });
    const router = createTradesRouter({ accounts: [express, live] });

    render(<RouterProvider router={router} />);
    expect(await screen.findByRole("button", { name: "Sync Latest" })).not.toBeNull();

    await act(async () => {
      await router.navigate("/?account=88001");
    });
    await waitFor(() => {
      expect(getSummary).toHaveBeenCalledWith(live.id, expect.any(Object));
      expect(getTrades).toHaveBeenCalledWith(live.id, expect.any(Object));
    });

    await act(async () => {
      await router.navigate("/?account=7101");
    });
    await waitFor(() => {
      expect(expressSummaryReads).toBe(2);
      expect(expressTradeReads).toBe(2);
    });

    expect(screen.getByRole("status").textContent).toContain("Loading the active account and trade history.");
    expect(screen.queryByText("No trades match your filters.")).toBeNull();

    await act(async () => {
      returningExpressSummary.resolve(summary);
      returningExpressTrades.resolve([]);
      await Promise.all([returningExpressSummary.promise, returningExpressTrades.promise]);
    });

    expect(await screen.findByText("No trades match your filters.")).not.toBeNull();
  });

  it("does not run the post-sync reload or repaint after switching Express to Live", async () => {
    const user = userEvent.setup();
    const express = account(7101, "projectx", true);
    const live = account(88001, "csv_import", false);
    const expressSync = deferred<{ fetched_count: number; inserted_count: number }>();
    const getSummary = vi.spyOn(accountsApi, "getSummary").mockResolvedValue(summary);
    const getTrades = vi.spyOn(accountsApi, "getTrades").mockResolvedValue([]);
    vi.spyOn(accountsApi, "refreshTrades").mockReturnValue(expressSync.promise);
    const router = createTradesRouter({ accounts: [express, live] });

    render(<RouterProvider router={router} />);
    const syncButton = await screen.findByRole("button", { name: "Sync Latest" });
    await waitFor(() => expect(getSummary).toHaveBeenCalledWith(7101, expect.any(Object)));
    await user.click(syncButton);

    await router.navigate("/?account=88001");
    expect(await screen.findByRole("button", { name: "Import-only Account" })).not.toBeNull();
    await waitFor(() => {
      expect(getSummary).toHaveBeenCalledWith(88001, expect.any(Object));
      expect(getTrades).toHaveBeenCalledWith(88001, expect.any(Object));
    });

    expressSync.resolve({ fetched_count: 4, inserted_count: 2 });
    await waitFor(() => expect(screen.queryByText(/Fetched 4, stored 2/)).toBeNull());
    expect(getSummary.mock.calls.map(([accountId]) => accountId)).toEqual([7101, 88001]);
    expect(getTrades.mock.calls.map(([accountId]) => accountId)).toEqual([7101, 88001]);
    expect(screen.getByText("Live Funded")).not.toBeNull();
  });
});
