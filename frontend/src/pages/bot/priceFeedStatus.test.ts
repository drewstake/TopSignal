import { describe, expect, it } from "vitest";
import type { MarketPriceStreamHealth } from "../../lib/api";
import { describePriceFeed, resolvePriceFeedStatus, type PriceFeedInputs } from "./priceFeedStatus";

const NOW = Date.parse("2026-11-11T19:30:00Z");

function health(patch: Partial<MarketPriceStreamHealth> = {}): MarketPriceStreamHealth {
  return {
    transport: "open", upstream: "connected", lastActivityAt: NOW - 2_000, lastConfirmedAt: NOW - 2_000,
    upstreamIdleMs: 1_000, serverClockOffsetMs: 0, heartbeats: true, reconnects: 0,
    lastInterruption: null, retryAt: null, ...patch,
  };
}

function inputs(patch: Partial<PriceFeedInputs> = {}): PriceFeedInputs {
  return {
    nowMs: NOW, sessionOpen: true, hasPrice: true, health: health(), streamStartedAtMs: NOW - 600_000,
    lastStreamPriceAtMs: NOW - 1_000, lastTradeTimestampMs: NOW - 1_000, lastPollSuccessAtMs: null,
    lastConfirmedLiveAtMs: NOW - 1_000, ...patch,
  };
}

describe("resolvePriceFeedStatus", () => {
  it("shows a streaming market as live", () => {
    expect(resolvePriceFeedStatus(inputs())).toMatchObject({ connection: "live", label: "Live stream", pricePrefix: "Live" });
  });

  it("keeps a healthy stream live through quiet tape, labelling the price as the last trade", () => {
    const quiet = resolvePriceFeedStatus(inputs({ lastStreamPriceAtMs: NOW - 45_000, lastTradeTimestampMs: NOW - 45_000 }));
    expect(quiet).toMatchObject({ connection: "live", label: "Live · quiet", pricePrefix: "Last" });
    expect(quiet.detail).toContain("last trade 45s ago");
  });

  it("judges a replayed last trade by its provider timestamp on the backend clock", () => {
    // Received just now on resume, but the trade itself printed 50s ago (backend clock 2s ahead).
    const replayed = resolvePriceFeedStatus(inputs({
      health: health({ serverClockOffsetMs: 2_000 }), lastStreamPriceAtMs: NOW, lastTradeTimestampMs: NOW + 2_000 - 50_000,
    }));
    expect(replayed).toMatchObject({ connection: "live", label: "Live · quiet", pricePrefix: "Last" });
    expect(replayed.detail).toContain("last trade 50s ago");
  });

  it("falls back to an explicitly delayed REST quote when heartbeats stop", () => {
    const status = resolvePriceFeedStatus(inputs({
      health: health({ lastActivityAt: NOW - 20_000 }), lastStreamPriceAtMs: NOW - 20_000, lastPollSuccessAtMs: NOW - 3_000,
    }));
    expect(status).toMatchObject({ connection: "delayed", label: "Delayed / polling", pricePrefix: "Delayed" });
  });

  it("reads Reconnecting, not Stale, for a fresh interruption or resume", () => {
    const dropped = resolvePriceFeedStatus(inputs({
      health: health({ transport: "retrying", upstream: null, lastActivityAt: NOW - 8_000, lastConfirmedAt: NOW - 8_000, reconnects: 1, lastInterruption: "idle_timeout", retryAt: NOW + 2_000 }),
      lastStreamPriceAtMs: NOW - 8_000, lastConfirmedLiveAtMs: NOW - 8_000,
    }));
    expect(dropped).toMatchObject({ connection: "reconnecting", label: "Reconnecting", pricePrefix: "Last" });
    expect(dropped.detail).toContain("retrying in 2s");
    expect(dropped.detail).toContain("1 reconnect (last: no data received)");

    const resumed = resolvePriceFeedStatus(inputs({
      health: health({ transport: "connecting", upstream: null, lastActivityAt: null, lastConfirmedAt: null }),
      streamStartedAtMs: NOW - 2_000, lastStreamPriceAtMs: NOW - 300_000, lastConfirmedLiveAtMs: NOW - 300_000,
    }));
    expect(resumed.connection).toBe("reconnecting");
  });

  it("escalates to stale once nothing has confirmed the feed within the recovery grace", () => {
    const status = resolvePriceFeedStatus(inputs({
      health: health({ transport: "retrying", upstream: null, lastActivityAt: NOW - 45_000, lastConfirmedAt: NOW - 45_000 }),
      lastStreamPriceAtMs: NOW - 45_000, lastConfirmedLiveAtMs: NOW - 45_000,
    }));
    expect(status).toMatchObject({ connection: "stale", label: "Stale price feed", pricePrefix: "Stale" });
  });

  it("treats a ProjectX outage behind healthy heartbeats as stale after the grace", () => {
    const status = resolvePriceFeedStatus(inputs({
      health: health({ upstream: "reconnecting", lastActivityAt: NOW - 1_000, lastConfirmedAt: NOW - 40_000 }),
      lastStreamPriceAtMs: NOW - 40_000, lastConfirmedLiveAtMs: NOW - 40_000,
    }));
    expect(status.connection).toBe("stale");
    expect(status.detail).toContain("ProjectX: reconnecting");
  });

  it("reports provider silence as a diagnostic without overriding the socket's health", () => {
    // The backend's websocket ping/pong timeout owns dead-socket detection.
    const status = resolvePriceFeedStatus(inputs({ health: health({ upstreamIdleMs: 120_000 }), lastStreamPriceAtMs: NOW - 120_000, lastTradeTimestampMs: NOW - 120_000 }));
    expect(status).toMatchObject({ connection: "live", label: "Live · quiet", pricePrefix: "Last" });
    expect(status.detail).toContain("ProjectX silent 2m");
  });

  it("reports scheduled and provider-reported closures as closed", () => {
    expect(resolvePriceFeedStatus(inputs({ sessionOpen: false })).connection).toBe("closed");
    expect(resolvePriceFeedStatus(inputs({ health: health({ upstream: "market_closed" }) })).connection).toBe("closed");
  });

  it("does not wait out the grace for a stopped stream", () => {
    const status = resolvePriceFeedStatus(inputs({
      health: health({ transport: "stopped", lastActivityAt: NOW - 5_000, lastInterruption: "http_401" }),
      lastStreamPriceAtMs: NOW - 20_000, lastConfirmedLiveAtMs: NOW - 5_000,
    }));
    expect(status.connection).toBe("stale");
  });

  it("is unavailable when nothing has ever been shown and nothing is recovering", () => {
    const status = resolvePriceFeedStatus(inputs({
      hasPrice: false, health: null, streamStartedAtMs: null, lastStreamPriceAtMs: null, lastTradeTimestampMs: null, lastConfirmedLiveAtMs: null,
    }));
    expect(status.connection).toBe("unavailable");
  });

  it("treats a recent trade as proof of a live path when no health is reported", () => {
    expect(resolvePriceFeedStatus(inputs({ health: null })).connection).toBe("live");
  });
});

describe("describePriceFeed", () => {
  it("separates browser transport, ProjectX state, clock skew and REST fallback", () => {
    const detail = describePriceFeed(inputs({
      health: health({ serverClockOffsetMs: -3_000, reconnects: 2, lastInterruption: "stale_after_wake" }),
      lastPollSuccessAtMs: NOW - 4_000,
    }));
    expect(detail).toContain("TopSignal stream: open");
    expect(detail).toContain("ProjectX: connected");
    expect(detail).toContain("clock offset −3s");
    expect(detail).toContain("2 reconnects (last: silent after sleep/network change)");
    expect(detail).toContain("REST quote 4s ago");
  });
});
