import { describe, expect, it } from "vitest";
import { formatProfitFactor, profitFactorValue } from "./profitFactor";

describe("profit factor states", () => {
  it.each([
    [null, 0, 0, "—"], // empty/breakeven-only
    [null, 1, 0, "∞"], // winning-only
    [0, 1, 0, "∞"], // older cached API response
    [0, 0, 1, "0.00"], // losing-only
    [2.5, 2, 1, "2.50"],
  ] as const)("formats %s with %s wins and %s losses", (ratio, wins, losses, expected) => {
    expect(formatProfitFactor({ profit_factor: ratio, win_count: wins, loss_count: losses })).toBe(expected);
  });
  it("does not classify unavailable ratios as negative evidence", () => {
    expect(profitFactorValue({ profit_factor: null, win_count: 0, loss_count: 0 }) < 1).toBe(false);
  });
});

it("uses the gross no-loss flag when fees turn a gross winner into a net loss", () => {
  expect(formatProfitFactor({ profit_factor: null, profit_factor_no_losses: true, win_count: 0, loss_count: 1 })).toBe("∞");
  expect(formatProfitFactor({ profit_factor: null, profit_factor_no_losses: false, win_count: 0, loss_count: 0 })).toBe("—");
});
