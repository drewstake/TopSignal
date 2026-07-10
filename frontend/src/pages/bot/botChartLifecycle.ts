import type { Logical, LogicalRange, UTCTimestamp } from "lightweight-charts";

/**
 * A request started by {@link LatestRequestCoordinator}. The context key should
 * identify every input that makes a response safe to apply (for example bot,
 * contract, timeframe, and environment).
 */
export interface ChartRequest {
  readonly id: number;
  readonly contextKey: string;
  readonly controller: AbortController;
  readonly signal: AbortSignal;
}

/**
 * Coordinates one latest-wins request lane without depending on React.
 *
 * Use a separate instance for independent request lanes such as candle history,
 * live price, pagination, and gap repair. Starting or invalidating a request
 * aborts the obsolete fetch immediately. `accepts` is still required because a
 * fetch implementation can resolve after its signal was aborted.
 */
export class LatestRequestCoordinator {
  private nextId = 0;
  private activeRequest: ChartRequest | null = null;

  begin(contextKey: string): ChartRequest {
    this.activeRequest?.controller.abort();
    const controller = new AbortController();
    const request: ChartRequest = {
      id: this.nextId + 1,
      contextKey,
      controller,
      signal: controller.signal,
    };
    this.nextId = request.id;
    this.activeRequest = request;
    return request;
  }

  /**
   * Returns whether a response may update chart state.
   *
   * An aborted request can still be current when its own timeout fired, so the
   * signal's aborted flag is intentionally not part of this decision. That lets
   * the current timeout surface an error while superseded requests stay silent.
   */
  accepts(request: ChartRequest, currentContextKey: string): boolean {
    return this.activeRequest === request && request.contextKey === currentContextKey;
  }

  /** Clear a settled request without disturbing a newer request. */
  finish(request: ChartRequest): boolean {
    if (this.activeRequest !== request) {
      return false;
    }
    this.activeRequest = null;
    return true;
  }

  /** Abort and reject the active request, typically after chart context changes. */
  invalidate(): void {
    this.activeRequest?.controller.abort();
    this.activeRequest = null;
  }

  /**
   * Abort pending work during cleanup. The coordinator remains reusable so this
   * is safe under React Strict Mode's development setup/cleanup cycle.
   */
  dispose(): void {
    this.invalidate();
  }

  get hasActiveRequest(): boolean {
    return this.activeRequest !== null;
  }
}

/** Abort every request lane that can mutate the same chart dataset. */
export function invalidateChartRequestLanes(lanes: readonly LatestRequestCoordinator[]): void {
  for (const lane of lanes) {
    lane.invalidate();
  }
}

/** Start independent chart-history and live-price lanes without serial latency. */
export async function runChartContextLoadsInParallel(
  loadCandles: () => Promise<unknown>,
  loadLivePrice: () => Promise<unknown>,
): Promise<void> {
  await Promise.allSettled([loadCandles(), loadLivePrice()]);
}

export type ChartViewportMutation = "refresh" | "pagination" | "live";

export interface PreservedLogicalViewport {
  range: LogicalRange;
  anchorTime: UTCTimestamp;
  indexDelta: number;
}

interface ViewportRestoreOptions {
  /** Bars from the final candle that still count as following live data. */
  liveEdgeTolerance?: number;
}

/**
 * Map a logical viewport from one candle array to another using a timestamp
 * shared by both arrays. Fractional range endpoints and whitespace are retained.
 *
 * This handles prepend pagination, front trimming, and newly repaired gaps more
 * accurately than adding the raw response length because the response can
 * contain duplicates. Candle times are expected to be unique within each array.
 */
export function preserveLogicalViewport(
  range: LogicalRange | null,
  previousTimes: readonly UTCTimestamp[],
  nextTimes: readonly UTCTimestamp[],
): PreservedLogicalViewport | null {
  if (!isValidLogicalRange(range) || previousTimes.length === 0 || nextTimes.length === 0) {
    return null;
  }

  const nextIndexByTime = new Map<number, number>();
  for (let index = 0; index < nextTimes.length; index += 1) {
    nextIndexByTime.set(Number(nextTimes[index]), index);
  }

  const visibleMidpoint = (Number(range.from) + Number(range.to)) / 2;
  const preferredIndex = clamp(Math.round(visibleMidpoint), 0, previousTimes.length - 1);
  const anchor = findNearestSharedTime(previousTimes, nextIndexByTime, preferredIndex);
  if (!anchor) {
    return null;
  }

  const indexDelta = anchor.nextIndex - anchor.previousIndex;
  return {
    range: {
      from: (Number(range.from) + indexDelta) as Logical,
      to: (Number(range.to) + indexDelta) as Logical,
    },
    anchorTime: anchor.time,
    indexDelta,
  };
}

/** Whether the visible range is close enough to the final candle to follow live bars. */
export function isViewportAtLiveEdge(
  range: LogicalRange | null,
  candleCount: number,
  tolerance = 0.5,
): boolean {
  if (!isValidLogicalRange(range) || candleCount <= 0) {
    return false;
  }

  const lastLogicalIndex = Math.max(0, Math.trunc(candleCount) - 1);
  return Number(range.to) >= lastLogicalIndex - Math.max(0, tolerance);
}

/**
 * Calculate the logical range to restore after a data change.
 *
 * Live updates at the right edge intentionally return `null` so Lightweight
 * Charts can keep following real time. Panned-away live views, refreshes, and
 * pagination restore their timestamp-anchored logical viewport.
 */
export function getViewportRestoreRange(
  mutation: ChartViewportMutation,
  range: LogicalRange | null,
  previousTimes: readonly UTCTimestamp[],
  nextTimes: readonly UTCTimestamp[],
  options: ViewportRestoreOptions = {},
): LogicalRange | null {
  if (
    mutation === "live" &&
    isViewportAtLiveEdge(range, previousTimes.length, options.liveEdgeTolerance)
  ) {
    return null;
  }

  return preserveLogicalViewport(range, previousTimes, nextTimes)?.range ?? null;
}

function findNearestSharedTime(
  previousTimes: readonly UTCTimestamp[],
  nextIndexByTime: ReadonlyMap<number, number>,
  preferredIndex: number,
): { time: UTCTimestamp; previousIndex: number; nextIndex: number } | null {
  for (let distance = 0; distance < previousTimes.length; distance += 1) {
    const leftIndex = preferredIndex - distance;
    if (leftIndex >= 0) {
      const leftMatch = sharedTimeAt(previousTimes, nextIndexByTime, leftIndex);
      if (leftMatch) {
        return leftMatch;
      }
    }

    const rightIndex = preferredIndex + distance;
    if (distance > 0 && rightIndex < previousTimes.length) {
      const rightMatch = sharedTimeAt(previousTimes, nextIndexByTime, rightIndex);
      if (rightMatch) {
        return rightMatch;
      }
    }
  }

  return null;
}

function sharedTimeAt(
  previousTimes: readonly UTCTimestamp[],
  nextIndexByTime: ReadonlyMap<number, number>,
  previousIndex: number,
): { time: UTCTimestamp; previousIndex: number; nextIndex: number } | null {
  const time = previousTimes[previousIndex];
  const nextIndex = nextIndexByTime.get(Number(time));
  if (time === undefined || nextIndex === undefined) {
    return null;
  }
  return { time, previousIndex, nextIndex };
}

function isValidLogicalRange(range: LogicalRange | null): range is LogicalRange {
  if (!range) {
    return false;
  }
  const from = Number(range.from);
  const to = Number(range.to);
  return Number.isFinite(from) && Number.isFinite(to) && from <= to;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}
