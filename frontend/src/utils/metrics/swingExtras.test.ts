import { describe, expect, it } from "vitest";

import type { AccountPnlCalendarDay } from "../../lib/types";
import { computeSwingExtras } from "./swingExtras";

const dailyDays: AccountPnlCalendarDay[] = [
  { date: "2026-01-02", trade_count: 3, gross_pnl: 100, fees: 0, net_pnl: 100 },
  { date: "2026-01-03", trade_count: 2, gross_pnl: -50, fees: 0, net_pnl: -50 },
  { date: "2026-01-04", trade_count: 4, gross_pnl: 200, fees: 0, net_pnl: 200 },
  { date: "2026-01-05", trade_count: 1, gross_pnl: -150, fees: 0, net_pnl: -150 },
];

describe("computeSwingExtras", () => {
  it("computes median daily pnl and avg green/red days", () => {
    const result = computeSwingExtras(dailyDays, 50);

    expect(result.medianDayPnl.value).toBeCloseTo(25);
    expect(result.avgGreenDay.value).toBeCloseTo(150);
    expect(result.avgRedDay.value).toBeCloseTo(-100);
    expect(result.redDayPercent.value).toBeCloseTo(50);
  });

  it("computes nuke ratio from worst day and average day profit", () => {
    const result = computeSwingExtras(dailyDays, 50);
    expect(result.nukeRatio.value).toBeCloseTo(3);
  });
});
