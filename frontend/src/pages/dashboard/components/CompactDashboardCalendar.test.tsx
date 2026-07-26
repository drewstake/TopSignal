// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CompactDashboardCalendar, type CompactCalendarProps } from "./CompactDashboardCalendar";

afterEach(cleanup);

function props(overrides: Partial<CompactCalendarProps> = {}): CompactCalendarProps {
  return {
    days: [{ date: "2026-07-01", trade_count: 1, gross_pnl: 25, fees: 0, net_pnl: 25 }],
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
    expect(value?.textContent).toMatch(/^\+\$25(?:\.0+)?$/);
    expect(value?.className).toContain("text-app-positive-text");
    expect(july1.className).toContain("sm:min-h-12");
    expect(july1.textContent).toContain("1t");
  });

  it("renders exactly 42 placeholders while the grid is loading", () => {
    render(<CompactDashboardCalendar {...props({ loading: true })} />);
    const calendar = screen.getByRole("heading", { name: "July 2026" }).closest("section");
    expect(calendar?.querySelectorAll(".animate-pulse")).toHaveLength(42);
  });
});
