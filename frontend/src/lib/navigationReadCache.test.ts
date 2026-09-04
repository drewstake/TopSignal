import { afterEach, describe, expect, it, vi } from "vitest";
import { NavigationReadCache } from "./navigationReadCache";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
afterEach(() => vi.useRealTimers());

describe("navigation display cache", () => {
  it("shares pending reads and expires successful results", async () => {
    vi.useFakeTimers();
    const cache = new NavigationReadCache(100);
    const pending = deferred<number>();
    const load = vi.fn(() => pending.promise);
    const first = cache.get("a", load);
    const second = cache.get("a", load);
    pending.resolve(1);
    expect(await Promise.all([first, second])).toEqual([1, 1]);
    await cache.get("a", load);
    expect(load).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(100);
    await cache.get("a", load);
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("does not let an obsolete response restore invalidated data", async () => {
    const cache = new NavigationReadCache();
    const old = deferred<string>();
    const first = cache.get("account|a", () => old.promise);
    cache.invalidate("account|");
    await cache.get("account|a", async () => "new");
    old.resolve("old");
    await first;
    expect(await cache.get("account|a", async () => "unexpected")).toBe("new");
  });

  it("manual refresh supersedes a pending automatic read", async () => {
    const cache = new NavigationReadCache();
    const old = deferred<string>();
    const first = cache.get("a", () => old.promise);
    expect(await cache.get("a", async () => "fresh", { bypassCache: true })).toBe("fresh");
    old.resolve("old");
    await first;
    expect(await cache.get("a", async () => "unexpected")).toBe("fresh");
  });

  it("one canceled page does not abort another consumer", async () => {
    const cache = new NavigationReadCache();
    const pending = deferred<string>();
    const controller = new AbortController();
    let transportSignal!: AbortSignal;
    const first = cache.get("a", (signal) => { transportSignal = signal; return pending.promise; }, { signal: controller.signal });
    const rejected = expect(first).rejects.toMatchObject({ name: "AbortError" });
    const second = cache.get("a", () => pending.promise);
    await Promise.resolve();
    controller.abort();
    await rejected;
    expect(transportSignal.aborted).toBe(false);
    pending.resolve("ok");
    expect(await second).toBe("ok");
  });

  it("aborts abandoned transports and never caches their late response", async () => {
    const cache = new NavigationReadCache();
    const pending = deferred<string>();
    const controller = new AbortController();
    let transportSignal!: AbortSignal;
    const first = cache.get("a", (signal) => { transportSignal = signal; return pending.promise; }, { signal: controller.signal });
    const rejected = expect(first).rejects.toMatchObject({ name: "AbortError" });
    await Promise.resolve();
    controller.abort();
    await rejected;
    expect(transportSignal.aborted).toBe(true);
    pending.resolve("abandoned");
    await Promise.resolve();
    expect(await cache.get("a", async () => "retry")).toBe("retry");
  });

  it("retries failures and rejects already canceled requests without loading", async () => {
    const cache = new NavigationReadCache();
    await expect(cache.get("a", async () => { throw new Error("offline"); })).rejects.toThrow("offline");
    expect(await cache.get("a", async () => "retry")).toBe("retry");
    const controller = new AbortController();
    controller.abort();
    const load = vi.fn(async () => "unused");
    await expect(cache.get("a", load, { signal: controller.signal })).rejects.toMatchObject({ name: "AbortError" });
    expect(load).not.toHaveBeenCalled();
  });

  it("bounds retained entries", async () => {
    const cache = new NavigationReadCache(60_000, 2);
    const load = vi.fn(async () => "value");
    for (const key of ["a", "b", "c", "a"]) await cache.get(key, load);
    expect(load).toHaveBeenCalledTimes(4);
  });
});
