const EASTERN_TIME_ZONE = "America/New_York";
const TRADING_DAY_ROLLOVER_HOUR = 18;

const easternDateTimePartsFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: EASTERN_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

type ParsedIsoDate = {
  year: number;
  month: number;
  day: number;
};

function readDatePart(parts: Intl.DateTimeFormatPart[], type: Intl.DateTimeFormatPartTypes) {
  return Number(parts.find((part) => part.type === type)?.value ?? "0");
}

function parseIsoDateInput(value: string): ParsedIsoDate | null {
  const [yearText, monthText, dayText] = value.split("-");
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  if (!Number.isInteger(year) || !Number.isInteger(month) || !Number.isInteger(day)) {
    return null;
  }
  return { year, month, day };
}

function getEasternOffsetMs(utcInstant: Date) {
  const parts = easternDateTimePartsFormatter.formatToParts(utcInstant);
  const year = readDatePart(parts, "year");
  const month = readDatePart(parts, "month");
  const day = readDatePart(parts, "day");
  const hour = readDatePart(parts, "hour");
  const minute = readDatePart(parts, "minute");
  const second = readDatePart(parts, "second");
  const asUtcMs = Date.UTC(year, month - 1, day, hour, minute, second);
  return asUtcMs - utcInstant.getTime();
}

function easternLocalDateTimeToUtc(
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
  second: number,
  millisecond: number,
) {
  const localTimeAsUtcMs = Date.UTC(year, month - 1, day, hour, minute, second, millisecond);
  let utcMs = localTimeAsUtcMs;
  // Iterate to account for DST transitions when converting ET local time to UTC.
  for (let index = 0; index < 4; index += 1) {
    const offsetMs = getEasternOffsetMs(new Date(utcMs));
    const nextUtcMs = localTimeAsUtcMs - offsetMs;
    if (nextUtcMs === utcMs) {
      break;
    }
    utcMs = nextUtcMs;
  }
  return new Date(utcMs);
}

function parseInstant(value: Date | string): Date {
  const parsed = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(parsed.getTime())) {
    throw new Error("invalid_timestamp");
  }
  return parsed;
}

export function formatIsoDateUtc(value: Date): string {
  const year = value.getUTCFullYear();
  const month = String(value.getUTCMonth() + 1).padStart(2, "0");
  const day = String(value.getUTCDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function addIsoDays(value: string, days: number): string | null {
  const parsed = parseIsoDateInput(value);
  if (!parsed) {
    return null;
  }
  const next = new Date(Date.UTC(parsed.year, parsed.month - 1, parsed.day + days, 0, 0, 0, 0));
  return formatIsoDateUtc(next);
}

export function tradingDayKey(value: Date | string): string {
  const instant = parseInstant(value);
  const parts = easternDateTimePartsFormatter.formatToParts(instant);
  const year = readDatePart(parts, "year");
  const month = readDatePart(parts, "month");
  const day = readDatePart(parts, "day");
  const hour = readDatePart(parts, "hour");
  const baseDate = `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;

  if (hour >= TRADING_DAY_ROLLOVER_HOUR) {
    const nextDate = addIsoDays(baseDate, 1);
    if (nextDate) {
      return nextDate;
    }
  }
  return baseDate;
}

export function getTradingDayRange(value: string): { start: string; end: string } | null {
  const parsed = parseIsoDateInput(value);
  const priorDate = addIsoDays(value, -1);
  const parsedPrior = priorDate ? parseIsoDateInput(priorDate) : null;
  if (!parsed || !parsedPrior) {
    return null;
  }

  const start = easternLocalDateTimeToUtc(parsedPrior.year, parsedPrior.month, parsedPrior.day, 18, 0, 0, 0);
  const nextBoundary = easternLocalDateTimeToUtc(parsed.year, parsed.month, parsed.day, 18, 0, 0, 0);
  return {
    start: start.toISOString(),
    end: new Date(nextBoundary.getTime() - 1).toISOString(),
  };
}

export function getTradingDayBoundaryIso(value: string, endOfDay: boolean): string | null {
  const range = getTradingDayRange(value);
  if (!range) {
    return null;
  }
  return endOfDay ? range.end : range.start;
}
