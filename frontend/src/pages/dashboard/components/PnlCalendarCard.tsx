import { useEffect, useMemo, useState } from "react";

import { Button } from "../../../components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "../../../components/ui/Card";
import { Skeleton } from "../../../components/ui/Skeleton";
import type { AccountPnlCalendarDay } from "../../../lib/types";

interface PnlCalendarCardProps {
  days: AccountPnlCalendarDay[];
  loading: boolean;
  error: string | null;
  selectedDate?: string | null;
  onDaySelect?: (date: string | null) => void;
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

const weekdayLabels = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

const monthLabelFormatter = new Intl.DateTimeFormat("en-US", {
  month: "long",
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

function toIsoDate(value: Date) {
  return value.toISOString().slice(0, 10);
}

function formatPnlCompact(value: number) {
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${compactCurrencyFormatter.format(value)}`;
}

function formatPnl(value: number) {
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${currencyFormatter.format(value)}`;
}

function pnlClass(value: number) {
  if (value > 0) {
    return "text-emerald-100";
  }
  if (value < 0) {
    return "text-rose-100";
  }
  return "text-slate-200";
}

function tileBackground(value: number, maxAbs: number) {
  if (value === 0) {
    return "rgba(148, 163, 184, 0.2)";
  }

  const intensity = Math.min(1, Math.abs(value) / maxAbs);
  const alpha = 0.2 + intensity * 0.55;
  if (value > 0) {
    return `rgba(16, 185, 129, ${alpha.toFixed(3)})`;
  }
  return `rgba(244, 63, 94, ${alpha.toFixed(3)})`;
}

export function PnlCalendarCard({ days, loading, error, selectedDate, onDaySelect }: PnlCalendarCardProps) {
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

  useEffect(() => {
    if (days.length === 0) {
      setVisibleMonth(monthStartUtc(new Date()));
      return;
    }

    setVisibleMonth(monthBounds.max);
  }, [days, monthBounds.max]);

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
      const isoDate = toIsoDate(dayDate);
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
      summaries.set(rowStart + 6, summary);
    }
    return summaries;
  }, [calendarCells]);

  const maxAbsWeekPnl = useMemo(() => {
    let maxAbs = 0;
    weeklySummaries.forEach((summary) => {
      maxAbs = Math.max(maxAbs, Math.abs(summary.netPnl));
    });
    return maxAbs > 0 ? maxAbs : 1;
  }, [weeklySummaries]);

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
              <p className="text-sm font-semibold text-slate-100">{monthLabelFormatter.format(visibleMonth)}</p>
              <p className="text-xs text-slate-400">
                {monthSummary.tradeCount} trades | {formatPnl(monthSummary.netPnl)}
              </p>
            </div>
            <div className="flex items-center justify-end gap-2">
              <Button
                variant="ghost"
                size="sm"
                disabled={!canGoPrev}
                onClick={() => setVisibleMonth((current) => addUtcMonths(current, -1))}
              >
                Prev
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={!canGoNext}
                onClick={() => setVisibleMonth((current) => addUtcMonths(current, 1))}
              >
                Next
              </Button>
            </div>
          </div>
        ) : (
          <CardTitle>PnL Calendar</CardTitle>
        )}
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="grid grid-cols-7 gap-2">
            {Array.from({ length: 35 }).map((_, index) => (
              <Skeleton key={`calendar-skeleton-${index}`} className="h-20" />
            ))}
          </div>
        ) : error ? (
          <p className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">{error}</p>
        ) : days.length === 0 ? (
          <p className="rounded-xl border border-slate-800/80 bg-slate-900/40 px-3 py-4 text-sm text-slate-400">
            No stored trade events yet. Sync trades to populate the calendar.
          </p>
        ) : (
          <div className="space-y-3">
            <div className="overflow-x-auto rounded-xl border border-slate-800/80 bg-slate-950/55 p-2">
              <div className="min-w-[680px]">
                <div className="mb-2 grid grid-cols-7 gap-2">
                  {weekdayLabels.map((label) => (
                    <p key={label} className="text-center text-[11px] uppercase tracking-wide text-slate-500">
                      {label}
                    </p>
                  ))}
                </div>

                <div className="grid grid-cols-7 gap-2">
                  {calendarCells.map((cell, index) => {
                    const isSaturdayColumn = index % 7 === 6;

                    if (isSaturdayColumn) {
                      const summary = weeklySummaries.get(index) ?? { tradeCount: 0, netPnl: 0 };
                      const weekNumber = Math.floor(index / 7) + 1;
                      return (
                        <div
                          key={cell.key}
                          className="flex h-20 flex-col items-center justify-center rounded-lg border border-slate-800/80 p-2 text-center"
                          style={{ backgroundColor: tileBackground(summary.netPnl, maxAbsWeekPnl) }}
                        >
                          <p className="text-xs font-medium uppercase tracking-wide text-slate-300">Week {weekNumber}</p>
                          <p className={`mt-1 text-sm font-semibold ${pnlClass(summary.netPnl)}`}>
                            {formatPnlCompact(summary.netPnl)}
                          </p>
                          <p className="text-[11px] text-slate-300">{summary.tradeCount} trade(s)</p>
                        </div>
                      );
                    }

                    if (cell.dayNumber === null) {
                      return <div key={cell.key} className="h-20 rounded-lg border border-transparent" />;
                    }

                    const point = cell.point;
                    const netPnl = point?.net_pnl ?? 0;
                    const backgroundColor = point
                      ? tileBackground(netPnl, maxAbsMonthPnl)
                      : "rgba(15, 23, 42, 0.6)";
                    const isSelected = selectedDate === cell.key;

                    return (
                      <button
                        key={cell.key}
                        type="button"
                        aria-pressed={isSelected}
                        onClick={() => onDaySelect?.(isSelected ? null : cell.key)}
                        className={`h-20 rounded-lg border p-2 text-left transition ${
                          isSelected
                            ? "border-cyan-400/90 ring-1 ring-cyan-300/70"
                            : "border-slate-800/80 hover:border-slate-700/80"
                        } ${onDaySelect ? "cursor-pointer" : "cursor-default"}`}
                        style={{ backgroundColor }}
                      >
                        <p className="text-xs font-medium text-slate-300">{cell.dayNumber}</p>
                        {point ? (
                          <>
                            <p className={`mt-1 text-sm font-semibold ${pnlClass(netPnl)}`}>{formatPnlCompact(netPnl)}</p>
                            <p className="text-[11px] text-slate-300">{point.trade_count} trade(s)</p>
                          </>
                        ) : (
                          <p className="mt-2 text-[11px] text-slate-500">No trades</p>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
