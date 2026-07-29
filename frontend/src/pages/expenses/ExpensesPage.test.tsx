// @vitest-environment jsdom

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../../lib/api";
import type { AccountInfo, FinancialSummary, PayoutRecord } from "../../lib/types";
import { loadFreshAccountsForExpenseReconciliation } from "./expenseAccountLoading";
import { buildAnniversaryYearRangeOptions, formatLocalIsoDate } from "./expenseNetRanges";
import { ExpensesPage } from "./ExpensesPage";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve;
  });
  return { promise, resolve };
}

function financialSummary(spendAmount: number): FinancialSummary {
  const expenseTotals = {
    range: "all_time" as const,
    start_date: null,
    end_date: "2026-07-25",
    total_amount: 100,
    total_amount_cents: 10_000,
    by_category: {},
    count: 2,
  };
  const payoutTotals = {
    total_amount: 300,
    total_amount_cents: 30_000,
    average_amount: 300,
    average_amount_cents: 30_000,
    count: 1,
  };
  return {
    as_of_date: "2026-07-25",
    first_cash_flow_date: "2026-01-01",
    expense_totals: expenseTotals,
    payout_totals: payoutTotals,
    spend_since_last_payout: {
      last_payout_date: "2026-07-10",
      total_amount: spendAmount,
      total_amount_cents: spendAmount * 100,
      expense_count: 1,
    },
    ranges: [
      {
        key: "one_month",
        label: "1 Month",
        start_date: "2026-06-25",
        end_date: "2026-07-25",
        expense_totals: expenseTotals,
        payout_totals: payoutTotals,
      },
      {
        key: "all_time",
        label: "All Time",
        start_date: null,
        end_date: null,
        expense_totals: expenseTotals,
        payout_totals: payoutTotals,
      },
    ],
  };
}

function projectXAccount(overrides: Partial<AccountInfo> = {}): AccountInfo {
  return {
    id: 101,
    name: "50KTC-101",
    provider_name: "50KTC-101",
    custom_display_name: null,
    trade_data_source: "projectx",
    balance: 50_000,
    provider_data_stale: false,
    provider_sync_status: "provider_fresh",
    provider_sync_error_code: null,
    provider_sync_error_message: null,
    provider_last_successful_refresh_at: "2026-07-29T12:00:00.000Z",
    last_seen_at: "2026-07-29T12:00:00.000Z",
    status: "ACTIVE",
    account_state: "ACTIVE",
    is_main: false,
    is_archived: false,
    can_trade: true,
    is_visible: true,
    last_trade_at: null,
    ...overrides,
  };
}

function mockExpenseStartup(summary: FinancialSummary | Promise<FinancialSummary>) {
  vi.spyOn(api, "listExpenses").mockResolvedValue({ items: [], total: 0 });
  vi.spyOn(api, "listPayouts").mockResolvedValue({ items: [], total: 0 });
  vi.spyOn(api, "getCombineTrackerExpenseSuppressions").mockResolvedValue({ account_ids: [] });
  return vi.spyOn(api, "getFinancialSummary").mockImplementation(() => Promise.resolve(summary));
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("loadFreshAccountsForExpenseReconciliation", () => {
  it("bypasses the frontend cache and forces the normal ProjectX refresh path", async () => {
    const getAccounts = vi.fn(async () => []);

    await loadFreshAccountsForExpenseReconciliation(getAccounts);

    expect(getAccounts).toHaveBeenCalledOnce();
    expect(getAccounts).toHaveBeenCalledWith({
      showInactive: true,
      showMissing: false,
      bypassCache: true,
      refreshProvider: true,
    });
  });
});

describe("buildAnniversaryYearRangeOptions", () => {
  it("builds started anniversary years and caps the current year at today", () => {
    const ranges = buildAnniversaryYearRangeOptions("2025-03-31", new Date(2026, 5, 25));

    expect(ranges).toEqual([
      {
        key: "anniversary_year_1",
        label: "Year 1",
        dateRange: {
          startDate: "2025-03-31",
          endDate: "2026-03-30",
        },
      },
      {
        key: "anniversary_year_2",
        label: "Year 2",
        dateRange: {
          startDate: "2026-03-31",
          endDate: "2026-06-25",
        },
      },
    ]);
  });

  it("does not include future anniversary years before they start", () => {
    const ranges = buildAnniversaryYearRangeOptions("2025-03-31", new Date(2026, 2, 30));

    expect(ranges).toHaveLength(1);
    expect(ranges[0]?.label).toBe("Year 1");
  });
});

describe("ExpensesPage consolidated startup", () => {
  it("shows loading states instead of false empty values before startup settles", async () => {
    const expenses = deferred<Awaited<ReturnType<typeof api.listExpenses>>>();
    const payouts = deferred<Awaited<ReturnType<typeof api.listPayouts>>>();
    const summary = deferred<FinancialSummary>();
    vi.spyOn(api, "listExpenses").mockReturnValue(expenses.promise);
    vi.spyOn(api, "listPayouts").mockReturnValue(payouts.promise);
    vi.spyOn(api, "getFinancialSummary").mockReturnValue(summary.promise);

    render(<ExpensesPage />);

    expect(screen.getByText("Loading expenses...")).not.toBeNull();
    expect(screen.getByText("Loading payouts...")).not.toBeNull();
    expect(screen.getByText("Loading recorded spend...")).not.toBeNull();
    const recordedSpendColumn = screen.getByText("Recorded spend").parentElement?.parentElement;
    expect(recordedSpendColumn?.textContent).toContain("...");
    expect(recordedSpendColumn?.textContent).not.toContain("No data");
    expect(screen.queryByText("No expenses found.")).toBeNull();
    expect(screen.queryByText("No payouts found.")).toBeNull();
    expect(screen.getByText("Loading expense total...")).not.toBeNull();
    expect(screen.getByText("Loading payout total...")).not.toBeNull();
    expect(screen.queryByText(/\(0 total\)/)).toBeNull();

    await act(async () => {
      expenses.resolve({ items: [], total: 0 });
      payouts.resolve({ items: [], total: 0 });
      summary.resolve(financialSummary(0));
      await Promise.all([expenses.promise, payouts.promise, summary.promise]);
    });

    expect(await screen.findByText("No expenses found.")).not.toBeNull();
    expect(screen.getByText("No payouts found.")).not.toBeNull();
  });

  it("loads the two visible lists and one financial summary on mount", async () => {
    const summaryRequest = mockExpenseStartup(financialSummary(25));
    const browserLocalToday = formatLocalIsoDate(new Date());

    render(<ExpensesPage />);

    await waitFor(() => expect(summaryRequest).toHaveBeenCalledOnce());
    expect(api.listExpenses).toHaveBeenCalledOnce();
    expect(api.listPayouts).toHaveBeenCalledOnce();
    expect(summaryRequest.mock.calls[0]?.[0]).toEqual({ asOfDate: browserLocalToday });
    expect(summaryRequest.mock.calls[0]?.[1]?.signal).toBeInstanceOf(AbortSignal);
    expect(await screen.findByText("$25.00")).not.toBeNull();
  });

  it("aborts and ignores a stale summary after a payout refresh starts", async () => {
    const user = userEvent.setup();
    const first = deferred<FinancialSummary>();
    const second = deferred<FinancialSummary>();
    vi.spyOn(api, "listExpenses").mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(api, "listPayouts").mockResolvedValue({ items: [], total: 0 });
    const summaryRequest = vi.spyOn(api, "getFinancialSummary")
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    vi.spyOn(api, "createPayout").mockResolvedValue({
      id: 1,
      payout_date: "2026-07-25",
      amount: 500,
      amount_cents: 50_000,
      currency: "USD",
      notes: null,
      created_at: "2026-07-25T12:00:00Z",
      updated_at: "2026-07-25T12:00:00Z",
    } satisfies PayoutRecord);

    render(<ExpensesPage />);
    await waitFor(() => expect(summaryRequest).toHaveBeenCalledTimes(1));

    await user.click(screen.getByRole("button", { name: "Add Payout" }));
    await user.type(screen.getByLabelText("Amount (USD)"), "500");
    await user.click(screen.getByRole("button", { name: "Save Payout" }));
    await waitFor(() => expect(summaryRequest).toHaveBeenCalledTimes(2));

    expect(summaryRequest.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
    await act(async () => {
      second.resolve(financialSummary(222));
      await second.promise;
    });
    expect(await screen.findByText("$222.00")).not.toBeNull();

    await act(async () => {
      first.resolve(financialSummary(111));
      await first.promise;
    });
    expect(screen.queryByText("$111.00")).toBeNull();
    expect(screen.getByText("$222.00")).not.toBeNull();
  });
});

describe("ExpensesPage combine reconciliation", () => {
  it("renders a provider account-load failure as a safe actionable message", async () => {
    const user = userEvent.setup();
    mockExpenseStartup(financialSummary(0));
    const detail = {
      code: "projectx_credentials_not_configured",
      message: "Configure ProjectX credentials for this user, then refresh accounts.",
    };
    vi.spyOn(api.accountsApi, "getAccounts").mockRejectedValue(
      new api.ApiError(JSON.stringify(detail), 400, { detail }, detail),
    );
    const createExpense = vi.spyOn(api, "createExpense");
    const deleteExpense = vi.spyOn(api, "deleteExpense");
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<ExpensesPage />);
    await user.click(screen.getByRole("button", { name: "Reconcile Combine Expenses" }));

    expect(await screen.findByText(
      /Configure ProjectX credentials for this user.*No expenses or local combine-tracker data were changed/i,
    )).not.toBeNull();
    expect(createExpense).not.toHaveBeenCalled();
    expect(deleteExpense).not.toHaveBeenCalled();
  });

  it("shows an actionable stale-provider error without creating, deleting, or changing the local ledger", async () => {
    const user = userEvent.setup();
    mockExpenseStartup(financialSummary(0));
    const getAccounts = vi.spyOn(api.accountsApi, "getAccounts").mockResolvedValue([
      projectXAccount({ provider_data_stale: true, provider_sync_status: "cache_stale" }),
    ]);
    const createExpense = vi.spyOn(api, "createExpense");
    const deleteExpense = vi.spyOn(api, "deleteExpense");
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<ExpensesPage />);
    await user.click(screen.getByRole("button", { name: "Reconcile Combine Expenses" }));

    const staleError = await screen.findByText(/ProjectX account data is stale/i);
    expect(staleError.getAttribute("role")).toBe("alert");
    expect(getAccounts).toHaveBeenCalledWith({
      showInactive: true,
      showMissing: false,
      bypassCache: true,
      refreshProvider: true,
    });
    expect(createExpense).not.toHaveBeenCalled();
    expect(deleteExpense).not.toHaveBeenCalled();
    expect(window.localStorage.length).toBe(0);
  });

  it("reports a successful refresh with zero recognized combines explicitly", async () => {
    const user = userEvent.setup();
    mockExpenseStartup(financialSummary(0));
    vi.spyOn(api.accountsApi, "getAccounts").mockResolvedValue([
      projectXAccount({ name: "XFA-101", provider_name: "XFA-101" }),
    ]);
    const createExpense = vi.spyOn(api, "createExpense");
    const deleteExpense = vi.spyOn(api, "deleteExpense");
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<ExpensesPage />);
    await user.click(screen.getByRole("button", { name: "Reconcile Combine Expenses" }));

    const zeroCombineNotice = await screen.findByText(
      /no ACTIVE or LOCKED_OUT 50KTC, 100KTC, or 150KTC combine accounts were found/i,
    );
    expect(zeroCombineNotice.getAttribute("role")).toBe("status");
    expect(createExpense).not.toHaveBeenCalled();
    expect(deleteExpense).not.toHaveBeenCalled();
  });
});
