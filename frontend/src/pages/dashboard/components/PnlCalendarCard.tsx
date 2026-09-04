import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "../../../components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "../../../components/ui/Card";
import { Skeleton } from "../../../components/ui/Skeleton";
import { formatDemoPnl } from "../../../lib/demoMode";
import { formatIsoDateUtc } from "../../../lib/tradingDay";
import type { AccountPnlCalendarDay } from "../../../lib/types";

interface PnlCalendarCardProps {
  days: AccountPnlCalendarDay[];
  loading: boolean;
  error: string | null;
  scopeKey?: string;
  journalDays?: Set<string>;
  journalDaysLoading?: boolean;
  selectedDate?: string | null;
  onDaySelect?: (date: string | null) => void;
  onJournalDayOpen?: (date: string) => void;
  onAddJournalForSelectedDay?: (date: string) => void;
  onVisibleRangeChange?: (startDate: string, endDate: string) => void;
}

interface CalendarCell {
  key: string;
  dayNumber: number | null;
  point: AccountPnlCalendarDay | null;
}

interface WeeklySummary {
  tradeCount: number;
  netPnl: number;
}

type MonthCopyStatus = "idle" | "copied" | "failed";

const weekdayLabels = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const COPY_FEEDBACK_MS = 1200;

const monthLabelFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

const copyDayFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  timeZone: "UTC",
});

const compactCurrencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function monthStartUtc(date: Date) {
  return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1));
}

function addUtcMonths(date: Date, delta: number) {
  return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + delta, 1));
}

function parseIsoDate(value: string) {
  return new Date(`${value}T00:00:00Z`);
}

function formatPnlCompact(value: number) {
  return formatDemoPnl(value, (nextValue) => {
    const prefix = nextValue > 0 ? "+" : "";
    return `${prefix}${compactCurrencyFormatter.format(nextValue)}`;
  });
}

function formatPnl(value: number) {
  return formatDemoPnl(value, (nextValue) => {
    const prefix = nextValue > 0 ? "+" : "";
    return `${prefix}${currencyFormatter.format(nextValue)}`;
  });
}

function formatCopyDay(value: string) {
  return copyDayFormatter.format(parseIsoDate(value));
}

function calendarDayDetails(point: AccountPnlCalendarDay) {
  const wins = point.win_count ?? 0;
  const losses = point.loss_count ?? 0;
  const breakeven = point.breakeven_count ?? 0;

  return {
    wins,
    losses,
    breakeven,
    accessibleLabel: [
      `${formatCopyDay(point.date)}.`,
      `Net P&L ${formatPnl(point.net_pnl)}.`,
      `${wins} wins, ${losses} losses, ${breakeven} breakeven, ${point.trade_count} total trades.`,
      `Gross P&L ${formatPnl(point.gross_pnl)}.`,
    ].join(" "),
  };
}

function buildPnlCalendarMonthCopyText(month: Date, netPnl: number, tradeDays: AccountPnlCalendarDay[]) {
  return [
    `Month: ${monthLabelFormatter.format(month)}`,
    `Total amount made: ${formatPnl(netPnl)}`,
    "",
    "Traded days:",
    ...(tradeDays.length > 0 ? tradeDays.map((day) => `${formatCopyDay(day.date)}: ${formatPnl(day.net_pnl)}`) : ["No traded days."]),
  ].join("\n");
}

async function fallbackCopyText(text: string) {
  if (typeof document === "undefined") {
    return false;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  textarea.style.pointerEvents = "none";
  textarea.style.top = "0";
  textarea.style.left = "-9999px";

  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();

  const copied = typeof document.execCommand === "function" ? document.execCommand("copy") : false;
  document.body.removeChild(textarea);
  return copied;
}

async function copyTextToClipboard(text: string) {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      return fallbackCopyText(text);
    }
  }

  return fallbackCopyText(text);
}

function pnlClass(value: number) {
  if (value > 0) {
    return "text-app-positive";
  }
  if (value < 0) {
    return "text-app-negative";
  }
  return "text-app-text-soft";
}

function tileBackground(value: number, maxAbs: number) {
  if (value === 0) {
    return "rgb(var(--dashboard-neutral-rgb) / 0.2)";
  }

  const intensity = Math.min(1, Math.abs(value) / maxAbs);
  const alpha = 0.14 + intensity * 0.38;
  if (value > 0) {
    return `rgb(var(--dashboard-positive-rgb) / ${alpha.toFixed(3)})`;
  }
  return `rgb(var(--dashboard-negative-rgb) / ${alpha.toFixed(3)})`;
}

export function PnlCalendarCard({
  days,
  loading,
  error,
  scopeKey = "default",
  journalDays,
  journalDaysLoading = false,
  selectedDate,
  onDaySelect,
  onJournalDayOpen,
  onAddJournalForSelectedDay,
  onVisibleRangeChange,
}: PnlCalendarCardProps) {
  const dayMap = useMemo(() => {
    const map = new Map<string, AccountPnlCalendarDay>();
    days.forEach((day) => map.set(day.date, day));
    return map;
  }, [days]);

  const monthBounds = useMemo(() => {
    if (days.length === 0) {
      const currentMonth = monthStartUtc(new Date());
      return { min: currentMonth, max: currentMonth };
    }

    const ordered = [...days]
      .map((day) => parseIsoDate(day.date).getTime())
      .sort((left, right) => left - right);
    return {
      min: monthStartUtc(new Date(ordered[0])),
      max: monthStartUtc(new Date(ordered[ordered.length - 1])),
    };
  }, [days]);

  const [visibleMonth, setVisibleMonth] = useState<Date>(() => monthStartUtc(new Date()));
  const previousScopeKeyRef = useRef(scopeKey);
  const previousDaysRef = useRef(days);
  const pendingScopeResetRef = useRef<{
    scopeKey: string;
    daysAtScopeChange: readonly AccountPnlCalendarDay[];
    sawLoading: boolean;
  } | null>(null);
  const scopeChangedBeforeEffects = previousScopeKeyRef.current !== scopeKey;
  const scopeChangedWithNewData =
    scopeChangedBeforeEffects && previousDaysRef.current !== days && !loading;
  const pendingScopeReset = pendingScopeResetRef.current?.scopeKey === scopeKey
    ? pendingScopeResetRef.current
    : null;
  const pendingScopeResetReady =
    pendingScopeReset !== null &&
    !loading &&
    (pendingScopeReset.sawLoading || pendingScopeReset.daysAtScopeChange !== days);
  const suppressVisibleRangeChange =
    (scopeChangedBeforeEffects && !scopeChangedWithNewData) ||
    (pendingScopeReset !== null && !pendingScopeResetReady);
  const reportLatestMonth = scopeChangedWithNewData || pendingScopeResetReady;

  useEffect(() => {
    if (previousScopeKeyRef.current !== scopeKey) {
      previousScopeKeyRef.current = scopeKey;
      const alreadyHasNewData = previousDaysRef.current !== days && !loading;
      pendingScopeResetRef.current = alreadyHasNewData
        ? null
        : { scopeKey, daysAtScopeChange: days, sawLoading: loading };
      previousDaysRef.current = days;
      if (alreadyHasNewData) {
        setVisibleMonth(monthBounds.max);
      }
      return;
    }

    const pendingReset = pendingScopeResetRef.current;
    if (pendingReset?.scopeKey === scopeKey) {
      if (loading) {
        pendingReset.sawLoading = true;
        previousDaysRef.current = days;
        return;
      }
      if (pendingReset.sawLoading || pendingReset.daysAtScopeChange !== days) {
        pendingScopeResetRef.current = null;
        previousDaysRef.current = days;
        setVisibleMonth(monthBounds.max);
      }
      return;
    }

    previousDaysRef.current = days;
    setVisibleMonth((current) => {
      if (current.getTime() < monthBounds.min.getTime()) {
        return monthBounds.min;
      }
      if (current.getTime() > monthBounds.max.getTime()) {
        return monthBounds.max;
      }
      return current;
    });
  }, [days, loading, monthBounds.max, monthBounds.min, scopeKey]);

  useEffect(() => {
    if (!onVisibleRangeChange) {
      return;
    }
    if (suppressVisibleRangeChange) {
      return;
    }
    const boundedVisibleMonth = reportLatestMonth
      ? monthBounds.max
      : visibleMonth.getTime() < monthBounds.min.getTime()
        ? monthBounds.min
        : visibleMonth.getTime() > monthBounds.max.getTime()
          ? monthBounds.max
          : visibleMonth;
    const start = monthStartUtc(boundedVisibleMonth);
    const end = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 0));
    onVisibleRangeChange(formatIsoDateUtc(start), formatIsoDateUtc(end));
  }, [
    monthBounds.max,
    monthBounds.min,
    onVisibleRangeChange,
    reportLatestMonth,
    suppressVisibleRangeChange,
    visibleMonth,
  ]);

  const calendarCells = useMemo(() => {
    const year = visibleMonth.getUTCFullYear();
    const month = visibleMonth.getUTCMonth();
    const firstDayOfMonth = new Date(Date.UTC(year, month, 1));
    const dayCount = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();

    const cells: CalendarCell[] = [];
    for (let index = 0; index < firstDayOfMonth.getUTCDay(); index += 1) {
      cells.push({ key: `pad-start-${index}`, dayNumber: null, point: null });
    }

    for (let day = 1; day <= dayCount; day += 1) {
      const dayDate = new Date(Date.UTC(year, month, day));
      const isoDate = formatIsoDateUtc(dayDate);
      cells.push({
        key: isoDate,
        dayNumber: day,
        point: dayMap.get(isoDate) ?? null,
      });
    }

    while (cells.length % 7 !== 0) {
      cells.push({ key: `pad-end-${cells.length}`, dayNumber: null, point: null });
    }

    return cells;
  }, [dayMap, visibleMonth]);

  const monthSummary = useMemo(() => {
    return calendarCells.reduce(
      (summary, cell) => {
        if (!cell.point) {
          return summary;
        }

        return {
          tradeCount: summary.tradeCount + cell.point.trade_count,
          netPnl: summary.netPnl + cell.point.net_pnl,
        };
      },
      { tradeCount: 0, netPnl: 0.0 },
    );
  }, [calendarCells]);

  const maxAbsMonthPnl = useMemo(() => {
    const maxAbs = calendarCells.reduce((maxValue, cell) => {
      if (!cell.point) {
        return maxValue;
      }
      return Math.max(maxValue, Math.abs(cell.point.net_pnl));
    }, 0);
    return maxAbs > 0 ? maxAbs : 1;
  }, [calendarCells]);

  const weeklySummaries = useMemo(() => {
    const summaries = new Map<number, WeeklySummary>();
    for (let rowStart = 0; rowStart < calendarCells.length; rowStart += 7) {
      const weekCells = calendarCells.slice(rowStart, rowStart + 7);
      const summary = weekCells.reduce(
        (acc, cell) => {
          if (!cell.point) {
            return acc;
          }
          return {
            tradeCount: acc.tradeCount + cell.point.trade_count,
            netPnl: acc.netPnl + cell.point.net_pnl,
          };
        },
        { tradeCount: 0, netPnl: 0.0 },
      );
      summaries.set(rowStart, summary);
    }
    return summaries;
  }, [calendarCells]);

  const calendarRows = useMemo(() => {
    const rows: CalendarCell[][] = [];
    for (let rowStart = 0; rowStart < calendarCells.length; rowStart += 7) {
      rows.push(calendarCells.slice(rowStart, rowStart + 7));
    }
    return rows;
  }, [calendarCells]);

  const maxAbsWeekPnl = useMemo(() => {
    let maxAbs = 0;
    weeklySummaries.forEach((summary) => {
      maxAbs = Math.max(maxAbs, Math.abs(summary.netPnl));
    });
    return maxAbs > 0 ? maxAbs : 1;
  }, [weeklySummaries]);

  const currentMonthTradeDays = useMemo(() => {
    return calendarCells.flatMap((cell) => (cell.point && cell.point.trade_count > 0 ? [cell.point] : []));
  }, [calendarCells]);

  const [monthCopyStatus, setMonthCopyStatus] = useState<MonthCopyStatus>("idle");
  const copyFeedbackTimeoutRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (copyFeedbackTimeoutRef.current !== null) {
        window.clearTimeout(copyFeedbackTimeoutRef.current);
      }
    };
  }, []);

  useEffect(() => {
    setMonthCopyStatus("idle");
  }, [visibleMonth]);

  const handleCopyVisibleMonth = async () => {
    const success = await copyTextToClipboard(buildPnlCalendarMonthCopyText(visibleMonth, monthSummary.netPnl, currentMonthTradeDays));
    setMonthCopyStatus(success ? "copied" : "failed");

    if (copyFeedbackTimeoutRef.current !== null) {
      window.clearTimeout(copyFeedbackTimeoutRef.current);
    }
    copyFeedbackTimeoutRef.current = window.setTimeout(() => {
      setMonthCopyStatus("idle");
      copyFeedbackTimeoutRef.current = null;
    }, COPY_FEEDBACK_MS);
  };

  const canGoPrev = visibleMonth.getTime() > monthBounds.min.getTime();
  const canGoNext = visibleMonth.getTime() < monthBounds.max.getTime();
  const hasCalendarData = !loading && !error && days.length > 0;

  return (
    <Card>
      <CardHeader className="mb-3 space-y-0">
        {hasCalendarData ? (
          <div className="grid gap-2 sm:grid-cols-[auto_1fr_auto] sm:items-center">
            <CardTitle className="sm:justify-self-start">PnL Calendar</CardTitle>
            <div className="text-center">
              <p className="text-sm font-medium text-app-text-soft md:text-base">
                {formatPnl(monthSummary.netPnl)} {" \u2022 "} {monthSummary.tradeCount} trades
              </p>
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Button
                variant="ghost"
                size="sm"
                disabled={!canGoPrev}
                onClick={() => setVisibleMonth((current) => addUtcMonths(current, -1))}
              >
                Prev
              </Button>
              <p className="min-w-[92px] text-center text-xs font-medium text-app-muted">
                {monthLabelFormatter.format(visibleMonth)}
              </p>
              <Button
                variant="ghost"
                size="sm"
                disabled={!canGoNext}
                onClick={() => setVisibleMonth((current) => addUtcMonths(current, 1))}
              >
                Next
              </Button>
              <Button
                variant={monthCopyStatus === "copied" ? "secondary" : "ghost"}
                size="sm"
                onClick={() => void handleCopyVisibleMonth()}
                className={
                  monthCopyStatus === "failed"
                    ? "border-app-negative/50 text-app-negative hover:border-app-negative/70 hover:bg-app-negative/10 hover:text-app-negative"
                    : undefined
                }
                title="Copy visible month PnL"
              >
                {monthCopyStatus === "copied" ? "Copied" : monthCopyStatus === "failed" ? "Copy failed" : "Copy Month PnL"}
              </Button>
            </div>
          </div>
        ) : (
          <CardTitle>PnL Calendar</CardTitle>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        {loading ? (
          <div className="grid grid-cols-7 gap-2">
            {Array.from({ length: 35 }).map((_, index) => (
              <Skeleton key={`calendar-skeleton-${index}`} className="h-24" />
            ))}
          </div>
        ) : error ? (
          <p className="rounded-xl border border-app-negative/30 bg-app-negative/10 px-3 py-2 text-sm text-app-negative">{error}</p>
        ) : days.length === 0 ? (
          <p className="rounded-xl border border-app-border/80 bg-app-surface/40 px-3 py-4 text-sm text-app-muted">
            No stored trade events yet. Sync or import trades to populate the calendar.
          </p>
        ) : (
          <div className="space-y-3">
            <div className="overflow-x-auto rounded-xl border border-app-border/80 bg-app-bg/55 p-2">
              <div className="min-w-[780px]">
                <div className="mb-2 grid grid-cols-[repeat(7,minmax(0,1fr))_96px] gap-2">
                  {weekdayLabels.map((label) => (
                    <p key={label} className="text-center text-[11px] uppercase tracking-wide text-app-muted-strong">
                      {label}
                    </p>
                  ))}
                  <p className="text-center text-[11px] uppercase tracking-wide text-app-muted-strong">Week</p>
                </div>

                <div className="space-y-2">
                  {calendarRows.map((weekCells, rowIndex) => {
                    const rowStart = rowIndex * 7;
                    const summary = weeklySummaries.get(rowStart) ?? { tradeCount: 0, netPnl: 0 };
                    return (
                      <div key={`calendar-row-${rowStart}`} className="grid grid-cols-[repeat(7,minmax(0,1fr))_96px] gap-2">
                        {weekCells.map((cell) => {
                          if (cell.dayNumber === null) {
                            return <div key={cell.key} className="h-24 rounded-lg border border-transparent" />;
                          }

                          const point = cell.point;
                          const netPnl = point?.net_pnl ?? 0;
                          const pointDetails = point ? calendarDayDetails(point) : null;
                          const backgroundColor = point
                            ? tileBackground(netPnl, maxAbsMonthPnl)
                            : "var(--dashboard-calendar-empty)";
                          const isSelected = selectedDate === cell.key;
                          const hasJournalEntry = journalDays?.has(cell.key) ?? false;

                          return (
                            <button
                              key={cell.key}
                              type="button"
                              aria-pressed={isSelected}
                              aria-label={pointDetails?.accessibleLabel ?? `${formatCopyDay(cell.key)}. No trades.`}
                              title={pointDetails?.accessibleLabel ?? "No trades"}
                              onClick={() => onDaySelect?.(isSelected ? null : cell.key)}
                              className={`h-24 rounded-lg border p-2 text-left transition ${
                                isSelected
                                  ? "border-app-accent/90 ring-1 ring-app-accent/70"
                                  : "border-app-border/80 hover:border-app-border/80"
                              } ${onDaySelect ? "cursor-pointer" : "cursor-default"}`}
                              style={{ backgroundColor }}
                            >
                              <div className="flex items-start justify-between gap-1">
                                <p className="text-xs font-medium text-app-muted">{cell.dayNumber}</p>
                                {hasJournalEntry ? (
                                  <span
                                    role="button"
                                    tabIndex={0}
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      onJournalDayOpen?.(cell.key);
                                    }}
                                    onKeyDown={(event) => {
                                      if (event.key === "Enter" || event.key === " ") {
                                        event.preventDefault();
                                        event.stopPropagation();
                                        onJournalDayOpen?.(cell.key);
                                      }
                                    }}
                                    className="inline-flex h-5 min-w-5 items-center justify-center rounded-full border border-app-accent/65 bg-app-accent/15 px-1 text-[10px] font-semibold text-app-accent"
                                    aria-label={`Open journal entry for ${cell.key}`}
                                    title="Open journal entry"
                                  >
                                    J
                                  </span>
                                ) : null}
                              </div>
                              {point ? (
                                <>
                                  <p className={`mt-1 text-sm font-semibold ${pnlClass(netPnl)}`}>{formatPnlCompact(netPnl)}</p>
                                  <p className="mt-0.5 text-[10px] font-medium text-app-text-soft">
                                    W {pointDetails?.wins ?? 0} · L {pointDetails?.losses ?? 0} · T {point.trade_count}
                                  </p>
                                </>
                              ) : (
                                <p className="mt-2 text-[11px] text-app-muted-strong">No trades</p>
                              )}
                            </button>
                          );
                        })}
                        <div
                          className="flex h-24 flex-col items-center justify-center rounded-lg border border-app-border/80 p-2 text-center"
                          style={{ backgroundColor: tileBackground(summary.netPnl, maxAbsWeekPnl) }}
                        >
                          <p className="text-xs font-medium uppercase tracking-wide text-app-muted">Week {rowIndex + 1}</p>
                          <p className={`mt-1 text-sm font-semibold ${pnlClass(summary.netPnl)}`}>
                            {formatPnlCompact(summary.netPnl)}
                          </p>
                          <p className="text-[11px] text-app-muted">{summary.tradeCount} trade(s)</p>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-app-muted">
              <div>{journalDaysLoading ? "Loading journal markers..." : "J marker indicates a journal entry for that day."}</div>
              {selectedDate && onAddJournalForSelectedDay ? (
                <Button variant="secondary" size="sm" onClick={() => onAddJournalForSelectedDay(selectedDate)}>
                  {journalDays?.has(selectedDate) ? "Open Journal Entry" : "Add Journal Entry"}
                </Button>
              ) : null}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
