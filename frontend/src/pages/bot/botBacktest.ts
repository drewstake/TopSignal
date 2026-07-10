import type { BotBacktestDrawdownPoint, BotBacktestEquityPoint, BotBacktestInput } from "../../lib/types";

export const BACKTEST_CHART_WIDTH = 720;
export const BACKTEST_EQUITY_TOP = 14;
export const BACKTEST_EQUITY_HEIGHT = 132;
export const BACKTEST_DRAWDOWN_TOP = 184;
export const BACKTEST_DRAWDOWN_HEIGHT = 58;

export interface BotBacktestFormState {
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
  const equityValues = equity.map((point) => point.equity).filter(Number.isFinite);
  const drawdownValues = drawdown.map((point) => Math.abs(point.drawdown_percent)).filter(Number.isFinite);
  const equityMin = equityValues.length > 0 ? Math.min(...equityValues) : 0;
  const equityMax = equityValues.length > 0 ? Math.max(...equityValues) : 0;
  const drawdownMax = drawdownValues.length > 0 ? Math.max(...drawdownValues) : 0;
  const equityPath = linePath(equityValues, BACKTEST_EQUITY_TOP, BACKTEST_EQUITY_HEIGHT, equityMin, equityMax);
  const drawdownPath = linePath(drawdownValues, BACKTEST_DRAWDOWN_TOP, BACKTEST_DRAWDOWN_HEIGHT, 0, drawdownMax, false);
  return {
    equity: equityPath,
    equityFill: equityPath
      ? `${equityPath} L ${BACKTEST_CHART_WIDTH} ${BACKTEST_EQUITY_TOP + BACKTEST_EQUITY_HEIGHT} L 0 ${BACKTEST_EQUITY_TOP + BACKTEST_EQUITY_HEIGHT} Z`
      : "",
    drawdown: drawdownPath,
    equityMin,
    equityMax,
    drawdownMax,
  };
}

function linePath(values: number[], top: number, height: number, min: number, max: number, invert = true): string {
  if (values.length === 0) {
    return "";
  }
  const span = max - min || 1;
  return values.map((value, index) => {
    const x = values.length === 1 ? BACKTEST_CHART_WIDTH / 2 : (index / (values.length - 1)) * BACKTEST_CHART_WIDTH;
    const ratio = (value - min) / span;
    const y = top + (invert ? 1 - ratio : ratio) * height;
    return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
  }).join(" ");
}
