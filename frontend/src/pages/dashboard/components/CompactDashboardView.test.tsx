// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AccountPnlCalendarDay, AccountSummary, AccountTrade } from "../../../lib/types";
import {
  CompactDashboardSkeleton,
  CompactDashboardView,
  type CompactDashboardViewProps,
} from "./CompactDashboardView";

const originalMatchMedia = window.matchMedia;

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: originalMatchMedia,
  });
});

const summary = {
  net_pnl: 248.78,
  expectancy_per_trade: 12.34,
  profit_factor: 1.24,
  win_rate: 39.02,
  win_count: 32,
  loss_count: 51,
  breakeven_count: 0,
  avg_win: 100,
  avg_loss: -50,
  trade_count: 83,
  max_drawdown: 125,
} as AccountSummary;

const days: AccountPnlCalendarDay[] = [
  { date: "2026-07-01", trade_count: 2, gross_pnl: 125, fees: 5, net_pnl: 120 },
  { date: "2026-07-02", trade_count: 1, gross_pnl: -45, fees: 5, net_pnl: -50 },
];

const trades = [
  {
    id: 1,
    account_id: 10,
    contract_id: "MNQU6",
    symbol: "MNQ",
    side: "SELL",
    size: 1,
    price: 22000,
    timestamp: "2026-07-02T15:00:00Z",
    exit_time: "2026-07-02T15:00:00Z",
    fees: 2,
    pnl: 48,
    order_id: "order-1",
    source_trade_id: "trade-1",
  },
  {
    id: 1,
    account_id: 11,
    contract_id: "MESU6",
    symbol: "MES",
    side: "BUY",
    size: 1,
    price: 6000,
    timestamp: "2026-07-01T15:00:00Z",
    exit_time: "2026-07-01T15:00:00Z",
    fees: 2,
    pnl: -22,
    order_id: "order-2",
    source_trade_id: "trade-2",
  },
] as AccountTrade[];

function viewProps(overrides: Partial<CompactDashboardViewProps> = {}): CompactDashboardViewProps {
  return {
    accountName: "Demo Account",
    rangeLabel: "Jul 1 – Jul 2, 2026",
    rangeStartDate: "2026-07-01",
    rangeEndDate: "2026-07-31",
    summary,
    score: 81,
    scoreBreakdown: {
      label: "Healthy",
      riskScore: 84,
      consistencyScore: 72,
      edgeScore: 78,
      sampleSize: 2,
      sampleConfidence: 0.64,
    },
    performanceContext: {
      tradingDayCount: 2,
      maxDrawdown: 125,
      riskBase: 50_000,
      riskBaseLabel: "Account balance",
    },
    days,
    trades,
    summaryLoading: false,
    summaryError: null,
    daysLoading: false,
    daysError: null,
    tradesLoading: false,
    tradesError: null,
    journalDays: new Set(),
    journalDaysLoading: false,
    journalDaysError: null,
    selectedDate: null,
    calendarScopeKey: "demo:july",
    onDaySelect: vi.fn(),
    onJournalDayOpen: vi.fn(),
    onCalendarVisibleRangeChange: vi.fn(),
    ...overrides,
  };
}

describe("CompactDashboardView", () => {
  it("renders real KPIs, score components, account-attributed trades, and both performance series", () => {
    const onDaySelect = vi.fn();
    render(
      <CompactDashboardView
        {...viewProps({
          onDaySelect,
          accountNameById: { 10: "Primary", 11: "Follower" },
        })}
      />,
    );

    expect(screen.getByRole("region", { name: "Compact dashboard" })).toBeTruthy();
    expect(screen.getByText("+$248.78")).toBeTruthy();
    expect(screen.getByText("$12.34")).toBeTruthy();
    expect(screen.getByText("39.02%")).toBeTruthy();
    expect(screen.getByRole("img", { name: /Cumulative P&L chart/i })).toBeTruthy();
    expect(screen.getByRole("img", { name: /TopSignal component scores/i })).toBeTruthy();
    expect(screen.getByText("Risk · 84")).toBeTruthy();
    expect(screen.getAllByText("LONG").length).toBeGreaterThan(0);
    expect(screen.getAllByText("SHORT").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Primary").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Follower").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /daily/i }));
    expect(screen.getByRole("img", { name: /Daily P&L chart/i })).toBeTruthy();
    expect(screen.getByText("Daily P&L data")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /July 2, 2026.*1 trade/i }));
    expect(onDaySelect).toHaveBeenCalledWith("2026-07-02");
  });

  it("replaces stale values during loading and exposes full-region loading states", () => {
    render(
      <CompactDashboardView
        {...viewProps({ summaryLoading: true, daysLoading: true, tradesLoading: true })}
      />,
    );

    expect(screen.queryByText("+$248.78")).toBeNull();
    expect(screen.queryByText("83 closed trades")).toBeNull();
    expect(screen.queryByRole("img", { name: /Cumulative P&L chart/i })).toBeNull();
    expect(screen.getByText("Loading performance")).toBeTruthy();
    expect(screen.getByText("Calculating score")).toBeTruthy();
    expect(screen.getByText("Loading recent trades")).toBeTruthy();

    const calendar = screen.getByRole("heading", { name: "July 2026" }).closest("section");
    expect(calendar?.querySelectorAll(".animate-pulse")).toHaveLength(42);
  });

  it("keeps day-derived score and context available when only the summary fails", () => {
    render(<CompactDashboardView {...viewProps({ summaryError: "Summary request failed" })} />);

    expect(screen.getByRole("alert").textContent).toContain("Summary request failed");
    expect(screen.queryByText("+$248.78")).toBeNull();
    expect(screen.getAllByText("Unavailable")).toHaveLength(5);
    expect(screen.getByRole("img", { name: /TopSignal component scores/i })).toBeTruthy();
    expect(screen.getByText("$50,000.00")).toBeTruthy();
  });

  it("uses truthful empty, insufficient, and all-winning metric states", () => {
    const emptySummary = {
      ...summary,
      net_pnl: 0,
      expectancy_per_trade: 0,
      profit_factor: 0,
      win_rate: 0,
      win_count: 0,
      loss_count: 0,
      avg_win: 0,
      avg_loss: 0,
      trade_count: 0,
    };
    const { rerender } = render(
      <CompactDashboardView
        {...viewProps({
          summary: emptySummary,
          days: [],
          calendarDays: [],
          trades: [],
          scoreBreakdown: null,
          performanceContext: {
            tradingDayCount: 0,
            maxDrawdown: 0,
            riskBase: null,
            riskBaseLabel: "No reliable base",
          },
        })}
      />,
    );

    expect(screen.getAllByText("Not enough data").length).toBeGreaterThanOrEqual(5);
    expect(screen.getByText("No performance data")).toBeTruthy();
    expect(screen.getByText("No recent trades")).toBeTruthy();

    rerender(
      <CompactDashboardView
        {...viewProps({
          summary: {
            ...summary,
            profit_factor: Number.POSITIVE_INFINITY,
            win_count: 10,
            loss_count: 0,
            trade_count: 10,
            avg_loss: 0,
          },
        })}
      />,
    );
    expect(screen.getByText("∞")).toBeTruthy();
    expect(screen.getAllByText("No losing trades").length).toBeGreaterThanOrEqual(2);
  });

  it("describes contextual info popovers and returns focus on Escape", () => {
    render(<CompactDashboardView {...viewProps()} />);
    const trigger = screen.getByRole("button", { name: "Net P&L information" });

    fireEvent.click(trigger);
    const tooltip = screen.getByRole("tooltip");
    expect(tooltip.textContent).toContain("Realized profit and loss after fees");
    expect(trigger.getAttribute("aria-describedby")).toBe(tooltip.id);

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("uses one roving calendar tab stop and crosses month boundaries with arrow keys", async () => {
    const multiMonthDays = [
      { date: "2026-06-30", trade_count: 1, gross_pnl: 20, fees: 0, net_pnl: 20 },
      ...days,
    ];
    render(
      <CompactDashboardView
        {...viewProps({
          days: multiMonthDays,
          calendarDays: multiMonthDays,
          rangeStartDate: "2026-06-01",
          rangeEndDate: "2026-07-31",
          calendarScopeKey: "demo:summer",
        })}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Previous month" }));
    const june30 = screen.getByRole("button", { name: /June 30, 2026/i });
    june30.focus();
    fireEvent.keyDown(june30, { key: "ArrowRight" });

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "July 2026" })).toBeTruthy();
      expect(document.activeElement).toBe(screen.getByRole("button", { name: /July 1, 2026/i }));
    });
    expect(document.querySelectorAll('button[aria-pressed][tabindex="0"]')).toHaveLength(1);
  });

  it("preserves a month on data refresh but resets it when the calendar scope changes", () => {
    const multiMonthDays = [
      { date: "2026-06-12", trade_count: 1, gross_pnl: 20, fees: 0, net_pnl: 20 },
      ...days,
    ];
    const { rerender } = render(
      <CompactDashboardView
        {...viewProps({
          days: multiMonthDays,
          calendarDays: multiMonthDays,
          rangeStartDate: "2026-06-01",
          calendarScopeKey: "scope-a",
        })}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Previous month" }));
    expect(screen.getByRole("heading", { name: "June 2026" })).toBeTruthy();

    rerender(
      <CompactDashboardView
        {...viewProps({
          days: [...multiMonthDays],
          calendarDays: [...multiMonthDays],
          rangeStartDate: "2026-06-01",
          calendarScopeKey: "scope-a",
        })}
      />,
    );
    expect(screen.getByRole("heading", { name: "June 2026" })).toBeTruthy();

    rerender(
      <CompactDashboardView
        {...viewProps({
          days: multiMonthDays,
          calendarDays: multiMonthDays,
          rangeStartDate: "2026-06-01",
          calendarScopeKey: "scope-b",
        })}
      />,
    );
    expect(screen.getByRole("heading", { name: "July 2026" })).toBeTruthy();
  });

  it("disables exact-range dates and exposes journal failures without hiding calendar data", () => {
    const onJournalDayOpen = vi.fn();
    const { rerender } = render(
      <CompactDashboardView
        {...viewProps({
          rangeEndDate: "2026-07-02",
          journalDays: new Set(["2026-07-02"]),
          journalDaysError: "Journal service timed out",
          selectedDate: "2026-07-02",
          onJournalDayOpen,
        })}
      />,
    );

    expect(screen.getByRole("gridcell", { name: /July 3, 2026, outside selected range/i })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /July 3, 2026/i })).toBeNull();
    expect(screen.getByText(/Journal markers unavailable.*timed out/i)).toBeTruthy();
    rerender(
      <CompactDashboardView
        {...viewProps({
          rangeEndDate: "2026-07-02",
          journalDays: new Set(["2026-07-02"]),
          selectedDate: "2026-07-02",
          onJournalDayOpen,
        })}
      />,
    );
    const journalButton = screen.getByRole("button", { name: "Open journal for Jul 2" });
    expect(journalButton.className).toContain("min-h-11");
    fireEvent.click(journalButton);
    expect(onJournalDayOpen).toHaveBeenCalledWith("2026-07-02");
  });

  it("uses a 44px agenda layout below 400px without an undersized calendar grid", () => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockImplementation(() => ({
        matches: true,
        media: "(max-width: 399px)",
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
    render(<CompactDashboardView {...viewProps()} />);

    expect(screen.queryByRole("grid")).toBeNull();
    const agenda = screen.getByRole("list", { name: "July 2026 P&L agenda" });
    const agendaButtons = agenda.querySelectorAll("button");
    expect(agendaButtons).toHaveLength(2);
    agendaButtons.forEach((button) => expect(button.className).toContain("min-h-11"));
  });

  it("marks a single-point cumulative series and provides its value as table data", () => {
    render(<CompactDashboardView {...viewProps({ days: [days[0]] })} />);

    const chart = screen.getByRole("img", { name: /Cumulative P&L chart/i });
    expect(chart.querySelector("circle")).toBeTruthy();
    expect(screen.getAllByText("+$120").length).toBeGreaterThan(0);
  });

  it("keeps the full scoped series beyond 32 days consistent with the KPI", () => {
    const longDays = Array.from({ length: 40 }, (_, index) => {
      const date = new Date(Date.UTC(2026, 6, 1 + index));
      return {
        date: date.toISOString().slice(0, 10),
        trade_count: 1,
        gross_pnl: 1,
        fees: 0,
        net_pnl: 1,
      };
    });
    render(
      <CompactDashboardView
        {...viewProps({
          summary: { ...summary, net_pnl: 40, trade_count: 40 },
          days: longDays,
          calendarDays: longDays,
          rangeStartDate: "2026-07-01",
          rangeEndDate: "2026-08-09",
          rangeLabel: "Jul 1 – Aug 9, 2026",
          performanceContext: {
            tradingDayCount: 40,
            maxDrawdown: 0,
            riskBase: 50_000,
            riskBaseLabel: "Account balance",
          },
        })}
      />,
    );

    expect(within(screen.getByRole("region", { name: "Net P&L" })).getByText("+$40")).toBeTruthy();
    const table = screen.getByRole("table", { name: "Cumulative P&L data" });
    expect(within(table).getAllByRole("row")).toHaveLength(41);
    expect(within(table).getByText("Jul 1")).toBeTruthy();
    expect(within(table).getByText("Aug 9")).toBeTruthy();
    expect(screen.getByRole("img", { name: /40 trading days from Jul 1 to Aug 9.*Ending \+\$40/i })).toBeTruthy();
  });

  it("updates the greeting after the time boundary without a dashboard rerender", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 6, 26, 11, 59, 30));
    render(<CompactDashboardView {...viewProps()} />);
    expect(screen.getByText("Good morning")).toBeTruthy();

    act(() => vi.advanceTimersByTime(60_000));
    expect(screen.getByText("Good afternoon")).toBeTruthy();
  });

  it("exports a Compact-specific stable initial loading skeleton", () => {
    render(<CompactDashboardSkeleton />);
    const region = screen.getByRole("region", { name: "Compact dashboard loading" });
    expect(region.getAttribute("data-dashboard-view")).toBe("compact");
    expect(region.getAttribute("aria-busy")).toBe("true");
    expect(screen.getByText("Loading compact dashboard")).toBeTruthy();
  });
});
