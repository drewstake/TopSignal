import type { Logical, LogicalRange, UTCTimestamp } from "lightweight-charts";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  LatestRequestCoordinator,
  LogicalViewportMemory,
  PrioritizedRequestScheduler,
  getViewportRestoreRange,
  invalidateChartRequestLanes,
  isViewportAtLiveEdge,
  preserveLogicalViewport,
} from "./botChartLifecycle";

interface Deferred<T> {
  readonly promise: Promise<T>;
  readonly resolve: (value: T) => void;
  readonly reject: (reason?: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

afterEach(() => {
  vi.useRealTimers();
});

function logicalRange(from: number, to: number): LogicalRange {
  return { from: from as Logical, to: to as Logical };
}

function times(...values: number[]): UTCTimestamp[] {
  return values.map((value) => value as UTCTimestamp);
}

describe("LatestRequestCoordinator", () => {
  it("aborts an obsolete request and rejects its response", () => {
    const requests = new LatestRequestCoordinator();
    const first = requests.begin("bot-a:MNQ:5m");
    const second = requests.begin("bot-a:MNQ:5m");

    expect(first.signal.aborted).toBe(true);
    expect(second.signal.aborted).toBe(false);
    expect(requests.accepts(first, "bot-a:MNQ:5m")).toBe(false);
    expect(requests.accepts(second, "bot-a:MNQ:5m")).toBe(true);
  });

  it("rejects an otherwise-latest response when the chart context changed", () => {
    const requests = new LatestRequestCoordinator();
    const request = requests.begin("bot-a:MNQ:5m");

    expect(requests.accepts(request, "bot-a:MNQ:15m")).toBe(false);
    expect(requests.accepts(request, "bot-a:MNQ:5m")).toBe(true);
  });

  it("prevents an abort-unaware stale response from being applied", async () => {
    const requests = new LatestRequestCoordinator();
    const first = requests.begin("bot-a:MNQ:5m");
    const applied: string[] = [];
    const staleTransport = Promise.resolve("old rows").then((rows) => {
      if (requests.accepts(first, "bot-a:MNQ:5m")) {
        applied.push(rows);
      }
    });

    requests.begin("bot-a:MNQ:5m");
    await staleTransport;

    expect(applied).toEqual([]);
  });

  it("still accepts the current request after its own timeout aborts the signal", () => {
    const requests = new LatestRequestCoordinator();
    const request = requests.begin("bot-a:MNQ:5m");

    request.controller.abort();

    expect(request.signal.aborted).toBe(true);
    expect(requests.accepts(request, "bot-a:MNQ:5m")).toBe(true);
  });

  it("invalidates pending work even when the transport ignores cancellation", () => {
    const requests = new LatestRequestCoordinator();
    const request = requests.begin("bot-a:MNQ:5m");

    requests.invalidate();

    expect(request.signal.aborted).toBe(true);
    expect(requests.accepts(request, "bot-a:MNQ:5m")).toBe(false);
    expect(requests.hasActiveRequest).toBe(false);
  });

  it("does not let a stale finally block clear a newer request", () => {
    const requests = new LatestRequestCoordinator();
    const first = requests.begin("bot-a:MNQ:5m");
    const second = requests.begin("bot-a:MNQ:5m");

    expect(requests.finish(first)).toBe(false);
    expect(requests.hasActiveRequest).toBe(true);
    expect(requests.accepts(second, "bot-a:MNQ:5m")).toBe(true);
    expect(requests.finish(second)).toBe(true);
    expect(requests.hasActiveRequest).toBe(false);
  });

  it("aborts on disposal and remains reusable after Strict Mode-style cleanup", () => {
    const requests = new LatestRequestCoordinator();
    const request = requests.begin("bot-a:MNQ:5m");

    requests.dispose();
    requests.dispose();

    expect(request.signal.aborted).toBe(true);
    expect(requests.accepts(request, "bot-a:MNQ:5m")).toBe(false);
    const remountedRequest = requests.begin("bot-a:MNQ:5m");
    expect(requests.accepts(remountedRequest, "bot-a:MNQ:5m")).toBe(true);
  });

  it("cancels every obsolete mutation lane before a canonical refresh", () => {
    const live = new LatestRequestCoordinator();
    const history = new LatestRequestCoordinator();
    const repair = new LatestRequestCoordinator();
    const liveRequest = live.begin("bot-a:MNQ:5m");
    const historyRequest = history.begin("bot-a:MNQ:5m");
    const repairRequest = repair.begin("bot-a:MNQ:5m");

    invalidateChartRequestLanes([live, history, repair]);

    expect([liveRequest, historyRequest, repairRequest].every((request) => request.signal.aborted)).toBe(true);
    expect(live.accepts(liveRequest, "bot-a:MNQ:5m")).toBe(false);
    expect(history.accepts(historyRequest, "bot-a:MNQ:5m")).toBe(false);
    expect(repair.accepts(repairRequest, "bot-a:MNQ:5m")).toBe(false);
  });
});

describe("PrioritizedRequestScheduler", () => {
  it("deduplicates exact keys across queued and active subscribers", async () => {
    const scheduler = new PrioritizedRequestScheduler({ maxConcurrency: 1 });
    const blocker = deferred<string>();
    const sharedWork = deferred<string>();
    const sharedTask = vi.fn(() => sharedWork.promise);

    const blockingRequest = scheduler.schedule("blocker", () => blocker.promise);
    const first = scheduler.schedule("shared", sharedTask);
    const secondTask = vi.fn(() => Promise.resolve("wrong task"));
    const second = scheduler.schedule("shared", secondTask);

    expect(scheduler.activeCount).toBe(1);
    expect(scheduler.queuedCount).toBe(1);
    expect(sharedTask).not.toHaveBeenCalled();
    expect(secondTask).not.toHaveBeenCalled();

    blocker.resolve("unblocked");
    await expect(blockingRequest).resolves.toBe("unblocked");
    expect(sharedTask).toHaveBeenCalledOnce();
    expect(scheduler.activeCount).toBe(1);
    expect(scheduler.queuedCount).toBe(0);
    const activeDuplicateTask = vi.fn(() => Promise.resolve("wrong active task"));
    const third = scheduler.schedule("shared", activeDuplicateTask);

    sharedWork.resolve("shared result");
    await expect(Promise.all([first, second, third])).resolves.toEqual([
      "shared result",
      "shared result",
      "shared result",
    ]);
    expect(sharedTask).toHaveBeenCalledOnce();
    expect(secondTask).not.toHaveBeenCalled();
    expect(activeDuplicateTask).not.toHaveBeenCalled();
  });

  it("always selects queued foreground work before background work", async () => {
    const scheduler = new PrioritizedRequestScheduler({
      maxConcurrency: 1,
      maxBackgroundConcurrency: 1,
    });
    const blocker = deferred<void>();
    const foreground = deferred<string>();
    const background = deferred<string>();
    const starts: string[] = [];

    const blockingRequest = scheduler.schedule("active", () => blocker.promise);
    const backgroundRequest = scheduler.schedule(
      "background",
      () => {
        starts.push("background");
        return background.promise;
      },
      { priority: "background" },
    );
    const foregroundRequest = scheduler.schedule("foreground", () => {
      starts.push("foreground");
      return foreground.promise;
    });

    blocker.resolve();
    await blockingRequest;
    expect(starts).toEqual(["foreground"]);
    expect(scheduler.queuedBackgroundCount).toBe(1);

    foreground.resolve("visible");
    await expect(foregroundRequest).resolves.toBe("visible");
    expect(starts).toEqual(["foreground", "background"]);
    background.resolve("warm");
    await expect(backgroundRequest).resolves.toBe("warm");
  });

  it("upgrades a queued background task when a foreground subscriber joins", async () => {
    const scheduler = new PrioritizedRequestScheduler({ maxConcurrency: 1 });
    const blocker = deferred<void>();
    const starts: string[] = [];

    const blockingRequest = scheduler.schedule("active", () => blocker.promise);
    const olderBackground = scheduler.schedule(
      "older-background",
      () => {
        starts.push("older-background");
        return "older";
      },
      { priority: "background" },
    );
    const upgradedTask = vi.fn(() => {
      starts.push("upgraded");
      return "upgraded";
    });
    const backgroundSubscriber = scheduler.schedule("upgrade-me", upgradedTask, {
      priority: "background",
    });
    const ignoredDuplicateTask = vi.fn(() => "duplicate");
    const foregroundSubscriber = scheduler.schedule(
      "upgrade-me",
      ignoredDuplicateTask,
      { priority: "foreground" },
    );

    expect(scheduler.queuedForegroundCount).toBe(1);
    expect(scheduler.queuedBackgroundCount).toBe(1);
    blocker.resolve();
    await blockingRequest;
    await expect(Promise.all([backgroundSubscriber, foregroundSubscriber])).resolves.toEqual([
      "upgraded",
      "upgraded",
    ]);
    await expect(olderBackground).resolves.toBe("older");

    expect(starts).toEqual(["upgraded", "older-background"]);
    expect(upgradedTask).toHaveBeenCalledOnce();
    expect(ignoredDuplicateTask).not.toHaveBeenCalled();
  });

  it("limits background concurrency while leaving capacity for foreground work", async () => {
    const scheduler = new PrioritizedRequestScheduler({
      maxConcurrency: 2,
      maxBackgroundConcurrency: 1,
    });
    const firstBackground = deferred<void>();
    const secondBackground = deferred<void>();
    const foreground = deferred<void>();
    const starts: string[] = [];

    const first = scheduler.schedule(
      "background-1",
      () => {
        starts.push("background-1");
        return firstBackground.promise;
      },
      { priority: "background" },
    );
    const second = scheduler.schedule(
      "background-2",
      () => {
        starts.push("background-2");
        return secondBackground.promise;
      },
      { priority: "background" },
    );
    const visible = scheduler.schedule("foreground", () => {
      starts.push("foreground");
      return foreground.promise;
    });

    expect(starts).toEqual(["background-1", "foreground"]);
    expect(scheduler.activeCount).toBe(2);
    expect(scheduler.activeBackgroundCount).toBe(1);
    expect(scheduler.queuedCount).toBe(1);

    foreground.resolve();
    await visible;
    expect(starts).toEqual(["background-1", "foreground"]);
    firstBackground.resolve();
    await first;
    expect(starts).toEqual(["background-1", "foreground", "background-2"]);
    secondBackground.resolve();
    await second;
  });

  it("rate-limits background starts even when another background slot is free", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(1_000);
    const scheduler = new PrioritizedRequestScheduler({
      maxConcurrency: 2,
      maxBackgroundConcurrency: 2,
      minBackgroundStartIntervalMs: 100,
    });
    const firstWork = deferred<void>();
    const secondWork = deferred<void>();
    const starts: number[] = [];

    const first = scheduler.schedule(
      "background-1",
      () => {
        starts.push(Date.now());
        return firstWork.promise;
      },
      { priority: "background" },
    );
    const second = scheduler.schedule(
      "background-2",
      () => {
        starts.push(Date.now());
        return secondWork.promise;
      },
      { priority: "background" },
    );

    expect(starts).toEqual([1_000]);
    expect(scheduler.activeCount).toBe(1);
    expect(scheduler.queuedCount).toBe(1);
    await vi.advanceTimersByTimeAsync(99);
    expect(starts).toEqual([1_000]);
    await vi.advanceTimersByTimeAsync(1);
    expect(starts).toEqual([1_000, 1_100]);

    firstWork.resolve();
    secondWork.resolve();
    await Promise.all([first, second]);
  });

  it("cancels subscribers independently and aborts shared work only after the last leaves", async () => {
    const scheduler = new PrioritizedRequestScheduler();
    const transport = deferred<string>();
    const firstController = new AbortController();
    const secondController = new AbortController();
    const sharedTransport: { signal?: AbortSignal } = {};
    const task = (signal: AbortSignal) => {
      sharedTransport.signal = signal;
      return transport.promise;
    };

    const first = scheduler.schedule("candles", task, { signal: firstController.signal });
    const second = scheduler.schedule("candles", task, { signal: secondController.signal });
    const firstOutcome = first.then(
      () => "resolved",
      (error: unknown) => (error as Error).name,
    );
    const secondOutcome = second.then(
      () => "resolved",
      (error: unknown) => (error as Error).name,
    );

    firstController.abort();
    expect(await firstOutcome).toBe("AbortError");
    expect(sharedTransport.signal?.aborted).toBe(false);
    expect(scheduler.activeCount).toBe(1);

    secondController.abort();
    expect(await secondOutcome).toBe("AbortError");
    expect(sharedTransport.signal?.aborted).toBe(true);
    // An abort-unaware transport still occupies its slot, but cannot resolve
    // either cancelled subscriber when it eventually returns.
    expect(scheduler.activeCount).toBe(1);
    transport.resolve("late candles");
    await Promise.resolve();
    expect(scheduler.activeCount).toBe(0);
  });
});

describe("LogicalViewportMemory", () => {
  it("saves and restores independent ranges across timeframe keys", () => {
    const memory = new LogicalViewportMemory<string>();

    expect(memory.save("MNQ:1m", logicalRange(10.25, 40.75))).toBe(true);
    expect(memory.save("MNQ:1H", logicalRange(100.5, 130.5))).toBe(true);
    expect(memory.restore("MNQ:1m")).toEqual(logicalRange(10.25, 40.75));
    expect(memory.restore("MNQ:1H")).toEqual(logicalRange(100.5, 130.5));
    expect(memory.restore("MNQ:5m")).toBeNull();
  });

  it("ignores invalid ranges and protects saved ranges from caller mutation", () => {
    const memory = new LogicalViewportMemory<string>();
    const range = logicalRange(1, 3);
    memory.save("5m", range);

    range.from = 99 as Logical;
    const restored = memory.restore("5m");
    expect(restored).toEqual(logicalRange(1, 3));
    if (restored) {
      restored.to = 100 as Logical;
    }
    expect(memory.restore("5m")).toEqual(logicalRange(1, 3));
    expect(memory.save("5m", logicalRange(4, 2))).toBe(false);
    expect(memory.restore("5m")).toEqual(logicalRange(1, 3));

    expect(memory.delete("5m")).toBe(true);
    expect(memory.restore("5m")).toBeNull();
  });
});

describe("preserveLogicalViewport", () => {
  it("shifts fractional logical coordinates by the number of genuinely prepended bars", () => {
    const preserved = preserveLogicalViewport(
      logicalRange(0.25, 2.75),
      times(300, 400, 500, 600),
      times(100, 200, 300, 400, 500, 600),
    );

    expect(preserved).toEqual({
      range: logicalRange(2.25, 4.75),
      anchorTime: 500,
      indexDelta: 2,
    });
  });

  it("accounts for a repaired gap before the visible anchor", () => {
    const preserved = preserveLogicalViewport(
      logicalRange(1.5, 3.5),
      times(100, 200, 400, 500, 600),
      times(100, 200, 300, 400, 500, 600),
    );

    expect(preserved?.range).toEqual(logicalRange(2.5, 4.5));
    expect(preserved?.indexDelta).toBe(1);
  });

  it("moves the logical range left when old front bars were trimmed", () => {
    const preserved = preserveLogicalViewport(
      logicalRange(2.25, 4.25),
      times(100, 200, 300, 400, 500),
      times(300, 400, 500, 600),
    );

    expect(preserved?.range).toEqual(logicalRange(0.25, 2.25));
    expect(preserved?.indexDelta).toBe(-2);
  });

  it("returns null for invalid ranges or unrelated datasets", () => {
    expect(preserveLogicalViewport(null, times(100), times(100))).toBeNull();
    expect(preserveLogicalViewport(logicalRange(3, 2), times(100), times(100))).toBeNull();
    expect(preserveLogicalViewport(logicalRange(0, 1), times(100, 200), times(300, 400))).toBeNull();
  });
});

describe("viewport restore decisions", () => {
  it("preserves a panned-away viewport during a live append", () => {
    expect(
      getViewportRestoreRange(
        "live",
        logicalRange(0, 1),
        times(100, 200, 300, 400),
        times(100, 200, 300, 400, 500),
      ),
    ).toEqual(logicalRange(0, 1));
  });

  it("lets Lightweight Charts follow a live append at the right edge", () => {
    expect(isViewportAtLiveEdge(logicalRange(1.5, 3.25), 4)).toBe(true);
    expect(
      getViewportRestoreRange(
        "live",
        logicalRange(1.5, 3.25),
        times(100, 200, 300, 400),
        times(100, 200, 300, 400, 500),
      ),
    ).toBeNull();
  });

  it("always maps refresh and pagination viewports, including at the right edge", () => {
    const previousTimes = times(300, 400, 500);
    const nextTimes = times(100, 200, 300, 400, 500, 600);
    const range = logicalRange(0.5, 2.5);

    expect(getViewportRestoreRange("refresh", range, previousTimes, nextTimes)).toEqual(logicalRange(2.5, 4.5));
    expect(getViewportRestoreRange("pagination", range, previousTimes, nextTimes)).toEqual(logicalRange(2.5, 4.5));
  });

  it("honors a configurable live-edge tolerance", () => {
    const range = logicalRange(1, 2.2);

    expect(isViewportAtLiveEdge(range, 4)).toBe(false);
    expect(isViewportAtLiveEdge(range, 4, 1)).toBe(true);
  });
});
