import { describe, expect, it } from "vitest";

import type { AccountPnlCalendarDay } from "../../../lib/types";
import { computeStabilityMetrics } from "./calculations";

function day(date: string, netPnl: number): AccountPnlCalendarDay {
  return {
    date,
    trade_count: 1,
    gross_pnl: netPnl,
    fees: 0,
    net_pnl: netPnl,
  };
}

describe("dashboard stability percentages", () => {
  it("uses net-PnL magnitude for a losing range", () => {
    const metrics = computeStabilityMetrics(
      [day("2026-08-01", 200), day("2026-08-02", -1_200)],
      -1_000,
      -500,
    );

    expect(metrics.bestDayPercentOfNet.value).toBe(20);
    expect(metrics.worstDayPercentOfNet.value).toBe(120);
  });

  it("keeps positive-range percentages unchanged", () => {
    const metrics = computeStabilityMetrics(
      [day("2026-08-01", 400), day("2026-08-02", -250), day("2026-08-03", 850)],
      1_000,
      1_000 / 3,
    );

    expect(metrics.bestDayPercentOfNet.value).toBe(85);
    expect(metrics.worstDayPercentOfNet.value).toBe(25);
  });

  it("keeps percentage magnitudes non-negative when every day is red", () => {
    const metrics = computeStabilityMetrics(
      [day("2026-08-01", -100), day("2026-08-02", -900)],
      -1_000,
      -500,
    );

    expect(metrics.bestDayPercentOfNet.value).toBe(10);
    expect(metrics.worstDayPercentOfNet.value).toBe(90);
  });
});
