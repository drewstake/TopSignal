import type { AccountSummary } from "../lib/types";

type ProfitFactorSummary = Pick<AccountSummary, "profit_factor" | "profit_factor_no_losses" | "win_count" | "loss_count">;

// Infinity is a display/comparison value only. The API uses null when the
// denominator is zero; counts distinguish no losses from no meaningful sample.
export function profitFactorValue(summary: ProfitFactorSummary): number {
  if (summary.profit_factor_no_losses !== undefined) {
    return summary.profit_factor_no_losses ? Infinity : summary.profit_factor ?? NaN;
  }
  if (summary.loss_count === 0) {
    return summary.win_count > 0 ? Infinity : NaN;
  }
  return summary.profit_factor !== null && Number.isFinite(summary.profit_factor)
    ? summary.profit_factor
    : NaN;
}

export function formatProfitFactor(summary: ProfitFactorSummary): string {
  const value = profitFactorValue(summary);
  return value === Infinity ? "∞" : Number.isFinite(value) ? value.toFixed(2) : "—";
}
