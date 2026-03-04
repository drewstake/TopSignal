import { describe, expect, it } from "vitest";

import { computeSustainability, getSustainabilityLabel } from "./sustainability";

describe("computeSustainability", () => {
  it("scores smooth profitable performance as healthy", () => {
    const dailyNetPnl = Array.from({ length: 30 }, (_, index) => [100, 110, 90, 105, 95][index % 5]);
    const result = computeSustainability({
      dailyNetPnl,
      maxDrawdown: -600,
      equityBase: 50_000,
    });

    expect(result.score).toBeGreaterThanOrEqual(80);
    expect(result.label).toBe("Healthy");
    expect(result.riskScore).toBeGreaterThanOrEqual(90);
    expect(result.consistencyScore).toBeGreaterThanOrEqual(90);
    expect(result.edgeScore).toBeGreaterThanOrEqual(90);
  });

  it("drops meaningfully when drawdown is near 10% of equity base", () => {
    const dailyNetPnl = [...Array.from({ length: 29 }, () => 100), -5_000];
    const result = computeSustainability({
      dailyNetPnl,
      maxDrawdown: -5_000,
      equityBase: 50_000,
    });

    expect(result.score).toBeLessThan(70);
    expect(result.riskScore).toBeLessThan(70);
  });

  it("keeps breakeven low-risk performance in a mid range and below healthy", () => {
    const result = computeSustainability({
      dailyNetPnl: [20, -19, 18, -18, 21, -20, 19, -19, 20, -20],
      maxDrawdown: -40,
      equityBase: 50_000,
    });

    expect(result.score).toBeGreaterThanOrEqual(40);
    expect(result.score).toBeLessThan(80);
    expect(result.label).not.toBe("Healthy");
  });

  it("scores losing performance as unsustainable", () => {
    const result = computeSustainability({
      dailyNetPnl: Array.from({ length: 30 }, (_, index) => [-300, -250, -350, -280, -320, -260][index % 6]),
      maxDrawdown: -9_000,
      equityBase: 50_000,
    });

    expect(result.score).toBeLessThan(40);
    expect(result.label).toBe("Unsustainable");
  });

  it("returns zero score when there are no trading days", () => {
    const result = computeSustainability({
      dailyNetPnl: [],
      maxDrawdown: 0,
      equityBase: 50_000,
    });

    expect(result.score).toBe(0);
    expect(result.label).toBe("Unsustainable");
    expect(result.riskScore).toBe(0);
    expect(result.consistencyScore).toBe(0);
    expect(result.edgeScore).toBe(0);
  });

  it("keeps profit factor finite when there are no positive days", () => {
    const result = computeSustainability({
      dailyNetPnl: [-100, -120, -80, -90],
      maxDrawdown: -390,
      equityBase: 50_000,
    });

    expect(Number.isFinite(result.debug.profitFactor)).toBe(true);
    expect(result.debug.profitFactor).toBe(0);
    expect(Number.isFinite(result.score)).toBe(true);
  });

  it("falls back to peak equity when equity base is non-positive and never returns NaN", () => {
    const result = computeSustainability({
      dailyNetPnl: [200, -50, 180, -40, 160],
      maxDrawdown: Number.NaN,
      equityBase: 0,
    });

    expect(result.debug.peakEquityFallback).toBeGreaterThan(0);
    expect(result.debug.effectiveEquityBase).toBe(result.debug.peakEquityFallback);
    expect(Number.isFinite(result.score)).toBe(true);
    expect(Number.isFinite(result.riskScore)).toBe(true);
    expect(Number.isFinite(result.consistencyScore)).toBe(true);
    expect(Number.isFinite(result.edgeScore)).toBe(true);
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
