import { useMemo, type ReactNode } from "react";

import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Progress } from "../../components/ui/Progress";
import { Skeleton } from "../../components/ui/Skeleton";
import { cn } from "../../components/ui/cn";
import type {
  BotAnalysis,
  BotConfig,
  BotDirectionalProbabilities,
  BotEvaluation,
  BotMarketBias,
  ProjectXMarketCandle,
  TradeEvaluationResult,
} from "../../lib/types";
import { intervalSecondsFor } from "./botCandleGaps";
import { buildCandlestickData, buildLiquidityLevels } from "./botChartData";
import {
  buildMarketContext,
  timeframeLabel,
  type BotMarketSnapshot,
  type MarketContext,
  type TimeframeTrend,
} from "./botMarketContext";

const MAX_REASONING_ITEMS = 5;
const MAX_RISK_ITEMS = 4;
const PRICE_FALLBACK = "-";
const STALE_EVALUATION_BARS = 2;

const priceFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});
const signedPriceFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
  signDisplay: "exceptZero",
});
const percentFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});
const signedPercentFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
  signDisplay: "exceptZero",
});
const generatedAtFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: true,
});

type BadgeVariant = "positive" | "negative" | "neutral" | "accent" | "warning";
type AnalysisSource = "backend" | "chart_fallback";
type ProbabilityTone = "bullish" | "bearish" | "sideways";

interface BotAnalysisPanelProps {
  bot: BotConfig | null;
  evaluation: BotEvaluation | null;
  /** Live candles currently on the signal chart; keeps context fresher than evaluations. */
  marketSnapshot?: BotMarketSnapshot | null;
  loading?: boolean;
  onEvaluate?: () => void;
}

interface EvaluationStaleness {
  barsBehind: number;
  isStale: boolean;
  priceDrift: number | null;
}

interface DisplayAnalysis {
  source: AnalysisSource;
  marketBias: BotMarketBias;
  probabilities: BotDirectionalProbabilities;
  currentPrice: number | null;
  priceChange: number | null;
  priceChangePercent: number | null;
  expectedMove: number | null;
  expectedMovePercent: number | null;
  nearestSupport: number | null;
  nearestResistance: number | null;
  volatilityState: string | null;
  volumeState: string | null;
  summary: string;
  reasoning: string[];
  riskNotes: string[];
  invalidationLevel: number | null;
  generatedAt: string | null;
  tradeEvaluation: TradeEvaluationResult | null;
}

interface SortedCandle {
  candle: ProjectXMarketCandle;
  timestampMs: number;
}

interface FallbackProbabilityInput {
  history: ProjectXMarketCandle[];
  currentPrice: number;
  priceChange: number;
  expectedMove: number | null;
  nearestSupport: number | null;
  nearestResistance: number | null;
}

export function BotAnalysisPanel({ bot, evaluation, marketSnapshot = null, loading = false, onEvaluate }: BotAnalysisPanelProps) {
  const analysis = useMemo(() => buildDisplayAnalysis(evaluation), [evaluation]);
  const snapshot = bot ? marketSnapshot : null;
  const marketContext = useMemo(() => buildMarketContext(snapshot), [snapshot]);
  const staleness = useMemo(
    () => computeEvaluationStaleness(evaluation, snapshot, bot),
    [bot, evaluation, snapshot],
  );
  const symbolLabel = bot?.symbol ?? bot?.contract_id ?? "Bot";
  const botTimeframeLabel = bot ? `${bot.timeframe_unit_number}${timeframeAbbreviation(bot.timeframe_unit)}` : null;

  return (
    <Card className="min-w-0">
      <CardHeader className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle>Analysis</CardTitle>
            <CardDescription>
              {bot ? `${symbolLabel}${botTimeframeLabel ? ` ${botTimeframeLabel}` : ""} chart context` : "Chart and price context"}
            </CardDescription>
          </div>
          {analysis ? (
            <div className="flex flex-wrap items-center gap-2">
              {staleness?.isStale ? (
                <Badge variant="warning" title="The market has printed new candles since this evaluation ran.">
                  {`Stale - ${staleness.barsBehind} bar${staleness.barsBehind === 1 ? "" : "s"} behind`}
                </Badge>
              ) : null}
              <Badge variant={marketBiasBadgeVariant(analysis.marketBias)}>{marketBiasLabel(analysis.marketBias)}</Badge>
              <Badge variant={analysis.source === "backend" ? "accent" : "neutral"}>
                {analysis.source === "backend" ? "Backend" : "Chart fallback"}
              </Badge>
            </div>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {bot && marketContext && snapshot ? (
          <MarketContextSection context={marketContext} snapshot={snapshot} />
        ) : null}
        {loading ? (
          <AnalysisLoadingState />
        ) : !bot ? (
          <AnalysisEmptyState
            title="No bot selected"
            description="Save or select a bot to load chart context and run an evaluation."
          />
        ) : !evaluation ? (
          <AnalysisEmptyState
            title="No evaluation yet"
            description="Run Evaluate to generate analysis from the latest candles and strategy decision."
            action={
              onEvaluate ? (
                <Button size="sm" variant="secondary" onClick={onEvaluate}>
                  Evaluate bot
                </Button>
              ) : null
            }
          />
        ) : !analysis ? (
          <AnalysisEmptyState
            title="Analysis unavailable"
            description="The latest evaluation did not include backend analysis or enough candle context for a local fallback."
            action={
              onEvaluate ? (
                <Button size="sm" variant="secondary" onClick={onEvaluate}>
                  Evaluate again
                </Button>
              ) : null
            }
          />
        ) : (
          <>
            <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_17rem]">
              <div className="rounded-xl border border-slate-800 bg-slate-950/45 p-3">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500">Market bias</p>
                  {analysis.generatedAt ? <span className="text-[11px] text-slate-500">{analysis.generatedAt}</span> : null}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <span className={cn("text-xl font-semibold tracking-tight", marketBiasTextClassName(analysis.marketBias))}>
                    {marketBiasLabel(analysis.marketBias)}
                  </span>
                  <span className="text-xs text-slate-500">scenario weighting, not a guarantee</span>
                </div>
                <p className="mt-2 text-sm leading-6 text-slate-300">{analysis.summary}</p>
                {staleness?.isStale ? (
                  <p className="mt-2 border-t border-slate-800/80 pt-2 text-xs leading-5 text-amber-200/90">
                    {staleness.barsBehind} new bar{staleness.barsBehind === 1 ? "" : "s"} since this evaluation
                    {staleness.priceDrift !== null
                      ? `; price has moved ${signedPriceFormatter.format(staleness.priceDrift)} since`
                      : ""}
                    . Run Evaluate for a current strategy read.
                  </p>
                ) : null}
              </div>
              <div className="space-y-2 rounded-xl border border-slate-800 bg-slate-950/45 p-3">
                <ProbabilityBar label="Bullish" value={analysis.probabilities.bullish} tone="bullish" />
                <ProbabilityBar label="Bearish" value={analysis.probabilities.bearish} tone="bearish" />
                <ProbabilityBar label="Sideways" value={analysis.probabilities.sideways} tone="sideways" />
              </div>
            </div>

            {analysis.tradeEvaluation ? <TradeEvaluationSummary evaluation={analysis.tradeEvaluation} /> : null}

            <div className="grid grid-cols-2 gap-2.5 md:grid-cols-4">
              <AnalysisMetric
                label="Current price"
                value={formatPrice(analysis.currentPrice)}
                valueClassName="font-mono text-slate-100"
              />
              <AnalysisMetric
                label="Price change"
                value={formatPriceChange(analysis.priceChange, analysis.priceChangePercent)}
                valueClassName={priceChangeClassName(analysis.priceChange)}
              />
              <AnalysisMetric
                label="Expected move"
                value={formatExpectedMove(analysis.expectedMove, analysis.expectedMovePercent)}
                valueClassName="font-mono text-slate-100"
              />
              <AnalysisMetric
                label="Invalidation"
                value={formatPrice(analysis.invalidationLevel)}
                valueClassName="font-mono text-amber-200"
              />
              <AnalysisMetric
                label="Support"
                value={formatPrice(analysis.nearestSupport)}
                valueClassName="font-mono text-emerald-300"
              />
              <AnalysisMetric
                label="Resistance"
                value={formatPrice(analysis.nearestResistance)}
                valueClassName="font-mono text-rose-300"
              />
              <AnalysisMetric label="Volatility" value={analysis.volatilityState ?? "Unknown"} />
              <AnalysisMetric label="Volume" value={analysis.volumeState ?? "Unknown"} />
            </div>

            <div className="grid gap-3 lg:grid-cols-2">
              <AnalysisList title="Reasoning" items={analysis.reasoning} tone="neutral" />
              <AnalysisList title="Risk notes" items={analysis.riskNotes} tone="risk" />
            </div>

            <p className="text-[11px] leading-5 text-slate-500">
              Context only. Directional probabilities are scenario weights from the latest evaluation and chart state, not guaranteed outcomes or
              financial advice.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function AnalysisLoadingState() {
  return (
    <div className="space-y-3">
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_17rem]">
        <Skeleton className="h-28" />
        <Skeleton className="h-28" />
      </div>
      <div className="grid grid-cols-2 gap-2.5 md:grid-cols-4">
        {Array.from({ length: 8 }).map((_, index) => (
          <Skeleton key={index} className="h-16" />
        ))}
      </div>
    </div>
  );
}

function AnalysisEmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="rounded-xl border border-dashed border-slate-800 bg-slate-950/35 p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-1">
          <p className="text-sm font-semibold text-slate-200">{title}</p>
          <p className="max-w-2xl text-sm leading-6 text-slate-400">{description}</p>
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
    </div>
  );
}

function ProbabilityBar({ label, value, tone }: { label: string; value: number; tone: ProbabilityTone }) {
  const clamped = clampNumber(value, 0, 100);
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-3 text-xs">
        <span className={cn("font-medium", probabilityTextClassName(tone))}>{label}</span>
        <span className="font-mono text-slate-300">{percentFormatter.format(clamped)}%</span>
      </div>
      <Progress value={clamped} className="h-2 bg-slate-900" indicatorClassName={probabilityBarClassName(tone)} />
    </div>
  );
}

function AnalysisMetric({
  label,
  value,
  valueClassName,
}: {
  label: string;
  value: string;
  valueClassName?: string;
}) {
  return (
    <div className="min-w-0 rounded-xl border border-slate-800 bg-slate-950/45 p-3">
      <p className="truncate text-[11px] uppercase tracking-wide text-slate-500">{label}</p>
      <p className={cn("mt-1 truncate text-sm font-semibold text-slate-100", valueClassName)} title={value}>
        {value}
      </p>
    </div>
  );
}

function AnalysisList({ title, items, tone }: { title: string; items: string[]; tone: "neutral" | "risk" }) {
  const displayItems = items.length > 0 ? items : ["No notes returned."];
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950/45 p-3">
      <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-slate-500">{title}</p>
      <ul className="space-y-2 text-sm leading-5 text-slate-300">
        {displayItems.map((item, index) => (
          <li key={`${title}-${index}-${item}`} className="flex gap-2">
            <span
              className={cn(
                "mt-2 h-1.5 w-1.5 shrink-0 rounded-full",
                tone === "risk" ? "bg-amber-300/80" : "bg-cyan-300/75",
              )}
            />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function TradeEvaluationSummary({ evaluation }: { evaluation: TradeEvaluationResult }) {
  return (
    <div className="rounded-xl border border-cyan-400/20 bg-cyan-950/10 p-3">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] font-medium uppercase tracking-wide text-cyan-200/80">Trade plan grade</p>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <span className={cn("text-2xl font-semibold", tradeGradeTextClassName(evaluation.grade))}>
              {evaluation.grade}
            </span>
            <span className="font-mono text-sm text-slate-200">{Math.round(evaluation.total_score)}/100</span>
            <Badge variant={tradeDecisionBadgeVariant(evaluation.decision)}>{formatStateLabel(evaluation.decision)}</Badge>
            <Badge variant="neutral">{formatStateLabel(evaluation.confidence)} confidence</Badge>
          </div>
        </div>
        <div className="grid grid-cols-3 gap-2 text-right text-[11px] text-slate-400">
          <MiniScore label="R:R" value={formatRatio(evaluation.features.risk_reward_ratio)} />
          <MiniScore label="Trend" value={`${evaluation.features.trend_alignment_score}%`} />
          <MiniScore label="Stop" value={formatRatio(evaluation.features.stop_atr_multiple, " ATR")} />
        </div>
      </div>
      <p className="text-sm leading-6 text-slate-300">{evaluation.summary}</p>
      <div className="mt-3 grid gap-3 lg:grid-cols-3">
        <CompactList title="Positives" items={evaluation.positives.slice(0, 3)} tone="positive" />
        <CompactList title="Warnings" items={evaluation.warnings.slice(0, 3)} tone="warning" />
        <CompactList title="Adjustments" items={evaluation.suggested_adjustments.slice(0, 3)} tone="neutral" />
      </div>
    </div>
  );
}

function MiniScore({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-[3.8rem] rounded-lg border border-slate-800 bg-slate-950/45 px-2 py-1.5">
      <p className="uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-0.5 font-mono text-xs font-semibold text-slate-100">{value}</p>
    </div>
  );
}

function CompactList({ title, items, tone }: { title: string; items: string[]; tone: "positive" | "warning" | "neutral" }) {
  const displayItems = items.length > 0 ? items : ["No items."];
  return (
    <div className="min-w-0">
      <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-slate-500">{title}</p>
      <ul className="space-y-1.5 text-xs leading-5 text-slate-300">
        {displayItems.map((item, index) => (
          <li key={`${title}-${index}-${item}`} className="flex gap-2">
            <span className={cn("mt-2 h-1.5 w-1.5 shrink-0 rounded-full", compactListDotClassName(tone))} />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function buildDisplayAnalysis(evaluation: BotEvaluation | null): DisplayAnalysis | null {
  if (!evaluation) {
    return null;
  }

  const fallback = buildFallbackAnalysis(evaluation);
  if (!evaluation.analysis) {
    return fallback;
  }

  return normalizeBackendAnalysis(evaluation.analysis, evaluation, fallback);
}

function normalizeBackendAnalysis(
  analysis: BotAnalysis,
  evaluation: BotEvaluation,
  fallback: DisplayAnalysis | null,
): DisplayAnalysis {
  const optionalAnalysis = analysis as BotAnalysis & {
    market_bias?: unknown;
    probabilities?: Partial<BotDirectionalProbabilities>;
    expected_move_percent?: number | null;
    generated_at?: string | null;
    trade_evaluation?: TradeEvaluationResult | null;
  };
  const probabilities = normalizeProbabilities(
    optionalAnalysis.probabilities ?? {
      bullish: analysis.bullish_probability,
      bearish: analysis.bearish_probability,
      sideways: analysis.sideways_probability,
    },
    fallback?.probabilities,
  );
  const currentPrice = finiteNumberOrNull(analysis.current_price) ?? fallback?.currentPrice ?? finiteNumberOrNull(evaluation.decision.price);
  const priceChange = finiteNumberOrNull(analysis.price_change) ?? fallback?.priceChange ?? null;
  const priceChangePercent = finiteNumberOrNull(analysis.price_change_percent) ?? fallback?.priceChangePercent ?? null;
  const expectedMove = finiteNumberOrNull(analysis.expected_move) ?? fallback?.expectedMove ?? null;
  const expectedMovePercent = finiteNumberOrNull(optionalAnalysis.expected_move_percent) ?? fallback?.expectedMovePercent ?? null;
  const nearestSupport = finiteNumberOrNull(analysis.nearest_support) ?? fallback?.nearestSupport ?? null;
  const nearestResistance = finiteNumberOrNull(analysis.nearest_resistance) ?? fallback?.nearestResistance ?? null;
  const invalidationLevel = finiteNumberOrNull(analysis.invalidation_level) ?? fallback?.invalidationLevel ?? null;
  const summary = normalizeText(analysis.summary) ?? fallback?.summary ?? "No summary returned for the latest evaluation.";
  const reasoning = normalizeTextList(analysis.reasoning, fallback?.reasoning).slice(0, MAX_REASONING_ITEMS);
  const riskNotes = normalizeTextList(analysis.risk_notes, fallback?.riskNotes).slice(0, MAX_RISK_ITEMS);

  return {
    source: "backend",
    marketBias: normalizeMarketBias(optionalAnalysis.market_bias ?? analysis.trend, probabilities),
    probabilities,
    currentPrice,
    priceChange,
    priceChangePercent,
    expectedMove,
    expectedMovePercent,
    nearestSupport,
    nearestResistance,
    volatilityState: formatStateLabel(analysis.volatility_state) ?? fallback?.volatilityState ?? null,
    volumeState: formatStateLabel(analysis.volume_state) ?? fallback?.volumeState ?? null,
    summary,
    reasoning,
    riskNotes,
    invalidationLevel,
    generatedAt: formatGeneratedAt(optionalAnalysis.generated_at),
    tradeEvaluation: optionalAnalysis.trade_evaluation ?? null,
  };
}

function buildFallbackAnalysis(evaluation: BotEvaluation): DisplayAnalysis | null {
  const history = sortedUsableCandles(evaluation.candles);
  if (history.length < 2) {
    return null;
  }

  const latest = history[history.length - 1];
  const previous = history[history.length - 2];
  const currentPrice = latest.close;
  const priceChange = currentPrice - previous.close;
  const priceChangePercent = previous.close !== 0 ? (priceChange / Math.abs(previous.close)) * 100 : null;
  const expectedMove = calculateAverageTrueRange(history, 14) ?? calculateAverageTrueRange(history, Math.min(5, history.length - 1));
  const expectedMovePercent = expectedMove !== null && currentPrice !== 0 ? (expectedMove / Math.abs(currentPrice)) * 100 : null;
  const { support, resistance } = findNearestLevels(history, currentPrice);
  const probabilities = buildFallbackProbabilities({
    history,
    currentPrice,
    priceChange,
    expectedMove,
    nearestSupport: support,
    nearestResistance: resistance,
  });
  const marketBias = inferBias(probabilities);
  const invalidationLevel = fallbackInvalidationLevel(marketBias, support, resistance);
  const volatilityState = calculateVolatilityState(history);
  const volumeState = calculateVolumeState(history);
  const summary = buildFallbackSummary({
    marketBias,
    currentPrice,
    priceChange,
    priceChangePercent,
    expectedMove,
    expectedMovePercent,
    support,
    resistance,
  });
  const reasoning = buildFallbackReasoning({
    evaluation,
    history,
    currentPrice,
    priceChange,
    priceChangePercent,
    support,
    resistance,
    volatilityState,
    volumeState,
  });
  const riskNotes = buildFallbackRiskNotes({
    marketBias,
    support,
    resistance,
    invalidationLevel,
  });

  return {
    source: "chart_fallback",
    marketBias,
    probabilities,
    currentPrice,
    priceChange,
    priceChangePercent,
    expectedMove,
    expectedMovePercent,
    nearestSupport: support,
    nearestResistance: resistance,
    volatilityState,
    volumeState,
    summary,
    reasoning,
    riskNotes,
    invalidationLevel,
    generatedAt: null,
    tradeEvaluation: null,
  };
}

function sortedUsableCandles(candles: ProjectXMarketCandle[]): ProjectXMarketCandle[] {
  const closed = toSortedCandles(candles.filter((candle) => !candle.is_partial));
  if (closed.length >= 2) {
    return closed.map((row) => row.candle);
  }
  return toSortedCandles(candles).map((row) => row.candle);
}

function toSortedCandles(candles: ProjectXMarketCandle[]): SortedCandle[] {
  return candles
    .map((candle) => ({
      candle,
      timestampMs: Date.parse(candle.timestamp),
    }))
    .filter(
      (row) =>
        Number.isFinite(row.timestampMs) &&
        [row.candle.open, row.candle.high, row.candle.low, row.candle.close].every(Number.isFinite),
    )
    .sort((left, right) => left.timestampMs - right.timestampMs);
}

function calculateAverageTrueRange(candles: ProjectXMarketCandle[], period: number): number | null {
  if (candles.length < 2 || period <= 0) {
    return null;
  }

  const ranges: number[] = [];
  const start = Math.max(1, candles.length - period);
  for (let index = start; index < candles.length; index += 1) {
    ranges.push(trueRange(candles[index], candles[index - 1].close));
  }
  return averagePositive(ranges);
}

function trueRange(candle: ProjectXMarketCandle, previousClose: number): number {
  return Math.max(
    candle.high - candle.low,
    Math.abs(candle.high - previousClose),
    Math.abs(candle.low - previousClose),
  );
}

function findNearestLevels(candles: ProjectXMarketCandle[], currentPrice: number): { support: number | null; resistance: number | null } {
  const chartCandles = buildCandlestickData(candles, { bridgeConsecutiveGaps: false });
  const liquidityLevels = buildLiquidityLevels(chartCandles);
  const liquiditySupport = liquidityLevels.find((level) => level.side === "sell")?.price ?? null;
  const liquidityResistance = liquidityLevels.find((level) => level.side === "buy")?.price ?? null;

  return {
    support: finiteNumberOrNull(liquiditySupport) ?? findNearestBelow(candles.map((candle) => candle.low), currentPrice),
    resistance: finiteNumberOrNull(liquidityResistance) ?? findNearestAbove(candles.map((candle) => candle.high), currentPrice),
  };
}

function findNearestBelow(values: number[], reference: number): number | null {
  let selected: number | null = null;
  for (const value of values) {
    if (!Number.isFinite(value) || value >= reference) {
      continue;
    }
    if (selected === null || value > selected) {
      selected = value;
    }
  }
  return selected;
}

function findNearestAbove(values: number[], reference: number): number | null {
  let selected: number | null = null;
  for (const value of values) {
    if (!Number.isFinite(value) || value <= reference) {
      continue;
    }
    if (selected === null || value < selected) {
      selected = value;
    }
  }
  return selected;
}

function calculateVolatilityState(candles: ProjectXMarketCandle[]): string | null {
  if (candles.length < 8) {
    return null;
  }

  const recentAtr = calculateAverageTrueRange(candles, Math.min(6, candles.length - 1));
  const baselineCandles = candles.slice(0, Math.max(2, candles.length - 6));
  const baselineAtr = calculateAverageTrueRange(baselineCandles, Math.min(24, baselineCandles.length - 1));
  if (recentAtr === null || baselineAtr === null || baselineAtr <= 0) {
    return null;
  }

  const ratio = recentAtr / baselineAtr;
  if (ratio >= 1.3) {
    return "Expanding";
  }
  if (ratio <= 0.75) {
    return "Compressed";
  }
  return "Normal";
}

function calculateVolumeState(candles: ProjectXMarketCandle[]): string | null {
  const candlesWithVolume = candles.filter((candle) => Number.isFinite(candle.volume) && candle.volume > 0);
  if (candlesWithVolume.length < 6) {
    return null;
  }

  const recent = averagePositive(candlesWithVolume.slice(-5).map((candle) => candle.volume));
  const baseline = averagePositive(candlesWithVolume.slice(0, -5).slice(-20).map((candle) => candle.volume));
  if (recent === null || baseline === null || baseline <= 0) {
    return null;
  }

  const ratio = recent / baseline;
  if (ratio >= 1.25) {
    return "Above average";
  }
  if (ratio <= 0.75) {
    return "Below average";
  }
  return "Normal";
}

function buildFallbackProbabilities(input: FallbackProbabilityInput): BotDirectionalProbabilities {
  const closes = input.history.map((candle) => candle.close);
  const shortLookback = Math.min(6, closes.length - 1);
  const mediumLookback = Math.min(24, closes.length - 1);
  const shortMove = input.currentPrice - closes[closes.length - 1 - shortLookback];
  const mediumMove = input.currentPrice - closes[closes.length - 1 - mediumLookback];
  const expectedMove = Math.max(input.expectedMove ?? Math.abs(input.priceChange), input.currentPrice * 0.0005, 0.0001);
  let levelTilt = 0;

  if (input.nearestSupport !== null && input.nearestResistance !== null && input.nearestResistance > input.nearestSupport) {
    const position = (input.currentPrice - input.nearestSupport) / (input.nearestResistance - input.nearestSupport);
    if (position <= 0.35) {
      levelTilt += 0.12;
    } else if (position >= 0.65) {
      levelTilt -= 0.12;
    }
  }

  const rawScore = clampNumber(
    (shortMove / expectedMove) * 0.24 +
      (mediumMove / (expectedMove * 3)) * 0.34 +
      (input.priceChange / expectedMove) * 0.16 +
      levelTilt,
    -1,
    1,
  );
  const trendStrength = Math.abs(rawScore);
  const sideways = clampNumber(44 - trendStrength * 24, 18, 52);
  const directionalPool = 100 - sideways;
  const bullish = directionalPool * clampNumber(0.5 + rawScore * 0.34, 0.12, 0.88);
  const bearish = directionalPool - bullish;

  return normalizeProbabilityValues({ bullish, bearish, sideways });
}

function normalizeProbabilities(
  probabilities: Partial<BotDirectionalProbabilities>,
  fallback?: BotDirectionalProbabilities,
): BotDirectionalProbabilities {
  const normalized = normalizeProbabilityValues({
    bullish: probabilityToPercent(finiteNumberOrNull(probabilities.bullish)),
    bearish: probabilityToPercent(finiteNumberOrNull(probabilities.bearish)),
    sideways: probabilityToPercent(finiteNumberOrNull(probabilities.sideways)),
  });
  if (normalized.bullish + normalized.bearish + normalized.sideways > 0) {
    return normalized;
  }
  return fallback ?? { bullish: 0, bearish: 0, sideways: 0 };
}

function normalizeProbabilityValues(probabilities: BotDirectionalProbabilities): BotDirectionalProbabilities {
  const bullish = clampNumber(probabilities.bullish, 0, 100);
  const bearish = clampNumber(probabilities.bearish, 0, 100);
  const sideways = clampNumber(probabilities.sideways, 0, 100);
  const total = bullish + bearish + sideways;
  if (total <= 0) {
    return { bullish: 0, bearish: 0, sideways: 0 };
  }
  return {
    bullish: (bullish / total) * 100,
    bearish: (bearish / total) * 100,
    sideways: (sideways / total) * 100,
  };
}

function probabilityToPercent(value: number | null): number {
  if (value === null) {
    return 0;
  }
  return value <= 1 ? value * 100 : value;
}

function inferBias(probabilities: BotDirectionalProbabilities): BotMarketBias {
  if (probabilities.sideways >= probabilities.bullish && probabilities.sideways >= probabilities.bearish) {
    return "neutral";
  }
  if (Math.abs(probabilities.bullish - probabilities.bearish) < 6) {
    return "neutral";
  }
  return probabilities.bullish > probabilities.bearish ? "bullish" : "bearish";
}

function normalizeMarketBias(value: unknown, probabilities: BotDirectionalProbabilities): BotMarketBias {
  if (value === "bullish" || value === "bearish" || value === "neutral") {
    return value;
  }
  return inferBias(probabilities);
}

function fallbackInvalidationLevel(
  marketBias: BotMarketBias,
  support: number | null,
  resistance: number | null,
): number | null {
  if (marketBias === "bullish") {
    return support;
  }
  if (marketBias === "bearish") {
    return resistance;
  }
  return support ?? resistance;
}

function buildFallbackSummary(input: {
  marketBias: BotMarketBias;
  currentPrice: number;
  priceChange: number;
  priceChangePercent: number | null;
  expectedMove: number | null;
  expectedMovePercent: number | null;
  support: number | null;
  resistance: number | null;
}): string {
  const directionText =
    input.marketBias === "bullish"
      ? "leans bullish"
      : input.marketBias === "bearish"
        ? "leans bearish"
        : "is balanced";
  const nextStepText =
    input.marketBias === "bullish"
      ? "A push toward resistance would need follow-through; losing support would weaken the setup."
      : input.marketBias === "bearish"
        ? "Further weakness puts support in focus; reclaiming resistance would weaken the bearish context."
        : "Range behavior is more likely until price accepts beyond nearby support or resistance.";

  return `Chart context ${directionText} around ${formatPrice(input.currentPrice)} after a ${formatPriceChange(
    input.priceChange,
    input.priceChangePercent,
  )} prior-candle move. Expected move is roughly ${formatExpectedMove(input.expectedMove, input.expectedMovePercent)}, with support near ${formatPrice(
    input.support,
  )} and resistance near ${formatPrice(input.resistance)}. ${nextStepText}`;
}

function buildFallbackReasoning(input: {
  evaluation: BotEvaluation;
  history: ProjectXMarketCandle[];
  currentPrice: number;
  priceChange: number;
  priceChangePercent: number | null;
  support: number | null;
  resistance: number | null;
  volatilityState: string | null;
  volumeState: string | null;
}): string[] {
  const trendLookback = Math.min(12, input.history.length - 1);
  const trendReference = input.history[input.history.length - 1 - trendLookback].close;
  const trendMove = input.currentPrice - trendReference;
  const trendPercent = trendReference !== 0 ? (trendMove / Math.abs(trendReference)) * 100 : null;

  return [
    `Price changed ${formatPriceChange(input.priceChange, input.priceChangePercent)} from the prior candle.`,
    `${trendLookback}-bar movement is ${formatPriceChange(trendMove, trendPercent)}, which sets the local directional tilt.`,
    `Nearest chart levels are support ${formatPrice(input.support)} and resistance ${formatPrice(input.resistance)}.`,
    `Volatility is ${input.volatilityState ?? "unknown"} and volume is ${input.volumeState ?? "unknown"} versus recent history.`,
    `Strategy decision was ${input.evaluation.decision.action}: ${input.evaluation.decision.reason}`,
  ].slice(0, MAX_REASONING_ITEMS);
}

function buildFallbackRiskNotes(input: {
  marketBias: BotMarketBias;
  support: number | null;
  resistance: number | null;
  invalidationLevel: number | null;
}): string[] {
  const invalidation =
    input.marketBias === "bullish"
      ? `Bullish context weakens below support near ${formatPrice(input.invalidationLevel)}.`
      : input.marketBias === "bearish"
        ? `Bearish context weakens above resistance near ${formatPrice(input.invalidationLevel)}.`
        : `Neutral context changes if price breaks beyond ${formatPrice(input.support)} support or ${formatPrice(input.resistance)} resistance.`;

  return [
    invalidation,
    "Fallback analysis is derived from candles because backend analysis was not returned.",
    "Use account risk limits and strategy stops before considering any live order.",
  ];
}

function normalizeText(value: string | null | undefined): string | null {
  const trimmed = typeof value === "string" ? value.trim() : "";
  return trimmed.length > 0 ? trimmed : null;
}

function normalizeTextList(values: string[] | null | undefined, fallback?: string[]): string[] {
  const normalized = Array.isArray(values)
    ? values.map((item) => normalizeText(item)).filter((item): item is string => item !== null)
    : [];
  return normalized.length > 0 ? normalized : fallback ?? [];
}

function formatStateLabel(value: string | null | undefined): string | null {
  const normalized = normalizeText(value);
  if (!normalized) {
    return null;
  }
  return normalized
    .replace(/[_-]+/g, " ")
    .split(" ")
    .filter(Boolean)
    .map((word) => `${word.charAt(0).toUpperCase()}${word.slice(1).toLowerCase()}`)
    .join(" ");
}

function averagePositive(values: number[]): number | null {
  const finiteValues = values.filter((value) => Number.isFinite(value) && value > 0);
  if (finiteValues.length === 0) {
    return null;
  }
  return finiteValues.reduce((sum, value) => sum + value, 0) / finiteValues.length;
}

function finiteNumberOrNull(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function clampNumber(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function formatPrice(value: number | null): string {
  return value === null ? PRICE_FALLBACK : priceFormatter.format(value);
}

function formatPriceChange(value: number | null, percent: number | null): string {
  if (value === null) {
    return PRICE_FALLBACK;
  }
  const priceText = signedPriceFormatter.format(value);
  return percent === null ? priceText : `${priceText} (${signedPercentFormatter.format(percent)}%)`;
}

function formatExpectedMove(value: number | null, percent: number | null): string {
  if (value === null) {
    return PRICE_FALLBACK;
  }
  const moveText = `+/-${priceFormatter.format(Math.abs(value))}`;
  return percent === null ? moveText : `${moveText} (${percentFormatter.format(Math.abs(percent))}%)`;
}

function formatRatio(value: number | null, suffix = "R"): string {
  if (value === null || !Number.isFinite(value)) {
    return PRICE_FALLBACK;
  }
  return `${percentFormatter.format(value)}${suffix}`;
}

function formatGeneratedAt(value: string | null | undefined): string | null {
  if (!value) {
    return null;
  }
  const timestampMs = Date.parse(value);
  return Number.isFinite(timestampMs) ? generatedAtFormatter.format(new Date(timestampMs)) : null;
}

function marketBiasLabel(value: BotMarketBias): string {
  if (value === "bullish") {
    return "Bullish";
  }
  if (value === "bearish") {
    return "Bearish";
  }
  return "Neutral";
}

function marketBiasBadgeVariant(value: BotMarketBias): BadgeVariant {
  if (value === "bullish") {
    return "positive";
  }
  if (value === "bearish") {
    return "negative";
  }
  return "warning";
}

function tradeDecisionBadgeVariant(value: TradeEvaluationResult["decision"]): BadgeVariant {
  if (value === "take") {
    return "positive";
  }
  if (value === "avoid") {
    return "negative";
  }
  return "warning";
}

function marketBiasTextClassName(value: BotMarketBias): string {
  if (value === "bullish") {
    return "text-emerald-300";
  }
  if (value === "bearish") {
    return "text-rose-300";
  }
  return "text-amber-200";
}

function tradeGradeTextClassName(value: TradeEvaluationResult["grade"]): string {
  if (value === "A" || value === "B") {
    return "text-emerald-300";
  }
  if (value === "C") {
    return "text-amber-200";
  }
  return "text-rose-300";
}

function compactListDotClassName(tone: "positive" | "warning" | "neutral"): string {
  if (tone === "positive") {
    return "bg-emerald-300/80";
  }
  if (tone === "warning") {
    return "bg-amber-300/80";
  }
  return "bg-cyan-300/75";
}

function probabilityTextClassName(tone: ProbabilityTone): string {
  if (tone === "bullish") {
    return "text-emerald-300";
  }
  if (tone === "bearish") {
    return "text-rose-300";
  }
  return "text-amber-200";
}

function probabilityBarClassName(tone: ProbabilityTone): string {
  if (tone === "bullish") {
    return "bg-emerald-400";
  }
  if (tone === "bearish") {
    return "bg-rose-400";
  }
  return "bg-amber-300";
}

function priceChangeClassName(value: number | null): string {
  if (value === null || value === 0) {
    return "font-mono text-slate-100";
  }
  return value > 0 ? "font-mono text-emerald-300" : "font-mono text-rose-300";
}

const contextAsOfFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
});

function MarketContextSection({ context, snapshot }: { context: MarketContext; snapshot: BotMarketSnapshot }) {
  const asOfText = context.asOfTimestamp
    ? `${contextAsOfFormatter.format(new Date(context.asOfTimestamp))} ET`
    : null;
  const vwapValue =
    context.vwap !== null
      ? `${priceFormatter.format(context.vwap)}${
          context.vwapDistance !== null ? ` (${signedPriceFormatter.format(context.vwapDistance)})` : ""
        }`
      : PRICE_FALLBACK;
  const atrValue =
    context.atr !== null
      ? `${priceFormatter.format(context.atr)}${
          context.atrPercent !== null ? ` (${percentFormatter.format(context.atrPercent)}%)` : ""
        }`
      : PRICE_FALLBACK;
  const relVolValue =
    context.relativeVolume !== null
      ? `${context.relativeVolume.toFixed(2)}x${context.volumeState ? ` ${context.volumeState}` : ""}`
      : "Unknown";
  const sessionRangeValue =
    context.sessionHigh !== null && context.sessionLow !== null
      ? `${priceFormatter.format(context.sessionLow)} - ${priceFormatter.format(context.sessionHigh)}`
      : PRICE_FALLBACK;
  const priorCloseValue =
    context.priorSessionClose !== null
      ? `${priceFormatter.format(context.priorSessionClose)}${
          context.sessionChangePercent !== null
            ? ` (${signedPercentFormatter.format(context.sessionChangePercent)}%)`
            : ""
        }`
      : PRICE_FALLBACK;

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950/45 p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500">
          Market context - {timeframeLabel(snapshot.unit, snapshot.unitNumber)} chart
        </p>
        {asOfText ? <span className="text-[11px] text-slate-500">as of {asOfText} - live from chart data</span> : null}
      </div>
      {context.trends.length > 0 ? (
        <div className="mb-2.5 flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] uppercase tracking-wide text-slate-500">Trend</span>
          {context.trends.map((trend) => (
            <TrendChip key={trend.label} trend={trend} />
          ))}
        </div>
      ) : null}
      <div className="grid grid-cols-2 gap-2.5 md:grid-cols-4">
        <ContextMetric label="VWAP (dist)" value={vwapValue} valueClassName="font-mono text-pink-200" />
        <ContextMetric
          label="ATR / volatility"
          value={context.volatilityState ? `${atrValue} ${context.volatilityState}` : atrValue}
          valueClassName="font-mono text-slate-100"
        />
        <ContextMetric label="Rel volume" value={relVolValue} valueClassName="font-mono text-slate-100" />
        <ContextMetric label="Session range" value={sessionRangeValue} valueClassName="font-mono text-slate-100" />
        <ContextMetric label="Prior close" value={priorCloseValue} valueClassName="font-mono text-slate-100" />
        <ContextMetric
          label="Support"
          value={formatPrice(context.nearestSupport)}
          valueClassName="font-mono text-emerald-300"
        />
        <ContextMetric
          label="Resistance"
          value={formatPrice(context.nearestResistance)}
          valueClassName="font-mono text-rose-300"
        />
        <ContextMetric
          label="Last price"
          value={formatPrice(context.lastPrice)}
          valueClassName="font-mono text-cyan-200"
        />
      </div>
    </div>
  );
}

function TrendChip({ trend }: { trend: TimeframeTrend }) {
  const arrow = trend.direction === "up" ? "▲" : trend.direction === "down" ? "▼" : "→";
  const tone =
    trend.direction === "up"
      ? "border-emerald-400/35 bg-emerald-400/10 text-emerald-300"
      : trend.direction === "down"
        ? "border-rose-400/35 bg-rose-400/10 text-rose-300"
        : "border-slate-700 bg-slate-900/60 text-slate-300";
  return (
    <span
      className={cn("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono text-[11px] font-semibold", tone)}
      title={`${trend.label} trend ${trend.direction} - strength ${Math.round(trend.strength * 100)}% over ${trend.bars} bars`}
    >
      {trend.label} {arrow}
    </span>
  );
}

function ContextMetric({
  label,
  value,
  valueClassName,
}: {
  label: string;
  value: string;
  valueClassName?: string;
}) {
  return (
    <div className="min-w-0 rounded-lg border border-slate-800/80 bg-slate-950/55 px-2.5 py-2">
      <p className="truncate text-[10px] uppercase tracking-wide text-slate-500">{label}</p>
      <p className={cn("mt-0.5 truncate text-[13px] font-semibold text-slate-100", valueClassName)} title={value}>
        {value}
      </p>
    </div>
  );
}

/**
 * How far behind the live chart the evaluation is, measured in bot-timeframe
 * bars between the evaluation's last analyzed candle and the latest closed
 * candle on the chart.
 */
function computeEvaluationStaleness(
  evaluation: BotEvaluation | null,
  snapshot: BotMarketSnapshot | null,
  bot: BotConfig | null,
): EvaluationStaleness | null {
  if (!evaluation || !snapshot || !bot) {
    return null;
  }

  const evaluationMs = evaluationCandleTimestampMs(evaluation);
  if (evaluationMs === null) {
    return null;
  }

  let latestClosedMs: number | null = null;
  let latestClose: number | null = null;
  for (const candle of snapshot.candles) {
    const candleMs = Date.parse(candle.timestamp);
    if (!Number.isFinite(candleMs) || candle.is_partial) {
      continue;
    }
    if (latestClosedMs === null || candleMs > latestClosedMs) {
      latestClosedMs = candleMs;
      latestClose = Number.isFinite(candle.close) ? candle.close : null;
    }
  }
  if (latestClosedMs === null) {
    return null;
  }

  const intervalMs = intervalSecondsFor(bot.timeframe_unit, bot.timeframe_unit_number) * 1000;
  if (intervalMs <= 0) {
    return null;
  }

  const barsBehind = Math.max(0, Math.floor((latestClosedMs - evaluationMs) / intervalMs));
  const referenceNow = snapshot.lastPrice ?? latestClose;
  const evaluationPrice =
    finiteNumberOrNull(evaluation.analysis?.current_price ?? null) ?? finiteNumberOrNull(evaluation.decision.price);
  return {
    barsBehind,
    isStale: barsBehind >= STALE_EVALUATION_BARS,
    priceDrift: referenceNow !== null && evaluationPrice !== null ? referenceNow - evaluationPrice : null,
  };
}

function evaluationCandleTimestampMs(evaluation: BotEvaluation): number | null {
  const analysisTimestamp = evaluation.analysis?.candle_timestamp;
  if (analysisTimestamp) {
    const ms = Date.parse(analysisTimestamp);
    if (Number.isFinite(ms)) {
      return ms;
    }
  }

  let latest: number | null = null;
  for (const candle of evaluation.candles) {
    if (candle.is_partial) {
      continue;
    }
    const ms = Date.parse(candle.timestamp);
    if (Number.isFinite(ms) && (latest === null || ms > latest)) {
      latest = ms;
    }
  }
  if (latest !== null) {
    return latest;
  }

  const decisionTimestamp = evaluation.decision.candle_timestamp;
  if (decisionTimestamp) {
    const ms = Date.parse(decisionTimestamp);
    if (Number.isFinite(ms)) {
      return ms;
    }
  }
  return null;
}

function timeframeAbbreviation(unit: BotConfig["timeframe_unit"]): string {
  if (unit === "second") {
    return "s";
  }
  if (unit === "minute") {
    return "m";
  }
  if (unit === "hour") {
    return "h";
  }
  if (unit === "day") {
    return "d";
  }
  if (unit === "week") {
    return "w";
  }
  return "mo";
}
