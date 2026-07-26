// @vitest-environment jsdom

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../../lib/api";
import type { FinancialSummary, PayoutRecord } from "../../lib/types";
import { loadLocalAccountsForExpenseReconciliation } from "./expenseAccountLoading";
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

function mockExpenseStartup(summary: FinancialSummary | Promise<FinancialSummary>) {
  vi.spyOn(api, "listExpenses").mockResolvedValue({ items: [], total: 0 });
  vi.spyOn(api, "listPayouts").mockResolvedValue({ items: [], total: 0 });
  return vi.spyOn(api, "getFinancialSummary").mockImplementation(() => Promise.resolve(summary));
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("loadLocalAccountsForExpenseReconciliation", () => {
  it("uses saved account data without refreshing ProjectX", async () => {
    const getAccounts = vi.fn(async () => []);

    await loadLocalAccountsForExpenseReconciliation(getAccounts);

    expect(getAccounts).toHaveBeenCalledOnce();
    expect(getAccounts).toHaveBeenCalledWith({
      showInactive: true,
      showMissing: false,
      bypassCache: false,
      refreshProvider: false,
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
