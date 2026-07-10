import { useMemo, type ReactNode } from "react";

import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Progress } from "../../components/ui/Progress";
import { Skeleton } from "../../components/ui/Skeleton";
import { cn } from "../../components/ui/cn";
import type {
  BotConfig,
  BotDataQualityStatus,
  BotEvaluation,
  BotMarketBias,
  TradeEvaluationDimension,
  TradeEvaluationResult,
} from "../../lib/types";
import {
  buildDisplayAnalysis,
  SCENARIO_WEIGHT_DISCLAIMER,
  SCENARIO_WEIGHT_LABELS,
  type DisplayAnalysis,
} from "./botAnalysisContract";
import { intervalSecondsFor } from "./botCandleGaps";
import type { BotMarketSnapshot } from "./botMarketContext";

interface BotAnalysisPanelProps {
  bot: BotConfig | null;
  evaluation: BotEvaluation | null;
  marketSnapshot?: BotMarketSnapshot | null;
  loading?: boolean;
  onEvaluate?: () => void;
}

type BadgeVariant = "positive" | "negative" | "neutral" | "accent" | "warning";
type Tone = "positive" | "negative" | "neutral" | "warning";

const priceFormatter = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
const percentFormatter = new Intl.NumberFormat("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 1 });
const timestampFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZoneName: "short",
});

export function BotAnalysisPanel({
  bot,
  evaluation,
  marketSnapshot = null,
  loading = false,
  onEvaluate,
}: BotAnalysisPanelProps) {
  const analysis = useMemo(() => buildDisplayAnalysis(evaluation), [evaluation]);
  const liveBarsBehind = useMemo(
    () => barsBehindLiveChart(analysis, bot ? marketSnapshot : null, bot),
    [analysis, bot, marketSnapshot],
  );
  const isStale = Boolean(
    analysis?.provenance.is_stale || analysis?.dataQuality.status === "stale" || (liveBarsBehind ?? 0) > 0,
  );
  const botLabel = bot?.symbol ?? bot?.contract_id ?? "Bot";

  return (
    <Card className="min-w-0">
      <CardHeader className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle>Evaluation &amp; market analysis</CardTitle>
            <CardDescription>{bot ? `${botLabel} — closed-candle decision context` : "Select a bot to evaluate"}</CardDescription>
          </div>
          {analysis ? (
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={isStale ? "warning" : "positive"}>{isStale ? "Stale" : "Fresh"}</Badge>
              <Badge variant={qualityBadgeVariant(analysis.dataQuality.status)}>
                {labelize(analysis.dataQuality.status)} data · {Math.round(analysis.dataQuality.confidence)}/100
              </Badge>
              <Badge variant="neutral">{labelize(analysis.marketRegime)} regime</Badge>
              <Badge variant={analysis.source === "backend" ? "accent" : "warning"}>
                {analysis.source === "backend" ? "Canonical backend" : "Local fallback analysis"}
              </Badge>
            </div>
          ) : null}
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {loading ? (
          <LoadingState />
        ) : !bot ? (
          <EmptyState title="No bot selected" description="Select a bot to load its evaluation context." />
        ) : !evaluation ? (
          <EmptyState
            title="No evaluation yet"
            description="Run Evaluate to calculate the versioned market read from closed candles."
            action={onEvaluate ? <Button onClick={onEvaluate}>Evaluate bot</Button> : null}
          />
        ) : !analysis ? (
          <EmptyState
            title="Insufficient closed-candle data"
            description="Neither canonical backend analysis nor enough closed bars for a local fallback were available. Partial candles are never used."
            action={onEvaluate ? <Button onClick={onEvaluate}>Evaluate again</Button> : null}
          />
        ) : (
          <AnalysisContent analysis={analysis} isStale={isStale} liveBarsBehind={liveBarsBehind} />
        )}
      </CardContent>
    </Card>
  );
}

function AnalysisContent({
  analysis,
  isStale,
  liveBarsBehind,
}: {
  analysis: DisplayAnalysis;
  isStale: boolean;
  liveBarsBehind: number | null;
}) {
  const warnings = uniqueStrings([...analysis.dataQuality.warnings, ...analysis.riskNotes]);
  return (
    <>
      <section className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="rounded-xl border border-slate-800 bg-slate-950/45 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className={cn("text-2xl font-semibold", biasTextClass(analysis.marketBias))}>
              {labelize(analysis.marketBias)} bias
            </span>
            <Badge variant={biasBadgeVariant(analysis.marketBias)}>{Math.round(analysis.trendStrength)}/100 trend</Badge>
          </div>
          <p className="mt-2 text-sm leading-6 text-slate-300">{analysis.summary}</p>
          {isStale ? (
            <p className="mt-3 rounded-lg border border-amber-400/20 bg-amber-400/5 px-3 py-2 text-xs leading-5 text-amber-200">
              This evaluation is stale
              {liveBarsBehind !== null && liveBarsBehind > 0
                ? ` — the chart has ${liveBarsBehind} newer closed bar${liveBarsBehind === 1 ? "" : "s"}`
                : ""}
              . Rerun Evaluate before relying on the read.
            </p>
          ) : null}
        </div>

        <div className="space-y-2 rounded-xl border border-slate-800 bg-slate-950/45 p-3">
          <ScenarioWeight label={SCENARIO_WEIGHT_LABELS.bullish} value={analysis.scenarioWeights.bullish} tone="positive" />
          <ScenarioWeight label={SCENARIO_WEIGHT_LABELS.bearish} value={analysis.scenarioWeights.bearish} tone="negative" />
          <ScenarioWeight label={SCENARIO_WEIGHT_LABELS.sideways} value={analysis.scenarioWeights.sideways} tone="warning" />
          <p className="font-mono text-[9px] text-slate-600">method: {analysis.probabilityMethod}</p>
          <p className="pt-1 text-[10px] leading-4 text-slate-500">{SCENARIO_WEIGHT_DISCLAIMER}</p>
        </div>
      </section>

      <ProvenanceSection analysis={analysis} />
      <FeatureSection analysis={analysis} />
      <DimensionSection analysis={analysis} />

      {analysis.tradeEvaluation ? <TradeEvaluationSummary evaluation={analysis.tradeEvaluation} /> : null}

      <section className="rounded-xl border border-amber-400/20 bg-amber-950/10 p-3">
        <p className="text-[11px] font-medium uppercase tracking-wide text-amber-200/80">What invalidates the setup?</p>
        <p className="mt-1 text-sm leading-6 text-amber-100">{analysis.invalidationReason}</p>
      </section>

      <section className="grid gap-3 lg:grid-cols-3">
        <TextList title="Bullish score drivers" items={analysis.scoreDrivers.bullish} tone="positive" />
        <TextList title="Bearish score drivers" items={analysis.scoreDrivers.bearish} tone="negative" />
        <TextList title="Neutral score drivers" items={analysis.scoreDrivers.neutral} tone="neutral" />
      </section>

      <section className="grid gap-3 lg:grid-cols-2">
        <TextList title="Why this read" items={analysis.reasoning} tone="neutral" />
        <TextList title="Warnings & execution risks" items={warnings} tone="warning" />
      </section>

      <MissingInformation analysis={analysis} />
    </>
  );
}

function ProvenanceSection({ analysis }: { analysis: DisplayAnalysis }) {
  const provenance = analysis.provenance;
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-950/45 p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500">Freshness &amp; provenance</p>
        <span className="font-mono text-[10px] text-slate-500">{analysis.analysisVersion}</span>
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 lg:grid-cols-6">
        <Metric label="Latest closed bar" value={formatTimestamp(provenance.latest_candle_timestamp)} />
        <Metric label="Data age" value={formatDuration(provenance.data_age_seconds)} />
        <Metric label="Closed candles" value={String(provenance.closed_candle_count)} />
        <Metric label="Partial excluded" value={String(provenance.partial_candle_count)} />
        <Metric label="Detected gaps" value={String(provenance.gap_count)} />
        <Metric label="Timeframe" value={provenance.timeframe.label} />
      </div>
    </section>
  );
}

function FeatureSection({ analysis }: { analysis: DisplayAnalysis }) {
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-950/45 p-3">
      <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-slate-500">Deterministic market features</p>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Metric label="Current price" value={formatPrice(analysis.currentPrice)} />
        <Metric label="Trend" value={`${labelize(analysis.marketBias)} · ${Math.round(analysis.trendStrength)}/100`} />
        <Metric label="Regime" value={labelize(analysis.marketRegime)} />
        <Metric
          label="ATR / percentile"
          value={`${formatPrice(analysis.expectedMove)}${analysis.atrPercentile !== null ? ` · p${Math.round(analysis.atrPercentile)}` : ""}${analysis.volatilityState ? ` · ${analysis.volatilityState}` : ""}`}
        />
        <Metric
          label="Relative volume"
          value={analysis.relativeVolume !== null ? `${analysis.relativeVolume.toFixed(2)}x · ${analysis.volumeState ?? "unknown"}` : "Missing"}
        />
        <Metric
          label="VWAP location"
          value={`${labelize(analysis.vwapLocation)}${analysis.vwap !== null ? ` · ${formatPrice(analysis.vwap)}` : ""}`}
        />
        <Metric label="MTF alignment" value={labelize(analysis.multiTimeframeStatus)} />
        <Metric label="Nearby support" value={formatPrice(analysis.nearestSupport)} />
        <Metric label="Nearby resistance" value={formatPrice(analysis.nearestResistance)} />
      </div>
    </section>
  );
}

function DimensionSection({ analysis }: { analysis: DisplayAnalysis }) {
  return (
    <section className="grid grid-cols-2 gap-2 md:grid-cols-4">
      <DimensionCard
        label="Setup quality"
        score={analysis.setupQuality?.score ?? null}
        state={analysis.setupQuality?.label ?? "not scored"}
        drivers={analysis.setupQuality?.drivers ?? []}
      />
      <DimensionCard
        label="Market direction"
        score={analysis.marketBiasDimension.strength}
        state={analysis.marketBiasDimension.direction}
        drivers={analysis.marketBiasDimension.drivers}
      />
      <DimensionCard
        label="Execution risk"
        score={analysis.executionRisk?.risk_score ?? null}
        state={analysis.executionRisk?.label ?? "not scored"}
        drivers={analysis.executionRisk?.drivers ?? []}
        inverse
      />
      <DimensionCard
        label="Data confidence"
        score={analysis.dataConfidence.score}
        state={analysis.dataConfidence.label}
        drivers={analysis.dataConfidence.drivers}
      />
    </section>
  );
}

function DimensionCard({
  label,
  score,
  state,
  drivers,
  inverse = false,
}: {
  label: string;
  score: number | null;
  state: string;
  drivers: string[];
  inverse?: boolean;
}) {
  const value = score === null ? null : clamp(score, 0, 100);
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950/45 p-3" title={drivers.join("\n") || undefined}>
      <p className="text-[10px] uppercase tracking-wide text-slate-500">{label}</p>
      <div className="mt-1 flex items-baseline justify-between gap-2">
        <span className="text-sm font-semibold text-slate-100">{labelize(state)}</span>
        <span className="font-mono text-xs text-slate-400">{value === null ? "—" : `${Math.round(value)}/100`}</span>
      </div>
      {value !== null ? (
        <Progress
          value={value}
          className="mt-2 h-1.5 bg-slate-900"
          indicatorClassName={inverse ? "bg-amber-400" : "bg-cyan-400"}
        />
      ) : null}
    </div>
  );
}

function TradeEvaluationSummary({ evaluation }: { evaluation: TradeEvaluationResult }) {
  const features = evaluation.features;
  const categories = evaluation.category_awarded_points ?? evaluation.category_scores;
  const categoryRows = Object.entries(categories).map(([name, awarded]) => ({
    name,
    awarded,
    maximum: evaluation.category_maximums?.[name] ?? null,
  }));
  const appliedCaps = (evaluation.caps ?? []).filter((cap) => cap.applied);
  const geometryIssues = features.geometry_issues ?? [];

  return (
    <section className="rounded-xl border border-cyan-400/20 bg-cyan-950/10 p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-wide text-cyan-200/80">Trade-plan score</p>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <span className="text-2xl font-semibold text-cyan-200">{Math.round(evaluation.total_score)}/100</span>
            <Badge variant={decisionVariant(evaluation.decision)}>{labelize(evaluation.decision)}</Badge>
            <Badge variant="neutral">Grade {evaluation.grade}</Badge>
            {evaluation.scoring_model_version ? <Badge variant="neutral">{evaluation.scoring_model_version}</Badge> : null}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <MiniMetric label="Risk" value={`${formatNumber(features.risk_points)} pts`} />
          <MiniMetric label="Reward" value={`${formatNumber(features.reward_points)} pts`} />
          <MiniMetric label="R multiple" value={formatR(features.r_multiple ?? features.risk_reward_ratio)} />
          <MiniMetric label="Break-even" value={formatPercent(features.breakeven_win_rate)} />
          <MiniMetric label="Dollar risk" value={formatCurrency(features.estimated_dollar_risk)} />
          <MiniMetric label="Risk ticks" value={formatNumber(features.risk_ticks)} />
          <MiniMetric label="Account risk" value={formatPercent(features.account_risk_percent)} />
          <MiniMetric label="Data confidence" value={evaluation.data_confidence ? labelize(evaluation.data_confidence) : "Missing"} />
        </div>
      </div>

      <p className="mt-3 text-sm leading-6 text-slate-300">{evaluation.summary}</p>

      {features.geometry_valid === false || geometryIssues.length > 0 ? (
        <div className="mt-3 rounded-lg border border-rose-400/20 bg-rose-400/5 px-3 py-2 text-xs text-rose-200">
          Invalid trade geometry: {geometryIssues.join("; ") || "entry, stop, and target are not directionally valid."}
        </div>
      ) : null}

      {evaluation.evaluation_dimensions ? (
        <div className="mt-3 grid gap-2 sm:grid-cols-3">
          <EvaluationDimension label="Setup quality" dimension={evaluation.evaluation_dimensions.setup_quality} />
          <EvaluationDimension label="Direction / bias" dimension={evaluation.evaluation_dimensions.market_direction_bias} />
          <EvaluationDimension label="Execution risk control" dimension={evaluation.evaluation_dimensions.execution_risk} />
        </div>
      ) : null}

      {categoryRows.length > 0 ? (
        <div className="mt-3 rounded-lg border border-slate-800/80 p-2.5">
          <p className="mb-2 text-[10px] uppercase tracking-wide text-slate-500">Category points</p>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs md:grid-cols-3">
            {categoryRows.map((row) => (
              <div key={row.name} className="flex justify-between gap-2">
                <span className="truncate text-slate-400">{labelize(row.name)}</span>
                <span className="font-mono text-slate-200">{row.maximum === null ? row.awarded : `${row.awarded}/${row.maximum}`}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="mt-3 grid gap-3 lg:grid-cols-2">
        <TextList title="Top positive score drivers" items={evaluation.top_positive_drivers ?? evaluation.positives} tone="positive" compact />
        <TextList title="Top negative score drivers" items={evaluation.top_negative_drivers ?? evaluation.warnings} tone="negative" compact />
      </div>

      {(evaluation.penalties?.length ?? 0) > 0 || appliedCaps.length > 0 ? (
        <div className="mt-3 grid gap-3 lg:grid-cols-2">
          <TextList
            title="Penalties"
            items={(evaluation.penalties ?? []).map((penalty) => `−${penalty.points_deducted} ${labelize(penalty.category ?? "uncategorized")}: ${penalty.reason}`)}
            tone="warning"
            compact
          />
          <TextList
            title="Applied score caps"
            items={appliedCaps.map((cap) => `${cap.score_before} → ${cap.score_after} (max ${cap.maximum}): ${cap.reason}`)}
            tone="warning"
            compact
          />
        </div>
      ) : null}

      {(evaluation.missing_inputs?.length ?? 0) > 0 ? (
        <p className="mt-3 text-xs leading-5 text-amber-200">
          Missing evaluation inputs: {evaluation.missing_inputs!.map(labelize).join(", ")}
        </p>
      ) : null}
    </section>
  );
}

function EvaluationDimension({ label, dimension }: { label: string; dimension: TradeEvaluationDimension }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/40 p-2.5">
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="text-slate-400">{label}</span>
        <span className="font-mono text-slate-200">{dimension.awarded_points}/{dimension.maximum_points}</span>
      </div>
      <Progress value={clamp(dimension.score_percent, 0, 100)} className="mt-2 h-1.5 bg-slate-900" indicatorClassName="bg-cyan-400" />
    </div>
  );
}

function MissingInformation({ analysis }: { analysis: DisplayAnalysis }) {
  const missing = analysis.dataQuality.missing_inputs;
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-950/45 p-3">
      <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500">Missing information</p>
      {missing.length > 0 ? (
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {missing.map((item) => (
            <li key={item}><Badge variant="warning">{labelize(item)}</Badge></li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-sm text-emerald-300">No required analysis inputs are missing.</p>
      )}
      <p className="mt-2 text-[11px] leading-5 text-slate-500">
        No account, news, macro, or order-book context is implied unless it appears explicitly above.
      </p>
    </section>
  );
}

function ScenarioWeight({ label, value, tone }: { label: string; value: number; tone: Tone }) {
  return (
    <div className="space-y-1.5">
      <div className="flex justify-between gap-3 text-xs">
        <span className={toneTextClass(tone)}>{label}</span>
        <span className="font-mono text-slate-300">{value}%</span>
      </div>
      <Progress value={value} className="h-2 bg-slate-900" indicatorClassName={toneBarClass(tone)} />
    </div>
  );
}

function TextList({
  title,
  items,
  tone,
  compact = false,
}: {
  title: string;
  items: string[];
  tone: Tone;
  compact?: boolean;
}) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950/45 p-3">
      <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-slate-500">{title}</p>
      {items.length > 0 ? (
        <ul className={cn("space-y-1.5 text-slate-300", compact ? "text-xs leading-5" : "text-sm leading-5")}>
          {items.map((item, index) => (
            <li key={`${title}-${index}-${item}`} className="flex gap-2">
              <span className={cn("mt-2 h-1.5 w-1.5 shrink-0 rounded-full", toneDotClass(tone))} />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-slate-500">No {title.toLowerCase()} returned.</p>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-lg border border-slate-800/80 bg-slate-950/55 px-2.5 py-2">
      <p className="truncate text-[10px] uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-0.5 truncate text-[13px] font-semibold text-slate-100" title={value}>{value}</p>
    </div>
  );
}

function MiniMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/45 px-2 py-1.5">
      <p className="text-[9px] uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-0.5 whitespace-nowrap font-mono text-xs text-slate-100">{value}</p>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="space-y-3">
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_20rem]"><Skeleton className="h-32" /><Skeleton className="h-32" /></div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">{Array.from({ length: 8 }, (_, index) => <Skeleton key={index} className="h-16" />)}</div>
    </div>
  );
}

function EmptyState({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-800 bg-slate-950/35 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><p className="font-semibold text-slate-200">{title}</p><p className="mt-1 text-sm text-slate-400">{description}</p></div>
        {action}
      </div>
    </div>
  );
}

function barsBehindLiveChart(
  analysis: DisplayAnalysis | null,
  snapshot: BotMarketSnapshot | null,
  bot: BotConfig | null,
): number | null {
  if (!analysis || !snapshot || !bot || !analysis.provenance.latest_candle_timestamp) return null;
  const analyzedMs = Date.parse(analysis.provenance.latest_candle_timestamp);
  const latestClosedMs = snapshot.candles.reduce<number | null>((latest, candle) => {
    const timestamp = Date.parse(candle.timestamp);
    return candle.is_partial || !Number.isFinite(timestamp) ? latest : Math.max(latest ?? timestamp, timestamp);
  }, null);
  if (!Number.isFinite(analyzedMs) || latestClosedMs === null) return null;
  const intervalMs = intervalSecondsFor(bot.timeframe_unit, bot.timeframe_unit_number) * 1000;
  return intervalMs > 0 ? Math.max(0, Math.floor((latestClosedMs - analyzedMs) / intervalMs)) : null;
}

function formatPrice(value: number | null): string {
  return value === null || !Number.isFinite(value) ? "Missing" : priceFormatter.format(value);
}

function formatNumber(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value) ? "—" : percentFormatter.format(value);
}

function formatR(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value) ? "—" : `${percentFormatter.format(value)}R`;
}

function formatPercent(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value) ? "—" : `${percentFormatter.format(value)}%`;
}

function formatCurrency(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value) ? "—" : `$${priceFormatter.format(value)}`;
}

function formatTimestamp(value: string | null): string {
  if (!value) return "Missing";
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestampFormatter.format(new Date(timestamp)) : "Invalid";
}

function formatDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "Unknown";
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

function labelize(value: string): string {
  return value.replace(/[_-]+/g, " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

function uniqueStrings(values: string[]): string[] {
  return Array.from(new Set(values.map((value) => value.trim()).filter(Boolean)));
}

function qualityBadgeVariant(status: BotDataQualityStatus): BadgeVariant {
  if (status === "good") return "positive";
  if (status === "limited") return "warning";
  return "negative";
}

function biasBadgeVariant(bias: BotMarketBias): BadgeVariant {
  return bias === "bullish" ? "positive" : bias === "bearish" ? "negative" : "warning";
}

function decisionVariant(decision: TradeEvaluationResult["decision"]): BadgeVariant {
  return decision === "take" ? "positive" : decision === "avoid" ? "negative" : "warning";
}

function biasTextClass(bias: BotMarketBias): string {
  return bias === "bullish" ? "text-emerald-300" : bias === "bearish" ? "text-rose-300" : "text-amber-200";
}

function toneTextClass(tone: Tone): string {
  return tone === "positive" ? "text-emerald-300" : tone === "negative" ? "text-rose-300" : tone === "warning" ? "text-amber-200" : "text-cyan-200";
}

function toneBarClass(tone: Tone): string {
  return tone === "positive" ? "bg-emerald-400" : tone === "negative" ? "bg-rose-400" : tone === "warning" ? "bg-amber-300" : "bg-cyan-400";
}

function toneDotClass(tone: Tone): string {
  return tone === "positive" ? "bg-emerald-300/80" : tone === "negative" ? "bg-rose-300/80" : tone === "warning" ? "bg-amber-300/80" : "bg-cyan-300/75";
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}
