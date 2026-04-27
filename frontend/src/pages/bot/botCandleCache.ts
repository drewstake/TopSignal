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
    const candles = parsed.candles.filter(isCachedMarketCandle);
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
  const byTimestamp = new Map<string, ProjectXMarketCandle>();
  for (const candle of [...existingCandles, ...incomingCandles]) {
    if (!isCachedMarketCandle(candle) || candle.is_partial) {
      continue;
    }
    byTimestamp.set(candle.timestamp, candle);
  }
  return trimCandlesForCache(Array.from(byTimestamp.values()), limit);
}

function trimCandlesForCache(candles: ProjectXMarketCandle[], limit: number): ProjectXMarketCandle[] {
  const boundedLimit = Math.max(1, Math.trunc(limit));
  return candles
    .filter((candle) => isCachedMarketCandle(candle) && !candle.is_partial)
    .sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp))
    .slice(-boundedLimit);
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
    typeof candle.open === "number" &&
    typeof candle.high === "number" &&
    typeof candle.low === "number" &&
    typeof candle.close === "number" &&
    typeof candle.volume === "number" &&
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
