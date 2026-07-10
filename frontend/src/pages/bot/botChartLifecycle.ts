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

export type RequestPriority = "foreground" | "background";

export interface PrioritizedRequestSchedulerOptions {
  /** Maximum number of tasks that may be running at once. */
  maxConcurrency?: number;
  /** Maximum number of running tasks that started as background work. */
  maxBackgroundConcurrency?: number;
  /** Minimum elapsed time between the starts of two background tasks. */
  minBackgroundStartIntervalMs?: number;
}

export interface ScheduledRequestOptions {
  /** Foreground by default so user-visible work is never accidentally deprioritized. */
  priority?: RequestPriority;
  /** Cancels only this subscription to a shared task. */
  signal?: AbortSignal;
}

export type ScheduledRequestTask<TResult> = (
  signal: AbortSignal,
) => PromiseLike<TResult> | TResult;

type ScheduledEntryState = "queued" | "active" | "detached" | "settled";

interface RequestSubscriber {
  readonly resolve: (value: unknown) => void;
  readonly reject: (reason?: unknown) => void;
  readonly signal?: AbortSignal;
  abortListener?: () => void;
  settled: boolean;
}

interface ScheduledEntry<TKey> {
  readonly key: TKey;
  readonly task: ScheduledRequestTask<unknown>;
  readonly controller: AbortController;
  readonly subscribers: Set<RequestSubscriber>;
  priority: RequestPriority;
  state: ScheduledEntryState;
  startedAsBackground: boolean;
}

/**
 * Runs deduplicated async work with separate foreground and background limits.
 *
 * Every exact key has at most one live queued/running task. Later callers with
 * the same key subscribe to the first task, so a key must identify all inputs
 * that affect both the request and its result type. Subscriber abort signals
 * are independent; the shared task is aborted only after its final subscriber
 * leaves. A transport that ignores abort is harmless because detached
 * subscribers have already been rejected and are never resolved later.
 */
export class PrioritizedRequestScheduler<TKey = string> {
  private readonly maxConcurrency: number;
  private readonly maxBackgroundConcurrency: number;
  private readonly minBackgroundStartIntervalMs: number;
  private readonly entriesByKey = new Map<TKey, ScheduledEntry<TKey>>();
  private readonly queuedEntries: ScheduledEntry<TKey>[] = [];
  private readonly activeEntries = new Set<ScheduledEntry<TKey>>();
  private activeBackgroundTasks = 0;
  private lastBackgroundStartTime: number | null = null;
  private backgroundStartTimer: ReturnType<typeof setTimeout> | null = null;
  private backgroundStartTimerDueAt: number | null = null;
  private pumping = false;

  constructor(options: PrioritizedRequestSchedulerOptions = {}) {
    this.maxConcurrency = positiveInteger(options.maxConcurrency ?? 2, "maxConcurrency");
    this.maxBackgroundConcurrency = nonNegativeInteger(
      options.maxBackgroundConcurrency ?? 1,
      "maxBackgroundConcurrency",
    );
    this.minBackgroundStartIntervalMs = nonNegativeFiniteNumber(
      options.minBackgroundStartIntervalMs ?? 0,
      "minBackgroundStartIntervalMs",
    );
  }

  /**
   * Subscribe to keyed work, creating the underlying task only for the first
   * subscriber. A queued background task is upgraded when a foreground caller
   * subscribes to the same key.
   */
  schedule<TResult>(
    key: TKey,
    task: ScheduledRequestTask<TResult>,
    options: ScheduledRequestOptions = {},
  ): Promise<TResult> {
    const priority = options.priority ?? "foreground";
    const subscriberSignal = options.signal;
    if (subscriberSignal?.aborted) {
      return Promise.reject(abortReason(subscriberSignal));
    }

    let entry = this.entriesByKey.get(key);
    if (!entry) {
      entry = {
        key,
        task: task as ScheduledRequestTask<unknown>,
        controller: new AbortController(),
        subscribers: new Set(),
        priority,
        state: "queued",
        startedAsBackground: false,
      };
      this.entriesByKey.set(key, entry);
      this.queuedEntries.push(entry);
    } else if (entry.state === "queued" && priority === "foreground") {
      entry.priority = "foreground";
    }

    const promise = new Promise<TResult>((resolve, reject) => {
      const subscriber: RequestSubscriber = {
        resolve: (value) => resolve(value as TResult),
        reject,
        signal: subscriberSignal,
        settled: false,
      };
      entry.subscribers.add(subscriber);

      if (subscriberSignal) {
        const abortListener = () => {
          this.abortSubscriber(entry, subscriber, abortReason(subscriberSignal));
        };
        subscriber.abortListener = abortListener;
        subscriberSignal.addEventListener("abort", abortListener, { once: true });

        // Close the small race between the initial check and listener setup.
        if (subscriberSignal.aborted) {
          abortListener();
        }
      }
    });

    this.pump();
    return promise;
  }

  get activeCount(): number {
    return this.activeEntries.size;
  }

  get queuedCount(): number {
    return this.queuedEntries.length;
  }

  get activeBackgroundCount(): number {
    return this.activeBackgroundTasks;
  }

  get queuedForegroundCount(): number {
    return this.queuedEntries.reduce(
      (count, entry) => count + (entry.priority === "foreground" ? 1 : 0),
      0,
    );
  }

  get queuedBackgroundCount(): number {
    return this.queuedEntries.reduce(
      (count, entry) => count + (entry.priority === "background" ? 1 : 0),
      0,
    );
  }

  private abortSubscriber(
    entry: ScheduledEntry<TKey>,
    subscriber: RequestSubscriber,
    reason: unknown,
  ): void {
    if (subscriber.settled) {
      return;
    }

    this.settleSubscriber(subscriber, "reject", reason);
    entry.subscribers.delete(subscriber);
    if (entry.subscribers.size > 0) {
      return;
    }

    // Remove the key immediately so a later caller can start fresh rather than
    // joining an abort-unaware transport whose shared signal is already dead.
    if (this.entriesByKey.get(entry.key) === entry) {
      this.entriesByKey.delete(entry.key);
    }

    if (entry.state === "queued") {
      this.removeQueuedEntry(entry);
      entry.state = "settled";
      entry.controller.abort(reason);
      this.pump();
      return;
    }

    if (entry.state === "active") {
      entry.state = "detached";
      entry.controller.abort(reason);
    }
  }

  private pump(): void {
    if (this.pumping) {
      return;
    }

    this.pumping = true;
    try {
      while (this.activeEntries.size < this.maxConcurrency) {
        const entry = this.takeNextRunnableEntry();
        if (!entry) {
          break;
        }
        this.startEntry(entry);
      }

      if (!this.queuedEntries.some((entry) => entry.priority === "background")) {
        this.clearBackgroundStartTimer();
      }
    } finally {
      this.pumping = false;
    }
  }

  private takeNextRunnableEntry(): ScheduledEntry<TKey> | null {
    const foregroundIndex = this.queuedEntries.findIndex(
      (entry) => entry.priority === "foreground",
    );
    if (foregroundIndex >= 0) {
      return this.queuedEntries.splice(foregroundIndex, 1)[0] ?? null;
    }

    const backgroundIndex = this.queuedEntries.findIndex(
      (entry) => entry.priority === "background",
    );
    if (
      backgroundIndex < 0 ||
      this.activeBackgroundTasks >= this.maxBackgroundConcurrency
    ) {
      return null;
    }

    const waitMs = this.backgroundStartWaitMs();
    if (waitMs > 0) {
      this.scheduleBackgroundStart(waitMs);
      return null;
    }

    return this.queuedEntries.splice(backgroundIndex, 1)[0] ?? null;
  }

  private startEntry(entry: ScheduledEntry<TKey>): void {
    entry.state = "active";
    entry.startedAsBackground = entry.priority === "background";
    this.activeEntries.add(entry);
    if (entry.startedAsBackground) {
      this.activeBackgroundTasks += 1;
      this.lastBackgroundStartTime = Date.now();
    }

    let result: PromiseLike<unknown> | unknown;
    try {
      result = entry.task(entry.controller.signal);
    } catch (error) {
      this.finishEntry(entry, "reject", error);
      return;
    }

    Promise.resolve(result).then(
      (value) => this.finishEntry(entry, "resolve", value),
      (error: unknown) => this.finishEntry(entry, "reject", error),
    );
  }

  private finishEntry(
    entry: ScheduledEntry<TKey>,
    outcome: "resolve" | "reject",
    value: unknown,
  ): void {
    if (entry.state !== "active" && entry.state !== "detached") {
      return;
    }

    this.activeEntries.delete(entry);
    if (entry.startedAsBackground) {
      this.activeBackgroundTasks -= 1;
    }
    if (this.entriesByKey.get(entry.key) === entry) {
      this.entriesByKey.delete(entry.key);
    }
    entry.state = "settled";

    for (const subscriber of entry.subscribers) {
      this.settleSubscriber(subscriber, outcome, value);
    }
    entry.subscribers.clear();
    this.pump();
  }

  private settleSubscriber(
    subscriber: RequestSubscriber,
    outcome: "resolve" | "reject",
    value: unknown,
  ): void {
    if (subscriber.settled) {
      return;
    }
    subscriber.settled = true;
    if (subscriber.signal && subscriber.abortListener) {
      subscriber.signal.removeEventListener("abort", subscriber.abortListener);
    }
    if (outcome === "resolve") {
      subscriber.resolve(value);
    } else {
      subscriber.reject(value);
    }
  }

  private removeQueuedEntry(entry: ScheduledEntry<TKey>): void {
    const index = this.queuedEntries.indexOf(entry);
    if (index >= 0) {
      this.queuedEntries.splice(index, 1);
    }
  }

  private backgroundStartWaitMs(): number {
    if (this.lastBackgroundStartTime === null) {
      return 0;
    }
    const nextStartTime =
      this.lastBackgroundStartTime + this.minBackgroundStartIntervalMs;
    return Math.max(0, nextStartTime - Date.now());
  }

  private scheduleBackgroundStart(waitMs: number): void {
    const dueAt = Date.now() + waitMs;
    if (
      this.backgroundStartTimer !== null &&
      this.backgroundStartTimerDueAt !== null &&
      this.backgroundStartTimerDueAt <= dueAt
    ) {
      return;
    }
    this.clearBackgroundStartTimer();
    this.backgroundStartTimerDueAt = dueAt;
    this.backgroundStartTimer = setTimeout(() => {
      this.backgroundStartTimer = null;
      this.backgroundStartTimerDueAt = null;
      this.pump();
    }, waitMs);
  }

  private clearBackgroundStartTimer(): void {
    if (this.backgroundStartTimer !== null) {
      clearTimeout(this.backgroundStartTimer);
      this.backgroundStartTimer = null;
      this.backgroundStartTimerDueAt = null;
    }
  }
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

/** Stores independent logical ranges for chart contexts such as timeframes. */
export class LogicalViewportMemory<TKey = string> {
  private readonly ranges = new Map<TKey, LogicalRange>();

  /** Saves a defensive copy of a valid range; invalid transient ranges are ignored. */
  save(key: TKey, range: LogicalRange | null): boolean {
    if (!isValidLogicalRange(range)) {
      return false;
    }
    this.ranges.set(key, cloneLogicalRange(range));
    return true;
  }

  /** Returns a defensive copy so chart-library mutations cannot corrupt memory. */
  restore(key: TKey): LogicalRange | null {
    const range = this.ranges.get(key);
    return range ? cloneLogicalRange(range) : null;
  }

  delete(key: TKey): boolean {
    return this.ranges.delete(key);
  }

  clear(): void {
    this.ranges.clear();
  }
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

function cloneLogicalRange(range: LogicalRange): LogicalRange {
  return { from: range.from, to: range.to };
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

function positiveInteger(value: number, name: string): number {
  if (!Number.isInteger(value) || value <= 0) {
    throw new RangeError(`${name} must be a positive integer`);
  }
  return value;
}

function nonNegativeInteger(value: number, name: string): number {
  if (!Number.isInteger(value) || value < 0) {
    throw new RangeError(`${name} must be a non-negative integer`);
  }
  return value;
}

function nonNegativeFiniteNumber(value: number, name: string): number {
  if (!Number.isFinite(value) || value < 0) {
    throw new RangeError(`${name} must be a non-negative finite number`);
  }
  return value;
}

function abortReason(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException("The operation was aborted", "AbortError");
}
