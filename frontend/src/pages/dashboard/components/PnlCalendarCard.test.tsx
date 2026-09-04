// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { formatIsoDateUtc } from "../../../lib/tradingDay";
import { PnlCalendarCard } from "./PnlCalendarCard";

describe("PnlCalendarCard", () => {
  afterEach(() => {
    cleanup();
  });

  it("shows daily outcomes without fee or commission details", () => {
    const now = new Date();
    const date = formatIsoDateUtc(new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 2)));
    const markup = renderToStaticMarkup(
      <PnlCalendarCard
        days={[
          {
            date,
            trade_count: 5,
            win_count: 3,
            loss_count: 1,
            breakeven_count: 1,
            gross_pnl: 220,
            non_commission_fees: 2.22,
            commissions: 1.5,
            fees: 3.72,
            net_pnl: 216.28,
          },
        ]}
        loading={false}
        error={null}
      />,
    );

    expect(markup).toContain("W 3 · L 1 · T 5");
    expect(markup).toContain("3 wins, 1 losses, 1 breakeven, 5 total trades");
    expect(markup).not.toContain("Fees $2.22");
    expect(markup).not.toContain("Comm $1.50");
    expect(markup).not.toContain("Total fees $3.72");
  });

  it("preserves the visible month when refreshed days arrive in the same scope", async () => {
    const augustDay = {
      date: "2026-08-12",
      trade_count: 1,
      gross_pnl: 100,
      fees: 0,
      net_pnl: 100,
    };
    const septemberDay = {
      date: "2026-09-02",
      trade_count: 1,
      gross_pnl: 200,
      fees: 0,
      net_pnl: 200,
    };
    const props = {
      loading: false,
      error: null,
      scopeKey: "account-1-all",
    } as const;
    const { rerender } = render(<PnlCalendarCard {...props} days={[augustDay, septemberDay]} />);

    await waitFor(() => expect(screen.getByText("Sep 2026")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Prev" }));
    expect(screen.getByText("Aug 2026")).toBeTruthy();

    rerender(
      <PnlCalendarCard
        {...props}
        days={[{ ...augustDay, net_pnl: 125 }, { ...septemberDay }]}
      />,
    );

    await waitFor(() => expect(screen.getByText("Aug 2026")).toBeTruthy());
  });

  it("resets to the new account's latest month after its data finishes loading", async () => {
    const onVisibleRangeChange = vi.fn();
    const augustDay = {
      date: "2026-08-12",
      trade_count: 1,
      gross_pnl: 100,
      fees: 0,
      net_pnl: 100,
    };
    const septemberDay = { ...augustDay, date: "2026-09-02" };
    const octoberDay = { ...augustDay, date: "2026-10-02" };
    const { rerender } = render(
      <PnlCalendarCard
        days={[augustDay, septemberDay]}
        loading={false}
        error={null}
        scopeKey="account-a"
        onVisibleRangeChange={onVisibleRangeChange}
      />,
    );

    await waitFor(() => expect(screen.getByText("Sep 2026")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Prev" }));
    expect(screen.getByText("Aug 2026")).toBeTruthy();
    onVisibleRangeChange.mockClear();

    rerender(
      <PnlCalendarCard
        days={[augustDay, septemberDay]}
        loading
        error={null}
        scopeKey="account-b"
        onVisibleRangeChange={onVisibleRangeChange}
      />,
    );
    rerender(
      <PnlCalendarCard
        days={[augustDay, septemberDay, octoberDay]}
        loading={false}
        error={null}
        scopeKey="account-b"
        onVisibleRangeChange={onVisibleRangeChange}
      />,
    );

    await waitFor(() => expect(screen.getByText("Oct 2026")).toBeTruthy());
    expect(onVisibleRangeChange).toHaveBeenLastCalledWith("2026-10-01", "2026-10-31");
    expect(onVisibleRangeChange).not.toHaveBeenCalledWith("2026-08-01", "2026-08-31");
  });
});
