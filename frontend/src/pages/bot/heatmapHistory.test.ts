import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../lib/api";
import { ServerClock } from "./heatmapClock";
import { decodeHeatmapHistory, HeatmapHistoryStore, isCovered, loadedSpans, sessionStartMs, TILE_MS, type HistoryFetcher,
  type RawHeatmapHistory } from "./heatmapHistory";
import { clampTime, defaultViewport, fitPrice, followPrice, panTime, zoomTime, chooseTier, niceSaturation } from "./heatmapViewport";

const contractId = "CON.F.US.MNQ.Z26";
const SESSION = Date.parse("2026-11-03T23:00:00Z");  // Tuesday 18:00 New York
const NOW = Date.parse("2026-11-04T14:30:00Z");

const b64 = (array: ArrayBufferView) => {
  const bytes = new Uint8Array(array.buffer, array.byteOffset, array.byteLength);
  let text = "";
  for (const byte of bytes) text += String.fromCharCode(byte);
  return btoa(text);
};

function raw(overrides: Partial<RawHeatmapHistory> & { tier?: number; start?: number } = {}): RawHeatmapHistory {
  const start = overrides.start ?? NOW - 60_000;
  return {
    schema: "liquidity-heatmap-history-v1", contract_id: contractId, data_live: false, tick_size: 0.25, tier: 0, bucket_ms: 500,
    trade_group_ms: 250, session: { start_ms: SESSION, end_ms: SESSION + 23 * 3600_000, key: "2026-11-03" },
    range: { start_ms: start, end_ms: start + 1000 }, server_time_ms: NOW, history_through_ms: NOW - 60_000,
    coverage: [[SESSION, NOW - 60_000]], gaps: [[NOW - 120_000, NOW - 119_000, "stale"]], notes: [], overlapping_excluded: 0,
    deriver: { state: "current", pending_segments: 0 }, time_origin_ms: start,
    frames: { count: 2, time: b64(new Uint32Array([0, 500])), level_count: b64(new Uint16Array([2, 1])), bid_count: b64(new Uint16Array([1, 1])),
      best_bid: b64(new Int32Array([80000, 80000])), best_ask: b64(new Int32Array([80001, -2147483648])), valid: b64(new Uint16Array([1, 1])),
      tick: b64(new Int32Array([80000, 80001, 80000])), qty: b64(new Float32Array([5, 7, 6])), side: b64(new Uint8Array([1, 2, 1])), levels: 3 },
    trades: { count: 1, time: b64(new Uint32Array([250])), tick: b64(new Int32Array([80001])), side: b64(new Uint8Array([1])),
      volume: b64(new Float32Array([4])), prints: b64(new Uint32Array([2])) },
    trades_truncated: false, trades_min_volume: null, ...overrides,
  };
}

describe("history decoding", () => {
  it("decodes columnar frames, trades and session metadata", () => {
    const decoded = decodeHeatmapHistory(raw(), { contractId, tickSize: 0.25, tier: 0 }, 1);
    expect(Array.from(decoded.frames!.time)).toEqual([NOW - 60_000, NOW - 59_500]);
    expect(Array.from(decoded.frames!.levelStart)).toEqual([0, 2]);
    expect(Array.from(decoded.frames!.qty)).toEqual([5, 7, 6]);
    expect(decoded.trades!.time[0]).toBe(NOW - 59_750);
    expect(decoded.meta.gaps).toEqual([{ start: NOW - 120_000, end: NOW - 119_000, reason: "stale" }]);
  });

  it.each([
    [{ contract_id: "CON.F.US.MNQ.H27" }, /different contract/],
    [{ tick_size: 0.5 }, /tick size/],
    [{ tier: 1 }, /resolution/],
    [{ schema: "other" }, /Unsupported/],
  ])("refuses history from a different scope %#", (patch, message) => {
    expect(() => decodeHeatmapHistory(raw(patch as Partial<RawHeatmapHistory>), { contractId, tickSize: 0.25, tier: 0 }, 1)).toThrow(message);
  });
});

interface Env { local: number; advance: (ms: number) => void; server: () => number }
function setup(make: (env: Env) => HistoryFetcher, start = NOW) {
  const env: Env = { local: start - 10_000, advance: ms => { env.local += ms; }, server: () => env.local + 10_000 };
  const clock = new ServerClock(() => env.local);  // Browser clock 10 s behind the server.
  const fetcher = vi.fn(make(env));
  const store = new HeatmapHistoryStore({ accountId: 7, contractId, tickSize: 0.25 }, clock, fetcher, () => env.local);
  return { clock, store, env, fetcher };
}
const respond = (env: Env, extra: Partial<RawHeatmapHistory> = {}): HistoryFetcher => async args => {
  const server = env.server();
  const session = sessionStartMs(server);
  return raw({ tier: args.tier, bucket_ms: [500, 5000, 60000][args.tier], start: args.start, server_time_ms: server,
    session: { start_ms: session, end_ms: session + 23 * 3600_000, key: "s" }, ...extra });
};
const tileCalls = (fetcher: { mock: { calls: Array<[{ summary: boolean }]> } }) => fetcher.mock.calls.filter(([args]) => !args.summary).length;

describe("history store", () => {
  it("loads the session summary and the visible tiles nearest the right edge, and aligns the clock", async () => {
    const { clock, store, fetcher } = setup(env => respond(env));
    store.ensure(0, NOW - 300_000, NOW);
    await vi.waitFor(() => expect(store.status.state).toBe("ready"));
    const calls = fetcher.mock.calls.map(([args]) => args);
    expect(calls[0]).toMatchObject({ summary: true, tier: 2, start: SESSION });
    const tiles = calls.filter(call => !call.summary);
    expect(tiles.length).toBe(1);  // Two requests in flight at most (matches the backend's read slots).
    expect(tiles[0].start).toBe(Math.floor((NOW - 10_000) / TILE_MS[0]) * TILE_MS[0]);  // Right edge first.
    expect(Math.abs(clock.now() - NOW)).toBeLessThan(50);
    expect(store.status.meta?.coverage).toEqual([[SESSION, NOW - 60_000]]);
  });

  it("keeps the clock and reports the message when history is unavailable, then backs off", async () => {
    let fail = true;
    const { clock, store, env, fetcher } = setup(env => async args => {
      if (fail) throw new ApiError("x", 503, null, { message: "Heatmap history reader is not installed on the recording host yet.", server_time_ms: env.server() });
      return respond(env)(args);
    });
    store.ensure(0, NOW - 60_000, NOW);
    await vi.waitFor(() => expect(store.status.state).toBe("unavailable"));
    expect(store.status.message).toMatch(/not installed/);
    expect(clock.synced).toBe(true);
    const count = fetcher.mock.calls.length;
    store.ensure(0, NOW - 600_000, NOW);
    expect(fetcher.mock.calls.length).toBe(count);  // No other tiles are tried during the backoff.
    fail = false;
    env.advance(31_000);
    store.ensure(0, NOW - 60_000, NOW);
    await vi.waitFor(() => expect(store.status.state).toBe("ready"));
  });

  it("refreshes the open tail but not sealed tiles", async () => {
    const { store, env, fetcher } = setup(env => respond(env, { history_through_ms: NOW - 60_000 }));
    const view = [NOW - 10 * 60_000, NOW - 9 * 60_000] as const;
    store.ensure(0, ...view);
    await vi.waitFor(() => expect(store.status.state).toBe("ready"));
    for (let i = 0; i < 3; i++) {  // Request the remaining margin tiles (two in flight at a time).
      store.ensure(0, ...view);
      await vi.waitFor(() => expect(store.tilesFor(0, view[0] - TILE_MS[0], view[1] + TILE_MS[0]).length).toBeGreaterThan(i));
    }
    await vi.waitFor(() => expect(store.tilesFor(0, view[0] - TILE_MS[0], view[1] + TILE_MS[0]).length).toBe(3));
    const before = tileCalls(fetcher);
    env.advance(20_000);
    store.ensure(0, ...view);
    expect(tileCalls(fetcher)).toBe(before);  // Sealed tiles are immutable.
    store.ensure(0, NOW - 60_000, NOW);
    expect(tileCalls(fetcher)).toBeGreaterThan(before);  // The open tail is fetched.
  });

  it("discards the previous session's tiles at the 6 p.m. rollover", async () => {
    const close = Date.parse("2026-11-04T22:59:30Z");  // 17:59:30 New York (server)
    const { store, env } = setup(env => respond(env), close);
    store.ensure(0, close - 60_000, close);
    await vi.waitFor(() => expect(store.tilesFor(0, close - 120_000, close).length).toBeGreaterThan(0));
    env.advance(60_000);  // 18:00:30 New York.
    store.ensure(0, close + 30_000, close + 60_000);
    expect(store.tilesFor(0, close - 120_000, close)).toEqual([]);
    expect(store.status.meta).toBeNull();
    await vi.waitFor(() => expect(store.status.meta?.session.start).toBe(Date.parse("2026-11-04T23:00:00Z")));
  });

  it("retries only the affected tile when the backend is momentarily busy", async () => {
    let busy = true;
    const { store, env, fetcher } = setup(env => async args => {
      if (busy && !args.summary) throw new ApiError("x", 503, null, { message: "Heatmap history is busy. Retrying shortly.", server_time_ms: env.server() });
      return respond(env)(args);
    });
    store.ensure(0, NOW - 60_000, NOW);
    await vi.waitFor(() => expect(fetcher.mock.calls.length).toBe(2));
    await vi.waitFor(() => expect(store.status.message).toMatch(/busy/));
    await vi.waitFor(() => expect(store.status.summary).not.toBeNull());  // Summary finished; only the tile was busy.
    busy = false;
    env.advance(3_500);
    store.ensure(0, NOW - 60_000, NOW);
    await vi.waitFor(() => expect(store.tilesFor(0, NOW - 60_000, NOW).length).toBeGreaterThan(0));
    expect(store.status.state).toBe("ready");
  });

  it("refreshes a tile loaded while history was still building, even after later responses say current", async () => {
    let state = "building";
    const { store, env, fetcher } = setup(env => respond(env, { history_through_ms: NOW - 60_000 }));
    vi.mocked(fetcher).mockImplementation(async args => respond(env, { history_through_ms: NOW - 60_000, deriver: { state } })(args));
    const view = [NOW - 10 * 60_000, NOW - 9 * 60_000] as const;
    store.ensure(0, ...view);
    await vi.waitFor(() => expect(store.tilesFor(0, ...view).length).toBe(1));
    state = "current";
    env.advance(20_000);
    store.ensure(0, NOW - 60_000, NOW);  // A later response reports current history.
    await vi.waitFor(() => expect(store.status.meta?.deriver?.state).toBe("current"));
    const before = tileCalls(fetcher);
    env.advance(20_000);
    store.ensure(0, ...view);
    expect(tileCalls(fetcher)).toBeGreaterThan(before);  // The partial tile is fetched again.
  });

  it("hides live samples only where loaded tiles actually hold recorded data", () => {
    const tile = (start: number, end: number, through: number | null) => ({ key: "k", tier: 0 as const, start, end, frames: {} as never,
      trades: null, tradesTruncated: false, tradesMinVolume: null, fetchedAt: 0, bytes: 0, through, deriverCurrent: true });
    expect(loadedSpans([tile(0, 100, 1000), tile(100, 200, 150), tile(300, 400, null)], [[0, 120], [130, 500]]))
      .toEqual([[0, 120], [130, 150]]);
  });

  it("finds coverage with binary search", () => {
    const coverage: Array<[number, number]> = [[0, 10], [20, 30], [40, 50]];
    expect([5, 15, 20, 30, 49].map(t => isCovered(coverage, t))).toEqual([true, false, true, false, true]);
  });

  it("matches the backend 6 p.m. New York session rule across DST and weekends", () => {
    expect(sessionStartMs(NOW)).toBe(SESSION);
    expect(sessionStartMs(Date.parse("2026-11-04T23:00:01Z"))).toBe(Date.parse("2026-11-04T23:00:00Z"));
    expect(sessionStartMs(Date.parse("2026-11-07T15:00:00Z"))).toBe(Date.parse("2026-11-05T23:00:00Z"));  // Saturday
    expect(sessionStartMs(Date.parse("2026-07-14T23:00:00Z"))).toBe(Date.parse("2026-07-14T22:00:00Z"));  // EDT
  });
});

describe("server clock", () => {
  it("applies the first offset at once, then slews without running backwards", () => {
    let local = 1_000_000;
    const clock = new ServerClock(() => local);
    clock.observe(2_000_000, local - 20, local);
    expect(clock.now()).toBeCloseTo(2_000_010, 0);
    const before = clock.now();
    clock.observe(1_999_000, local - 10, local);  // Better sample (not 4x faster) says we are 1 s ahead.
    local += 1000;
    const after = clock.now();
    expect(after).toBeGreaterThanOrEqual(before);
    expect(after - before).toBeGreaterThan(900);  // At most 50 ms/s correction.
    expect(clock.skewMs).toBe(-999_005);
  });

  it("replaces a poor first estimate when a much faster sample disagrees", () => {
    const local = 1_000_000;
    const clock = new ServerClock(() => local);
    clock.observe(1_005_000, local - 8000, local);  // Slow first response: 4 s midpoint error possible.
    clock.now();
    clock.observe(1_001_000, local - 20, local);    // Fast sample says the offset is ~1 s, not ~9 s.
    expect(Math.abs(clock.now() - 1_001_010)).toBeLessThan(5);
  });

  it("ignores invalid samples", () => {
    const clock = new ServerClock(() => 5);
    clock.observe(Number.NaN, 0, 1);
    clock.observe(10, 3, 1);
    expect(clock.synced).toBe(false);
  });
});

describe("viewport", () => {
  it("recenters only when the market leaves the central band", () => {
    const view = { ...defaultViewport(), centerTick: 1000, rangeTicks: 100 };
    expect(followPrice(view, 1020)).toBe(view);
    expect(followPrice(view, 1031).centerTick).toBe(1031);
    expect(followPrice({ ...view, priceFollow: false }, 2000).centerTick).toBe(1000);
  });

  it("separates time-follow from manual navigation and clamps to the session", () => {
    const live = defaultViewport();
    const paused = panTime(live, -60_000, NOW, SESSION);
    expect(paused.end).toBe(NOW - 60_000);
    expect(panTime(paused, 59_900, NOW, SESSION).end).toBeNull();  // Reaching now resumes live.
    expect(clampTime({ ...paused, end: SESSION - 3600_000 }, NOW, SESSION).end).toBe(SESSION + paused.span);
    const zoomed = zoomTime(paused, 0.5, NOW - 120_000, NOW, SESSION);
    expect(zoomed.span).toBe(150_000);
    expect(zoomed.end).toBeCloseTo(NOW - 90_000, -2);
    expect(zoomTime(live, 2, null, NOW, SESSION).end).toBeNull();
  });

  it("chooses tiers and fits the price range to an observed extent", () => {
    expect([60_000, 600_000, 3_600_000, 14_400_000].map(chooseTier)).toEqual([0, 0, 1, 2]);
    const fitted = fitPrice({ ...defaultViewport(), centerTick: 0 }, { low: 1000, high: 1400 });
    expect(fitted).toMatchObject({ rangeTicks: 640, centerTick: 1200 });
    // The live price must stay inside the follow band, or auto-recentering would undo the fit.
    const wide = fitPrice({ ...defaultViewport(), centerTick: 0 }, { low: 1000, high: 1400 }, 1390);
    expect(followPrice(wide, 1390)).toBe(wide);
    expect(niceSaturation([1, 2, 3, 4, 40])).toBe(50);
    expect(niceSaturation([])).toBe(25);
  });
});
