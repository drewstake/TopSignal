// @vitest-environment jsdom
import { renderToStaticMarkup } from "react-dom/server";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { BotAnalysis, BotConfig, BotEvaluation, ProjectXMarketCandle } from "../../lib/types";
import { DEMO_AS_OF_ISO } from "../../lib/demoScenario";
import { BotAnalysisPanel } from "./BotAnalysisPanel";
import type { BotMarketSnapshot } from "./botMarketContext";

const bot = {
  id: 1,
  name: "MNQ bot",
  symbol: "MNQ",
  contract_id: "CON.F.US.MNQ.M26",
  timeframe_unit: "minute",
  timeframe_unit_number: 5,
  max_data_staleness_seconds: 600,
  execution_mode: "dry_run",
  strategy_type: "topbot_adaptive",
  trading_start_time: "09:30",
  trading_end_time: "15:45",
} as BotConfig;

const analysis = {
  analysis_version: "market_analysis_v2",
  probability_method: "heuristic_scenario_weight",
  scenario_weights: { bullish: 54, bearish: 18, sideways: 28 },
  provenance: {
    closed_candle_count: 80,
    partial_candle_count: 1,
    latest_candle_timestamp: "2026-07-09T15:00:00Z",
    data_age_seconds: 20,
    is_stale: false,
    stale_after_seconds: 600,
    timeframe: { unit: "minute", unit_number: 5, label: "5m" },
    detected_gaps: [],
    gap_count: 0,
    resolved_contract_id: bot.contract_id,
    configured_contract_id: bot.contract_id,
    resolved_symbol: "MNQ",
  },
  data_quality: {
    status: "limited",
    confidence: 74,
    missing_inputs: ["News context"],
    warnings: ["News data was not provided."],
  },
  market_regime: "trend",
  features: {
    trend: { direction: "bullish", strength: 72, strength_label: "strong", agreement: "mixed", fast_ema: 105, slow_ema: 103, slow_ema_slope: 0.4 },
    volatility: { atr: 2, atr_percent: 1.9, percentile: 64, state: "normal" },
    volume: { relative_volume: 1.2, state: "normal" },
    vwap: { value: 104, location: "above" },
    multi_timeframe_alignment: {
      status: "bullish",
      aligned_timeframes: 2,
      conflicting_timeframes: 0,
      timeframes: [{ timeframe: "5m", direction: "bullish" }],
    },
    nearby_levels: { support: 102, resistance: 108 },
  },
  score_drivers: {
    bullish: ["Trend and VWAP align."],
    bearish: ["Resistance is nearby."],
    neutral: ["Volume is average."],
  },
  setup_quality: { score: 68, label: "acceptable", drivers: ["Structure is defined."] },
  market_bias: { direction: "bullish", strength: 72, drivers: ["Trend and VWAP align."] },
  execution_risk: { risk_score: 35, label: "moderate", drivers: ["Resistance is nearby."] },
  data_confidence: { score: 74, label: "limited", drivers: ["News context is missing."] },
  current_price: 105,
  previous_close: 104,
  price_change: 1,
  price_change_percent: 0.96,
  trend: "bullish",
  trend_strength: 72,
  volatility_state: "normal",
  volume_state: "normal",
  support_levels: [102],
  resistance_levels: [108],
  nearest_support: 102,
  nearest_resistance: 108,
  bullish_probability: 54,
  bearish_probability: 18,
  sideways_probability: 28,
  expected_move: 2,
  expected_move_percent: 1.9,
  invalidation_level: 102,
  summary: "Closed-bar trend and VWAP support a bullish bias.",
  reasoning: ["Trend is bullish."],
  risk_notes: ["Resistance remains overhead."],
  generated_at: "2026-07-09T15:05:20Z",
  explanation: {
    headline: "Price is rising, with conflicting directional indicators.",
    supporting_evidence: ["The fast EMA is above the slow EMA."],
    conflicting_evidence: ["The latest close is below the recent high."],
    limitations: ["Higher-timeframe history is incomplete."],
    change_levels: [{ direction: "bearish", price: 102, condition: "A closed candle below 102 would weaken this bullish read." }],
    scope: "Closed candles only.",
  },
  context_coverage: {
    summary: "Context is incomplete", available: ["Closed candles"], limited: ["Level 1 quote"], missing: ["News", "Economic calendar", "Related markets"],
    items: [{ id: "news", label: "News", status: "missing", detail: "No recorded news coverage." }], scope: "Availability only.",
  },
} satisfies BotAnalysis;

const evaluation = {
  status: "evaluated",
  correlation_id: null,
  idempotency_key: null,
  duplicate_of_order_attempt_id: null,
  config: bot,
  decision: { action: "HOLD", price: 105, contract_id: bot.contract_id, candle_timestamp: "2026-07-09T15:00:00Z", created_at: "2026-07-09T15:05:20Z", reason: "Waiting for a pullback to the 20 EMA." },
  analysis,
  candles: [],
  risk_events: [],
  run: null,
  order_attempt: null,
} as unknown as BotEvaluation;

function evaluationWithAnalysis(nextAnalysis: BotAnalysis, decisionPrice = 105): BotEvaluation {
  return {
    ...evaluation,
    decision: { ...evaluation.decision, price: decisionPrice },
    analysis: nextAnalysis,
  };
}

function insufficientAnalysis(
  closedCandleCount = 0,
  latestCandleTimestamp: string | null = null,
): BotAnalysis {
  const confidence = Math.min(35, closedCandleCount * 3);
  return {
    ...analysis,
    scenario_weights: { bullish: 33, bearish: 33, sideways: 34 },
    provenance: {
      ...analysis.provenance,
      closed_candle_count: closedCandleCount,
      partial_candle_count: 0,
      latest_candle_timestamp: latestCandleTimestamp,
      data_age_seconds: latestCandleTimestamp ? 20 : null,
    },
    data_quality: {
      status: "insufficient",
      confidence,
      missing_inputs: ["at_least_25_closed_candles", "trend_history", "atr_history"],
      warnings: [
        `Only ${closedCandleCount} closed candle(s) were available; at least 10 are needed for a reliable heuristic read.`,
      ],
    },
    market_regime: "unknown",
    features: {
      ...analysis.features,
      trend: { direction: "neutral", strength: 0, fast_ema: null, slow_ema: null, slow_ema_slope: null },
      volatility: { atr: null, atr_percent: null, percentile: null, state: "normal" },
      volume: { relative_volume: null, state: "normal" },
      vwap: { value: null, location: "unavailable" },
      multi_timeframe_alignment: {
        status: "unavailable",
        aligned_timeframes: 0,
        conflicting_timeframes: 0,
        timeframes: [],
      },
      nearby_levels: { support: null, resistance: null },
    },
    score_drivers: {
      bullish: [],
      bearish: [],
      neutral: ["Insufficient closed-candle history for a directional feature set."],
    },
    setup_quality: { score: confidence, label: "weak", drivers: [] },
    market_bias: { direction: "neutral", strength: 0, drivers: [] },
    execution_risk: { risk_score: 100 - confidence, label: "high", drivers: [] },
    data_confidence: { score: confidence, label: "insufficient", drivers: [] },
    current_price: null,
    previous_close: null,
    price_change: null,
    price_change_percent: null,
    trend: "neutral",
    trend_strength: 0,
    nearest_support: null,
    nearest_resistance: null,
    expected_move: null,
    invalidation_level: null,
    summary: "Insufficient closed-candle history for a directional read.",
    reasoning: ["Insufficient closed-candle history for a directional feature set."],
    candle_timestamp: latestCandleTimestamp,
  };
}

function chartCandle(timestamp: string): ProjectXMarketCandle {
  return {
    id: null,
    contract_id: bot.contract_id,
    symbol: "MNQ",
    live: false,
    unit: "minute",
    unit_number: 5,
    timestamp,
    open: 105,
    high: 106,
    low: 104,
    close: 105.5,
    volume: 100,
    is_partial: false,
    fetched_at: null,
  };
}

function withDecisionExplanation(status: BotEvaluation["status"] = "held", action: "HOLD" | "BUY" = "HOLD"): BotEvaluation {
  return { ...evaluation, status, decision: { ...evaluation.decision, action }, analysis: { ...analysis,
    bot_decision: {
      status, action, strategy: { name: "TopBot EMA/VWAP pullback", revision: "v1" },
      summary: status === "risk_blocked" ? "BUY setup rejected: the daily loss limit is reached." : "Waiting for a pullback to the 20 EMA.",
      strategy_reason: "Previous candle did not touch the 20 EMA.", execution_mode: "dry_run",
      contract_id: bot.contract_id, candle_timestamp: evaluation.decision.candle_timestamp,
      candle_close_timestamp: "2026-07-09T15:05:00Z", evaluated_at: "2026-07-09T15:05:20Z",
      checks: [{ id: "risk", label: "Account and routing checks", status: action === "HOLD" ? "not_evaluated" : "failed", detail: action === "HOLD" ? "No new order considered; account risk checks were not evaluated." : "Daily loss limit reached." }],
      limits: { max_contracts: 1, max_open_position: 1, max_daily_loss: 250, max_trades_per_day: 30, delivery_grace_seconds: 600 },
      basis: "Recorded strategy result and final routing outcome.",
    },
  } };
}

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date("2026-07-09T15:05:20Z")); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

function accountFreeSnapshot(unitNumber = 5): BotMarketSnapshot {
  const end = Date.parse("2026-07-09T15:05:00Z");
  return {
    contractKey: `${bot.contract_id}:minute:${unitNumber}`, unit: "minute", unitNumber,
    candles: Array.from({ length: 40 }, (_, index) => ({
      ...chartCandle(new Date(end - (40 - index) * unitNumber * 60_000).toISOString()), unit_number: unitNumber,
      open: 100 + index, high: 102 + index, low: 99 + index, close: 101 + index,
    })),
    lastPrice: 888888, updatedAt: new Date().toISOString(),
  };
}

it("shows account-free context from closed candles without turning it into a bot decision", () => {
  const snapshot = accountFreeSnapshot();
  snapshot.candles.push({ ...chartCandle("2026-07-09T15:05:00Z"), close: 999999, high: 999999, is_partial: true });
  render(<BotAnalysisPanel bot={null} market={bot} evaluation={null} marketSnapshot={snapshot} />);
  expect(screen.getByRole("heading", { name: "Chart market context" })).not.toBeNull();
  expect(screen.getByRole("heading", { name: "5m trend leans bullish" })).not.toBeNull();
  expect(screen.getByRole("heading", { name: "What supports this read" })).not.toBeNull();
  expect(screen.getByRole("heading", { name: "What conflicts with it" })).not.toBeNull();
  expect(screen.getByRole("heading", { name: "Computed levels · 5m" })).not.toBeNull();
  expect(screen.getByText("140.00")).not.toBeNull();
  expect(screen.getByText(/Based on 40 completed 5m candles/)).not.toBeNull();
  expect(screen.getByText("Excluded 1 partial candle.")).not.toBeNull();
  expect(screen.queryByText(/888,888|999,999/)).toBeNull();
  expect(screen.queryByRole("button", { name: "Evaluate bot" })).toBeNull();
});

it("rejects another market's snapshot and follows chart timeframe changes without a bot", () => {
  const snapshot = accountFreeSnapshot(1);
  const view = render(<BotAnalysisPanel bot={null} market={{ ...bot, contract_id: "different-contract" }} evaluation={null} marketSnapshot={snapshot} />);
  expect(screen.getByText("Waiting for chart candles")).not.toBeNull();
  expect(screen.queryByText("140.00")).toBeNull();
  view.rerender(<BotAnalysisPanel bot={null} market={bot} evaluation={null} marketSnapshot={snapshot} />);
  expect(screen.getByText(/Based on 40 completed 1m candles/)).not.toBeNull();
});

it("labels stale chart context without claiming a fresh bot evaluation", () => {
  const snapshot = accountFreeSnapshot();
  vi.setSystemTime(new Date("2026-07-09T17:00:00Z"));
  render(<BotAnalysisPanel bot={null} market={bot} evaluation={null} marketSnapshot={snapshot} />);
  expect(screen.getByText("Stale candles")).not.toBeNull();
  expect(screen.queryByRole("heading", { name: "TopBot decision" })).toBeNull();
});

it("follows the selected chart timeframe without treating its bars as matching evaluation bars", () => {
  const snapshot = accountFreeSnapshot(15);
  const view = render(<BotAnalysisPanel bot={bot} evaluation={null} marketSnapshot={snapshot} />);
  expect(screen.getByText(/Based on 40 completed 15m candles/)).not.toBeNull();
  expect(screen.queryByText("Waiting for chart candles")).toBeNull();
  vi.setSystemTime(new Date("2026-07-09T15:30:20Z"));
  snapshot.candles.push({ ...chartCandle("2026-07-09T15:15:00Z"), unit_number: 15 });
  view.rerender(<BotAnalysisPanel bot={bot} evaluation={evaluation} marketSnapshot={snapshot} />);
  expect(screen.queryByText(/matching chart has .* newer closed bar/)).toBeNull();
});

describe("BotAnalysisPanel evidence and decision", () => {
  it("keeps evaluation available after success and when the saved read becomes stale", () => {
    const onEvaluate = vi.fn();
    const view = render(<BotAnalysisPanel bot={bot} evaluation={null} onEvaluate={onEvaluate} />);
    fireEvent.click(screen.getByRole("button", { name: "Evaluate bot" }));
    expect(onEvaluate).toHaveBeenCalledTimes(1);

    view.rerender(<BotAnalysisPanel bot={bot} evaluation={evaluation} onEvaluate={onEvaluate} />);
    fireEvent.click(screen.getByRole("button", { name: "Refresh evaluation" }));
    expect(onEvaluate).toHaveBeenCalledTimes(2);
    act(() => { vi.advanceTimersByTime(20 * 60_000); });
    expect(screen.getByText("Stale evaluation")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Refresh evaluation" }));
    expect(onEvaluate).toHaveBeenCalledTimes(3);
  });

  it("disables duplicate evaluation while loading and leaves retry available for insufficient data", () => {
    const onEvaluate = vi.fn();
    const view = render(<BotAnalysisPanel bot={bot} evaluation={evaluation} loading onEvaluate={onEvaluate} />);
    const busy = screen.getByRole("button", { name: "Evaluating…" }) as HTMLButtonElement;
    expect(busy.disabled).toBe(true);
    fireEvent.click(busy);
    expect(onEvaluate).not.toHaveBeenCalled();

    view.rerender(<BotAnalysisPanel bot={bot} evaluation={evaluationWithAnalysis(insufficientAnalysis())} onEvaluate={onEvaluate} />);
    fireEvent.click(screen.getByRole("button", { name: "Retry evaluation" }));
    expect(onEvaluate).toHaveBeenCalledTimes(1);
    view.rerender(<BotAnalysisPanel bot={bot} evaluation={evaluation} demoMode onEvaluate={onEvaluate} />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("uses the fixed demo timeline for chart freshness even after the scenario date", () => {
    vi.setSystemTime(new Date("2026-09-12T05:00:00Z"));
    const snapshot: BotMarketSnapshot = {
      contractKey: `${bot.contract_id}:minute:5`, unit: "minute", unitNumber: 5,
      candles: Array.from({ length: 40 }, (_, index) => chartCandle(
        new Date(Date.parse(DEMO_AS_OF_ISO) - (40 - index) * 5 * 60_000).toISOString(),
      )),
      lastPrice: 105.5, updatedAt: DEMO_AS_OF_ISO,
    };
    const view = render(<BotAnalysisPanel bot={bot} evaluation={null} marketSnapshot={snapshot} demoMode />);
    expect(screen.getByText(/Based on 40 completed 5m candles/)).not.toBeNull();
    expect(screen.queryByText("Stale candles")).toBeNull();
    expect(screen.getByText(/This demo snapshot has no saved evaluation/)).not.toBeNull();
    view.rerender(<BotAnalysisPanel bot={bot} evaluation={null} marketSnapshot={snapshot} />);
    expect(screen.getByText("Stale candles")).not.toBeNull();
  });

  it("answers the first-screen questions without probability bars or overlapping scores", () => {
    const html = renderToStaticMarkup(<BotAnalysisPanel bot={bot} evaluation={withDecisionExplanation()} />);
    expect(html).toContain("Price is rising, with conflicting directional indicators.");
    expect(html).toContain("What supports this read");
    expect(html).toContain("What conflicts with it");
    expect(html).toContain("What would change the read");
    expect(html).toContain("Holding — no new entry");
    expect(html).toContain("Waiting for a pullback to the 20 EMA.");
    expect(html).toContain("Not Evaluated");
    expect(html).toContain("Configured limits: 1 contracts per order");
    expect(html).toContain("Current closed-candle read");
    expect(html).toContain("Indicator agreement:");
    expect(html).not.toContain("scenario weight");
    expect(html).not.toContain("/100");
    expect(html).not.toContain("Moderate-conviction");
    expect(html).toContain("<details");
    expect(html).not.toContain(" open=");
  });

  it("separates good candles from missing context", () => {
    const html = renderToStaticMarkup(<BotAnalysisPanel bot={bot} evaluation={evaluationWithAnalysis({ ...analysis, data_quality: { status: "good", confidence: 100, warnings: [], missing_inputs: ["news_context"] } })} />);
    expect(html).toContain("Candles: good quality");
    expect(html).toContain("Context incomplete");
    expect(html).toContain("Missing: News, Economic calendar, Related markets");
    expect(html).toContain("Missing observations are unknown, never neutral evidence");
    expect(html).not.toContain("100/100");
  });

  it("explains a high ATR rank and cooling ranges as different measurements", () => {
    const html = renderToStaticMarkup(<BotAnalysisPanel bot={bot} evaluation={evaluationWithAnalysis({ ...analysis, features: { ...analysis.features, volatility: { atr: 2, atr_percent: 1.9, percentile: 92, state: "low", recent_range_state: "cooling", recent_range_ratio: 0.6 } } })} />);
    expect(html).toContain("92th percentile");
    expect(html).toContain("Cooling");
    expect(html).toContain("A high ATR rank can coexist with cooling recent ranges");
    expect(html).not.toContain("p92");
    expect(html).not.toContain("· low");
  });

  it("shows actual rejection even when descriptive evidence is bullish", () => {
    const html = renderToStaticMarkup(<BotAnalysisPanel bot={bot} evaluation={withDecisionExplanation("risk_blocked", "BUY")} />);
    expect(html).toContain("Entry rejected by checks");
    expect(html).toContain("daily loss limit is reached");
    expect(html).toContain("Price is rising");
    expect(html).not.toContain("Entry permitted");
  });

  it("labels a completed dry-run attempt without implying live submission", () => {
    const row = withDecisionExplanation("dry_run_attempt", "BUY");
    row.analysis!.bot_decision!.summary = "BUY permitted for a dry-run attempt. No order was sent.";
    const html = renderToStaticMarkup(<BotAnalysisPanel bot={bot} evaluation={row} />);
    expect(html).toContain("Entry permitted for dry run");
    expect(html).toContain("No order was sent");
    expect(html).not.toContain("Entry submitted");
  });

  it("rejects an explanation from a different decision timestamp", () => {
    const row = withDecisionExplanation();
    row.analysis!.bot_decision!.candle_timestamp = "2026-07-09T14:55:00Z";
    row.analysis!.bot_decision!.summary = "WRONG EVALUATION";
    const html = renderToStaticMarkup(<BotAnalysisPanel bot={bot} evaluation={row} />);
    expect(html).not.toContain("WRONG EVALUATION");
    expect(html).toContain("does not match this decision");
    expect(html).toContain("Holding — no new entry");
  });

  it("keeps a zero-bar response unknown rather than neutral and still explains the hold", () => {
    const html = renderToStaticMarkup(<BotAnalysisPanel bot={bot} evaluation={evaluationWithAnalysis(insufficientAnalysis(), 30_275)} onEvaluate={() => undefined} />);
    expect(html).toContain("No directional read yet");
    expect(html).toContain("0 closed 5m candles");
    expect(html).toContain("Freshness unknown");
    expect(html).toContain("Holding — no new entry");
    expect(html).not.toContain("What supports this read");
    expect(html).not.toContain("neutral evidence confirms");
  });

  it("ages without new quotes and permits a whole candle interval plus delivery grace", () => {
    vi.setSystemTime(new Date("2026-07-09T15:19:50Z"));
    render(<BotAnalysisPanel bot={bot} evaluation={evaluation} />);
    expect(screen.getByText("Current closed-candle read")).toBeTruthy();
    act(() => { vi.advanceTimersByTime(15_000); });
    expect(screen.getByText("Stale evaluation")).toBeTruthy();
  });

  it("uses only matching, actually closed chart candles to detect newer data", () => {
    vi.setSystemTime(new Date("2026-07-09T15:10:20Z"));
    const snapshot: BotMarketSnapshot = { contractKey: `${bot.contract_id}:minute:5`, unit: "minute", unitNumber: 5, candles: [chartCandle("2026-07-09T15:05:00Z"), chartCandle("2026-07-09T15:10:00Z")], lastPrice: 106, updatedAt: "2026-07-09T15:10:20Z" };
    const html = renderToStaticMarkup(<BotAnalysisPanel bot={bot} evaluation={evaluation} marketSnapshot={snapshot} />);
    expect(html).toContain("matching chart has 1 newer closed bar");
    expect(html).toContain("Live chart quote — separate");
    expect(html).toContain("quote freshness is unverified");
    const wrong = { ...snapshot, contractKey: "OTHER:minute:5" };
    const otherHtml = renderToStaticMarkup(<BotAnalysisPanel bot={bot} evaluation={evaluation} marketSnapshot={wrong} />);
    expect(otherHtml).not.toContain("newer closed bar");
    expect(otherHtml).not.toContain("Latest chart quote");
    const formingCache = { ...snapshot, candles: [{ ...chartCandle("2026-07-09T15:05:00Z"), fetched_at: "2026-07-09T15:06:00Z" }] };
    expect(renderToStaticMarkup(<BotAnalysisPanel bot={bot} evaluation={evaluation} marketSnapshot={formingCache} />)).not.toContain("newer closed bar");
  });

  it.each([
    ["2026-07-09T15:04:59Z", true],
    ["2026-07-09T15:05:01Z", false],
  ])("checks profile receipt cutoff %s", (receivedThrough, eligible) => {
    const row = evaluationWithAnalysis({ ...analysis, collected_context: {
      as_of: "2026-07-09T15:05:00Z", captured_at: "2026-07-09T15:05:20Z", contract_id: bot.contract_id,
      volume_profile: { status: "partial", eligible: true, contract_id: bot.contract_id, poc: 105,
        observation_start: "2026-07-09T15:00:00Z", observation_end: "2026-07-09T15:04:59Z", received_through: receivedThrough },
    } });
    const html = renderToStaticMarkup(<BotAnalysisPanel bot={bot} evaluation={row} />);
    expect(html).toContain(`Viewer-observed volume profile · ${eligible ? "partial window eligible" : "not eligible for this read"}`);
    expect(html).toContain("Received through:");
  });

  it("does not silently combine observations with a mismatching contract or unknown cutoff", () => {
    const row = evaluationWithAnalysis({ ...analysis, collected_context: {
      as_of: "2026-07-09T15:05:00Z", captured_at: "2026-07-09T15:05:20Z", contract_id: bot.contract_id,
      order_book: { status: "fresh", eligible: true, contract_id: "OTHER", bid: 105, ask: 105.25, spread: 0.25, bid_size: 2, ask_size: null, received_at: "2026-07-09T15:05:01Z" },
      volume_profile: { status: "partial", eligible: false, poc: 105, observation_start: "2026-07-09T14:57:00Z", observation_end: "2026-07-09T15:04:00Z", cumulative_delta: 999 },
    } });
    const html = renderToStaticMarkup(<BotAnalysisPanel bot={bot} evaluation={row} />);
    expect(html).toContain("Level 1 quote · not eligible for this read");
    expect(html).toContain("Recorded best bid 105.00 / ask 105.25");
    expect(html).toContain("ask unavailable");
    expect(html).toContain("not a complete session profile");
    expect(html).toContain("does not establish deeper liquidity");
    expect(html).not.toContain("999");
  });

  it("does not display another selected bot's evaluation", () => {
    const html = renderToStaticMarkup(<BotAnalysisPanel bot={{ ...bot, id: 2 }} evaluation={evaluation} />);
    expect(html).toContain("No evaluation yet");
    expect(html).not.toContain("Price is rising");
  });

  it("distinguishes a closed market from stale missing open-session bars", () => {
    vi.setSystemTime(new Date("2026-07-11T14:00:00Z"));
    const latest = "2026-07-10T20:55:00Z";
    const html = renderToStaticMarkup(<BotAnalysisPanel bot={bot} evaluation={evaluationWithAnalysis({ ...analysis, provenance: { ...analysis.provenance, latest_candle_timestamp: latest, is_stale: false } })} />);
    expect(html).toContain("Market closed");
    expect(html).not.toContain("Stale evaluation");
    expect(html).toContain("closed-session time does not count as a feed delay");
  });
});
