import { describe, expect, it } from "vitest";

import { getDemoApiResponse } from "./demoData";
import type {
  AccountInfo,
  AccountPnlCalendarDay,
  AccountSummary,
  AccountTrade,
  JournalDaysResponse,
} from "./types";

describe("demoData", () => {
  it("provides selectable demo accounts plus inactive/missing examples", () => {
    const activeOnly = getDemoApiResponse<AccountInfo[]>("/api/accounts", {
      show_inactive: false,
      show_missing: false,
    });
    const fullList = getDemoApiResponse<AccountInfo[]>("/api/accounts", {
      show_inactive: true,
      show_missing: true,
    });

    expect(activeOnly?.data).toHaveLength(3);
    expect(fullList?.data.length).toBeGreaterThan(activeOnly?.data.length ?? 0);
    expect(fullList?.data.some((account) => account.account_state === "LOCKED_OUT")).toBe(true);
    expect(fullList?.data.some((account) => account.account_state === "MISSING")).toBe(true);
  });

  it("builds dashboard-ready trade, summary, and calendar data", () => {
    const trades = getDemoApiResponse<AccountTrade[]>("/api/accounts/910001/trades", {
      start: "2026-06-01",
      end: "2026-06-30",
      limit: 200,
    })?.data;
    const summary = getDemoApiResponse<AccountSummary>("/api/accounts/910001/summary", {
      start: "2026-06-01",
      end: "2026-06-30",
    })?.data;
    const calendar = getDemoApiResponse<AccountPnlCalendarDay[]>("/api/accounts/910001/pnl-calendar", {
      start: "2026-06-01",
      end: "2026-06-30",
    })?.data;

    expect(trades?.length).toBeGreaterThan(25);
    expect(summary?.trade_count).toBe(trades?.length);
    expect(summary?.win_count).toBeGreaterThan(0);
    expect(summary?.loss_count).toBeGreaterThan(0);
    expect(summary?.green_days).toBeGreaterThan(0);
    expect(summary?.red_days).toBeGreaterThan(0);
    expect(summary?.profit_factor).toBeGreaterThan(1);
    expect(summary?.avgPointGain).not.toBeNull();
    expect(summary?.avgPointLoss).not.toBeNull();
    expect(calendar?.length).toBeGreaterThan(10);
  });

  it("filters journal day fixtures by requested date range", () => {
    const response = getDemoApiResponse<JournalDaysResponse>("/api/accounts/910001/journal/days", {
      start_date: "2026-06-20",
      end_date: "2026-06-30",
    });

    expect(response?.data.days).toEqual(["2026-06-22", "2026-06-26", "2026-06-30"]);
  });
});
