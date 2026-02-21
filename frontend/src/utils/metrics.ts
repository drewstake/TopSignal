export interface DerivedMetricValue {
  value: number | null;
  missingReason?: string;
}

const EPSILON = 1e-9;

function metric(value: number): DerivedMetricValue {
  return { value };
}

function missingMetric(missingReason: string): DerivedMetricValue {
  return {
    value: null,
    missingReason,
  };
}

export function computeDrawdownPercentOfNetPnl(maxDrawdown: number, netPnl: number): DerivedMetricValue {
  if (Math.abs(netPnl) <= EPSILON) {
    return missingMetric("Needs non-zero net PnL.");
  }
  const percent = (Math.abs(maxDrawdown) / Math.abs(netPnl)) * 100;
  return metric(percent);
}

export function computeBreakevenWinRate(avgWin: number, avgLoss: number): DerivedMetricValue {
  const winMagnitude = Math.abs(avgWin);
  const lossMagnitude = Math.abs(avgLoss);
  const denominator = winMagnitude + lossMagnitude;

  if (denominator <= EPSILON || lossMagnitude <= EPSILON) {
    return missingMetric("Needs non-zero average win and average loss.");
  }

  return metric((lossMagnitude / denominator) * 100);
}

export interface DirectionPercentages {
  longPercent: DerivedMetricValue;
  shortPercent: DerivedMetricValue;
}

export function computeDirectionPercentages(longTrades: number, shortTrades: number): DirectionPercentages {
  const safeLongTrades = Number.isFinite(longTrades) ? Math.max(longTrades, 0) : 0;
  const safeShortTrades = Number.isFinite(shortTrades) ? Math.max(shortTrades, 0) : 0;
  const total = safeLongTrades + safeShortTrades;
  if (total <= EPSILON) {
    const missing = missingMetric("Needs at least one directional trade.");
    return {
      longPercent: missing,
      shortPercent: missing,
    };
  }

  const longPercent = (safeLongTrades / total) * 100;
  return {
    longPercent: metric(longPercent),
    shortPercent: metric(100 - longPercent),
  };
}

export function computeStabilityScoreFromWorstDayPercent(worstDayPercentOfNet: number | null): DerivedMetricValue {
  if (worstDayPercentOfNet === null || !Number.isFinite(worstDayPercentOfNet)) {
    return missingMetric("Needs worst day % of net PnL.");
  }

  // 100 = most stable, 0 = highly unstable.
  const score = Math.max(0, Math.min(100, 100 - Math.abs(worstDayPercentOfNet)));
  return metric(score);
}

