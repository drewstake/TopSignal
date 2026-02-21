import type { AccountPnlCalendarDay } from "../../lib/types";
import { EPSILON, average, median, metric, missingMetric, type DerivedMetricValue } from "./shared";

export interface SwingExtrasMetrics {
  medianDayPnl: DerivedMetricValue;
  avgGreenDay: DerivedMetricValue;
  avgRedDay: DerivedMetricValue;
  redDayPercent: DerivedMetricValue;
  nukeRatio: DerivedMetricValue;
  greenRedDaySizeRatio: DerivedMetricValue;
  insight: string;
}

const MISSING_DAILY_DATA_REASON = "needs daily net PnL data";

export function computeSwingExtras(dailyPnlDays: AccountPnlCalendarDay[], profitPerDay: number): SwingExtrasMetrics {
  const dailyNetValues = dailyPnlDays.map((day) => day.net_pnl).filter((value) => Number.isFinite(value));

  if (dailyNetValues.length === 0) {
    return {
      medianDayPnl: missingMetric(MISSING_DAILY_DATA_REASON),
      avgGreenDay: missingMetric(MISSING_DAILY_DATA_REASON),
      avgRedDay: missingMetric(MISSING_DAILY_DATA_REASON),
      redDayPercent: missingMetric(MISSING_DAILY_DATA_REASON),
      nukeRatio: missingMetric("needs non-zero average day profit"),
      greenRedDaySizeRatio: missingMetric(MISSING_DAILY_DATA_REASON),
      insight: `N/A (${MISSING_DAILY_DATA_REASON})`,
    };
  }

  const greenDays = dailyNetValues.filter((value) => value > 0);
  const redDays = dailyNetValues.filter((value) => value < 0);
  const nonFlatDays = greenDays.length + redDays.length;

  const medianDay = median(dailyNetValues);
  const avgGreen = average(greenDays);
  const avgRed = average(redDays);
  const worstDay = Math.min(...dailyNetValues);

  const nukeRatio =
    Math.abs(profitPerDay) <= EPSILON
      ? missingMetric("needs non-zero average day profit")
      : metric(Math.abs(worstDay) / Math.abs(profitPerDay));

  const greenRedDaySizeRatio =
    avgGreen === null || avgRed === null || Math.abs(avgRed) <= EPSILON
      ? missingMetric("needs both green and red day history")
      : metric(Math.abs(avgGreen) / Math.abs(avgRed));

  return {
    medianDayPnl: medianDay === null ? missingMetric(MISSING_DAILY_DATA_REASON) : metric(medianDay),
    avgGreenDay: avgGreen === null ? missingMetric("needs at least one green day") : metric(avgGreen),
    avgRedDay: avgRed === null ? missingMetric("needs at least one red day") : metric(avgRed),
    redDayPercent: nonFlatDays === 0 ? missingMetric("needs green/red day history") : metric((redDays.length / nonFlatDays) * 100),
    nukeRatio,
    greenRedDaySizeRatio,
    insight: computeSwingInsight(nukeRatio, avgGreen, avgRed),
  };
}

function computeSwingInsight(nukeRatio: DerivedMetricValue, avgGreenDay: number | null, avgRedDay: number | null): string {
  if (nukeRatio.value !== null && nukeRatio.value >= 10) {
    return `One worst day can erase ~${nukeRatio.value.toFixed(1)} average days.`;
  }
  if (avgGreenDay !== null && avgRedDay !== null && Math.abs(avgRedDay) > Math.abs(avgGreenDay)) {
    return "Red days are larger than green days on average.";
  }
  return "Daily swings look controlled relative to average day.";
}
