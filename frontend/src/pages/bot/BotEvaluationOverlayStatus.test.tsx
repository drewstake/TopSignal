import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { EvaluationOverlayModel } from "./botEvaluationOverlay";
import { BotEvaluationOverlayStatus } from "./BotEvaluationOverlayStatus";

const model: EvaluationOverlayModel = {
  decisionId: 1,
  action: "BUY",
  direction: "long",
  timestamp: "2026-07-09T14:00:00.000Z",
  evaluatedAt: "2026-07-09T14:00:01.000Z",
  levels: { entry: 100, stop: 98, target: null },
  geometry: {
    entry: { role: "entry", label: "Entry", price: 100 },
    stop: { role: "stop", label: "Stop", price: 98 },
    target: null,
    riskBand: { kind: "risk", fromPrice: 100, toPrice: 98, lowPrice: 98, highPrice: 100 },
    rewardBand: null,
    riskRewardRatio: null,
  },
  staleness: {
    status: "stale",
    isStale: true,
    barsBehind: 3,
    staleAfterBars: 2,
    latestClosedTimestamp: "2026-07-09T14:15:00.000Z",
  },
  riskRewardRatio: 2,
  riskRewardRatioSource: "api",
};

describe("BotEvaluationOverlayStatus", () => {
  it("shows supported values and never invents an absent target", () => {
    const markup = renderToStaticMarkup(<BotEvaluationOverlayStatus model={model} />);

    expect(markup).toContain("Latest BUY");
    expect(markup).toContain("3 bars behind");
    expect(markup).toContain("Evaluated Jul 9, 10:00:01 AM ET");
    expect(markup).toContain("Candle Jul 9, 10:00:00 AM ET");
    expect(markup).toContain("Entry");
    expect(markup).toContain("Stop");
    expect(markup).not.toContain("Target");
    expect(markup).toContain("R:R 1:2.00");
  });

  it("does not describe unknown freshness or missing timestamps as current", () => {
    const markup = renderToStaticMarkup(
      <BotEvaluationOverlayStatus
        model={{
          ...model,
          timestamp: null,
          evaluatedAt: null,
          staleness: {
            ...model.staleness,
            status: "unknown",
            isStale: false,
            barsBehind: null,
          },
        }}
      />,
    );

    expect(markup).toContain("Freshness unknown");
    expect(markup).not.toContain("Current");
    expect(markup).toContain("Evaluation time unavailable");
    expect(markup).toContain("Candle time unavailable");
    expect(markup).not.toContain("unavailable ET");
  });
});
