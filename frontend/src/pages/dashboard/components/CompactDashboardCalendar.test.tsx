// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CompactDashboardCalendar, type CompactCalendarProps } from "./CompactDashboardCalendar";

afterEach(cleanup);

function props(overrides: Partial<CompactCalendarProps> = {}): CompactCalendarProps {
  return {
    days: [{
      date: "2026-07-01",
      trade_count: 1,
      win_count: 1,
      loss_count: 0,
      breakeven_count: 0,
      gross_pnl: 25,
      fees: 0,
      net_pnl: 25,
    }],
    rangeStartDate: "2026-07-01",
    rangeEndDate: "2026-07-31",
    loading: false,
    error: null,
    journalDays: new Set(),
    journalDaysLoading: false,
    journalDaysError: null,
    scopeKey: "july",
    selectedDate: null,
    onDaySelect: vi.fn(),
    onJournalDayOpen: vi.fn(),
    onVisibleRangeChange: vi.fn(),
    ...overrides,
  };
}

describe("CompactDashboardCalendar", () => {
  it("reserves six calendar rows and uses signed, semantic P&L text", () => {
    render(<CompactDashboardCalendar {...props()} />);
    expect(screen.getAllByRole("gridcell")).toHaveLength(42);
    const july1 = screen.getByRole("button", { name: /July 1, 2026, \+\$25/i });
    const value = Array.from(july1.querySelectorAll("span")).find((span) => (
      span.classList.contains("text-app-positive-text")
    ));
    expect(value).not.toBeNull();
    expect(value?.textContent).toContain("+$25");
    expect(value?.className).toContain("text-app-positive-text");
    expect(july1.className).toContain("sm:min-h-[72px]");
    expect(july1.className).toContain("lg:min-h-20");
    expect(july1.textContent).not.toContain("1t");
    expect(july1.textContent).not.toContain("1W");
    expect(july1.textContent).not.toContain("0L");
    expect(screen.getByText(/1 trade · 1 day/i)).not.toBeNull();
  });

  it("renders exactly 42 placeholders while the grid is loading", () => {
    render(<CompactDashboardCalendar {...props({ loading: true })} />);
    const calendar = screen.getByRole("heading", { name: "July 2026" }).closest("section");
    const placeholders = calendar?.querySelectorAll(".animate-pulse");
    expect(placeholders).toHaveLength(42);
    expect(placeholders?.[0]?.className).toContain("lg:min-h-20");
  });

  it("shows each row's range-aware weekly P&L on Saturday", () => {
    render(
      <CompactDashboardCalendar
        {...props({
          days: [
            { date: "2026-07-01", trade_count: 2, gross_pnl: 100, fees: 0, net_pnl: 100 },
            { date: "2026-07-02", trade_count: 16, gross_pnl: 6_100, fees: 0, net_pnl: 6_100 },
            { date: "2026-07-03", trade_count: 1, gross_pnl: 439.2, fees: 0, net_pnl: 439.2 },
          ],
          rangeStartDate: "2026-07-02",
        })}
      />,
    );

    const saturday = screen.getByRole("button", {
      name: /July 4, 2026, no daily P&L, no trades, weekly P&L \+\$6,539\.2 across 17 trades/i,
    });
    expect(saturday.textContent).toContain("W");
    expect(saturday.textContent).toContain("+$6.5K");
    expect(saturday.textContent).not.toContain("17t");
  });

  it("keeps Saturday's daily result when also showing its weekly total", () => {
    render(
      <CompactDashboardCalendar
        {...props({
          days: [
            { date: "2026-07-02", trade_count: 1, gross_pnl: 100, fees: 0, net_pnl: 100 },
            { date: "2026-07-04", trade_count: 2, gross_pnl: 25, fees: 0, net_pnl: 25 },
          ],
        })}
      />,
    );

    const saturday = screen.getByRole("button", {
      name: /July 4, 2026, \+\$25, 2 trades, weekly P&L \+\$125 across 3 trades/i,
    });
    expect(saturday.textContent).toContain("+$25");
    expect(saturday.textContent).toContain("+$125");
    expect(saturday.textContent).not.toContain("3t");
  });

  it("shows an in-range week total when Saturday falls outside the selected range", () => {
    render(
      <CompactDashboardCalendar
        {...props({
          days: [
            { date: "2026-07-20", trade_count: 1, gross_pnl: -1_032.2, fees: 0, net_pnl: -1_032.2 },
            { date: "2026-07-21", trade_count: 2, gross_pnl: -1_068, fees: 0, net_pnl: -1_068 },
            { date: "2026-07-22", trade_count: 5, gross_pnl: 3_086.2, fees: 0, net_pnl: 3_086.2 },
          ],
          rangeStartDate: "2026-07-02",
          rangeEndDate: "2026-07-22",
        })}
      />,
    );

    const saturday = screen.getByRole("gridcell", {
      name: /July 25, 2026, outside selected range, weekly P&L \+\$986 across 8 trades/i,
    });
    expect(saturday.textContent).toContain("Week");
    expect(saturday.textContent).toContain("+$986");
    expect(saturday.textContent).not.toContain("8t");
    expect(screen.queryByRole("button", { name: /July 25, 2026/i })).toBeNull();
  });
});
