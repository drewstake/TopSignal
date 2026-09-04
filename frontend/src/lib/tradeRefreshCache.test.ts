import { describe, expect, it, vi } from "vitest";
import { TradeRefreshCache, tradeRefreshCadence } from "./tradeRefreshCache";

const friday = Date.parse("2026-09-04T21:05:00Z");
const range = { start: "2026-08-31T22:00:00Z", end: new Date(friday).toISOString() };

function setup() {
  const cache = new TradeRefreshCache<number>();
  const load = vi.fn(async () => 1);
  const input = { scope: "user:a", accountId: 1, range, automatic: true, now: friday, load };
  return { cache, load, input };
}

describe("automatic trade refresh", () => {
  it("reuses Friday's successful tail sync despite a newer end timestamp on remount", async () => {
    const { cache, load, input } = setup();
    await cache.run(input);
    await cache.run({ ...input, now: friday + 5 * 60_000, range: { ...range, end: new Date(friday + 5 * 60_000).toISOString() } });
    expect(load).toHaveBeenCalledTimes(1);
    await cache.run({ ...input, now: friday + 10 * 60_000 });
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("shares an in-flight request for overlapping covered ranges", async () => {
    const { cache, load, input } = setup();
    await Promise.all([cache.run(input), cache.run(input), cache.run({ ...input, range: { ...range, start: "2026-09-02T22:00:00Z" } })]);
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("refreshes uncovered earlier history, later historical ranges, and unknown default lookbacks", async () => {
    const { cache, load, input } = setup();
    await cache.run({ ...input, range: { start: "2026-09-02T22:00:00Z", end: "2026-09-03T21:00:00Z" } });
    await cache.run(input);
    await cache.run({ ...input, range: {} });
    await cache.run({ ...input, range: { ...range, start: "2026-07-01T00:00:00Z" } });
    expect(load).toHaveBeenCalledTimes(4);
  });

  it("isolates accounts and signed-in users", async () => {
    const { cache, load, input } = setup();
    await cache.run(input);
    await cache.run({ ...input, accountId: 2 });
    await cache.run({ ...input, scope: "user:b" });
    expect(load).toHaveBeenCalledTimes(3);
  });

  it("always honors manual refresh and reuses its new observation afterwards", async () => {
    const { cache, load, input } = setup();
    await cache.run(input);
    await cache.run({ ...input, automatic: false });
    await cache.run(input);
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("never caches a rejected sync as successful", async () => {
    const { cache, load, input } = setup();
    load.mockRejectedValueOnce(new Error("Provider unavailable"));
    await expect(cache.run(input)).rejects.toThrow("Provider unavailable");
    await cache.run(input);
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("invalidates observations after local data changes without reviving older pending work", async () => {
    const { cache, load, input } = setup();
    let finish!: (result: number) => void;
    load.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    const old = cache.run(input);
    await Promise.resolve();
    cache.invalidate(1);
    await cache.run(input);
    finish(0);
    await old;
    expect(await cache.run(input)).toBe(1);
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("expires open-session observations after 30 seconds", async () => {
    const { cache, load, input } = setup();
    const now = Date.parse("2026-09-04T19:00:00Z");
    const open = { ...input, now, range: { start: range.start } };
    await cache.run(open);
    await cache.run({ ...open, now: now + 29_000 });
    await cache.run({ ...open, now: now + 30_000 });
    expect(load).toHaveBeenCalledTimes(2);
  });

  it.each([
    ["2026-09-06T21:59:59Z", "2026-09-06T22:00:00Z"],
    ["2026-12-06T22:59:59Z", "2026-12-06T23:00:00Z"],
    ["2026-09-08T21:59:59Z", "2026-09-08T22:00:00Z"],
  ])("refreshes immediately on reopening (%s) regardless of closed-session TTL", async (before, after) => {
    const { cache, load, input } = setup();
    await cache.run({ ...input, range: {}, now: Date.parse(before) });
    await cache.run({ ...input, range: {}, now: Date.parse(after) });
    expect(load).toHaveBeenCalledTimes(2);
    expect(tradeRefreshCadence(new Date(before)).ttlMs).toBe(600_000);
    expect(tradeRefreshCadence(new Date(after)).ttlMs).toBe(30_000);
  });
});
