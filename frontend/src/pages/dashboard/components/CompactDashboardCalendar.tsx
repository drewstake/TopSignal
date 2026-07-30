import {
  memo,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import { Card } from "../../../components/ui/Card";
import { cn } from "../../../components/ui/cn";
import type { AccountPnlCalendarDay } from "../../../lib/types";
import { formatPnl } from "../../../utils/formatters";
import {
  CompactState,
  InfoPopover,
  compactFocusRing,
} from "./CompactDashboardPrimitives";

const weekdayLabels = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

const monthFormatter = new Intl.DateTimeFormat("en-US", {
  month: "long",
  year: "numeric",
  timeZone: "UTC",
});

const shortDateFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

const longDateFormatter = new Intl.DateTimeFormat("en-US", {
  month: "long",
  day: "numeric",
  year: "numeric",
  timeZone: "UTC",
});

const compactCurrencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});

function parseIsoDay(value: string) {
  return new Date(`${value}T00:00:00Z`);
}

function formatShortDate(value: string) {
  return shortDateFormatter.format(parseIsoDay(value));
}

function formatLongDate(value: string) {
  return longDateFormatter.format(parseIsoDay(value));
}

function formatCompactCurrency(value: number) {
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${compactCurrencyFormatter.format(value)}`;
}

function pnlTextClass(value: number) {
  if (value > 0) {
    return "text-app-positive-text";
  }
  if (value < 0) {
    return "text-app-negative-text";
  }
  return "text-app-text";
}

function monthStartUtc(value: Date) {
  return new Date(Date.UTC(value.getUTCFullYear(), value.getUTCMonth(), 1));
}

function addUtcMonths(value: Date, delta: number) {
  return new Date(Date.UTC(value.getUTCFullYear(), value.getUTCMonth() + delta, 1));
}

function formatIsoDay(value: Date) {
  return value.toISOString().slice(0, 10);
}

function isSameUtcMonth(date: string, month: Date) {
  const parsed = parseIsoDay(date);
  return parsed.getUTCFullYear() === month.getUTCFullYear() && parsed.getUTCMonth() === month.getUTCMonth();
}

function dayTone(point: AccountPnlCalendarDay | null) {
  if (!point) {
    return "border-app-border/45 bg-app-bg/25";
  }
  if (point.net_pnl > 0) {
    return "border-app-positive/35 bg-app-positive-soft/25";
  }
  if (point.net_pnl < 0) {
    return "border-app-negative/35 bg-app-negative-soft/25";
  }
  return "border-app-border/60 bg-app-surface-raised/50";
}

interface CalendarCell {
  date: string | null;
  dayNumber: number | null;
  point: AccountPnlCalendarDay | null;
}

interface WeeklySummary {
  tradeCount: number;
  netPnl: number;
}

export interface CompactCalendarProps {
  days: readonly AccountPnlCalendarDay[];
  rangeStartDate?: string;
  rangeEndDate?: string;
  loading: boolean;
  error: string | null;
  journalDays: ReadonlySet<string>;
  journalDaysLoading: boolean;
  journalDaysError?: string | null;
  scopeKey: string;
  selectedDate: string | null;
  onDaySelect: (date: string | null) => void;
  onJournalDayOpen: (date: string) => void;
  onVisibleRangeChange: (startDate: string, endDate: string) => void;
}

export const CompactDashboardCalendar = memo(function CompactDashboardCalendar({
  days,
  rangeStartDate,
  rangeEndDate,
  loading,
  error,
  journalDays,
  journalDaysLoading,
  journalDaysError,
  scopeKey,
  selectedDate,
  onDaySelect,
  onJournalDayOpen,
  onVisibleRangeChange,
}: CompactCalendarProps) {
  const titleId = `${useId().replace(/:/g, "")}-calendar-title`;
  const dayRefs = useRef(new Map<string, HTMLButtonElement>());
  const pendingFocusRef = useRef<string | null>(null);
  const previousScopeKeyRef = useRef(scopeKey);
  const dayMap = useMemo(() => new Map(days.map((day) => [day.date, day])), [days]);
  const bounds = useMemo(() => {
    if (rangeStartDate && rangeEndDate) {
      return {
        min: monthStartUtc(parseIsoDay(rangeStartDate)),
        max: monthStartUtc(parseIsoDay(rangeEndDate)),
      };
    }
    if (days.length === 0) {
      const current = monthStartUtc(new Date());
      return { min: current, max: current };
    }
    const ordered = [...days].sort((left, right) => left.date.localeCompare(right.date));
    return {
      min: monthStartUtc(parseIsoDay(ordered[0].date)),
      max: monthStartUtc(parseIsoDay(ordered[ordered.length - 1].date)),
    };
  }, [days, rangeEndDate, rangeStartDate]);
  const [visibleMonth, setVisibleMonth] = useState(() => bounds.max);
  const [rovingDate, setRovingDate] = useState(() => (
    selectedDate && isSameUtcMonth(selectedDate, bounds.max)
      ? selectedDate
      : formatIsoDay(bounds.max)
  ));
  const [useAgendaLayout, setUseAgendaLayout] = useState(() => (
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia("(max-width: 399px)").matches
      : false
  ));

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return undefined;
    }
    const mediaQuery = window.matchMedia("(max-width: 399px)");
    const handleChange = (event: MediaQueryListEvent) => setUseAgendaLayout(event.matches);
    setUseAgendaLayout(mediaQuery.matches);
    mediaQuery.addEventListener("change", handleChange);
    return () => mediaQuery.removeEventListener("change", handleChange);
  }, []);

  useEffect(() => {
    if (previousScopeKeyRef.current === scopeKey) {
      return;
    }
    previousScopeKeyRef.current = scopeKey;
    pendingFocusRef.current = null;
    setVisibleMonth(bounds.max);
    setRovingDate(
      selectedDate && isSameUtcMonth(selectedDate, bounds.max)
        ? selectedDate
        : formatIsoDay(bounds.max),
    );
  }, [bounds.max, scopeKey, selectedDate]);

  useEffect(() => {
    setVisibleMonth((current) => {
      if (current.getTime() < bounds.min.getTime()) {
        return bounds.min;
      }
      if (current.getTime() > bounds.max.getTime()) {
        return bounds.max;
      }
      return current;
    });
  }, [bounds.max, bounds.min]);

  useEffect(() => {
    const start = monthStartUtc(visibleMonth);
    const end = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 0));
    onVisibleRangeChange(formatIsoDay(start), formatIsoDay(end));
  }, [onVisibleRangeChange, visibleMonth]);

  const cells = useMemo(() => {
    const year = visibleMonth.getUTCFullYear();
    const month = visibleMonth.getUTCMonth();
    const first = new Date(Date.UTC(year, month, 1));
    const daysInMonth = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
    const result: CalendarCell[] = [];
    for (let index = 0; index < first.getUTCDay(); index += 1) {
      result.push({ date: null, dayNumber: null, point: null });
    }
    for (let day = 1; day <= daysInMonth; day += 1) {
      const date = formatIsoDay(new Date(Date.UTC(year, month, day)));
      result.push({ date, dayNumber: day, point: dayMap.get(date) ?? null });
    }
    while (result.length < 42) {
      result.push({ date: null, dayNumber: null, point: null });
    }
    return result;
  }, [dayMap, visibleMonth]);
  const weeks = useMemo(() => {
    const rows: CalendarCell[][] = [];
    for (let index = 0; index < cells.length; index += 7) {
      rows.push(cells.slice(index, index + 7));
    }
    return rows;
  }, [cells]);
  const weeklySummaries = useMemo<WeeklySummary[]>(() => (
    weeks.map((week) => week.reduce<WeeklySummary>((summary, cell) => {
      if (
        !cell.date
        || !cell.point
        || (rangeStartDate && cell.date < rangeStartDate)
        || (rangeEndDate && cell.date > rangeEndDate)
      ) {
        return summary;
      }
      return {
        tradeCount: summary.tradeCount + cell.point.trade_count,
        netPnl: summary.netPnl + cell.point.net_pnl,
      };
    }, { tradeCount: 0, netPnl: 0 }))
  ), [rangeEndDate, rangeStartDate, weeks]);
  const weeklySummaryBySaturday = useMemo(() => {
    const summaries = new Map<string, WeeklySummary>();
    weeks.forEach((week, weekIndex) => {
      const saturday = week[6];
      const summary = weeklySummaries[weekIndex];
      if (saturday?.date && summary.tradeCount > 0) {
        summaries.set(saturday.date, summary);
      }
    });
    return summaries;
  }, [weeklySummaries, weeks]);
  const visibleSummary = useMemo(() => cells.reduce((summary, cell) => {
    if (
      !cell.date
      || !cell.point
      || (rangeStartDate && cell.date < rangeStartDate)
      || (rangeEndDate && cell.date > rangeEndDate)
    ) {
      return summary;
    }
    return {
      tradingDays: summary.tradingDays + 1,
      tradeCount: summary.tradeCount + cell.point.trade_count,
      netPnl: summary.netPnl + cell.point.net_pnl,
    };
  }, { tradingDays: 0, tradeCount: 0, netPnl: 0 }), [cells, rangeEndDate, rangeStartDate]);
  const visibleDates = useMemo(
    () => cells.flatMap((cell) => cell.date ? [cell.date] : []),
    [cells],
  );
  const isWithinSelectedRange = (date: string) => (
    (!rangeStartDate || date >= rangeStartDate) && (!rangeEndDate || date <= rangeEndDate)
  );
  const selectableDates = useMemo(
    () => visibleDates.filter((date) => (
      (!rangeStartDate || date >= rangeStartDate) && (!rangeEndDate || date <= rangeEndDate)
    )),
    [rangeEndDate, rangeStartDate, visibleDates],
  );

  useEffect(() => {
    if (selectedDate && selectableDates.includes(selectedDate)) {
      setRovingDate(selectedDate);
      return;
    }
    if (!selectableDates.includes(rovingDate)) {
      setRovingDate(selectableDates[0] ?? "");
    }
  }, [rovingDate, selectableDates, selectedDate]);

  useEffect(() => {
    const pendingDate = pendingFocusRef.current;
    if (!pendingDate || !isSameUtcMonth(pendingDate, visibleMonth)) {
      return;
    }
    const target = dayRefs.current.get(pendingDate);
    if (target) {
      pendingFocusRef.current = null;
      setRovingDate(pendingDate);
      target.focus();
    }
  }, [cells, visibleMonth]);

  const focusDate = (date: string | undefined) => {
    if (!date || !isWithinSelectedRange(date)) {
      return;
    }
    const targetMonth = monthStartUtc(parseIsoDay(date));
    if (targetMonth.getTime() !== visibleMonth.getTime()) {
      pendingFocusRef.current = date;
      setVisibleMonth(targetMonth);
      return;
    }
    setRovingDate(date);
    dayRefs.current.get(date)?.focus();
  };

  const handleDayKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>, date: string) => {
    const current = parseIsoDay(date);
    const weekday = current.getUTCDay();
    let delta: number | null = null;
    if (event.key === "ArrowLeft") {
      delta = -1;
    } else if (event.key === "ArrowRight") {
      delta = 1;
    } else if (event.key === "ArrowUp") {
      delta = -7;
    } else if (event.key === "ArrowDown") {
      delta = 7;
    } else if (event.key === "Home") {
      delta = -weekday;
    } else if (event.key === "End") {
      delta = 6 - weekday;
    }
    if (delta === null) {
      return;
    }
    const target = new Date(current);
    target.setUTCDate(target.getUTCDate() + delta);
    let targetDate = formatIsoDay(target);
    if (event.key === "Home" && rangeStartDate && targetDate < rangeStartDate) {
      targetDate = rangeStartDate;
    }
    if (event.key === "End" && rangeEndDate && targetDate > rangeEndDate) {
      targetDate = rangeEndDate;
    }
    if (!isWithinSelectedRange(targetDate)) {
      return;
    }
    event.preventDefault();
    focusDate(targetDate);
  };

  const canGoPrevious = visibleMonth.getTime() > bounds.min.getTime();
  const canGoNext = visibleMonth.getTime() < bounds.max.getTime();
  const visibleTradeDays = selectableDates.filter((date) => dayMap.has(date)).length;
  const agendaCells = cells.filter((cell) => (
    cell.date
    && isWithinSelectedRange(cell.date)
    && (
      cell.point
      || weeklySummaryBySaturday.has(cell.date)
      || journalDays.has(cell.date)
      || selectedDate === cell.date
    )
  ));
  const agendaDates = agendaCells.flatMap((cell) => cell.date ? [cell.date] : []);
  const agendaRovingDate = agendaDates.includes(rovingDate) ? rovingDate : (agendaDates[0] ?? "");
  const handleAgendaKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>, date: string) => {
    const currentIndex = agendaDates.indexOf(date);
    let targetIndex: number | null = null;
    if (event.key === "ArrowUp") {
      targetIndex = currentIndex - 1;
    } else if (event.key === "ArrowDown") {
      targetIndex = currentIndex + 1;
    } else if (event.key === "Home") {
      targetIndex = 0;
    } else if (event.key === "End") {
      targetIndex = agendaDates.length - 1;
    }
    if (targetIndex === null || targetIndex < 0 || targetIndex >= agendaDates.length) {
      return;
    }
    event.preventDefault();
    focusDate(agendaDates[targetIndex]);
  };
  const selectedJournalDate = selectedDate && isSameUtcMonth(selectedDate, visibleMonth) && journalDays.has(selectedDate)
    ? selectedDate
    : null;
  const showCalendarFooter = Boolean(
    journalDaysError
    || selectedJournalDate
    || (visibleTradeDays === 0 && !journalDaysError),
  );
  const today = formatIsoDay(new Date());

  return (
    <Card
      aria-labelledby={titleId}
      aria-busy={loading}
      className="min-w-0 overflow-hidden p-0 md:p-0"
    >
      <div className="flex min-h-14 flex-wrap items-center gap-1 border-b border-app-border/70 px-2 py-1.5 sm:px-4">
        <button
          type="button"
          onClick={() => setVisibleMonth((current) => addUtcMonths(current, -1))}
          disabled={!canGoPrevious}
          className={cn(
            "inline-flex h-11 w-11 items-center justify-center rounded-xl text-xl text-app-muted-text transition hover:bg-app-accent/10 hover:text-app-text disabled:cursor-not-allowed disabled:opacity-30",
            compactFocusRing,
          )}
          aria-label="Previous month"
        >
          ‹
        </button>
        <button
          type="button"
          onClick={() => setVisibleMonth((current) => addUtcMonths(current, 1))}
          disabled={!canGoNext}
          className={cn(
            "inline-flex h-11 w-11 items-center justify-center rounded-xl text-xl text-app-muted-text transition hover:bg-app-accent/10 hover:text-app-text disabled:cursor-not-allowed disabled:opacity-30",
            compactFocusRing,
          )}
          aria-label="Next month"
        >
          ›
        </button>
        <h2 id={titleId} className="ml-1 text-sm font-semibold text-app-text sm:text-base" aria-live="polite">
          {monthFormatter.format(visibleMonth)}
        </h2>
        <p className="ml-auto hidden min-w-0 truncate text-xs text-app-muted-text sm:block">
          <span className={cn("font-semibold", pnlTextClass(visibleSummary.netPnl))}>{formatPnl(visibleSummary.netPnl)}</span>
          {" · "}{visibleSummary.tradeCount} trade{visibleSummary.tradeCount === 1 ? "" : "s"}
          {" · "}{visibleSummary.tradingDays} day{visibleSummary.tradingDays === 1 ? "" : "s"}
        </p>
        <div className="ml-auto sm:ml-0">
          <InfoPopover
            triggerLabel="P&L calendar"
            align="end"
            label={journalDaysLoading
              ? "Journal markers are still loading."
              : "Each cell shows daily net P&L. Saturdays also show the weekly total. A dot marks a linked journal entry. Use arrow keys to move between days."}
          />
        </div>
      </div>

      {loading ? (
        useAgendaLayout ? (
          <div className="min-h-[360px] space-y-2 p-2" role="status" aria-live="polite">
            <span className="sr-only">Loading P&amp;L calendar agenda</span>
            {Array.from({ length: 6 }, (_, index) => (
              <div key={index} className="h-11 animate-pulse rounded-xl bg-app-border/35 motion-reduce:animate-none" />
            ))}
            <div className="h-11 animate-pulse rounded-xl bg-app-border/25 motion-reduce:animate-none" />
          </div>
        ) : (
        <div className="min-h-[360px] p-2 sm:min-h-[416px] sm:p-4" role="status" aria-live="polite">
          <span className="sr-only">Loading P&amp;L calendar</span>
          <div className="mb-1 grid grid-cols-7 gap-px sm:gap-1.5">
            {weekdayLabels.map((label) => <div key={label} className="py-2 text-center text-xs text-app-muted-text">{label.slice(0, 1)}</div>)}
          </div>
          <div className="grid grid-cols-7 gap-px sm:gap-1.5">
            {Array.from({ length: 42 }, (_, index) => (
              <div key={index} className="min-h-16 animate-pulse rounded-md bg-app-border/35 motion-reduce:animate-none sm:min-h-[72px] lg:min-h-20" />
            ))}
          </div>
        </div>
        )
      ) : error ? (
        <CompactState kind="error" title="Calendar unavailable" detail={error} minHeightClassName="min-h-[360px] sm:min-h-[416px]" announce={false} />
      ) : (
        <div className={cn("min-w-0 p-2 sm:p-4", useAgendaLayout && "min-h-[360px]")}>
          {useAgendaLayout ? (
            agendaCells.length > 0 ? (
              <ul className="divide-y divide-app-border/60" aria-label={`${monthFormatter.format(visibleMonth)} P&L agenda`}>
                {agendaCells.map((cell) => {
                  if (!cell.date || cell.dayNumber === null) {
                    return null;
                  }
                  const cellDate = cell.date;
                  const point = cell.point;
                  const weeklySummary = weeklySummaryBySaturday.get(cellDate) ?? null;
                  const hasJournal = journalDays.has(cellDate);
                  const isSelected = selectedDate === cellDate;
                  const tradeLabel = point
                    ? `${point.trade_count} trade${point.trade_count === 1 ? "" : "s"}`
                    : "no trades";
                  const weeklyLabel = weeklySummary
                    ? `, weekly P&L ${formatPnl(weeklySummary.netPnl)} across ${weeklySummary.tradeCount} trade${weeklySummary.tradeCount === 1 ? "" : "s"}`
                    : "";
                  const accessibleLabel = `${formatLongDate(cellDate)}, ${point ? formatPnl(point.net_pnl) : "no daily P&L"}, ${tradeLabel}${weeklyLabel}${hasJournal ? ", journal entry" : ""}`;
                  return (
                    <li key={cellDate} className="py-1">
                      <button
                        ref={(element) => {
                          if (element) {
                            dayRefs.current.set(cellDate, element);
                          } else {
                            dayRefs.current.delete(cellDate);
                          }
                        }}
                        type="button"
                        tabIndex={agendaRovingDate === cellDate ? 0 : -1}
                        aria-label={accessibleLabel}
                        aria-pressed={isSelected}
                        aria-current={today === cellDate ? "date" : undefined}
                        onFocus={() => setRovingDate(cellDate)}
                        onKeyDown={(event) => handleAgendaKeyDown(event, cellDate)}
                        onClick={() => {
                          setRovingDate(cellDate);
                          onDaySelect(isSelected ? null : cellDate);
                        }}
                        className={cn(
                          "flex min-h-11 w-full items-center gap-3 rounded-xl border px-3 py-2 text-left transition",
                          dayTone(point),
                          isSelected ? "border-app-focus ring-2 ring-app-focus/70" : "hover:border-app-border-strong",
                          compactFocusRing,
                        )}
                      >
                        <span className="w-14 shrink-0 text-xs font-semibold text-app-text">{formatShortDate(cellDate)}</span>
                        <span className="min-w-0 flex-1">
                          <span className={cn(
                            "block truncate text-xs font-semibold",
                            point ? pnlTextClass(point.net_pnl) : "text-app-text",
                          )}>
                            {point
                              ? formatCompactCurrency(point.net_pnl)
                              : weeklySummary
                                ? `Week ${formatCompactCurrency(weeklySummary.netPnl)}`
                              : hasJournal
                                ? "Journal entry"
                                : "No trading activity"}
                          </span>
                          {point && weeklySummary ? (
                            <span className="block truncate text-xs text-app-muted-text">
                              Week {formatCompactCurrency(weeklySummary.netPnl)}
                            </span>
                          ) : null}
                        </span>
                        {hasJournal ? <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-app-accent" aria-hidden="true" /> : null}
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <CompactState
                kind="empty"
                title="No activity this month"
                detail="No trades or journal entries fall inside this month and range."
                minHeightClassName="min-h-[220px]"
              />
            )
          ) : null}
          <div
            role="grid"
            aria-labelledby={titleId}
            aria-rowcount={weeks.length + 1}
            aria-colcount={7}
            aria-hidden={useAgendaLayout || undefined}
            className={cn("min-w-0", useAgendaLayout && "hidden")}
          >
            <div role="row" className="mb-1 grid grid-cols-7 gap-px sm:gap-1.5">
              {weekdayLabels.map((label) => (
                <div
                  key={label}
                  role="columnheader"
                  aria-label={label}
                  className="py-1.5 text-center text-xs font-semibold uppercase text-app-muted-text sm:text-sm"
                >
                  <span className="sm:hidden" aria-hidden="true">{label.slice(0, 1)}</span>
                  <span className="hidden sm:inline" aria-hidden="true">{label}</span>
                </div>
              ))}
            </div>
            {weeks.map((week, weekIndex) => (
              <div key={weekIndex} role="row" className="mb-px grid grid-cols-7 gap-px sm:mb-1.5 sm:gap-1.5">
                {week.map((cell, dayIndex) => {
                  if (!cell.date || cell.dayNumber === null) {
                    return (
                      <div
                        key={`empty-${weekIndex}-${dayIndex}`}
                        role="gridcell"
                        aria-label="Outside current month"
                        className="min-h-16 rounded-md border border-app-border/25 bg-app-bg/10 sm:min-h-[72px] lg:min-h-20"
                      />
                    );
                  }
                  const cellDate = cell.date;
                  const point = cell.point;
                  const weeklySummary = dayIndex === 6 ? weeklySummaries[weekIndex] : null;
                  const showWeeklySummary = weeklySummary !== null && weeklySummary.tradeCount > 0;
                  const isSelected = selectedDate === cellDate;
                  const hasJournal = journalDays.has(cellDate);
                  const tradeLabel = point
                    ? `${point.trade_count} trade${point.trade_count === 1 ? "" : "s"}`
                    : "no trades";
                  const weeklyLabel = showWeeklySummary
                    ? `, weekly P&L ${formatPnl(weeklySummary.netPnl)} across ${weeklySummary.tradeCount} trade${weeklySummary.tradeCount === 1 ? "" : "s"}`
                    : "";
                  const accessibleLabel = `${formatLongDate(cellDate)}, ${point ? formatPnl(point.net_pnl) : "no daily P&L"}, ${tradeLabel}${weeklyLabel}${hasJournal ? ", journal entry" : ""}`;
                  if (!isWithinSelectedRange(cellDate)) {
                    return (
                      <div
                        key={cellDate}
                        role="gridcell"
                        aria-disabled="true"
                        aria-label={`${formatLongDate(cellDate)}, outside selected range${weeklyLabel}`}
                        className={cn(
                          "flex h-full min-h-16 flex-col rounded-md border border-app-border/25 bg-app-bg/10 p-1.5 text-sm text-app-muted-text sm:min-h-[72px] sm:p-2 lg:min-h-20",
                          !showWeeklySummary && "opacity-45",
                        )}
                      >
                        <span className={cn("font-medium", showWeeklySummary && "opacity-45")}>{cell.dayNumber}</span>
                        {showWeeklySummary ? (
                          <span className="mt-auto block w-full min-w-0 border-t border-app-border/45 pt-1">
                            <span className="flex min-w-0 items-baseline gap-1">
                              <span className="shrink-0 text-[9px] font-semibold uppercase text-app-muted-text sm:text-[10px]" aria-hidden="true">
                                <span className="lg:hidden">W</span>
                                <span className="hidden lg:inline">Week</span>
                              </span>
                              <span className={cn("min-w-0 truncate text-[11px] font-semibold lg:text-sm", pnlTextClass(weeklySummary.netPnl))}>
                                <span className="lg:hidden">{formatCompactCurrency(weeklySummary.netPnl)}</span>
                                <span className="hidden lg:inline">{formatPnl(weeklySummary.netPnl)}</span>
                              </span>
                            </span>
                          </span>
                        ) : null}
                      </div>
                    );
                  }
                  return (
                    <div key={cellDate} role="gridcell" aria-selected={isSelected} className="min-w-0">
                      <button
                        ref={(element) => {
                          if (element && !useAgendaLayout) {
                            dayRefs.current.set(cellDate, element);
                          } else if (!element) {
                            dayRefs.current.delete(cellDate);
                          }
                        }}
                        type="button"
                        tabIndex={!useAgendaLayout && rovingDate === cellDate ? 0 : -1}
                        aria-label={accessibleLabel}
                        aria-pressed={isSelected}
                        aria-current={today === cellDate ? "date" : undefined}
                        title={accessibleLabel}
                        onFocus={() => setRovingDate(cellDate)}
                        onKeyDown={(event) => handleDayKeyDown(event, cellDate)}
                        onClick={() => {
                          setRovingDate(cellDate);
                          onDaySelect(isSelected ? null : cellDate);
                        }}
                        className={cn(
                          "relative flex h-full min-h-16 w-full min-w-0 flex-col overflow-hidden rounded-md border p-1.5 text-left transition sm:min-h-[72px] sm:rounded-lg sm:p-2 lg:min-h-20",
                          dayTone(point),
                          isSelected
                            ? "border-app-focus ring-2 ring-app-focus/70"
                            : "hover:border-app-border-strong",
                          compactFocusRing,
                        )}
                      >
                        <span className="flex w-full items-start justify-between gap-0.5 text-xs text-app-muted-text sm:text-sm">
                          <span className="font-medium">{cell.dayNumber}</span>
                          {hasJournal ? (
                            <span className="mt-0.5 h-2 w-2 shrink-0 rounded-full bg-app-accent ring-1 ring-app-surface" aria-hidden="true" />
                          ) : null}
                        </span>
                        {point ? (
                          <span className="mt-auto block w-full min-w-0">
                            <span className={cn("min-w-0 truncate text-xs font-semibold lg:text-sm", pnlTextClass(point.net_pnl))}>
                              <span className="lg:hidden">{formatCompactCurrency(point.net_pnl)}</span>
                              <span className="hidden lg:inline">{formatPnl(point.net_pnl)}</span>
                            </span>
                          </span>
                        ) : null}
                        {showWeeklySummary ? (
                          <span className={cn(
                            "block w-full min-w-0 border-t border-app-border/45 pt-1",
                            point ? "mt-1" : "mt-auto",
                          )}>
                            <span className="flex min-w-0 items-baseline gap-1">
                              <span className="shrink-0 text-[9px] font-semibold uppercase text-app-muted-text sm:text-[10px]" aria-hidden="true">
                                <span className="lg:hidden">W</span>
                                <span className="hidden lg:inline">Week</span>
                              </span>
                              <span className={cn("min-w-0 truncate text-[11px] font-semibold lg:text-sm", pnlTextClass(weeklySummary.netPnl))}>
                                <span className="lg:hidden">{formatCompactCurrency(weeklySummary.netPnl)}</span>
                                <span className="hidden lg:inline">{formatPnl(weeklySummary.netPnl)}</span>
                              </span>
                            </span>
                          </span>
                        ) : null}
                      </button>
                    </div>
                  );
                })}
              </div>
            ))}
          </div>

          {showCalendarFooter ? (
            <div className="mt-3 flex min-h-11 items-center justify-center gap-2">
              {journalDaysError ? (
                <p
                  role="status"
                  aria-live="polite"
                  title={`Journal markers unavailable: ${journalDaysError}`}
                  className="inline-flex min-h-11 min-w-0 flex-1 items-center truncate rounded-xl border border-app-warning/35 bg-app-warning/10 px-3 py-2 text-xs text-app-text"
                >
                  Journal markers unavailable: {journalDaysError}
                </p>
              ) : null}
              {selectedJournalDate ? (
                <button
                  type="button"
                  className={cn(
                    "inline-flex min-h-11 min-w-0 flex-1 items-center justify-center rounded-xl border border-app-accent/40 bg-app-accent/10 px-2 text-center text-sm font-semibold text-app-text transition hover:bg-app-accent/15",
                    compactFocusRing,
                  )}
                  aria-label={`Open journal for ${formatShortDate(selectedJournalDate)}`}
                  onClick={() => onJournalDayOpen(selectedJournalDate)}
                >
                  Open {formatShortDate(selectedJournalDate)} journal
                </button>
              ) : visibleTradeDays === 0 && !journalDaysError ? (
                <p role="status" className="text-center text-xs text-app-muted-text">No closed trades in this month.</p>
              ) : null}
            </div>
          ) : null}
        </div>
      )}
    </Card>
  );
});
