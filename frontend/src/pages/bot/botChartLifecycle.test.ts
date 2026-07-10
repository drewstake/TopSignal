import type { Logical, LogicalRange, UTCTimestamp } from "lightweight-charts";
import { describe, expect, it, vi } from "vitest";

import {
  LatestRequestCoordinator,
  getViewportRestoreRange,
  invalidateChartRequestLanes,
  isViewportAtLiveEdge,
  preserveLogicalViewport,
  runChartContextLoadsInParallel,
} from "./botChartLifecycle";

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

describe("runChartContextLoadsInParallel", () => {
  it("starts exactly one history and one live request together so live is not delayed by history", async () => {
    vi.useFakeTimers();
    const events: string[] = [];
    const delayed = (name: string, milliseconds: number) => () => {
      events.push(`${name}:start`);
      return new Promise<void>((resolve) => {
        setTimeout(() => {
          events.push(`${name}:end`);
          resolve();
        }, milliseconds);
      });
    };

    try {
      const load = runChartContextLoadsInParallel(delayed("history", 250), delayed("live", 100));
      expect(events).toEqual(["history:start", "live:start"]);

      await vi.advanceTimersByTimeAsync(100);
      expect(events).toEqual(["history:start", "live:start", "live:end"]);

      await vi.advanceTimersByTimeAsync(150);
      await load;
      expect(events).toEqual(["history:start", "live:start", "live:end", "history:end"]);
    } finally {
      vi.useRealTimers();
    }
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
