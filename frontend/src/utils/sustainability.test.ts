import { describe, expect, it } from "vitest";

import { computeSustainability, getSustainabilityLabel } from "./sustainability";

describe("computeSustainability", () => {
  it("computes sustainability score and subscores from the requested formulas", () => {
    const result = computeSustainability({
      netPnl: 10_000,
      profitPerDay: 500,
      maxDrawdown: -1_200,
      bestDay: 900,
      worstDay: -600,
      dailyPnlVolatility: 650,
    });

    expect(result.debug.swingRatio).toBeCloseTo(1.3);
    expect(result.debug.bestDayPct).toBeCloseTo(0.09);
    expect(result.debug.worstDayPct).toBeCloseTo(0.06);
    expect(result.debug.ddRatio).toBeCloseTo(0.12);
    expect(result.swingScore).toBeCloseTo(89.5);
    expect(result.outlierScore).toBeCloseTo(88.3);
    expect(result.riskScore).toBeCloseTo(85.6);
    expect(result.score).toBe(88);
    expect(result.label).toBe("Healthy");
  });

  it("returns zero subscores and N/A ratios when net pnl is zero", () => {
    const result = computeSustainability({
      netPnl: 0,
      profitPerDay: 250,
      maxDrawdown: -500,
      bestDay: 300,
      worstDay: -200,
      dailyPnlVolatility: 300,
    });

    expect(result.score).toBe(0);
    expect(result.label).toBe("Unsustainable");
    expect(result.swingScore).toBe(0);
    expect(result.outlierScore).toBe(0);
    expect(result.riskScore).toBe(0);
    expect(result.debug.swingRatio).toBe("N/A");
    expect(result.debug.bestDayPct).toBe("N/A");
    expect(result.debug.worstDayPct).toBe("N/A");
    expect(result.debug.ddRatio).toBe("N/A");
  });

  it("returns zero subscores and N/A ratios when profit/day is zero", () => {
    const result = computeSustainability({
      netPnl: 5_000,
      profitPerDay: 0,
      maxDrawdown: -500,
      bestDay: 600,
      worstDay: -350,
      dailyPnlVolatility: 320,
    });

    expect(result.score).toBe(0);
    expect(result.label).toBe("Unsustainable");
    expect(result.swingScore).toBe(0);
    expect(result.outlierScore).toBe(0);
    expect(result.riskScore).toBe(0);
    expect(result.debug.swingRatio).toBe("N/A");
    expect(result.debug.bestDayPct).toBe("N/A");
    expect(result.debug.worstDayPct).toBe("N/A");
    expect(result.debug.ddRatio).toBe("N/A");
  });

  it("uses abs(netPnl) for ratios when net pnl is negative", () => {
    const result = computeSustainability({
      netPnl: -2_000,
      profitPerDay: 100,
      maxDrawdown: -500,
      bestDay: 200,
      worstDay: -300,
      dailyPnlVolatility: 200,
    });

    expect(result.debug.bestDayPct).toBeCloseTo(0.1);
    expect(result.debug.worstDayPct).toBeCloseTo(0.15);
    expect(result.debug.ddRatio).toBeCloseTo(0.25);
  });
});

describe("getSustainabilityLabel", () => {
  it("maps score thresholds to the required labels", () => {
    expect(getSustainabilityLabel(100)).toBe("Healthy");
    expect(getSustainabilityLabel(80)).toBe("Healthy");
    expect(getSustainabilityLabel(79)).toBe("Mostly healthy");
    expect(getSustainabilityLabel(60)).toBe("Mostly healthy");
    expect(getSustainabilityLabel(59)).toBe("Unstable");
    expect(getSustainabilityLabel(40)).toBe("Unstable");
    expect(getSustainabilityLabel(39)).toBe("Unsustainable");
    expect(getSustainabilityLabel(0)).toBe("Unsustainable");
  });
});
