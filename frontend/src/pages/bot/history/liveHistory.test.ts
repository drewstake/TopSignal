import { expect, it } from "vitest";
import { OrderBookStore } from "../orderBook";
import { continuousSegments, sessionWindow, type BookHistory } from "./history";
import { mergeLiveHistory, mergeSessionArchive, sampleLiquidity } from "./liveHistory";

const contract = "CON.F.US.MNQ.Z26";
const now = Date.parse("2026-11-02T02:00:00Z");
function makeBook() {
  const store = new OrderBookStore(contract, 1);
  store.setConnectionState({ contract_id: contract, state: "connected" });
  store.applySnapshot({ contract_id: contract, timestamp: new Date(now).toISOString(), sequence: 1,
    bids: Array.from({ length: 20 }, (_, i) => ({ price: 100 - i, size: 5 })),
    asks: Array.from({ length: 20 }, (_, i) => ({ price: 101 + i, size: 2 })) });
  return store;
}
it("samples all received levels independently of the ladder filter and respects history scope", () => {
  const store = makeBook();
  expect(sampleLiquidity(store, "all", now)).toMatchObject({ bid_contracts: 100, ask_contracts: 40, bid_levels: 20, ask_levels: 20, mid_price: 100.5, spread: 1, quality_flags: [] });
  expect(sampleLiquidity(store, "10", now)).toMatchObject({ bid_contracts: 50, ask_contracts: 20, bid_levels: 10 });
  expect(sampleLiquidity(store, "all", now + 2001)).toBeNull();
  expect(sampleLiquidity(store, "all", now - 2000)).toBeNull();
});
it("rejects disconnected and sequence-gap books and separates reconnect generations", () => {
  const store = makeBook();
  const first = sampleLiquidity(store, "all", now)!;
  store.applyUpdate({ contract_id: contract, timestamp: new Date(now + 1000).toISOString(), sequence: 3, side: "bid", price: 100, size: 10 });
  expect(sampleLiquidity(store, "all", now + 1000)).toBeNull();
  store.setConnectionState({ contract_id: contract, state: "connected" });
  expect(sampleLiquidity(store, "all", now + 1000)).toBeNull();
  store.applySnapshot({ contract_id: contract, timestamp: new Date(now + 1000).toISOString(), sequence: 3, bids: [{ price: 100, size: 10 }], asks: [{ price: 101, size: 5 }] });
  const next = sampleLiquidity(store, "all", now + 1000)!;
  expect(continuousSegments([first, next])).toHaveLength(2);
  store.setConnectionState({ contract_id: contract, state: "disconnected" });
  expect(sampleLiquidity(store, "all", now + 1000)).toBeNull();
  store.selectContract("OTHER");
  expect(sampleLiquidity(store, "all", now + 1000)).toBeNull();
});
it("keeps ordinary full-book updates continuous, but breaks on provider reset", () => {
  const store = makeBook();
  const first = sampleLiquidity(store, "all", now)!;
  const snapshot = { contract_id: contract, timestamp: new Date(now + 1000).toISOString(), sequence: 2, bids: [{ price: 101, size: 10 }], asks: [{ price: 102, size: 5 }] };
  store.applySnapshot(snapshot);
  const next = sampleLiquidity(store, "all", now + 1000)!;
  expect(continuousSegments([first, next])).toHaveLength(1);
  store.applySnapshot({ ...snapshot, timestamp: new Date(now + 2000).toISOString(), sequence: 3, reset: true });
  expect(continuousSegments([next, sampleLiquidity(store, "all", now + 2000)!])).toHaveLength(2);
});
it("merges a live tail without bridging sources and prefers sealed data when it catches up", () => {
  const point = sampleLiquidity(makeBook(), "all", now)!;
  const saved = { ...point, time: point.time - 60, timestamp: new Date(now - 60000).toISOString(), capture_id: "recorder" };
  const history: BookHistory = { points: [saved], scope: "all", start: new Date(now - 900000).toISOString(), end: saved.timestamp, latest: saved.timestamp, earliest: saved.timestamp, available_days: [], contracts: [contract], skipped_segments: 0, sealed_only: true, interval_ms: 250, source: "recorder", generated_at: saved.timestamp };
  const merged = mergeLiveHistory(history, [point], "all")!;
  expect(merged.points).toEqual([saved, point]);
  expect(merged.latest).toBe(point.timestamp);
  expect(continuousSegments(merged.points)).toHaveLength(2);
  expect(mergeLiveHistory({ ...history, points: [{ ...point, capture_id: "recorder" }] }, [point], "all")?.points).toHaveLength(1);
  expect(mergeLiveHistory(null, [point], "all")?.points).toEqual([point]);
});

it("keeps the full session's early measurements while the live edge advances", () => {
  const point = sampleLiquidity(makeBook(), "all", now)!;
  const start = sessionWindow(now).start;
  const early = { ...point, time: start + 60, timestamp: new Date((start + 60) * 1000).toISOString(), capture_id: "saved" };
  const history = { points: [early], scope: "all", start: new Date(start * 1000).toISOString(), end: early.timestamp,
    latest: early.timestamp, earliest: early.timestamp, available_days: [], contracts: [contract], skipped_segments: 0,
    sealed_only: true, interval_ms: 10000, source: "recorder", generated_at: early.timestamp, window: "session" } as BookHistory;
  expect(mergeLiveHistory(history, [point], "all", "session")?.points).toEqual([early, point]);
  expect(mergeLiveHistory(history, [point], "all", "session")?.start).toBe(history.start);
  const tail = { ...history, points: [{ ...point, capture_id: "saved" }], start: new Date(now - 900000).toISOString(), latest: point.timestamp, end: point.timestamp };
  const refreshed = mergeSessionArchive(history, tail, now);
  expect(refreshed.points[0].time).toBe(early.time);
  expect(refreshed.points.at(-1)?.time).toBe(point.time);
  expect(refreshed.start).toBe(history.start);
  expect(mergeSessionArchive(history, { ...tail, points: [] }, now + 86400000).points).toEqual([]);
});

it("matches the Eastern session across DST, daily close, and weekends", () => {
  for (const [at, start, end] of [
    ["2026-11-02T02:00:00Z", "2026-11-01T23:00:00Z", "2026-11-02T02:00:00Z"],
    ["2026-03-09T02:00:00Z", "2026-03-08T22:00:00Z", "2026-03-09T02:00:00Z"],
    ["2026-11-02T22:30:00Z", "2026-11-01T23:00:00Z", "2026-11-02T22:00:00Z"],
    ["2026-11-07T14:00:00Z", "2026-11-05T23:00:00Z", "2026-11-06T22:00:00Z"],
  ]) {
    expect(sessionWindow(Date.parse(at))).toEqual({ start: Date.parse(start) / 1000, end: Date.parse(end) / 1000 });
  }
});

it("connects overview samples but never bridges flagged missing depth", () => {
  const point = { ...sampleLiquidity(makeBook(), "all", now)!, sample_interval_ms: 10000 };
  const next = { ...point, time: point.time + 10 };
  expect(continuousSegments([point, next])).toHaveLength(1);
  expect(continuousSegments([point, { ...next, quality_flags: ["sampling_gap"] }, { ...next, time: point.time + 20 }])).toHaveLength(2);
});
