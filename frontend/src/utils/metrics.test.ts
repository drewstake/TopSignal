import { describe, expect, it } from "vitest";

import {
  computeBreakevenWinRate,
  computeDirectionPercentages,
  computeDrawdownPercentOfNetPnl,
} from "./metrics";

describe("computeDrawdownPercentOfNetPnl", () => {
  it("computes abs(max drawdown) as a percent of net pnl", () => {
    const result = computeDrawdownPercentOfNetPnl(-500, 2000);
    expect(result.value).toBeCloseTo(25);
  });

  it("returns missing when net pnl is zero", () => {
    const result = computeDrawdownPercentOfNetPnl(-500, 0);
    expect(result.value).toBeNull();
    expect(result.missingReason).toContain("non-zero net PnL");
  });
});

describe("computeBreakevenWinRate", () => {
  it("computes breakeven win rate from average win/loss", () => {
    const result = computeBreakevenWinRate(200, -100);
    expect(result.value).toBeCloseTo(33.3333, 3);
  });

  it("returns missing when avg win/loss are not usable", () => {
    const result = computeBreakevenWinRate(0, 0);
    expect(result.value).toBeNull();
    expect(result.missingReason).toContain("average win");
  });
});

describe("computeDirectionPercentages", () => {
  it("computes long and short percentages from trade counts", () => {
    const result = computeDirectionPercentages(40, 60);
    expect(result.longPercent.value).toBeCloseTo(40);
    expect(result.shortPercent.value).toBeCloseTo(60);
  });

  it("returns missing percentages with no directional trades", () => {
    const result = computeDirectionPercentages(0, 0);
    expect(result.longPercent.value).toBeNull();
    expect(result.shortPercent.value).toBeNull();
    expect(result.longPercent.missingReason).toContain("directional trade");
  });
});

