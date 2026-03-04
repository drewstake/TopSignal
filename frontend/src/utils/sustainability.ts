const EPSILON = 1e-9;

export interface SustainabilityInputs {
  dailyNetPnl: number[];
  maxDrawdown: number;
  equityBase?: number | null;
}

export type SustainabilityLabel = "Healthy" | "Mostly healthy" | "Unstable" | "Unsustainable";

export interface SustainabilityResult {
  score: number;
  label: SustainabilityLabel;
  riskScore: number;
  consistencyScore: number;
  edgeScore: number;
  debug: {
    nDays: number;
    avgDay: number;
    vol: number;
    posSum: number;
    negSum: number;
    profitFactor: number;
    concentration: number;
    effectiveEquityBase: number;
    peakEquityFallback: number;
    maxDDPct: number;
    worstDayPct: number;
    swingRatio: number;
    swingScore: number;
    concScore: number;
    confidence: number;
    rawScore: number;
  };
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function normalizeNumber(value: number): number {
  return Number.isFinite(value) ? value : 0;
}

function sum(values: number[]): number {
  return values.reduce((total, value) => total + value, 0);
}

function populationStandardDeviation(values: number[]): number {
  if (values.length === 0) {
    return 0;
  }
  const mean = sum(values) / values.length;
  const variance = sum(values.map((value) => (value - mean) ** 2)) / values.length;
  return Math.sqrt(Math.max(variance, 0));
}

function computePeakEquityFromDailyPnl(dailyNetPnl: number[]): number {
  let equity = 0;
  let peak = 0;
  dailyNetPnl.forEach((dailyPnl) => {
    equity += dailyPnl;
    peak = Math.max(peak, equity);
  });
  return peak;
}

function computeMaxDrawdownFromDailyPnl(dailyNetPnl: number[]): number {
  let equity = 0;
  let peak = 0;
  let maxDrawdown = 0;
  dailyNetPnl.forEach((dailyPnl) => {
    equity += dailyPnl;
    peak = Math.max(peak, equity);
    maxDrawdown = Math.min(maxDrawdown, equity - peak);
  });
  return Math.abs(maxDrawdown);
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
  const dailyNetPnl = input.dailyNetPnl.map((value) => normalizeNumber(value));
  const nDays = dailyNetPnl.length;
  if (nDays === 0) {
    return {
      score: 0,
      label: "Unsustainable",
      riskScore: 0,
      consistencyScore: 0,
      edgeScore: 0,
      debug: {
        nDays: 0,
        avgDay: 0,
        vol: 0,
        posSum: 0,
        negSum: 0,
        profitFactor: 0,
        concentration: 0,
        effectiveEquityBase: 1,
        peakEquityFallback: 0,
        maxDDPct: 0,
        worstDayPct: 0,
        swingRatio: 0,
        swingScore: 0,
        concScore: 0,
        confidence: 0,
        rawScore: 0,
      },
    };
  }

  const posSum = sum(dailyNetPnl.map((dailyPnl) => Math.max(0, dailyPnl)));
  const negSum = sum(dailyNetPnl.map((dailyPnl) => Math.abs(Math.min(0, dailyPnl))));
  const avgDay = sum(dailyNetPnl) / nDays;
  const vol = populationStandardDeviation(dailyNetPnl);
  const profitFactor = posSum / (negSum + EPSILON);

  const topPositiveDaysSum = sum(
    dailyNetPnl
      .filter((dailyPnl) => dailyPnl > 0)
      .sort((a, b) => b - a)
      .slice(0, 3),
  );
  const concentration = topPositiveDaysSum / (posSum + EPSILON);

  const peakEquityFallback = computePeakEquityFromDailyPnl(dailyNetPnl);
  const normalizedEquityBase = normalizeNumber(input.equityBase ?? 0);
  const effectiveEquityBase =
    normalizedEquityBase > EPSILON ? normalizedEquityBase : peakEquityFallback > EPSILON ? peakEquityFallback : 1;

  const maxDrawdownInput = normalizeNumber(input.maxDrawdown);
  const maxDrawdownDollars = Number.isFinite(input.maxDrawdown)
    ? Math.abs(maxDrawdownInput)
    : computeMaxDrawdownFromDailyPnl(dailyNetPnl);
  const maxDDPct = maxDrawdownDollars / (effectiveEquityBase + EPSILON);
  const worstDayPct = Math.abs(Math.min(...dailyNetPnl)) / (effectiveEquityBase + EPSILON);

  const riskScore =
    0.6 * clamp(100 - 400 * maxDDPct, 0, 100) +
    0.4 * clamp(100 - 600 * worstDayPct, 0, 100);

  const swingRatio = vol / Math.max(Math.abs(avgDay), EPSILON);
  const swingScore = clamp(100 - 35 * (swingRatio - 1), 0, 100);
  const concScore = clamp(100 - 140 * Math.max(0, concentration - 0.35), 0, 100);
  const consistencyScore = 0.7 * swingScore + 0.3 * concScore;

  const edgeScore = clamp(50 + 100 * (profitFactor - 1.0), 0, 100);

  let rawScore = 0.45 * riskScore + 0.35 * consistencyScore + 0.2 * edgeScore;
  if (avgDay <= 0) {
    rawScore *= 0.6;
  }

  const confidence = Math.min(1, Math.sqrt(nDays / 30));
  const finalScore = clamp(Math.round(confidence * rawScore + (1 - confidence) * 50), 0, 100);

  return {
    score: finalScore,
    label: getSustainabilityLabel(finalScore),
    riskScore,
    consistencyScore,
    edgeScore,
    debug: {
      nDays,
      avgDay,
      vol,
      posSum,
      negSum,
      profitFactor,
      concentration,
      effectiveEquityBase,
      peakEquityFallback,
      maxDDPct,
      worstDayPct,
      swingRatio,
      swingScore,
      concScore,
      confidence,
      rawScore,
    },
  };
}
