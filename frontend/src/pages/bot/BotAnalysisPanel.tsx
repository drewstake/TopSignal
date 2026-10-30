import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Skeleton } from "../../components/ui/Skeleton";
import { DEMO_AS_OF_ISO } from "../../lib/demoScenario";
import type { BotAnalysis, BotCollectedContext, BotConfig, BotEvaluation } from "../../lib/types";
import { buildDisplayAnalysis, currentAnalysisFreshness, type DisplayAnalysis } from "./botAnalysisContract";
import { buildMarketContext, candleEndMs, isConfirmedClosedCandle, type BotMarketSnapshot } from "./botMarketContext";
import type { BotChartMarket } from "./botChartData";
import { buildMarketExplanation, levelDistance, regimeMeaning } from "./botMarketExplanation";
import { BotProbabilisticPanel } from "./BotProbabilisticPanel";

import "./BotAnalysisPanel.css";

interface BotAnalysisPanelProps {
  bot: BotConfig | null;
  evaluation: BotEvaluation | null;
  marketSnapshot?: BotMarketSnapshot | null;
  market?: BotChartMarket | null;
  loading?: boolean;
  demoMode?: boolean;
  onEvaluate?: () => void;
}
const priceFormatter = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const timestampFormatter = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit", timeZoneName: "short" });

export function BotAnalysisPanel({ bot, evaluation, marketSnapshot = null, market = null, loading = false, demoMode = false, onEvaluate }: BotAnalysisPanelProps) {
  const [wallClockMs, setWallClockMs] = useState(Date.now);
  const nowMs = demoMode ? Date.parse(DEMO_AS_OF_ISO) : wallClockMs;
  useEffect(() => {
    if (demoMode) return;
    const timer = window.setInterval(() => setWallClockMs(Date.now()), 15_000);
    return () => window.clearInterval(timer);
  }, [demoMode]);
  // A previous bot's response or chart must never supply this bot's explanation.
  const selectedEvaluation = bot && evaluation?.config.id === bot.id ? evaluation : null;
  const analysis = useMemo(() => buildDisplayAnalysis(selectedEvaluation, nowMs), [selectedEvaluation, nowMs]);
  const snapshot = matchingMarketSnapshot(marketSnapshot, bot ?? market) ? marketSnapshot : null;
  const raw = selectedEvaluation?.analysis;
  const freshness = analysis ? currentAnalysisFreshness(analysis, nowMs) : null;
  const newerBars = analysis ? newerClosedBars(analysis, snapshot, nowMs) : 0;
  const freshnessStatus = newerBars ? "stale" : freshness?.status;
  const hasRead = Boolean(analysis && analysis.provenance.closed_candle_count >= (analysis.provenance.minimum_feature_bars ?? 10));
  return <Card className="market-analysis-panel min-w-0">
    <CardHeader className="space-y-3"><div className="flex flex-wrap items-start justify-between gap-3">
      <div><CardTitle>Evaluation &amp; market analysis</CardTitle><CardDescription>{bot ? `${bot.symbol ?? bot.contract_id} · closed-candle market read and bot decision` : "Market context from the chart · No trading account required"}</CardDescription></div>
      <div className="flex flex-wrap items-center gap-2">
      {analysis && <>
        <Badge variant={freshnessStatus === "stale" ? "warning" : "neutral"}>{freshnessLabel(freshnessStatus)}</Badge>
        <Badge variant={analysis.dataQuality.status === "good" ? "positive" : "warning"}>Candles: {analysis.dataQuality.status === "good" ? "good quality" : labelize(analysis.dataQuality.status).toLowerCase()}</Badge>
        <Badge variant="neutral">{raw?.context_coverage?.missing.length || raw?.context_coverage?.limited.length ? "Context incomplete" : raw?.context_coverage ? "Context recorded" : "Context coverage unverified"}</Badge>
      </>}
      {bot && onEvaluate && !demoMode && <Button onClick={onEvaluate} disabled={loading} aria-busy={loading}>
        {loading ? "Evaluating…" : !selectedEvaluation ? "Evaluate bot" : hasRead ? "Refresh evaluation" : "Retry evaluation"}
      </Button>}
      </div>
    </div></CardHeader>
    <CardContent className="space-y-4">
      {loading ? <Skeleton className="h-56" /> : !bot ? <>
        <ChartMarketAnalysis snapshot={snapshot} nowMs={nowMs} />
        <p className="text-xs text-app-muted">Bot-specific decisions and account risk checks require a configured bot. Viewing market data does not run a bot or place orders.</p>
      </> : !selectedEvaluation ? <>
        <ChartMarketAnalysis snapshot={snapshot} nowMs={nowMs} />
        <EmptyState title="No evaluation yet" description={demoMode ? "This demo snapshot has no saved evaluation. Live evaluation is disabled." : "Evaluate this bot to explain its latest closed-candle signal and checks."} />
      </> : <>
          {analysis && hasRead ? <>
            <div className="analysis-overview">
              <section className="min-w-0 rounded-xl border border-cyan-400/20 bg-cyan-950/10 p-5">
                <p className="text-xs font-medium uppercase tracking-wider text-cyan-300">Market interpretation</p>
                <h3 className="mt-2 text-lg font-semibold leading-7">{raw?.explanation?.headline ?? legacyHeadline(analysis)}</h3>
                <p className="mt-2 text-sm leading-6 text-app-text-soft">{regimeMeaning(analysis.marketRegime, analysis.marketBias)}</p>
                <p className="mt-3 text-xs leading-5 text-app-muted">Based on the {analysis.provenance.timeframe.label} candle closed {formatTimestamp(candleClose(analysis))}. This describes the observed market; entry permission comes from the bot’s checks.</p>
                <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 border-t border-app-border pt-3 text-xs">
                  <span><span className="text-app-muted">Direction: </span>{analysis.marketBias === "neutral" ? "No clear direction" : labelize(analysis.marketBias)}</span>
                  <span><span className="text-app-muted">Movement strength: </span>{labelize(raw?.features?.trend.strength_label ?? "unavailable")}</span>
                  <span><span className="text-app-muted">Indicator agreement: </span>{agreementLabel(raw?.features?.trend.agreement)}</span>
                </div>
              </section>
              <BotDecisionSummary evaluation={selectedEvaluation} />
            </div>
            {freshnessStatus === "stale" && <p role="status" className="rounded-lg border border-amber-400/25 bg-amber-400/5 p-3 text-sm text-amber-200">This is a saved, stale evaluation{newerBars ? `; the matching chart has ${newerBars} newer closed bar${newerBars === 1 ? "" : "s"}` : ""}. Evaluate again to refresh the market read and bot checks.</p>}
            {freshnessStatus === "market_closed" && <p className="text-xs leading-5 text-app-muted">The scheduled market session is closed. The last completed candle is retained; closed-session time does not count as a feed delay. The configured entry window is checked separately.</p>}
            {freshnessStatus === "unavailable" && <p className="text-xs text-amber-200">The candle close time cannot be verified. Treat this as a recorded interpretation until a fresh evaluation is available.</p>}
            <EvaluationMeasurements analysis={analysis} raw={raw} />
            <section className="analysis-evidence">
              <EvidenceList title="What supports this read" items={raw?.explanation?.supporting_evidence ?? analysis.scoreDrivers[analysis.marketBias]} empty="No supporting evidence was returned." />
              <EvidenceList title="What conflicts with it" items={raw?.explanation?.conflicting_evidence ?? conflictingEvidence(analysis)} empty="No direct conflict reported by these indicators. Unmeasured context may disagree." />
            </section>
            <LevelPanel support={analysis.nearestSupport} resistance={analysis.nearestResistance} current={analysis.priceSource === "closed_bar" ? analysis.currentPrice : null} atr={analysis.expectedMove} timeframe={analysis.provenance.timeframe.label} vwap={analysis.vwap} direction={analysis.marketBias} />
          </> : <>
            <div className="grid gap-4 xl:grid-cols-2">
              <EmptyState title="No directional read yet" description={`This evaluation received ${analysis?.provenance.closed_candle_count ?? 0} closed ${analysis?.provenance.timeframe.label ?? ""} candles. At least ${analysis?.provenance.minimum_feature_bars ?? 10} are needed for the first feature set; partial candles are excluded.`} />
              <BotDecisionSummary evaluation={selectedEvaluation} />
            </div>
            {snapshot && <SeparateChartContext snapshot={snapshot} bot={bot} nowMs={nowMs} />}
          </>}
          <ContextCoverage analysis={raw} />
          <Details title="Technical details & source coverage">
            {analysis && <CalculationDetails analysis={analysis} raw={raw} ageSeconds={freshness?.ageSeconds ?? null} />}
            {raw?.collected_context && <div className="mt-5 border-t border-app-border pt-4"><CollectedContextDetails collected={raw.collected_context} /></div>}
            {snapshot?.lastPrice != null && <p className="mt-4 text-xs text-app-muted">Separate chart quote: {formatPrice(snapshot.lastPrice)} · not an input to this saved read. Quote event time is unavailable.</p>}
          </Details>
        </>}
    </CardContent>
  </Card>;
}

function Fact({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <div className="min-w-0 rounded-xl border border-app-border bg-app-bg/30 p-4"><p className="text-xs text-app-muted">{label}</p><p className="mt-2 break-words text-lg font-semibold tabular-nums">{value}</p><p className="mt-2 text-xs leading-5 text-app-muted">{detail}</p></div>;
}

function rangeLabel(state: string | null): string {
  return ({ low: "Cooling ranges", normal: "Stable ranges", elevated: "Expanding ranges", extreme: "Sharply expanding ranges" })[state ?? ""] ?? "Range change unavailable";
}

function volumeMeaning(ratio: number | null): string {
  if (ratio == null) return "Latest volume or prior-bar baseline missing";
  return `${ratio < 0.7 ? "Subdued" : ratio > 1.5 ? "Elevated" : "Typical"} participation vs prior bars; not buyer/seller direction`;
}

function EvaluationMeasurements({ analysis, raw }: { analysis: DisplayAnalysis; raw: BotAnalysis | null | undefined }) {
  const frames = raw?.features?.multi_timeframe_alignment.timeframes ?? [];
  return <section aria-label="Measured candle facts">
    <p className="mb-2 text-xs font-medium uppercase tracking-wider text-app-muted">Measured candle facts</p>
    <div className="analysis-measurements">
      <Fact label={analysis.priceSource === "decision" ? "Decision/reference price" : "Last analyzed close"} value={formatPrice(analysis.currentPrice)} detail={`${analysis.provenance.closed_candle_count} closed bars · ${analysis.provenance.partial_candle_count} excluded · ${analysis.provenance.gap_count} gaps`} />
      <Fact label="Timeframe agreement" value={labelize(analysis.multiTimeframeStatus)} detail={frames.map(frame => `${frame.timeframe} ${frame.direction}`).join(" · ") || "Complete higher-timeframe history unavailable"} />
      <Fact label="Observed VWAP" value={formatPrice(analysis.vwap)} detail={analysis.vwap == null ? "Session volume unavailable" : `${labelize(analysis.vwapLocation)} · ${raw?.features?.vwap.complete_session ? "Covered from session start" : "Partial session window"}`} />
      <Fact label="ATR · recent range" value={formatPrice(analysis.expectedMove)} detail={`${rangeLabel(analysis.volatilityState)} · ${analysis.atrPercentile == null ? "Rank unavailable" : `${Math.round(analysis.atrPercentile)}th percentile`}`} />
      <Fact label="Relative volume" value={analysis.relativeVolume == null ? "Unavailable" : `${analysis.relativeVolume.toFixed(1)}×`} detail={volumeMeaning(analysis.relativeVolume)} />
    </div>
  </section>;
}

function LevelPanel({ support, resistance, current, atr, timeframe, vwap, direction }: {
  support: number | null; resistance: number | null; current: number | null; atr: number | null;
  timeframe: string; vwap: number | null; direction: string;
}) {
  return <section className="analysis-levels rounded-xl border border-app-border p-4" aria-label="Key levels and invalidation">
    <div className="min-w-0">
      <h3 className="text-sm font-semibold">Key levels · {timeframe}</h3>
      <p className="mt-1 text-xs text-app-muted">Distance from last analyzed close {formatPrice(current)}.</p>
      <div className="analysis-level-values mt-4">
        <Fact label="Nearest support" value={formatPrice(support)} detail={support == null ? "No supported level in this history" : levelDistance(support, current, atr)} />
        <Fact label="Nearest resistance" value={formatPrice(resistance)} detail={resistance == null ? "No supported level in this history" : levelDistance(resistance, current, atr)} />
      </div>
      <p className="mt-3 text-xs leading-5 text-app-muted">Candle-derived reference levels. Resting order size is not measured; these are not strategy stops.</p>
    </div>
    <div className="analysis-invalidation min-w-0">
      <h3 className="text-sm font-semibold">What would change the read</h3>
      <ul className="mt-3 space-y-2 text-sm leading-6">
        {resistance != null && <li><span className="font-medium">Above {formatPrice(resistance)}:</span> a completed {timeframe} close crosses resistance{direction === "bearish" ? " and challenges the bearish read" : ""}.</li>}
        {support != null && <li><span className="font-medium">Below {formatPrice(support)}:</span> a completed {timeframe} close crosses support{direction === "bullish" ? " and challenges the bullish read" : ""}.</li>}
        {support == null && resistance == null && <li className="text-app-muted">No supported swing boundary is available. Wait for completed swings before setting a structure threshold.</li>}
        {vwap != null && (direction === "bullish" || direction === "bearish") && <li>A close {direction === "bullish" ? "below" : "above"} observed VWAP ({formatPrice(vwap)}) conflicts with the {direction} tilt; VWAP alone does not establish reversal.</li>}
      </ul>
      <p className="mt-3 rounded-lg bg-app-bg/50 p-3 text-xs leading-5 text-app-muted"><span className="font-medium text-app-text-soft">Crossing ≠ confirmed structure change.</span> Look for subsequent closes holding beyond the level and higher lows / highs for a bullish change, or lower highs / lows for a bearish change. That confirmation is not measured here.</p>
    </div>
  </section>;
}

function BotDecisionSummary({ evaluation }: { evaluation: BotEvaluation }) {
  const decision = evaluation.decision;
  const explanation = evaluation.analysis?.bot_decision;
  const matches = explanation?.status === evaluation.status && explanation.action === decision.action && explanation.contract_id === decision.contract_id && sameTimestamp(explanation.candle_timestamp, decision.candle_timestamp);
  const detail = matches ? explanation : null;
  const blocked = evaluation.status === "risk_blocked" || evaluation.order_attempt?.status === "rejected" || evaluation.order_attempt?.status === "blocked";
  const failed = detail?.checks.filter(check => check.status === "failed") ?? [];
  const reasons = uniqueStrings(failed.length ? failed.map(check => check.detail) : evaluation.risk_events.map(event => event.message));
  const mode = detail?.execution_mode ?? evaluation.order_attempt?.execution_mode ?? (evaluation.run?.dry_run ? "dry_run" : evaluation.config.execution_mode);
  return <section className="rounded-xl border border-cyan-400/20 bg-cyan-950/10 p-4">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-semibold">{evaluation.config.strategy_type === "topbot_adaptive" ? "TopBot decision" : "Bot decision"}</h3><Badge variant={blocked ? "negative" : "neutral"}>{mode === "dry_run" ? "Dry run" : mode ? labelize(mode) : "Mode unavailable"}</Badge></div>
    <p className="mt-2 text-lg font-semibold">{decisionHeadline(evaluation)}</p>
    <p className="mt-2 text-sm leading-6">{detail?.summary ?? (decision.reason ? humanReason(decision.reason) : "No strategy reason was returned.")}</p>
    {!detail && reasons.length > 0 && <ul className="mt-2 space-y-1 text-sm text-amber-200">{reasons.slice(0, 2).map(reason => <li key={reason}>{reason}</li>)}</ul>}
    {explanation && !matches && <p className="mt-2 text-xs text-amber-200">The saved explanation does not match this decision’s action, contract, timestamp or outcome. The recorded decision is shown; evaluate again for aligned checks.</p>}
    <p className="mt-3 text-xs leading-5 text-app-muted">{detail?.strategy.name ?? labelize(evaluation.config.strategy_type ?? "Configured strategy")} · {evaluation.config.trading_start_time ?? "Unknown"}–{evaluation.config.trading_end_time ?? "Unknown"} America/New_York entry window.</p>
    <Details title="Strategy, session and risk checks" compact>
      <p className="text-sm leading-6">{detail?.strategy_reason ?? humanReason(decision.reason || "Strategy explanation unavailable")}</p>
      {detail?.checks.length ? <ul className="mt-3 space-y-3">{detail.checks.map(check => <li key={check.id} className="text-xs leading-5"><div className="flex flex-wrap justify-between gap-2"><span className="font-medium">{check.label}</span><span className={check.status === "failed" ? "text-amber-200" : "text-app-muted"}>{labelize(check.status)}</span></div><p className="text-app-muted">{check.detail}</p></li>)}</ul> : <p className="mt-3 text-xs text-app-muted">{decision.action === "HOLD" ? "No entry was requested. Entry risk checks were not evaluated for this hold." : "This older response does not include the completed check list. An entry signal alone does not establish permission."}</p>}
      <p className="mt-3 text-xs text-app-muted">Decision contract: {decision.contract_id ?? "Unavailable"}. Signal candle opened {formatTimestamp(decision.candle_timestamp)}; decision recorded {formatTimestamp(decision.created_at)}.</p>
      {detail?.basis && <p className="mt-2 text-xs text-app-muted">{detail.basis}</p>}
      {detail?.limits && <p className="mt-2 text-xs text-app-muted">Configured limits: {detail.limits.max_contracts} contracts per order; {detail.limits.max_open_position} maximum open position; ${detail.limits.max_daily_loss} daily loss; {detail.limits.max_trades_per_day} trades per day. Candle delivery grace: {detail.limits.delivery_grace_seconds}s.</p>}
    </Details>
    {detail?.probabilistic_research && <BotProbabilisticPanel live={mode === "live"} research={detail.probabilistic_research} selectedStrategy={detail.strategy.revision === "mnq_bayesian_payoff_v1"} />}
  </section>;
}

function ContextCoverage({ analysis }: { analysis: BotAnalysis | null | undefined }) {
  const coverage = analysis?.context_coverage;
  return <section className="border-t border-app-border pt-3"><h3 className="text-sm font-semibold">Scope and missing context</h3>
    <p className="mt-1 text-xs leading-5 text-app-muted">{coverage ? `${coverage.summary}. ${coverage.missing.length ? `Missing: ${coverage.missing.join(", ")}. ` : ""}${coverage.limited.length ? `Partial: ${coverage.limited.join(", ")}. ` : ""}` : "Candle quality describes the price history only. News, macro, related markets and observation coverage have not been verified in this response. "}Missing observations are unknown, never neutral evidence.</p>
    <Details title="Context availability" compact>
      {coverage ? <ul className="space-y-2 text-xs leading-5">{coverage.items.map(item => <li key={item.id}><span className="font-medium">{item.label} · {labelize(item.status)}. </span><span className="text-app-muted">{item.detail}</span></li>)}</ul> : <p className="text-xs text-app-muted">{uniqueStrings(analysis?.data_quality?.missing_inputs ?? []).map(labelize).join("; ") || "No coverage assessment was returned."}</p>}
      {analysis?.explanation?.limitations.map(item => <p key={item} className="mt-2 text-xs text-app-muted">{item}</p>)}
      {analysis?.explanation?.context_evidence?.map(item => <p key={item} className="mt-2 text-xs text-app-muted">{item}</p>)}
    </Details>
  </section>;
}

function CalculationDetails({ analysis, raw, ageSeconds }: { analysis: DisplayAnalysis; raw: BotAnalysis | null | undefined; ageSeconds: number | null }) {
  const p = analysis.provenance;
  const volatility = raw?.features?.volatility;
  const vwap = raw?.features?.vwap;
  const definitions = Object.entries(raw?.score_definitions ?? {});
  return <div className="space-y-4">
    <p className="text-xs leading-5 text-app-muted">{analysis.source === "backend" ? "Calculated by the backend from this evaluation’s closed candles." : "Local fallback calculated from evaluation candles; this is not a new TopBot decision."} Candle quality is separate from context coverage. Heuristic scores are not calibrated probabilities or forecasts.</p>
    <p className="text-xs leading-5 text-app-muted">Relative volume compares the latest bar with up to 20 preceding bars; it is not time-of-day adjusted. Evaluation levels use recent swings and rolling-window extremes; chart-only levels use confirmed unswept swings, so they may differ. Displayed prices are rounded; calculations retain precision.</p>
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      <Metric label={analysis.priceSource === "decision" ? "Decision/reference price" : "Last analyzed close"} value={formatPrice(analysis.currentPrice)} />
      <Metric label="Candle opened" value={formatTimestamp(p.latest_candle_timestamp)} />
      <Metric label="Candle closed" value={formatTimestamp(candleClose(analysis))} />
      <Metric label="Wall-clock age since close" value={formatDuration(ageSeconds)} />
      <Metric label="Closed candles / partial excluded" value={`${p.closed_candle_count} / ${p.partial_candle_count}`} />
      <Metric label="In-session gaps" value={String(p.gap_count)} />
      <Metric label="Analyzed contract" value={p.resolved_contract_id ?? "Unavailable"} />
      <Metric label="Configured contract" value={p.configured_contract_id ?? "Unavailable"} />
      <Metric label="Timeframe" value={p.timeframe.label} />
      <Metric label={`ATR${volatility?.period ? ` (${volatility.period})` : ""}`} value={formatPrice(analysis.expectedMove)} />
      <Metric label="Historical ATR rank" value={analysis.atrPercentile == null ? "Unavailable" : `${Math.round(analysis.atrPercentile)}th percentile`} />
      <Metric label="Recent range change" value={volatility?.recent_range_state ? labelize(volatility.recent_range_state) : "Not separately reported"} />
      <Metric label="Relative candle volume" value={analysis.relativeVolume == null ? "Unavailable" : `${analysis.relativeVolume.toFixed(2)}× prior-bar baseline`} />
      <Metric label="Observed-window VWAP" value={analysis.vwap == null ? "Unavailable" : `${formatPrice(analysis.vwap)} · close ${analysis.vwapLocation}`} />
      <Metric label="Timeframe agreement" value={labelize(analysis.multiTimeframeStatus)} />
    </div>
    {volatility?.recent_range_ratio != null && <p className="text-xs leading-5 text-app-muted">Recent range change compares the last 6 true ranges with up to 28 preceding true ranges ({volatility.recent_range_ratio.toFixed(2)}×). Historical ATR rank compares rolling ATR values across a longer window. A high ATR rank can coexist with cooling recent ranges.</p>}
    {volatility?.reference_observations != null && <p className="text-xs text-app-muted">ATR reference: {volatility.reference_observations} observations, {formatTimestamp(volatility.reference_window_start)}–{formatTimestamp(volatility.reference_window_end)}.</p>}
    {vwap?.window_start && <p className="text-xs text-app-muted">VWAP uses the observed candle window {formatTimestamp(vwap.window_start)}–{formatTimestamp(vwap.window_end)}. {vwap.complete_session ? "Session coverage is reported complete through the analysis cutoff." : "Full session coverage is not established."}</p>}
    {raw?.features?.trend.components?.length ? <ul className="space-y-1 text-xs text-app-muted">{raw.features.trend.components.map(component => <li key={component.label}>{component.label}: {labelize(component.direction)}{component.value == null ? " · unavailable" : ` (${formatPrice(component.value)})`}.</li>)}</ul> : null}
    {raw?.features?.multi_timeframe_alignment.timeframes?.length ? <Details title="Timeframe input windows"><ul className="space-y-2 text-xs text-app-muted">{raw.features.multi_timeframe_alignment.timeframes.map(item => <li key={item.timeframe}>{item.timeframe}: {labelize(item.direction)}; {item.closed_candle_count ?? "unknown count of"} closed candles ending {formatTimestamp(item.latest_candle_end_timestamp)}. Contract: {item.contract_id ?? "not reported"}; EMA periods: {item.fast_period ?? "unknown"}/{item.slow_period ?? "unknown"}.</li>)}</ul></Details> : null}
    {definitions.length > 0 && <Details title="Metric definitions, scales and missing-data rules"><dl className="space-y-4 text-xs leading-5">{definitions.map(([name, definition]) => <div key={name}><dt className="font-semibold">{labelize(name)}</dt><dd className="text-app-muted">{definition.interpretation}<br />Inputs: {definition.inputs.join("; ")}<br />Scale: {definition.scale}<br />Reference window: {definition.reference_window}<br />Missing data: {definition.missing_data}</dd></div>)}</dl></Details>}
    {analysis.tradeEvaluation && <Details title="Advisory trade geometry"><p className="text-xs text-app-muted">This descriptive trade-plan assessment is separate from TopBot’s strategy and completed risk checks.</p><p className="mt-2 text-sm">Risk: {formatPrice(analysis.tradeEvaluation.features.risk_points)} points; reward: {formatPrice(analysis.tradeEvaluation.features.reward_points)} points. Estimated dollar risk: {formatPrice(analysis.tradeEvaluation.features.estimated_dollar_risk)}.</p><p className="mt-2 text-xs text-app-muted">{analysis.tradeEvaluation.summary}</p></Details>}
    {uniqueStrings(analysis.dataQuality.warnings).map(warning => <p key={warning} className="text-xs text-app-muted">{warning}</p>)}
    <p className="text-xs text-app-muted">Calculated {formatTimestamp(analysis.generatedAt)} · version {analysis.analysisVersion}. {p.contract_rollover ? "The analyzed contract differs from the configured contract after rollover." : ""}</p>
  </div>;
}

function CollectedContextDetails({ collected }: { collected: BotCollectedContext }) {
  const book = collected.order_book;
  const profile = collected.volume_profile;
  const cutoff = Date.parse(collected.as_of);
  const quoteEligible = book?.eligible === true && book.status === "fresh" && book.contract_id === collected.contract_id && atCutoff(book.received_at, cutoff, book.freshness_limit_seconds ?? 10) && atCutoff(book.provider_timestamp, cutoff, book.freshness_limit_seconds ?? 10);
  const profileEligible = profile?.eligible === true && profile.status === "partial" && profile.contract_id === collected.contract_id && atCutoff(profile.observation_end, cutoff, profile.freshness_limit_seconds ?? 300) && atCutoff(profile.received_through, cutoff, Infinity) && Number.isFinite(Date.parse(profile.observation_start ?? "")) && Date.parse(profile.observation_start!) <= Date.parse(profile.observation_end!);
  return <div className="space-y-4 text-xs leading-5">
    <p className="text-app-muted">Analysis cutoff: {formatTimestamp(collected.as_of)}. Collection ran {formatTimestamp(collected.captured_at)} for {collected.contract_id ?? "an unspecified contract"}. Only observations explicitly eligible for that contract and cutoff support this interpretation.</p>
    <section><h4 className="font-semibold">Level 1 quote · {quoteEligible ? "eligible at cutoff" : "not eligible for this read"}</h4>
      <p className="mt-1 text-app-muted">{book?.reason ?? "No eligible quote was supplied."}</p>
      <p className="mt-1">Recorded best bid {formatPrice(book?.bid)} / ask {formatPrice(book?.ask)}; spread {formatPrice(book?.spread)}. Available sizes: bid {formatNumber(book?.bid_size)}, ask {formatNumber(book?.ask_size)}.</p>
      <p className="mt-1 text-app-muted">Received {formatTimestamp(book?.received_at)}; provider time {formatTimestamp(book?.provider_timestamp)}. Contract: {book?.contract_id ?? "Unavailable"}.</p>
      <p className="mt-1 text-app-muted">Level 1 covers the best bid and ask only. It does not establish deeper liquidity, queue position, full-book imbalance or order-flow delta.</p>
    </section>
    <section><h4 className="font-semibold">Viewer-observed volume profile · {profileEligible ? "partial window eligible" : "not eligible for this read"}</h4>
      <p className="mt-1 text-app-muted">{profile?.reason ?? "No profile observation was supplied."}</p>
      <p className="mt-1">Recorded POC {formatPrice(profile?.poc)}; value area {formatPrice(profile?.value_area_low)}–{formatPrice(profile?.value_area_high)}.</p>
      <p className="mt-1 text-app-muted">Observation window: {formatTimestamp(profile?.observation_start)}–{formatTimestamp(profile?.observation_end)}. Received through: {formatTimestamp(profile?.received_through)}. Contract: {profile?.contract_id ?? "Unavailable"}. This viewer-driven sample is not a complete session profile.</p>
    </section>
    <section><h4 className="font-semibold">News, scheduled events and related markets</h4><p className="mt-1 text-app-muted">{collected.events?.reason ?? "Event and news context unavailable."}</p>
      {collected.related_markets?.items?.map(item => <p key={item.symbol} className="mt-1 text-app-muted">{item.symbol}: {labelize(item.status)}{item.reason ? `. ${item.reason}` : ""}</p>)}
      {collected.events?.headlines?.slice(0, 3).map(headline => <p key={headline.id} className="mt-2">{headline.title} <span className="text-app-muted">· {headline.source} · {formatTimestamp(headline.published_at)}</span></p>)}
    </section>
  </div>;
}

function ChartMarketAnalysis({ snapshot, nowMs }: { snapshot: BotMarketSnapshot | null; nowMs: number }) {
  const context = useMemo(() => snapshot ? buildMarketContext(snapshot, nowMs) : null, [snapshot, nowMs]);
  if (!context || context.provenance.closedCandleCount < 10) {
    return <EmptyState title="Waiting for chart candles" description="Market analysis starts after 10 completed chart candles; trend needs 25. A market-data connection is required, but no trading account is needed." />;
  }
  const closedAt = snapshot && context.asOfTimestamp
    ? new Date(candleEndMs(Date.parse(context.asOfTimestamp), snapshot.unit, snapshot.unitNumber)).toISOString()
    : null;
  const explanation = buildMarketExplanation(context);
  return <section className="space-y-4" aria-label="Chart market context">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="text-base font-semibold">Chart market context</h3>
      <Badge variant={context.provenance.isStale ? "warning" : "neutral"}>
        {context.provenance.isStale ? "Stale candles" : "Closed-candle analysis"}
      </Badge>
    </div>
    <p className="text-xs text-app-muted">Based on {context.provenance.closedCandleCount} completed {context.provenance.timeframe} candles. Latest candle closed {formatTimestamp(closedAt)}. Partial candles are excluded.</p>
    <section className="rounded-xl border border-app-border bg-app-bg/40 p-4" aria-label="Market interpretation">
      <h3 className="text-lg font-semibold">{explanation.headline}</h3>
      <p className="mt-2 text-sm text-app-muted">{explanation.summary}</p>
      <div className="mt-4 analysis-evidence">
        <EvidenceList title="What supports this read" items={explanation.supporting} empty="No directional evidence is available yet." />
        <EvidenceList title="What conflicts with it" items={explanation.conflicting} empty="No direct conflict reported by these candle indicators. Unmeasured context may disagree." />
      </div>
    </section>
    <div className="analysis-measurements" aria-label="Measured candle facts">
      <Fact label="Last closed price" value={formatPrice(context.lastPrice)} detail={`${context.provenance.timeframe} · live quote excluded`} />
      <Fact label="Timeframe agreement" value={labelize(context.multiTimeframeAlignment.status)} detail={context.trends.map(trend => `${trend.label} ${trend.direction}`).join(" · ") || "Needs 25 complete bars per timeframe"} />
      <Fact label="Observed VWAP" value={formatPrice(context.vwap)} detail={context.vwap === null ? "Missing session volume" : `${labelize(context.vwapLocation)} · ${context.vwapCoverage?.complete ? "Covered from 18:00 ET" : "Partial session window"}`} />
      <Fact label="ATR · recent range" value={formatPrice(context.atr)} detail={`${rangeLabel(context.volatilityState)} · ${context.atrPercentile == null ? "Rank unavailable" : `${Math.round(context.atrPercentile)}th percentile`}`} />
      <Fact label="Relative volume" value={context.relativeVolume == null ? "Unavailable" : `${context.relativeVolume.toFixed(1)}×`} detail={volumeMeaning(context.relativeVolume)} />
    </div>
    <LevelPanel support={context.nearestSupport} resistance={context.nearestResistance} current={context.lastPrice} atr={context.atr} timeframe={context.provenance.timeframe} vwap={context.vwap} direction={context.trend?.direction ?? "neutral"} />
    {context.dataQuality.warnings.length > 0 && <div className="space-y-1 text-xs text-amber-200" role="status">
      {context.dataQuality.warnings.map(warning => <p key={warning}>{warning}</p>)}
    </div>}
    <p className="text-xs leading-5 text-app-muted">Coverage: {context.provenance.closedCandleCount} closed bars · {context.provenance.partialCandleCount} incomplete excluded · {context.provenance.detectedGapCount} gaps. {context.dataQuality.missingInputs.length ? `Missing: ${context.dataQuality.missingInputs.join(", ")}.` : "Core candle inputs available."} Order-book, news and related-market data are not inputs.</p>
    <Details title="Technical details & source coverage">
      <p className="mb-3 text-xs leading-5 text-app-muted">Trend uses EMA 9/21 spread and slow-EMA slope; regime is a separate range / movement heuristic. Higher timeframes need 25 complete aggregated bars. ATR is the mean of 14 true ranges; rank uses up to 100 rolling observations. Range change compares the latest 6 true ranges with up to 28 prior ranges.</p>
      <p className="mb-3 text-xs leading-5 text-app-muted">VWAP weights typical candle price by reported volume since 18:00 New York. Window: {formatTimestamp(context.vwapCoverage?.start)}–{formatTimestamp(context.vwapCoverage?.end)}. {context.vwapCoverage?.complete ? "Coverage begins at session start and has no detected gaps through this close." : "Full session coverage is not established."} Volume is participation, not buyer/seller imbalance.</p>
      <p className="mb-3 text-xs leading-5 text-app-muted">Levels use the chart’s confirmed, unswept swing highs/lows; manual line changes do not affect them. Candle swings do not measure resting orders. Displayed prices are rounded; calculations retain precision.</p>
      <p className="text-sm">Candle quality: {labelize(context.dataQuality.status)}.</p>
      {context.dataQuality.missingInputs.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-app-muted">
        {context.dataQuality.missingInputs.map(input => <li key={input}>{input}</li>)}
      </ul>}
      <p className="mt-2 text-xs text-app-muted">Calculated from the chart’s loaded history. Account risk, news, and order-book depth are not inputs to this read.</p>
    </Details>
  </section>;
}

function matchingMarketSnapshot(snapshot: BotMarketSnapshot | null, market: BotChartMarket | null): boolean {
  return Boolean(snapshot && market && snapshot.contractKey === `${market.contract_id}:${snapshot.unit}:${snapshot.unitNumber}`);
}

function SeparateChartContext({ snapshot, bot, nowMs }: { snapshot: BotMarketSnapshot; bot: BotConfig; nowMs: number }) {
  const context = useMemo(() => buildMarketContext(snapshot, nowMs, bot.max_data_staleness_seconds), [snapshot, bot.max_data_staleness_seconds, nowMs]);
  if (!context || context.provenance.closedCandleCount < 10) return null;
  return <Details title="Local chart context — separate from evaluation"><p className="text-xs leading-5 text-app-muted">The chart has {context.provenance.closedCandleCount} closed bars. They were not included in this evaluation and do not replace its bot decision. Last chart close: {formatPrice(context.lastPrice)} at {formatTimestamp(context.asOfTimestamp)}.</p></Details>;
}
function Details({ title, children, compact = false }: { title: string; children: ReactNode; compact?: boolean }) { return <details className={compact ? "mt-3" : "rounded-xl border border-app-border p-3"}><summary className="cursor-pointer text-xs font-medium text-app-muted">{title}</summary><div className="mt-3">{children}</div></details>; }
function EvidenceList({ title, items, empty }: { title: string; items: string[]; empty: string }) {
  const entries = uniqueStrings(items);
  return <div className="rounded-xl border border-app-border p-4"><h3 className="text-sm font-semibold">{title}</h3>{entries.length ? <ul className="mt-2 list-disc space-y-2 pl-4 text-sm leading-6">{entries.slice(0, 4).map(item => <li key={item}>{item}</li>)}</ul> : <p className="mt-2 text-sm leading-6 text-app-muted">{empty}</p>}{entries.length > 4 && <Details title="More evidence" compact><ul className="list-disc space-y-2 pl-4 text-xs">{entries.slice(4).map(item => <li key={item}>{item}</li>)}</ul></Details>}</div>;
}
function Metric({ label, value }: { label: string; value: string }) { return <div className="min-w-0"><p className="text-xs text-app-muted">{label}</p><p className="mt-1 break-words text-sm font-medium">{value}</p></div>; }
function EmptyState({ title, description, action }: { title: string; description: string; action?: ReactNode }) { return <section className="rounded-xl border border-dashed border-app-border p-4"><h3 className="font-semibold">{title}</h3><p className="mt-2 text-sm leading-6 text-app-muted">{description}</p>{action && <div className="mt-3">{action}</div>}</section>; }
function newerClosedBars(analysis: DisplayAnalysis, snapshot: BotMarketSnapshot | null, nowMs: number): number {
  if (!snapshot || snapshot.unit !== analysis.provenance.timeframe.unit || snapshot.unitNumber !== analysis.provenance.timeframe.unit_number) return 0;
  const analyzed = Date.parse(analysis.provenance.latest_candle_timestamp ?? "");
  const contract = analysis.provenance.resolved_contract_id ?? analysis.provenance.configured_contract_id;
  if (!Number.isFinite(analyzed) || !contract) return 0;
  return new Set(snapshot.candles.filter(candle => isConfirmedClosedCandle(candle, nowMs) && candle.contract_id === contract && candle.unit === snapshot.unit && candle.unit_number === snapshot.unitNumber && Date.parse(candle.timestamp) > analyzed).map(candle => candle.timestamp)).size;
}
function decisionHeadline(evaluation: BotEvaluation): string {
  if (evaluation.status === "risk_blocked" || ["blocked", "rejected"].includes(evaluation.order_attempt?.status ?? "")) return "Entry rejected by checks";
  if (evaluation.status === "error" || evaluation.order_attempt?.status === "error") return "Evaluation or routing failed";
  if (evaluation.status === "duplicate_skipped") return "Entry skipped — already processed";
  if (evaluation.status === "dry_run_attempt") return "Entry permitted for dry run";
  if (evaluation.status === "submitted") return "Entry submitted";
  if (evaluation.decision.action === "HOLD" || evaluation.status === "held") return "Holding — no new entry";
  return `${evaluation.decision.action === "BUY" ? "Buy" : "Sell"} signal — permission not established`;
}
function legacyHeadline(analysis: DisplayAnalysis): string { return analysis.marketBias === "neutral" ? "Closed candles do not establish a clear direction." : `Closed candles lean ${analysis.marketBias}.`; }
function conflictingEvidence(analysis: DisplayAnalysis): string[] { return analysis.marketBias === "neutral" ? [...analysis.scoreDrivers.bullish, ...analysis.scoreDrivers.bearish] : analysis.scoreDrivers[analysis.marketBias === "bullish" ? "bearish" : "bullish"]; }
function candleClose(analysis: DisplayAnalysis): string | null {
  const p = analysis.provenance;
  if (p.latest_candle_end_timestamp) return p.latest_candle_end_timestamp;
  const start = Date.parse(p.latest_candle_timestamp ?? "");
  return Number.isFinite(start) ? new Date(candleEndMs(start, p.timeframe.unit, p.timeframe.unit_number)).toISOString() : null;
}
function agreementLabel(value: string | undefined): string { return value === "aligned" ? "Aligned" : value === "mixed" ? "Mixed" : value === "flat" ? "Flat readings" : "Unavailable"; }
function freshnessLabel(value: string | undefined): string { return value === "fresh" ? "Current closed-candle read" : value === "stale" ? "Stale evaluation" : value === "market_closed" ? "Market closed" : "Freshness unknown"; }
function formatPrice(value: number | null | undefined): string { return value == null || !Number.isFinite(value) ? "Unavailable" : priceFormatter.format(value); }
function formatNumber(value: number | null | undefined): string { return value == null || !Number.isFinite(value) ? "unavailable" : String(value); }
function formatTimestamp(value: string | null | undefined): string { const ms = Date.parse(value ?? ""); return Number.isFinite(ms) ? timestampFormatter.format(new Date(ms)) : "unavailable"; }
function formatDuration(seconds: number | null): string { return seconds == null ? "Unknown" : seconds < 60 ? `${Math.round(seconds)}s` : seconds < 3600 ? `${Math.round(seconds / 60)}m` : `${(seconds / 3600).toFixed(1)}h`; }
function labelize(value: string): string { return value.replace(/[_-]+/g, " ").replace(/\b\w/g, character => character.toUpperCase()); }
function humanReason(value: string): string { return value.includes(" ") ? value : labelize(value); }
function sameTimestamp(left: string | null | undefined, right: string | null | undefined): boolean { return !left && !right ? true : Number.isFinite(Date.parse(left ?? "")) && Date.parse(left!) === Date.parse(right ?? ""); }
function atCutoff(timestamp: string | null | undefined, cutoff: number, maxAgeSeconds: number): boolean { const ms = Date.parse(timestamp ?? ""); return Number.isFinite(ms) && ms <= cutoff && cutoff - ms <= maxAgeSeconds * 1000; }
function uniqueStrings(values: string[]): string[] { return Array.from(new Set(values.map(value => value.trim()).filter(Boolean))); }
