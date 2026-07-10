import { describe, expect, it } from "vitest";

import type { BotActivity, BotConfig, BotEvaluation, ProjectXMarketCandle } from "../../lib/types";
import {
  buildEvaluationOverlayGeometry,
  buildEvaluationOverlayModel,
  buildEvaluationOverlayStaleness,
  evaluationCandleTimestampMs,
  selectLatestActionableEvaluation,
} from "./botEvaluationOverlay";

describe("selectLatestActionableEvaluation", () => {
  it("keeps the latest actionable evaluation when a newer run returns HOLD", () => {
    const cached = marketEvaluation({ action: "BUY", createdAt: "2026-07-09T14:00:00.000Z" });
    const hold = marketEvaluation({ action: "HOLD", createdAt: "2026-07-09T14:05:00.000Z" });

    expect(
      selectLatestActionableEvaluation({ bot: cached.config, activity: null, lastEvaluation: hold, cachedEvaluation: cached }),
    ).toBe(cached);
  });

  it("rejects cached levels and activity from a bot's previous contract", () => {
    const oldEvaluation = marketEvaluation({ contractId: "OLD.CONTRACT" });
    const editedBot = { ...oldEvaluation.config, contract_id: "NEW.CONTRACT" };
    const oldActivity = {
      config: oldEvaluation.config,
      decisions: [oldEvaluation.decision],
      runs: [],
      order_attempts: [],
      risk_events: [],
    } as BotActivity;

    expect(
      selectLatestActionableEvaluation({
        bot: editedBot,
        activity: oldActivity,
        lastEvaluation: null,
        cachedEvaluation: oldEvaluation,
      }),
    ).toBeNull();
  });

  it("reconstructs only API-supported decision entry data from newer activity", () => {
    const cached = marketEvaluation({ action: "BUY", createdAt: "2026-07-09T14:00:00.000Z" });
    const newer = marketEvaluation({ action: "SELL", createdAt: "2026-07-09T14:10:00.000Z", decisionPrice: 99 });
    const activity = {
      config: cached.config,
      decisions: [newer.decision],
      runs: [],
      order_attempts: [],
      risk_events: [],
    } as BotActivity;

    const selected = selectLatestActionableEvaluation({
      bot: cached.config,
      activity,
      lastEvaluation: null,
      cachedEvaluation: cached,
    });

    expect(selected?.decision).toBe(newer.decision);
    expect(selected?.trade_levels).toBeNull();
    expect(buildEvaluationOverlayModel(selected)?.levels).toEqual({ entry: 99, stop: null, target: null });
  });
});

describe("buildEvaluationOverlayGeometry", () => {
  it("builds long risk and reward price geometry from explicit levels", () => {
    const geometry = buildEvaluationOverlayGeometry("BUY", {
      entry: 100,
      stop: 98,
      target: 105,
    });

    expect(geometry.riskBand).toEqual({
      kind: "risk",
      fromPrice: 100,
      toPrice: 98,
      lowPrice: 98,
      highPrice: 100,
    });
    expect(geometry.rewardBand).toEqual({
      kind: "reward",
      fromPrice: 100,
      toPrice: 105,
      lowPrice: 100,
      highPrice: 105,
    });
    expect(geometry.riskRewardRatio).toBe(2.5);
  });

  it("builds short geometry and declines misleading fills for reversed levels", () => {
    const shortGeometry = buildEvaluationOverlayGeometry("SELL", {
      entry: 100,
      stop: 102,
      target: 96,
    });
    expect(shortGeometry.riskBand?.lowPrice).toBe(100);
    expect(shortGeometry.riskBand?.highPrice).toBe(102);
    expect(shortGeometry.rewardBand?.lowPrice).toBe(96);
    expect(shortGeometry.rewardBand?.highPrice).toBe(100);
    expect(shortGeometry.riskRewardRatio).toBe(2);

    const invalidGeometry = buildEvaluationOverlayGeometry("BUY", {
      entry: 100,
      stop: 101,
      target: 99,
    });
    expect(invalidGeometry.stop?.price).toBe(101);
    expect(invalidGeometry.target?.price).toBe(99);
    expect(invalidGeometry.riskBand).toBeNull();
    expect(invalidGeometry.rewardBand).toBeNull();
    expect(invalidGeometry.riskRewardRatio).toBeNull();
  });

  it("does not invent geometry for missing levels", () => {
    const geometry = buildEvaluationOverlayGeometry("BUY", {
      entry: 100,
      stop: null,
      target: null,
    });

    expect(geometry.entry?.price).toBe(100);
    expect(geometry.stop).toBeNull();
    expect(geometry.target).toBeNull();
    expect(geometry.riskBand).toBeNull();
    expect(geometry.rewardBand).toBeNull();
  });
});

describe("buildEvaluationOverlayModel", () => {
  it("uses only standardized API levels and reports staleness in chart bars", () => {
    const model = buildEvaluationOverlayModel(
      evaluation({
        tradeLevels: { entry: 100, stop: 98, target: 104 },
        analysisTimestamp: "2026-07-09T14:00:00.000Z",
      }),
      {
        latestClosedTimestamp: "2026-07-09T14:10:00.000Z",
        timeframeUnit: "minute",
        timeframeUnitNumber: 5,
      },
    );

    expect(model).not.toBeNull();
    expect(model?.levels).toEqual({ entry: 100, stop: 98, target: 104 });
    expect(model?.timestamp).toBe("2026-07-09T14:00:00.000Z");
    expect(model?.staleness).toMatchObject({ status: "stale", isStale: true, barsBehind: 2 });
    expect(model?.riskRewardRatio).toBe(2);
    expect(model?.riskRewardRatioSource).toBe("levels");
  });

  it("never treats analysis invalidation or scored feature distances as missing strategy levels", () => {
    const subject = evaluation({ tradeLevels: null, decisionPrice: 100 });
    subject.analysis = {
      ...subject.analysis!,
      invalidation_level: 98,
      trade_evaluation: {
        total_score: 80,
        score: 80,
        grade: "A",
        decision: "take",
        confidence: "high",
        summary: "Test",
        reasons: [],
        warnings: [],
        positives: [],
        suggested_adjustments: [],
        category_scores: {},
        features: {
          risk_points: 2,
          reward_points: 6,
          risk_reward_ratio: 3,
        },
      } as unknown as NonNullable<BotEvaluation["analysis"]>["trade_evaluation"],
    };

    const model = buildEvaluationOverlayModel(subject);

    expect(model?.levels).toEqual({ entry: 100, stop: null, target: null });
    expect(model?.geometry.riskBand).toBeNull();
    expect(model?.geometry.rewardBand).toBeNull();
    expect(model?.riskRewardRatio).toBe(3);
    expect(model?.riskRewardRatioSource).toBe("api");
  });

  it("returns no overlay for a non-actionable evaluation", () => {
    expect(buildEvaluationOverlayModel(evaluation({ action: "HOLD" }))).toBeNull();
  });
});

describe("evaluation timestamp and stale semantics", () => {
  it("prefers the analysis timestamp, then latest closed evaluation candle, then decision timestamp", () => {
    const withAnalysis = evaluation({
      analysisTimestamp: "2026-07-09T14:00:00.000Z",
      candles: [candle("2026-07-09T14:05:00.000Z")],
      decisionTimestamp: "2026-07-09T14:10:00.000Z",
    });
    expect(evaluationCandleTimestampMs(withAnalysis)).toBe(Date.parse("2026-07-09T14:00:00.000Z"));

    const withCandles = evaluation({
      analysisTimestamp: null,
      candles: [candle("2026-07-09T14:05:00.000Z"), candle("2026-07-09T14:10:00.000Z", true)],
      decisionTimestamp: "2026-07-09T14:15:00.000Z",
    });
    expect(evaluationCandleTimestampMs(withCandles)).toBe(Date.parse("2026-07-09T14:05:00.000Z"));

    const withDecision = evaluation({
      analysisTimestamp: null,
      candles: [candle("2026-07-09T14:10:00.000Z", true)],
      decisionTimestamp: "2026-07-09T14:15:00.000Z",
    });
    expect(evaluationCandleTimestampMs(withDecision)).toBe(Date.parse("2026-07-09T14:15:00.000Z"));
  });

  it("marks one newer bar fresh, two newer bars stale, and missing timestamps unknown", () => {
    expect(
      buildEvaluationOverlayStaleness(
        Date.parse("2026-07-09T14:00:00.000Z"),
        "2026-07-09T14:05:00.000Z",
        "minute",
        5,
      ),
    ).toMatchObject({ status: "fresh", isStale: false, barsBehind: 1 });
    expect(
      buildEvaluationOverlayStaleness(
        Date.parse("2026-07-09T14:00:00.000Z"),
        "2026-07-09T14:10:00.000Z",
        "minute",
        5,
      ),
    ).toMatchObject({ status: "stale", isStale: true, barsBehind: 2 });
    expect(buildEvaluationOverlayStaleness(null, null, "minute", 5)).toMatchObject({
      status: "unknown",
      isStale: false,
      barsBehind: null,
    });
    expect(
      buildEvaluationOverlayStaleness(Number.NaN, "2026-07-09T14:10:00.000Z", "minute", 5, Number.NaN),
    ).toMatchObject({ status: "unknown", barsBehind: null, staleAfterBars: 2 });
  });
});

interface EvaluationOverrides {
  action?: BotEvaluation["decision"]["action"];
  decisionPrice?: number | null;
  decisionTimestamp?: string | null;
  analysisTimestamp?: string | null;
  tradeLevels?: BotEvaluation["trade_levels"];
  candles?: ProjectXMarketCandle[];
}

function marketEvaluation({
  action = "BUY",
  contractId = "CON.F.US.MNQ.U26",
  createdAt = "2026-07-09T14:00:02.000Z",
  decisionPrice = 100,
}: {
  action?: BotEvaluation["decision"]["action"];
  contractId?: string;
  createdAt?: string;
  decisionPrice?: number;
} = {}): BotEvaluation {
  const subject = evaluation({ action, decisionPrice });
  subject.config = {
    ...subject.config,
    id: 42,
    contract_id: contractId,
    timeframe_unit: "minute",
    timeframe_unit_number: 5,
  } as BotConfig;
  subject.decision = {
    ...subject.decision,
    id: action === "SELL" ? 100 : 99,
    bot_config_id: 42,
    contract_id: contractId,
    created_at: createdAt,
  };
  return subject;
}

function evaluation(overrides: EvaluationOverrides = {}): BotEvaluation {
  return {
    status: "evaluated",
    correlation_id: null,
    idempotency_key: null,
    duplicate_of_order_attempt_id: null,
    config: {
      id: 42,
      timeframe_unit: "minute",
      timeframe_unit_number: 5,
    },
    run: null,
    decision: {
      id: 99,
      action: overrides.action ?? "BUY",
      price: overrides.decisionPrice === undefined ? 100 : overrides.decisionPrice,
      candle_timestamp:
        overrides.decisionTimestamp === undefined ? "2026-07-09T14:00:00.000Z" : overrides.decisionTimestamp,
      created_at: "2026-07-09T14:00:02.000Z",
    },
    order_attempt: null,
    risk_events: [],
    trade_levels:
      overrides.tradeLevels === undefined ? { entry: 100, stop: 98, target: 104 } : overrides.tradeLevels,
    analysis: {
      candle_timestamp:
        overrides.analysisTimestamp === undefined ? "2026-07-09T14:00:00.000Z" : overrides.analysisTimestamp,
      generated_at: "2026-07-09T14:00:01.000Z",
    },
    candles: overrides.candles ?? [],
  } as unknown as BotEvaluation;
}

function candle(timestamp: string, isPartial = false): ProjectXMarketCandle {
  return {
    id: null,
    contract_id: "CON.F.US.MNQ.U26",
    symbol: "MNQ",
    live: false,
    unit: "minute",
    unit_number: 5,
    timestamp,
    open: 100,
    high: 101,
    low: 99,
    close: 100,
    volume: 10,
    is_partial: isPartial,
    fetched_at: null,
  };
}
