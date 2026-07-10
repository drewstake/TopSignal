import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { BotChartStateOverlay, BotChartStatus } from "./BotChartStatus";
import { resolveBotChartViewState } from "./botChartViewState";

describe("resolveBotChartViewState", () => {
  it("distinguishes unselected, loading, error, empty, and ready states", () => {
    expect(resolveBotChartViewState({ hasBot: false, loading: false, error: null, candleCount: 0 }).kind).toBe("unselected");
    expect(resolveBotChartViewState({ hasBot: true, loading: true, error: null, candleCount: 0 }).kind).toBe("loading");
    expect(resolveBotChartViewState({ hasBot: true, loading: false, error: "Network failed", candleCount: 0 })).toEqual({
      kind: "error",
      message: "Network failed",
    });
    expect(resolveBotChartViewState({ hasBot: true, loading: false, error: null, candleCount: 0 }).kind).toBe("empty");
    expect(resolveBotChartViewState({ hasBot: true, loading: true, error: null, candleCount: 4 }).kind).toBe("ready");
  });

  it("renders accessible loading and error overlays", () => {
    const loading = renderToStaticMarkup(
      <BotChartStateOverlay state={resolveBotChartViewState({ hasBot: true, loading: true, error: null, candleCount: 0 })} />,
    );
    const error = renderToStaticMarkup(
      <BotChartStateOverlay state={resolveBotChartViewState({ hasBot: true, loading: false, error: "No connection", candleCount: 0 })} />,
    );

    expect(loading).toContain('role="status"');
    expect(loading).toContain('aria-busy="true"');
    expect(error).toContain('role="alert"');
    expect(error).toContain("No connection");
  });

  it("renders explicit empty and unselected chart states", () => {
    const empty = renderToStaticMarkup(
      <BotChartStateOverlay state={resolveBotChartViewState({ hasBot: true, loading: false, error: null, candleCount: 0 })} />,
    );
    const unselected = renderToStaticMarkup(
      <BotChartStateOverlay state={resolveBotChartViewState({ hasBot: false, loading: false, error: null, candleCount: 0 })} />,
    );

    expect(empty).toContain('role="status"');
    expect(empty).toContain("No candles returned for this chart window.");
    expect(unselected).toContain("Select or save a bot");
  });
});

describe("BotChartStatus", () => {
  it("communicates connection, bar authority, freshness, gaps, timeframe, and timezone", () => {
    const markup = renderToStaticMarkup(
      <BotChartStatus
        connection="delayed"
        barState="closed"
        lastRefreshText="Refreshed 15s ago"
        stale
        unrepairedGapCount={2}
        timeframeLabel="5m"
        timezoneLabel="ET"
      />,
    );

    expect(markup).toContain("Delayed / polling");
    expect(markup).toContain("Closed bar");
    expect(markup).toContain("Stale · Refreshed 15s ago");
    expect(markup).toContain("2 unrepaired gaps");
    expect(markup).toContain("5m · ET");
    expect(markup.match(/aria-live="polite"/g)).toHaveLength(1);
    expect(markup).not.toContain('aria-label="Chart data status" aria-live="polite"');
  });

  it("gives a stale price feed a distinct, assertive visual state", () => {
    const markup = renderToStaticMarkup(
      <BotChartStatus
        connection="stale"
        barState="partial"
        lastRefreshText="Refreshed 2m ago"
        stale
        unrepairedGapCount={0}
        timeframeLabel="1m"
        timezoneLabel="ET"
      />,
    );

    expect(markup).toContain("Stale price feed");
    expect(markup).toContain("border-app-negative/35");
    expect(markup).toContain("text-app-negative");
  });
});
