import type { BotTimeframeUnit, ProjectXMarketCandle } from "../../lib/types";

/**
 * Candle gap detection and classification for the bot signal chart.
 *
 * A "gap" is a run of expected candle buckets with no data between two loaded
 * candles. Gaps are classified as:
 * - "session": every missing bucket falls inside a scheduled market closure
 *   (CME Globex futures hours: nightly 5-6pm ET maintenance break and the
 *   Fri 5pm -> Sun 6pm ET weekend closure). These are normal and not a data
 *   quality problem.
 * - "data": at least one missing bucket falls inside regular trading hours.
 *   These are either provider holes (repairable by refetching) or genuine
 *   no-trade/holiday periods the session model does not know about.
 *
 * Exchange holidays are intentionally not modeled; a holiday shows up as a
 * "data" gap that a repair fetch cannot fill. Callers should treat a gap that
 * survives a repair attempt as "confirmed empty" rather than retrying forever.
 */

export type CandleGapKind = "session" | "data";

export interface CandleGap {
  kind: CandleGapKind;
  /** Timestamp (ISO) of the last candle before the gap. */
  beforeTimestamp: string;
  /** Timestamp (ISO) of the first candle after the gap. */
  afterTimestamp: string;
  /** First missing bucket start, epoch ms. */
  fromMs: number;
  /** End of the gap (start of the first present bucket), epoch ms. */
  toMs: number;
  /** Total missing buckets. */
  missingBars: number;
  /** Missing buckets that fall inside regular session hours. */
  missingSessionBars: number;
}

export interface GapRepairWindow {
  start: string;
  end: string;
}

const UNIT_SECONDS_BY_NAME: Record<BotTimeframeUnit, number> = {
  second: 1,
  minute: 60,
  hour: 60 * 60,
  day: 24 * 60 * 60,
  week: 7 * 24 * 60 * 60,
  month: 31 * 24 * 60 * 60,
};

const EASTERN_TIME_ZONE = "America/New_York";
/** Globex maintenance break: 17:00-17:59 ET Monday-Thursday (and Friday close). */
const SESSION_BREAK_START_MINUTES = 17 * 60;
const SESSION_BREAK_END_MINUTES = 18 * 60;
/** Cap per-gap bucket scans; beyond this a gap is sampled instead of walked. */
const MAX_GAP_BUCKET_SCAN = 5_000;

const easternHourFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: EASTERN_TIME_ZONE,
  weekday: "short",
  hour: "2-digit",
  hourCycle: "h23",
});

interface EasternHourInfo {
  /** 0 = Sunday ... 6 = Saturday */
  weekday: number;
  hour: number;
}

const WEEKDAY_INDEX: Record<string, number> = {
  Sun: 0,
  Mon: 1,
  Tue: 2,
  Wed: 3,
  Thu: 4,
  Fri: 5,
  Sat: 6,
};

const easternHourInfoCache = new Map<number, EasternHourInfo>();

function easternHourInfo(timestampMs: number): EasternHourInfo {
  const hourKey = Math.floor(timestampMs / 3_600_000);
  const cached = easternHourInfoCache.get(hourKey);
  if (cached) {
    return cached;
  }

  let weekday = 1;
  let hour = 0;
  for (const part of easternHourFormatter.formatToParts(new Date(hourKey * 3_600_000))) {
    if (part.type === "weekday") {
      weekday = WEEKDAY_INDEX[part.value] ?? 1;
    } else if (part.type === "hour") {
      hour = Number(part.value);
    }
  }

  const info = { weekday, hour };
  if (easternHourInfoCache.size > 20_000) {
    easternHourInfoCache.clear();
  }
  easternHourInfoCache.set(hourKey, info);
  return info;
}

/**
 * Whether a candle bucket starting at `timestampMs` falls inside CME Globex
 * futures trading hours (Sun 18:00 ET through Fri 17:00 ET, with a daily
 * 17:00-18:00 ET maintenance break).
 *
 * The ET offset is always a whole number of hours, so the UTC minute is the
 * ET minute; only weekday+hour need the timezone conversion (memoized per hour).
 */
export function isFuturesSessionOpen(timestampMs: number): boolean {
  const { weekday, hour } = easternHourInfo(timestampMs);
  const minute = new Date(timestampMs).getUTCMinutes();
  const minutesOfDay = hour * 60 + minute;

  if (weekday === 6) {
    return false; // Saturday
  }
  if (weekday === 0) {
    return minutesOfDay >= SESSION_BREAK_END_MINUTES; // Sunday opens 18:00 ET
  }
  if (weekday === 5) {
    return minutesOfDay < SESSION_BREAK_START_MINUTES; // Friday closes 17:00 ET
  }
  return minutesOfDay < SESSION_BREAK_START_MINUTES || minutesOfDay >= SESSION_BREAK_END_MINUTES;
}

/**
 * Weekday (Mon-Fri) check for daily bars. Daily buckets are date-stamped, so
 * the UTC weekday is used; converting to ET would shift a midnight-stamped bar
 * into the previous evening and misclassify weekends.
 */
function isUtcWeekday(timestampMs: number): boolean {
  const weekday = new Date(timestampMs).getUTCDay();
  return weekday >= 1 && weekday <= 5;
}

export function intervalSecondsFor(unit: BotTimeframeUnit, unitNumber: number): number {
  return UNIT_SECONDS_BY_NAME[unit] * Math.max(1, Math.trunc(unitNumber));
}

/**
 * Scan sorted candles for missing buckets and classify each gap.
 * Detection is supported for second/minute/hour/day timeframes; week/month
 * gaps are not meaningful enough to flag and return [].
 */
export function findCandleGaps(
  candles: ProjectXMarketCandle[],
  unit: BotTimeframeUnit,
  unitNumber: number,
): CandleGap[] {
  if (unit === "week" || unit === "month") {
    return [];
  }

  const intervalMs = intervalSecondsFor(unit, unitNumber) * 1000;
  if (intervalMs <= 0) {
    return [];
  }

  const sortedTimestamps = dedupeSortedTimestamps(candles);
  if (sortedTimestamps.length < 2) {
    return [];
  }

  const gaps: CandleGap[] = [];
  for (let index = 1; index < sortedTimestamps.length; index += 1) {
    const previous = sortedTimestamps[index - 1];
    const current = sortedTimestamps[index];
    const deltaMs = current.ms - previous.ms;
    if (deltaMs <= intervalMs) {
      continue;
    }

    const fromMs = previous.ms + intervalMs;
    const toMs = current.ms;
    const { missingBars, missingSessionBars } = countMissingBuckets(fromMs, toMs, intervalMs, unit);
    if (missingBars <= 0) {
      continue;
    }

    gaps.push({
      kind: missingSessionBars > 0 ? "data" : "session",
      beforeTimestamp: previous.iso,
      afterTimestamp: current.iso,
      fromMs,
      toMs,
      missingBars,
      missingSessionBars,
    });
  }

  return gaps;
}

function countMissingBuckets(
  fromMs: number,
  toMs: number,
  intervalMs: number,
  unit: BotTimeframeUnit,
): { missingBars: number; missingSessionBars: number } {
  const missingBars = Math.max(0, Math.round((toMs - fromMs) / intervalMs));
  if (missingBars === 0) {
    return { missingBars: 0, missingSessionBars: 0 };
  }

  const isInSession = unit === "day" ? isUtcWeekday : isFuturesSessionOpen;
  const step = missingBars > MAX_GAP_BUCKET_SCAN ? Math.ceil(missingBars / MAX_GAP_BUCKET_SCAN) : 1;
  let missingSessionBars = 0;
  for (let bucket = 0; bucket < missingBars; bucket += step) {
    if (isInSession(fromMs + bucket * intervalMs)) {
      missingSessionBars += step;
    }
  }

  return { missingBars, missingSessionBars: Math.min(missingBars, missingSessionBars) };
}

/**
 * Build bounded fetch windows that cover the data gaps, for backfill requests.
 * Adjacent/overlapping windows (within one interval) are merged and the result
 * is capped to `maxWindows`, keeping the largest gaps first.
 */
export function buildGapRepairWindows(
  gaps: CandleGap[],
  unit: BotTimeframeUnit,
  unitNumber: number,
  maxWindows = 3,
): GapRepairWindow[] {
  const intervalMs = intervalSecondsFor(unit, unitNumber) * 1000;
  const dataGaps = gaps
    .filter((gap) => gap.kind === "data")
    .sort((left, right) => right.missingSessionBars - left.missingSessionBars)
    .slice(0, Math.max(1, maxWindows));

  const ranges = dataGaps
    .map((gap) => ({
      // Pad one interval on each side so the provider returns the bracketing bars too.
      fromMs: gap.fromMs - intervalMs,
      toMs: gap.toMs + intervalMs,
    }))
    .sort((left, right) => left.fromMs - right.fromMs);

  const merged: { fromMs: number; toMs: number }[] = [];
  for (const range of ranges) {
    const last = merged[merged.length - 1];
    if (last && range.fromMs <= last.toMs + intervalMs) {
      last.toMs = Math.max(last.toMs, range.toMs);
    } else {
      merged.push({ ...range });
    }
  }

  return merged.map((range) => ({
    start: new Date(range.fromMs).toISOString(),
    end: new Date(range.toMs).toISOString(),
  }));
}

export function buildGapRangeKey(gap: CandleGap): string {
  return `${gap.fromMs}:${gap.toMs}`;
}

function dedupeSortedTimestamps(candles: ProjectXMarketCandle[]): { ms: number; iso: string }[] {
  const byMs = new Map<number, string>();
  for (const candle of candles) {
    const ms = Date.parse(candle.timestamp);
    if (Number.isFinite(ms)) {
      byMs.set(ms, candle.timestamp);
    }
  }
  return Array.from(byMs.entries())
    .map(([ms, iso]) => ({ ms, iso }))
    .sort((left, right) => left.ms - right.ms);
}
