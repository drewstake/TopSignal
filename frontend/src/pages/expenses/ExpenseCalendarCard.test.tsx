// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ExpenseMonthlySummary, PayoutMonthlySummary } from "../../lib/types";
import { ExpenseCalendarCard, type ExpenseCalendarCardProps } from "./ExpenseCalendarCard";

afterEach(cleanup);

function expenseMonth(
  value: string,
  totalAmountCents: number,
  count: number,
): ExpenseMonthlySummary {
  return {
    month: `${value}-01`,
    total_amount: totalAmountCents / 100,
    total_amount_cents: totalAmountCents,
    count,
    by_category: {},
  };
}

function payoutMonth(
  value: string,
  totalAmountCents: number,
  count: number,
): PayoutMonthlySummary {
  return {
    month: `${value}-01`,
    total_amount: totalAmountCents / 100,
    total_amount_cents: totalAmountCents,
    count,
  };
}

function props(overrides: Partial<ExpenseCalendarCardProps> = {}): ExpenseCalendarCardProps {
  return {
    months: [expenseMonth("2025-03", 123_450, 2)],
    payoutMonths: [payoutMonth("2025-03", 200_000, 1)],
    loading: false,
    error: null,
    asOfDate: "2026-08-16",
    ...overrides,
  };
}

describe("ExpenseCalendarCard", () => {
  it("merges payouts and expenses into positive, negative, and neutral monthly net tiles", () => {
    render(
      <ExpenseCalendarCard
        {...props({
          months: [
            expenseMonth("2025-03", 123_450, 2),
            expenseMonth("2025-04", 50_000, 1),
            expenseMonth("2025-05", 10_000, 1),
          ],
          payoutMonths: [
            payoutMonth("2025-03", 200_000, 1),
            payoutMonth("2025-04", 20_000, 2),
            payoutMonth("2025-05", 10_000, 1),
          ],
        })}
      />,
    );

    expect(screen.getByRole("grid", { name: "2025 monthly payouts and expenses" })).not.toBeNull();
    expect(screen.getAllByRole("gridcell")).toHaveLength(12);

    const march = screen.getByRole("button", {
      name: "March 2025, $765.50 net",
    });
    expect(march.textContent).toContain("$765.50 net");
    expect(march.textContent).not.toContain("$2,000.00");
    expect(march.textContent).not.toContain("$1,234.50");
    expect(march.className).toContain("border-app-positive/35");
    expect(march.style.backgroundColor).toContain("--dashboard-positive-rgb");

    const april = screen.getByRole("button", {
      name: "April 2025, -$300.00 net",
    });
    expect(april.textContent).toContain("-$300.00 net");
    expect(april.className).toContain("border-app-negative/35");
    expect(april.style.backgroundColor).toContain("--dashboard-negative-rgb");

    const may = screen.getByRole("button", {
      name: "May 2025, $0.00 net",
    });
    expect(may.textContent).toContain("$0.00 net");
    expect(may.className).toContain("border-app-border/60");
    expect(may.style.backgroundColor).toContain("--dashboard-calendar-empty");

    const june = screen.getByRole("button", {
      name: "June 2025, $0.00 net",
    });
    expect(june.textContent).toBe("June$0.00 net");
  });

  it("shows the cents-precise visible-year net without the payout and expense breakdown", () => {
    render(
      <ExpenseCalendarCard
        {...props({
          months: [expenseMonth("2025-01", 101, 1), expenseMonth("2025-02", 202, 1)],
          payoutMonths: [payoutMonth("2025-01", 203, 1), payoutMonth("2025-02", 304, 1)],
        })}
      />,
    );

    expect(screen.getByText("$2.04 net")).not.toBeNull();
    expect(screen.queryByText("+$5.07")).toBeNull();
    expect(screen.queryByText("-$3.03")).toBeNull();
  });

  it("initializes to the latest data year after asynchronously loaded cash-flow months arrive", () => {
    const { rerender } = render(
      <ExpenseCalendarCard {...props({ months: [], payoutMonths: [], loading: true })} />,
    );

    expect(screen.getByText("2026")).not.toBeNull();

    rerender(
      <ExpenseCalendarCard
        {...props({
          months: [expenseMonth("2024-01", 2_000, 1)],
          payoutMonths: [payoutMonth("2025-12", 4_000, 2)],
          loading: false,
        })}
      />,
    );

    expect(screen.getByRole("grid", { name: "2025 monthly payouts and expenses" })).not.toBeNull();
  });

  it("navigates by year within combined data and as-of bounds", async () => {
    const user = userEvent.setup();
    render(
      <ExpenseCalendarCard
        {...props({
          months: [expenseMonth("2024-01", 2_000, 1)],
          payoutMonths: [payoutMonth("2025-12", 4_000, 2)],
        })}
      />,
    );

    const previous = screen.getByRole("button", { name: "Previous year" });
    const next = screen.getByRole("button", { name: "Next year" });
    expect((previous as HTMLButtonElement).disabled).toBe(false);
    expect((next as HTMLButtonElement).disabled).toBe(false);

    await user.click(previous);
    expect(screen.getByRole("grid", { name: "2024 monthly payouts and expenses" })).not.toBeNull();
    expect((previous as HTMLButtonElement).disabled).toBe(true);

    await user.click(next);
    await user.click(next);
    expect(screen.getByRole("grid", { name: "2026 monthly payouts and expenses" })).not.toBeNull();
    expect((next as HTMLButtonElement).disabled).toBe(true);
  });

  it("announces selection and clears a selected month when clicked again", async () => {
    const user = userEvent.setup();
    const onMonthSelect = vi.fn();
    render(
      <ExpenseCalendarCard
        {...props({
          asOfDate: "2025-08-16",
          selectedMonth: "2025-03",
          onMonthSelect,
        })}
      />,
    );

    const march = screen.getByRole("button", { name: /March 2025/ });
    const april = screen.getByRole("button", { name: /April 2025/ });
    expect(march.getAttribute("aria-pressed")).toBe("true");
    expect(april.getAttribute("aria-pressed")).toBe("false");

    await user.click(march);
    await user.click(april);
    expect(onMonthSelect).toHaveBeenNthCalledWith(1, null);
    expect(onMonthSelect).toHaveBeenNthCalledWith(2, "2025-04");
  });

  it("renders twelve skeletons while loading and an inline error state", () => {
    const { rerender } = render(
      <ExpenseCalendarCard {...props({ loading: true })} />,
    );
    const calendar = screen
      .getByRole("heading", { name: "Monthly P&L Calendar" })
      .closest("section");
    expect(calendar?.querySelectorAll(".animate-pulse")).toHaveLength(12);
    expect(screen.getByRole("status").textContent).toContain("Loading payout and expense calendar");
    expect(screen.getByText("Loading monthly cash flow...")).not.toBeNull();
    expect(screen.queryByText("$0.00 net")).toBeNull();

    rerender(
      <ExpenseCalendarCard {...props({ loading: false, error: "Monthly totals unavailable" })} />,
    );
    expect(screen.getByText("Monthly cash flow unavailable")).not.toBeNull();
    expect(screen.getByRole("alert").textContent).toBe("Monthly totals unavailable");
    expect(screen.queryByRole("grid")).toBeNull();
  });
});
