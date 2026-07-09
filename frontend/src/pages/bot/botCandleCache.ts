import type { BotTimeframeUnit, ProjectXMarketCandle } from "../../lib/types";

const STORAGE_KEY_PREFIX = "topsignal:bot-candles:v1:";

interface BotCandleCacheKeyInput {
  contractId: string;
  symbol?: string | null;
  live: boolean;
  unit: BotTimeframeUnit;
  unitNumber: number;
}

interface BotCandleCachePayload {
  savedAt: string;
  candles: ProjectXMarketCandle[];
}

interface CandleQueryWindow {
  start: string;
  end: string;
}

export interface BotCandleCacheEntry {
  savedAt: Date | null;
  candles: ProjectXMarketCandle[];
}

export function buildBotCandleCacheKey(input: BotCandleCacheKeyInput): string {
  const parts = [
    input.live ? "live" : "practice",
    input.contractId.trim().toUpperCase(),
    (input.symbol ?? "").trim().toUpperCase(),
    input.unit,
    String(Math.max(1, Math.trunc(input.unitNumber))),
  ];
  return `${STORAGE_KEY_PREFIX}${encodeURIComponent(parts.join("|"))}`;
}

export function readBotCandleCache(cacheKey: string): BotCandleCacheEntry | null {
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
    const candles = selectMarketCandles(parsed.candles, false);
    if (candles.length === 0) {
      return null;
    }
    const savedAtMs = typeof parsed.savedAt === "string" ? Date.parse(parsed.savedAt) : Number.NaN;
    return {
      savedAt: Number.isFinite(savedAtMs) ? new Date(savedAtMs) : null,
      candles,
    };
  } catch {
    storage.removeItem(cacheKey);
    return null;
  }
}

export function writeBotCandleCache(cacheKey: string, candles: ProjectXMarketCandle[], limit: number): void {
  const storage = getLocalStorage();
  if (!storage) {
    return;
  }

  const rows = trimCandlesForCache(candles, limit);
  if (rows.length === 0) {
    storage.removeItem(cacheKey);
    return;
  }

  try {
    storage.setItem(
      cacheKey,
      JSON.stringify({
        savedAt: new Date().toISOString(),
        candles: rows,
      } satisfies BotCandleCachePayload),
    );
  } catch {
    // localStorage may be full or disabled; the network path still works.
  }
}

export function mergeMarketCandles(
  existingCandles: ProjectXMarketCandle[],
  incomingCandles: ProjectXMarketCandle[],
  limit: number,
): ProjectXMarketCandle[] {
  return trimCandlesForCache([...existingCandles, ...incomingCandles], limit);
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

  return selectMarketCandles(candles, true)
    .filter((candle) => {
      const timestampMs = Date.parse(candle.timestamp);
      return timestampMs >= startMs && timestampMs <= endMs;
    });
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
  const sorted = selectMarketCandles([...existingCandles, ...incomingCandles], true);
  if (limit === undefined) {
    return sorted;
  }
  return sorted.slice(-Math.max(1, Math.trunc(limit)));
}

function trimCandlesForCache(candles: ProjectXMarketCandle[], limit: number): ProjectXMarketCandle[] {
  const boundedLimit = Math.max(1, Math.trunc(limit));
  return selectMarketCandles(candles, false).slice(-boundedLimit);
}

/**
 * Select one canonical row per instant without altering its provider OHLC.
 * Closed rows always outrank partial rows, regardless of array order or ISO
 * timestamp spelling; rows with equal completion state retain last-write-wins.
 */
function selectMarketCandles(candles: unknown[], includePartial: boolean): ProjectXMarketCandle[] {
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

  return Array.from(byTimestamp.entries())
    .sort(([leftTimestamp], [rightTimestamp]) => leftTimestamp - rightTimestamp)
    .map(([, candle]) => candle);
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
