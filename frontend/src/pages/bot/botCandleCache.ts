import type { BotTimeframeUnit, ProjectXMarketCandle } from "../../lib/types";

const STORAGE_KEY_PREFIX = "topsignal:bot-candles:v2:";
const LEGACY_STORAGE_KEY_PREFIX = "topsignal:bot-candles:v1:";

export interface BotCandleCacheKeyInput {
  /** Stable non-secret authenticated-user namespace. */
  userScope: string;
  contractId: string;
  symbol?: string | null;
  live: boolean;
  unit: BotTimeframeUnit;
  unitNumber: number;
}

interface BotCandleCachePayload {
  savedAt: string;
  candles: ProjectXMarketCandle[];
  /** Optional v1 extension. Old payloads without coverage remain readable. */
  coverageStart?: string;
  /** Optional v1 extension. Old payloads without coverage remain readable. */
  coverageEnd?: string;
}

export interface CandleQueryWindow {
  start: string;
  end: string;
}

export interface BotCandleCacheEntry {
  savedAt: Date | null;
  candles: ProjectXMarketCandle[];
  /** The query range known to have been checked, including session-empty ranges. */
  coverage: CandleQueryWindow | null;
}

export interface BotCandleCacheWriteMetadata {
  /** Defaults to the current time. Supplying it keeps tests and warmers deterministic. */
  savedAt?: Date;
  /** Request coverage is more accurate than deriving coverage from the first/last row. */
  coverage?: CandleQueryWindow | null;
}

/**
 * The hot read path stays entirely in memory after the first localStorage read.
 * Entries are scoped by the already contract+environment+timeframe-specific key.
 */
const memoryCache = new Map<string, BotCandleCacheEntry>();
const canonicalMetadataByArray = new WeakMap<
  ProjectXMarketCandle[],
  { timestamps: number[]; hasPartial: boolean }
>();

export function buildBotCandleCacheKey(input: BotCandleCacheKeyInput): string {
  const parts = [
    input.userScope.trim(),
    input.live ? "live" : "practice",
    input.contractId.trim().toUpperCase(),
    (input.symbol ?? "").trim().toUpperCase(),
    input.unit,
    String(Math.max(1, Math.trunc(input.unitNumber))),
  ];
  return `${STORAGE_KEY_PREFIX}${encodeURIComponent(parts.join("|"))}`;
}

/** Remove v1 entries rather than assigning their unknown owner to this user. */
export function invalidateLegacyBotCandleCache(input: BotCandleCacheKeyInput): void {
  // Legacy v1 keys are unscoped. Demo hydration must not invalidate a live
  // user's previously cached market data.
  if (input.userScope.trim() === "demo") {
    return;
  }
  const storage = getLocalStorage();
  if (!storage) {
    return;
  }
  try {
    storage.removeItem(buildLegacyBotCandleCacheKey(input));
  } catch {
    // localStorage may be blocked; the scoped network path still works.
  }
}

export function readBotCandleCache(cacheKey: string): BotCandleCacheEntry | null {
  const inMemory = memoryCache.get(cacheKey);
  if (inMemory) {
    return inMemory;
  }

  const storage = getLocalStorage();
  if (!storage) {
    return null;
  }

  try {
    const raw = storage.getItem(cacheKey);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as Partial<BotCandleCachePayload>;
    if (!Array.isArray(parsed.candles)) {
      return null;
    }
    const candles = canonicalizeMarketCandles(parsed.candles, false).rows;
    if (candles.length === 0) {
      return null;
    }
    const savedAt = parseDate(parsed.savedAt);
    const entry: BotCandleCacheEntry = {
      savedAt,
      candles,
      coverage: parseCoverage(parsed.coverageStart, parsed.coverageEnd),
    };
    memoryCache.set(cacheKey, entry);
    return entry;
  } catch {
    storage.removeItem(cacheKey);
    return null;
  }
}

export function writeBotCandleCache(
  cacheKey: string,
  candles: ProjectXMarketCandle[],
  limit: number,
  metadata: BotCandleCacheWriteMetadata = {},
): void {
  const rows = trimCandlesForCache(candles, limit);
  const storage = getLocalStorage();
  if (rows.length === 0) {
    memoryCache.delete(cacheKey);
    storage?.removeItem(cacheKey);
    return;
  }

  const savedAt = validDateOrNow(metadata.savedAt);
  const coverage = normalizeCoverage(metadata.coverage);
  const entry: BotCandleCacheEntry = { savedAt, candles: rows, coverage };
  memoryCache.set(cacheKey, entry);

  if (!storage) {
    return;
  }

  const payload: BotCandleCachePayload = {
    savedAt: savedAt.toISOString(),
    candles: rows,
    ...(coverage ? { coverageStart: coverage.start, coverageEnd: coverage.end } : {}),
  };
  try {
    storage.setItem(cacheKey, JSON.stringify(payload));
  } catch {
    // localStorage may be full or disabled; the memory/network paths still work.
  }
}

/**
 * Clear only the process-local layer. Exported so tests can verify persisted-v1
 * fallback without coupling production code to a browser storage event.
 */
export function resetBotCandleMemoryCacheForTests(): void {
  memoryCache.clear();
}

/**
 * Merge closed candles for persistence. Canonical inputs use a tail-aware
 * linear merge and retain existing row references. Partial rows are never
 * persisted, and a no-op returns `existingCandles` itself when it is canonical.
 */
export function mergeMarketCandles(
  existingCandles: ProjectXMarketCandle[],
  incomingCandles: ProjectXMarketCandle[],
  limit: number,
): ProjectXMarketCandle[] {
  return mergeCanonicalMarketCandles(existingCandles, incomingCandles, false, Math.max(1, Math.trunc(limit)));
}

export function filterMarketCandlesForWindow(
  candles: ProjectXMarketCandle[],
  window: CandleQueryWindow,
): ProjectXMarketCandle[] {
  const startMs = Date.parse(window.start);
  const endMs = Date.parse(window.end);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || startMs > endMs) {
    return [];
  }

  const canonical = canonicalizeMarketCandles(candles, true);
  const fromIndex = lowerBound(canonical.timestamps, startMs);
  const toIndex = upperBound(canonical.timestamps, endMs);
  if (canonical.reusedInput && fromIndex === 0 && toIndex === candles.length) {
    return candles;
  }
  return canonical.rows.slice(fromIndex, toIndex);
}

/**
 * In-memory merge that, unlike `mergeMarketCandles`, keeps partial candles so
 * a just-rolled-over live bar stays on the chart until an authoritative closed
 * bar replaces it. A closed candle is never replaced by a partial one at the
 * same timestamp.
 */
export function upsertMarketCandles(
  existingCandles: ProjectXMarketCandle[],
  incomingCandles: ProjectXMarketCandle[],
  limit?: number,
): ProjectXMarketCandle[] {
  const boundedLimit = limit === undefined ? Number.POSITIVE_INFINITY : Math.max(1, Math.trunc(limit));
  return mergeCanonicalMarketCandles(existingCandles, incomingCandles, true, boundedLimit);
}

function trimCandlesForCache(candles: ProjectXMarketCandle[], limit: number): ProjectXMarketCandle[] {
  const boundedLimit = Math.max(1, Math.trunc(limit));
  const canonical = canonicalizeMarketCandles(candles, false);
  if (canonical.rows.length <= boundedLimit) {
    return canonical.rows;
  }
  return canonical.rows.slice(-boundedLimit);
}

interface CanonicalCandles {
  rows: ProjectXMarketCandle[];
  timestamps: number[];
  reusedInput: boolean;
}

/**
 * Validate and canonicalize only when necessary. The overwhelmingly common
 * API/cache arrays are already sorted and unique, so they skip Map allocation
 * and sorting entirely.
 */
function canonicalizeMarketCandles(candles: unknown[], includePartial: boolean): CanonicalCandles {
  const knownMetadata = canonicalMetadataByArray.get(candles as ProjectXMarketCandle[]);
  if (knownMetadata && (includePartial || !knownMetadata.hasPartial)) {
    return {
      rows: candles as ProjectXMarketCandle[],
      timestamps: knownMetadata.timestamps,
      reusedInput: true,
    };
  }

  const timestamps: number[] = [];
  let previousTimestamp = Number.NEGATIVE_INFINITY;
  let alreadyCanonical = true;
  let hasPartial = false;

  for (const value of candles) {
    if (!isCachedMarketCandle(value) || (!includePartial && value.is_partial)) {
      alreadyCanonical = false;
      break;
    }
    hasPartial ||= value.is_partial;
    const timestampMs = Date.parse(value.timestamp);
    if (timestampMs <= previousTimestamp) {
      alreadyCanonical = false;
      break;
    }
    timestamps.push(timestampMs);
    previousTimestamp = timestampMs;
  }

  if (alreadyCanonical) {
    canonicalMetadataByArray.set(candles as ProjectXMarketCandle[], { timestamps, hasPartial });
    return {
      rows: candles as ProjectXMarketCandle[],
      timestamps,
      reusedInput: true,
    };
  }

  const byTimestamp = new Map<number, ProjectXMarketCandle>();
  for (const value of candles) {
    if (!isCachedMarketCandle(value) || (!includePartial && value.is_partial)) {
      continue;
    }
    const timestampMs = Date.parse(value.timestamp);
    const existing = byTimestamp.get(timestampMs);
    if (existing && !existing.is_partial && value.is_partial) {
      continue;
    }
    byTimestamp.set(timestampMs, value);
  }

  const entries = Array.from(byTimestamp.entries()).sort(
    ([leftTimestamp], [rightTimestamp]) => leftTimestamp - rightTimestamp,
  );
  const rows = entries.map(([, candle]) => candle);
  const sortedTimestamps = entries.map(([timestamp]) => timestamp);
  canonicalMetadataByArray.set(rows, {
    timestamps: sortedTimestamps,
    hasPartial: rows.some((candle) => candle.is_partial),
  });
  return {
    rows,
    timestamps: sortedTimestamps,
    reusedInput: false,
  };
}

function mergeCanonicalMarketCandles(
  existingInput: ProjectXMarketCandle[],
  incomingInput: ProjectXMarketCandle[],
  includePartial: boolean,
  limit: number,
): ProjectXMarketCandle[] {
  const existing = canonicalizeMarketCandles(existingInput, includePartial);
  const incoming = canonicalizeMarketCandles(incomingInput, includePartial);

  if (existingInput === incomingInput && existing.reusedInput && existing.rows.length <= limit) {
    return existingInput;
  }

  if (incoming.rows.length === 0) {
    if (existing.rows.length <= limit) {
      return existing.reusedInput ? existingInput : existing.rows;
    }
    return existing.rows.slice(-limit);
  }
  if (existing.rows.length === 0) {
    if (incoming.rows.length <= limit) {
      return incoming.rows;
    }
    return incoming.rows.slice(-limit);
  }

  const incomingFirstTimestamp = incoming.timestamps[0];
  const existingLastIndex = existing.rows.length - 1;
  const existingLastTimestamp = existing.timestamps[existingLastIndex];
  if (incoming.rows.length === 1 && incomingFirstTimestamp === existingLastTimestamp) {
    const existingLast = existing.rows[existingLastIndex];
    const incomingLast = incoming.rows[0];
    if (
      (!existingLast.is_partial && incomingLast.is_partial) ||
      marketCandlesSemanticallyEqual(existingLast, incomingLast)
    ) {
      return existing.rows.length <= limit && existing.reusedInput
        ? existingInput
        : existing.rows.slice(-limit);
    }
  }
  if (incomingFirstTimestamp > existingLastTimestamp) {
    const appended = [...existing.rows, ...incoming.rows];
    const bounded = appended.length <= limit ? appended : appended.slice(-limit);
    canonicalMetadataByArray.set(bounded, {
      timestamps:
        appended.length <= limit
          ? [...existing.timestamps, ...incoming.timestamps]
          : [...existing.timestamps, ...incoming.timestamps].slice(-limit),
      hasPartial: bounded.some((candle) => candle.is_partial),
    });
    return bounded;
  }

  // Incoming polling data normally overlaps only the tail. Keep the older
  // prefix without walking it, then linearly merge just the overlapping suffix.
  const mergeFrom = lowerBound(existing.timestamps, incoming.timestamps[0]);
  const merged = existing.rows.slice(0, mergeFrom);
  let existingIndex = mergeFrom;
  let incomingIndex = 0;
  let changed = false;

  while (existingIndex < existing.rows.length && incomingIndex < incoming.rows.length) {
    const existingTimestamp = existing.timestamps[existingIndex];
    const incomingTimestamp = incoming.timestamps[incomingIndex];
    if (existingTimestamp < incomingTimestamp) {
      merged.push(existing.rows[existingIndex]);
      existingIndex += 1;
      continue;
    }
    if (incomingTimestamp < existingTimestamp) {
      merged.push(incoming.rows[incomingIndex]);
      changed = true;
      incomingIndex += 1;
      continue;
    }

    const existingRow = existing.rows[existingIndex];
    const incomingRow = incoming.rows[incomingIndex];
    if (!existingRow.is_partial && incomingRow.is_partial) {
      merged.push(existingRow);
    } else if (marketCandlesSemanticallyEqual(existingRow, incomingRow)) {
      // Retaining the existing object makes a semantically unchanged poll a
      // complete array identity no-op after the comparison below.
      merged.push(existingRow);
    } else {
      merged.push(incomingRow);
      changed = true;
    }
    existingIndex += 1;
    incomingIndex += 1;
  }

  while (existingIndex < existing.rows.length) {
    merged.push(existing.rows[existingIndex]);
    existingIndex += 1;
  }
  while (incomingIndex < incoming.rows.length) {
    merged.push(incoming.rows[incomingIndex]);
    changed = true;
    incomingIndex += 1;
  }

  const bounded = merged.length <= limit ? merged : merged.slice(-limit);
  if (existing.reusedInput && !changed && existing.rows.length <= limit) {
    return existingInput;
  }
  canonicalMetadataByArray.set(bounded, {
    timestamps: bounded.map((candle) => Date.parse(candle.timestamp)),
    hasPartial: bounded.some((candle) => candle.is_partial),
  });
  return bounded;
}

function marketCandlesSemanticallyEqual(left: ProjectXMarketCandle, right: ProjectXMarketCandle): boolean {
  return (
    Date.parse(left.timestamp) === Date.parse(right.timestamp) &&
    left.contract_id === right.contract_id &&
    left.symbol === right.symbol &&
    left.live === right.live &&
    left.unit === right.unit &&
    left.unit_number === right.unit_number &&
    left.open === right.open &&
    left.high === right.high &&
    left.low === right.low &&
    left.close === right.close &&
    left.volume === right.volume &&
    left.is_partial === right.is_partial
  );
}

function lowerBound(values: readonly number[], target: number): number {
  let low = 0;
  let high = values.length;
  while (low < high) {
    const midpoint = (low + high) >>> 1;
    if (values[midpoint] < target) {
      low = midpoint + 1;
    } else {
      high = midpoint;
    }
  }
  return low;
}

function upperBound(values: readonly number[], target: number): number {
  let low = 0;
  let high = values.length;
  while (low < high) {
    const midpoint = (low + high) >>> 1;
    if (values[midpoint] <= target) {
      low = midpoint + 1;
    } else {
      high = midpoint;
    }
  }
  return low;
}

function isCachedMarketCandle(value: unknown): value is ProjectXMarketCandle {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candle = value as Partial<ProjectXMarketCandle>;
  return (
    typeof candle.contract_id === "string" &&
    typeof candle.timestamp === "string" &&
    Number.isFinite(Date.parse(candle.timestamp)) &&
    typeof candle.open === "number" && Number.isFinite(candle.open) &&
    typeof candle.high === "number" && Number.isFinite(candle.high) &&
    typeof candle.low === "number" && Number.isFinite(candle.low) &&
    typeof candle.close === "number" && Number.isFinite(candle.close) &&
    typeof candle.volume === "number" && Number.isFinite(candle.volume) &&
    typeof candle.is_partial === "boolean"
  );
}

function parseCoverage(start: unknown, end: unknown): CandleQueryWindow | null {
  if (typeof start !== "string" || typeof end !== "string") {
    return null;
  }
  return normalizeCoverage({ start, end });
}

function normalizeCoverage(coverage: CandleQueryWindow | null | undefined): CandleQueryWindow | null {
  if (!coverage) {
    return null;
  }
  const startMs = Date.parse(coverage.start);
  const endMs = Date.parse(coverage.end);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || startMs > endMs) {
    return null;
  }
  return {
    start: new Date(startMs).toISOString(),
    end: new Date(endMs).toISOString(),
  };
}

function parseDate(value: unknown): Date | null {
  if (typeof value !== "string") {
    return null;
  }
  const timestampMs = Date.parse(value);
  return Number.isFinite(timestampMs) ? new Date(timestampMs) : null;
}

function validDateOrNow(value: Date | undefined): Date {
  return value && Number.isFinite(value.getTime()) ? new Date(value.getTime()) : new Date();
}

function getLocalStorage(): Storage | null {
  try {
    if (typeof window === "undefined") {
      return null;
    }
    return window.localStorage;
  } catch {
    return null;
  }
}

function buildLegacyBotCandleCacheKey(input: BotCandleCacheKeyInput): string {
  const parts = [
    input.live ? "live" : "practice",
    input.contractId.trim().toUpperCase(),
    (input.symbol ?? "").trim().toUpperCase(),
    input.unit,
    String(Math.max(1, Math.trunc(input.unitNumber))),
  ];
  return `${LEGACY_STORAGE_KEY_PREFIX}${encodeURIComponent(parts.join("|"))}`;
}
