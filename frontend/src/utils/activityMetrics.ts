import type { AccountPnlCalendarDay } from "../lib/types";

const MILLISECONDS_PER_DAY = 24 * 60 * 60 * 1000;

export interface ActivityMetricsInput {
  totalTrades: number;
  activeDays: number;
  dailyPnlDays: AccountPnlCalendarDay[];
  rangeStart?: string;
  rangeEnd?: string;
  activeHours?: number | null;
}

export interface ActivityMetrics {
  medianTradesPerDay: number | null;
  maxTradesInDay: number | null;
  tradesPerWeek: number | null;
  activeDaysPerWeek: number | null;
  tradesPerActiveHour: number | null;
  rangeDays: number | null;
}

function safeCount(value: number) {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.max(0, value);
}

function parseIsoTimestamp(value: string | undefined) {
  if (!value) {
    return null;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  return parsed;
}

function parseIsoDate(value: string) {
  return new Date(`${value}T00:00:00.000Z`);
}

function computeRangeDaysFromTimestamps(start: Date, end: Date) {
  const elapsedDays = (end.getTime() - start.getTime()) / MILLISECONDS_PER_DAY;
  if (!Number.isFinite(elapsedDays) || elapsedDays < 0) {
    return null;
  }
  // Weekly pacing uses elapsed range days; partial days round up, minimum 1 day.
  return Math.max(1, Math.ceil(elapsedDays));
}

function computeRangeDaysFromDailySeries(dailyPnlDays: AccountPnlCalendarDay[]) {
  if (dailyPnlDays.length === 0) {
    return null;
  }

  const orderedDayTimestamps = dailyPnlDays
    .map((point) => parseIsoDate(point.date).getTime())
    .filter((value) => Number.isFinite(value))
    .sort((left, right) => left - right);

  if (orderedDayTimestamps.length === 0) {
    return null;
  }

  const first = orderedDayTimestamps[0];
  const last = orderedDayTimestamps[orderedDayTimestamps.length - 1];
  return Math.max(1, Math.floor((last - first) / MILLISECONDS_PER_DAY) + 1);
}

function computeRangeDays(rangeStart: string | undefined, rangeEnd: string | undefined, dailyPnlDays: AccountPnlCalendarDay[]) {
  const parsedStart = parseIsoTimestamp(rangeStart);
  const parsedEnd = parseIsoTimestamp(rangeEnd);

  if (parsedStart && parsedEnd) {
    return computeRangeDaysFromTimestamps(parsedStart, parsedEnd);
  }

  return computeRangeDaysFromDailySeries(dailyPnlDays);
}

function median(values: number[]) {
  if (values.length === 0) {
    return null;
  }

  const ordered = [...values].sort((left, right) => left - right);
  const middleIndex = Math.floor(ordered.length / 2);
  if (ordered.length % 2 === 1) {
    return ordered[middleIndex];
  }
  return (ordered[middleIndex - 1] + ordered[middleIndex]) / 2;
}

export function computeActivityMetrics({
  totalTrades,
  activeDays,
  dailyPnlDays,
  rangeStart,
  rangeEnd,
  activeHours,
}: ActivityMetricsInput): ActivityMetrics {
  // Daily points are already grouped by account trading day from the API.
  const activeDailyTradeCounts = dailyPnlDays
    .map((point) => safeCount(point.trade_count))
    .filter((count) => count > 0);

  const medianTradesPerDay = median(activeDailyTradeCounts);
  const maxTradesInDay =
    activeDailyTradeCounts.length > 0 ? activeDailyTradeCounts.reduce((maxCount, count) => Math.max(maxCount, count), 0) : null;

  const rangeDays = computeRangeDays(rangeStart, rangeEnd, dailyPnlDays);
  const weeks = rangeDays === null ? null : rangeDays / 7;
  const safeTotalTrades = safeCount(totalTrades);
  const safeActiveDays = safeCount(activeDays);
  const tradesPerWeek = weeks && weeks > 0 ? safeTotalTrades / weeks : null;
  const activeDaysPerWeek = weeks && weeks > 0 ? safeActiveDays / weeks : null;

  const safeActiveHours = activeHours === null || activeHours === undefined ? null : activeHours;
  const tradesPerActiveHour =
    safeActiveHours !== null && Number.isFinite(safeActiveHours) && safeActiveHours > 0 ? safeTotalTrades / safeActiveHours : null;

  return {
    medianTradesPerDay,
    maxTradesInDay,
    tradesPerWeek,
    activeDaysPerWeek,
    tradesPerActiveHour,
    rangeDays,
  };
}

