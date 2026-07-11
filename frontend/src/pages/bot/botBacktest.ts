import type {
  BotBacktestDrawdownPoint,
  BotBacktestEquityPoint,
  BotBacktestInput,
  BotBacktestProgress,
  BotBacktestInstrument,
  BotStrategyType,
} from "../../lib/types";

export const BACKTEST_CHART_WIDTH = 720;
export const BACKTEST_EQUITY_TOP = 14;
export const BACKTEST_EQUITY_HEIGHT = 132;
export const BACKTEST_DRAWDOWN_TOP = 184;
export const BACKTEST_DRAWDOWN_HEIGHT = 58;
export const BACKTEST_MAX_RENDERED_CHART_POINTS = 2_000;

export interface BotBacktestFormState {
  strategyType: BotStrategyType;
  instrument: BotBacktestInstrument;
  startingBalance: string;
  commissionPerContract: string;
  slippageTicks: string;
}

export interface BotBacktestChartPaths {
  equity: string;
  equityFill: string;
  drawdown: string;
  equityMin: number;
  equityMax: number;
  drawdownMax: number;
  equitySourcePointCount: number;
  equityRenderedPointCount: number;
  drawdownSourcePointCount: number;
  drawdownRenderedPointCount: number;
}

export interface BotBacktestProgressCopy {
  title: string;
  detail: string;
  percent: number | null;
}

export function describeBacktestProgress(
  progress: BotBacktestProgress | null,
): BotBacktestProgressCopy {
  if (!progress || progress.phase === "preparing") {
    return {
      title: "Preparing candle history",
      detail: "Finding and caching the complete closed-candle range.",
      percent: null,
    };
  }
  if (progress.phase === "loading") {
    return {
      title: "Loading replay streams",
      detail: "Synchronizing the stored candle inputs required by this strategy.",
      percent: null,
    };
  }
  if (progress.phase === "replaying") {
    const percent = Math.max(0, Math.min(100, Math.round(progress.percent ?? 0)));
    const remaining = Math.max(0, Math.min(100, Math.round(progress.remaining_percent ?? (100 - percent))));
    const completed = Math.max(0, Math.round(progress.completed ?? 0));
    const total = Math.max(completed, Math.round(progress.total ?? completed));
    return {
      title: `Replaying closed candles — ${percent}%`,
      detail: `${remaining}% remaining · ${completed.toLocaleString("en-US")} of ${total.toLocaleString("en-US")} candles`,
      percent,
    };
  }
  if (progress.phase === "finalizing") {
    return {
      title: "Replay 100% complete",
      detail: "Building metrics, fingerprinting inputs, and saving the result.",
      percent: 100,
    };
  }
  return {
    title: "Backtest complete — 100%",
    detail: "0% remaining · opening the saved result.",
    percent: 100,
  };
}

export function validateBacktestForm(form: BotBacktestFormState): string | null {
  const startingBalance = Number(form.startingBalance);
  if (!Number.isFinite(startingBalance) || startingBalance <= 0) {
    return "Starting balance must be greater than zero.";
  }
  const commission = Number(form.commissionPerContract);
  if (!Number.isFinite(commission) || commission < 0) {
    return "Commission must be zero or greater.";
  }
  const slippage = Number(form.slippageTicks);
  if (!Number.isInteger(slippage) || slippage < 0) {
    return "Slippage must be a whole number of ticks, zero or greater.";
  }
  return null;
}

export function buildBacktestPayload(form: BotBacktestFormState): BotBacktestInput {
  return {
    strategy_type: form.strategyType,
    instrument: form.instrument,
    starting_balance: Number(form.startingBalance),
    commission_per_contract: Number(form.commissionPerContract),
    slippage_ticks: Number(form.slippageTicks),
    force_close_at_end: true,
  };
}

export function buildBacktestChartPaths(
  equity: BotBacktestEquityPoint[],
  drawdown: BotBacktestDrawdownPoint[],
): BotBacktestChartPaths {
  const equityValues = toIndexedValues(equity.map((point) => point.equity));
  const drawdownValues = toIndexedValues(drawdown.map((point) => Math.abs(point.drawdown_percent)));
  const equityBounds = findBounds(equityValues);
  const drawdownBounds = findBounds(drawdownValues);
  const sampledEquity = sampleChartValues(equityValues, BACKTEST_MAX_RENDERED_CHART_POINTS);
  const sampledDrawdown = sampleChartValues(drawdownValues, BACKTEST_MAX_RENDERED_CHART_POINTS);
  const equityMin = equityBounds.min;
  const equityMax = equityBounds.max;
  const drawdownMax = drawdownBounds.max;
  const equityPath = linePath(sampledEquity, equity.length, BACKTEST_EQUITY_TOP, BACKTEST_EQUITY_HEIGHT, equityMin, equityMax);
  const drawdownPath = linePath(sampledDrawdown, drawdown.length, BACKTEST_DRAWDOWN_TOP, BACKTEST_DRAWDOWN_HEIGHT, 0, drawdownMax, false);
  return {
    equity: equityPath,
    equityFill: equityPath
      ? `${equityPath} L ${BACKTEST_CHART_WIDTH} ${BACKTEST_EQUITY_TOP + BACKTEST_EQUITY_HEIGHT} L 0 ${BACKTEST_EQUITY_TOP + BACKTEST_EQUITY_HEIGHT} Z`
      : "",
    drawdown: drawdownPath,
    equityMin,
    equityMax,
    drawdownMax,
    equitySourcePointCount: equity.length,
    equityRenderedPointCount: sampledEquity.length,
    drawdownSourcePointCount: drawdown.length,
    drawdownRenderedPointCount: sampledDrawdown.length,
  };
}

interface IndexedChartValue {
  index: number;
  value: number;
}

function linePath(
  values: IndexedChartValue[],
  sourceLength: number,
  top: number,
  height: number,
  min: number,
  max: number,
  invert = true,
): string {
  if (values.length === 0) {
    return "";
  }
  const span = max - min || 1;
  return values.map(({ index, value }) => {
    const x = sourceLength <= 1 ? BACKTEST_CHART_WIDTH / 2 : (index / (sourceLength - 1)) * BACKTEST_CHART_WIDTH;
    const ratio = (value - min) / span;
    const y = top + (invert ? 1 - ratio : ratio) * height;
    return `${index === values[0].index ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
  }).join(" ");
}

function toIndexedValues(values: number[]): IndexedChartValue[] {
  const indexed: IndexedChartValue[] = [];
  values.forEach((value, index) => {
    if (Number.isFinite(value)) {
      indexed.push({ index, value });
    }
  });
  return indexed;
}

function findBounds(values: IndexedChartValue[]): { min: number; max: number } {
  if (values.length === 0) {
    return { min: 0, max: 0 };
  }
  let min = values[0].value;
  let max = values[0].value;
  for (let index = 1; index < values.length; index += 1) {
    min = Math.min(min, values[index].value);
    max = Math.max(max, values[index].value);
  }
  return { min, max };
}

/** Deterministic min/max bucket sampling keeps spikes while bounding SVG work. */
function sampleChartValues(values: IndexedChartValue[], maxPoints: number): IndexedChartValue[] {
  if (values.length <= maxPoints) {
    return values;
  }
  const bucketCount = Math.max(1, Math.floor((maxPoints - 2) / 2));
  const sampled: IndexedChartValue[] = [values[0]];
  const interiorLength = values.length - 2;
  for (let bucket = 0; bucket < bucketCount; bucket += 1) {
    const start = 1 + Math.floor((bucket * interiorLength) / bucketCount);
    const end = 1 + Math.floor(((bucket + 1) * interiorLength) / bucketCount);
    if (start >= end) {
      continue;
    }
    let min = values[start];
    let max = values[start];
    for (let index = start + 1; index < end; index += 1) {
      if (values[index].value < min.value) min = values[index];
      if (values[index].value > max.value) max = values[index];
    }
    if (min.index <= max.index) {
      sampled.push(min);
      if (max.index !== min.index) sampled.push(max);
    } else {
      sampled.push(max, min);
    }
  }
  sampled.push(values[values.length - 1]);
  return sampled;
}
