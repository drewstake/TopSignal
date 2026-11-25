import type { AccountTrade } from "../../lib/types";
import { getTradeNetPnl } from "../../lib/tradePnl";
import { EPSILON, metric, missingMetric, percentile, type DerivedMetricValue } from "./shared";

export interface PayoffExtrasInput {
  trades: AccountTrade[];
  avgWin: number;
  avgLoss: number;
  currentWinRate: number;
  breakevenWinRate: number | null;
  canUseTradeDistribution: boolean;
  tradeDistributionReason: string;
}

export interface PayoffExtrasMetrics {
  wrCushion: DerivedMetricValue;
  largeLossThreshold: DerivedMetricValue;
  largeLossRate: DerivedMetricValue;
  p95Loss: DerivedMetricValue;
  capture: DerivedMetricValue;
  insight: string;
}

export function computePayoffExtras(input: PayoffExtrasInput): PayoffExtrasMetrics {
  const wrCushion =
    input.breakevenWinRate === null
      ? missingMetric("needs breakeven win rate")
      : metric(input.currentWinRate - input.breakevenWinRate);

  if (!input.canUseTradeDistribution) {
    const missing = missingMetric(input.tradeDistributionReason);
    return {
      wrCushion,
      largeLossThreshold: missing,
      largeLossRate: missing,
      p95Loss: missing,
      capture: missingMetric("needs MFE data"),
      insight: computePayoffInsight({
        currentWinRate: input.currentWinRate,
        breakevenWinRate: input.breakevenWinRate,
        largeLossRate: missing,
        capture: missing,
      }),
    };
  }

  const pnlValues = input.trades
    .map(getTradeNetPnl)
    .filter((value): value is number => value !== null && Number.isFinite(value));
  const lossPnls = pnlValues.filter((value) => value < 0);

  const largeLossThresholdValue = 2 * Math.abs(input.avgLoss);
  const largeLossThreshold =
    largeLossThresholdValue <= EPSILON ? missingMetric("needs non-zero average loss") : metric(largeLossThresholdValue);
  const largeLossRate = computeLargeLossRate(pnlValues, largeLossThreshold);

  const p95LossMagnitude =
    lossPnls.length < 5 ? null : percentile(lossPnls.map((loss) => Math.abs(loss)), 95);
  const p95Loss =
    p95LossMagnitude === null ? missingMetric("needs at least 5 losing trades") : metric(-p95LossMagnitude);

  const capture = computeCapture(input.trades);

  return {
    wrCushion,
    largeLossThreshold,
    largeLossRate,
    p95Loss,
    capture,
    insight: computePayoffInsight({
      currentWinRate: input.currentWinRate,
      breakevenWinRate: input.breakevenWinRate,
      largeLossRate,
      capture,
    }),
  };
}

function computeLargeLossRate(pnlValues: number[], largeLossThreshold: DerivedMetricValue): DerivedMetricValue {
  if (largeLossThreshold.value === null) {
    return missingMetric("needs non-zero average loss");
  }
  if (pnlValues.length === 0) {
    return missingMetric("needs closed trade PnL data");
  }

  const threshold = largeLossThreshold.value;
  return metric((pnlValues.filter((pnl) => pnl <= -threshold).length / pnlValues.length) * 100);
}

function computeCapture(trades: AccountTrade[]): DerivedMetricValue {
  const wins = trades.flatMap((trade) => {
    const net = getTradeNetPnl(trade);
    const mfe = trade.mfe;
    return net !== null && net > 0 && typeof mfe === "number" && Number.isFinite(mfe) && mfe > EPSILON
      ? [{ net, mfe }]
      : [];
  });
  if (wins.length === 0) return missingMetric("needs MFE data");
  return metric(wins.reduce((total, win) => total + win.net, 0) / wins.reduce((total, win) => total + win.mfe, 0));
}

function computePayoffInsight(args: {
  currentWinRate: number;
  breakevenWinRate: number | null;
  largeLossRate: DerivedMetricValue;
  capture: DerivedMetricValue;
}): string {
  if (args.breakevenWinRate !== null && args.currentWinRate < args.breakevenWinRate) {
    return "Your win rate is below breakeven for this payoff.";
  }
  if (args.largeLossRate.value !== null && args.largeLossRate.value > 5) {
    return "Large losses happen often, tighten stops or reduce size.";
  }
  if (args.capture.value !== null && args.capture.value < 0.35) {
    return "You're capturing a small share of MFE, review exits.";
  }
  if (args.breakevenWinRate === null) {
    return "N/A (needs breakeven win rate)";
  }
  return "Payoff supports your current win rate.";
}
