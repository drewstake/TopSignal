const EPSILON = 1e-9;

export interface SustainabilityInputs {
  netPnl: number;
  profitPerDay: number;
  maxDrawdown: number;
  bestDay: number;
  worstDay: number;
  dailyPnlVolatility: number;
}

export type SustainabilityLabel = "Healthy" | "Mostly healthy" | "Unstable" | "Unsustainable";

export type SustainabilityRatioValue = number | "N/A";

export interface SustainabilityResult {
  score: number;
  label: SustainabilityLabel;
  swingScore: number;
  outlierScore: number;
  riskScore: number;
  debug: {
    swingRatio: SustainabilityRatioValue;
    bestDayPct: SustainabilityRatioValue;
    worstDayPct: SustainabilityRatioValue;
    ddRatio: SustainabilityRatioValue;
  };
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function normalizeNumber(value: number): number {
  return Number.isFinite(value) ? value : 0;
}

export function getSustainabilityLabel(score: number): SustainabilityLabel {
  if (score >= 80) {
    return "Healthy";
  }
  if (score >= 60) {
    return "Mostly healthy";
  }
  if (score >= 40) {
    return "Unstable";
  }
  return "Unsustainable";
}

export function computeSustainability(input: SustainabilityInputs): SustainabilityResult {
  const netPnl = normalizeNumber(input.netPnl);
  const profitPerDay = normalizeNumber(input.profitPerDay);
  const maxDrawdown = normalizeNumber(input.maxDrawdown);
  const bestDay = normalizeNumber(input.bestDay);
  const worstDay = normalizeNumber(input.worstDay);
  const dailyPnlVolatility = normalizeNumber(input.dailyPnlVolatility);

  const netMagnitude = Math.abs(netPnl);
  const profitPerDayMagnitude = Math.abs(profitPerDay);

  // Edge case guard: if the key denominators are zero, ratios are undefined.
  if (netMagnitude <= EPSILON || profitPerDayMagnitude <= EPSILON) {
    return {
      score: 0,
      label: "Unsustainable",
      swingScore: 0,
      outlierScore: 0,
      riskScore: 0,
      debug: {
        swingRatio: "N/A",
        bestDayPct: "N/A",
        worstDayPct: "N/A",
        ddRatio: "N/A",
      },
    };
  }

  // Ratios are normalized by absolute net PnL and absolute profit/day as requested.
  const bestDayPct = bestDay / netMagnitude;
  const worstDayPct = Math.abs(worstDay) / netMagnitude;
  const swingRatio = dailyPnlVolatility / profitPerDayMagnitude;
  const ddRatio = Math.abs(maxDrawdown) / netMagnitude;

  // Swing score: penalizes volatility when it exceeds profit/day.
  const swingScore = clamp(100 - 35 * (swingRatio - 1), 0, 100);
  // Outlier score: penalizes over-reliance on one best day and vulnerability to one worst day.
  const outlierPenalty = 70 * bestDayPct + 90 * worstDayPct;
  const outlierScore = clamp(100 - outlierPenalty, 0, 100);
  // Risk score: penalizes high drawdown relative to net PnL.
  const riskScore = clamp(100 - 120 * ddRatio, 0, 100);

  const score = Math.round((swingScore + outlierScore + riskScore) / 3);

  return {
    score,
    label: getSustainabilityLabel(score),
    swingScore,
    outlierScore,
    riskScore,
    debug: {
      swingRatio,
      bestDayPct,
      worstDayPct,
      ddRatio,
    },
  };
}
