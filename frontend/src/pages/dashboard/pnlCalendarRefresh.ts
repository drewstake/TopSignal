import { getTradingDayBoundaryIso } from "../../lib/tradingDay";

export interface PnlCalendarMonthRange {
  startDate: string;
  endDate: string;
}

export interface PnlCalendarRefreshWindow extends PnlCalendarMonthRange {
  start: string;
  end: string;
}

export function buildPnlCalendarRefreshWindow(
  range: PnlCalendarMonthRange,
  now: Date = new Date(),
): PnlCalendarRefreshWindow | null {
  if (range.startDate > range.endDate || !Number.isFinite(now.getTime())) {
    return null;
  }

  const start = getTradingDayBoundaryIso(range.startDate, false);
  const requestedEnd = getTradingDayBoundaryIso(range.endDate, true);
  if (!start || !requestedEnd) {
    return null;
  }

  const startTime = Date.parse(start);
  const requestedEndTime = Date.parse(requestedEnd);
  if (!Number.isFinite(startTime) || !Number.isFinite(requestedEndTime)) {
    return null;
  }

  const nowTime = now.getTime();
  // JavaScript Date values stop at milliseconds while the API boundary carries
  // microseconds. Treat an equal millisecond as still open and cap it at `now`.
  const end = requestedEndTime >= nowTime ? now.toISOString() : requestedEnd;
  if (startTime > Math.min(requestedEndTime, nowTime)) {
    return null;
  }

  return { ...range, start, end };
}
