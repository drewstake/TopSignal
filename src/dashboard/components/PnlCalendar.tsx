import { useMemo, useState } from "react";

import { fmtMoney } from "../../lib/format";
import type { DayPoint } from "../../types/metrics";

const DAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

// turn a date into a yyyy-mm-dd string so map lookups are stable
function formatDateKey(date: Date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

// normalize incoming iso strings to noon to avoid timezone drift
function normalizeDate(iso: string) {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return null;
  const normalized = new Date(parsed);
  normalized.setHours(12, 0, 0, 0);
  return normalized;
}

type CalendarCell =
  | { type: "pad"; key: string }
  | { type: "day"; key: string; date: Date; pnl: number | null; trades: number; inRange: boolean };

type CalendarMonth = {
  key: string;
  label: string;
  cells: CalendarCell[];
  netPnl: number;
  trades: number;
};

interface PnlCalendarProps {
  days: DayPoint[];
  loading: boolean;
  startISO: string;
  endISO: string;
}

function getMonthKey(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

function buildMonths(days: DayPoint[], startISO: string, endISO: string): CalendarMonth[] {
  const startDate = normalizeDate(startISO);
  const endDate = normalizeDate(endISO);

  if (!startDate || !endDate || startDate > endDate) return [];

  const dayMap = new Map(days.map((d) => [d.date, d]));

  const months: CalendarMonth[] = [];

  // walk month by month across the requested range
  for (
    let cursor = new Date(startDate.getFullYear(), startDate.getMonth(), 1);
    cursor <= new Date(endDate.getFullYear(), endDate.getMonth(), 1);
    cursor = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1)
  ) {
    const monthKey = getMonthKey(cursor);
    const label = cursor.toLocaleString("default", { month: "long", year: "numeric" });
    const firstDayOffset = new Date(cursor.getFullYear(), cursor.getMonth(), 1).getDay();
    const daysInMonth = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 0).getDate();

    const cells: CalendarCell[] = [];
    let monthNet = 0;
    let monthTrades = 0;

    // pad to align the first weekday
    for (let i = 0; i < firstDayOffset; i++) {
      cells.push({ type: "pad", key: `${monthKey}-pad-${i}` });
    }

    // create an entry for every calendar day
    for (let day = 1; day <= daysInMonth; day++) {
      const date = new Date(cursor.getFullYear(), cursor.getMonth(), day);
      date.setHours(12, 0, 0, 0);
      const key = formatDateKey(date);

      const entry = dayMap.get(key);
      const pnl = entry?.netPnl ?? null;
      const trades = entry?.trades ?? 0;
      const inRange = date >= startDate && date <= endDate;

      if (entry) {
        monthNet += entry.netPnl;
        monthTrades += entry.trades;
      }

      cells.push({ type: "day", key, date, pnl, trades, inRange });
    }

    months.push({ key: monthKey, label, cells, netPnl: monthNet, trades: monthTrades });
  }

  return months;
}

function cellStyles(cell: CalendarCell) {
  if (cell.type === "pad") return "h-16";

  const base = "flex h-16 flex-col justify-between rounded-xl border p-2 text-left transition";

  if (!cell.inRange) {
    return `${base} border-dashed border-zinc-800 bg-zinc-950/20 text-zinc-600`;
  }

  if (cell.pnl === null) {
    return `${base} border border-zinc-300 bg-white dark:border-zinc-800 dark:bg-zinc-950/40 text-zinc-600 dark:text-zinc-400`;
  }

  if (cell.pnl > 0) {
    return `${base} border-emerald-500/50 bg-emerald-500/10 text-emerald-100`;
  }

  if (cell.pnl < 0) {
    return `${base} border-rose-500/50 bg-rose-500/10 text-rose-100`;
  }

  return `${base} border-zinc-700 bg-zinc-800 text-zinc-900 dark:text-zinc-100`;
}

function cellContent(cell: CalendarCell) {
  if (cell.type === "pad") return null;

  const tradesLabel = cell.trades ? `${cell.trades} trade${cell.trades === 1 ? "" : "s"}` : "No trades";

  return (
    <>
      <div className="text-[10px] text-zinc-600 dark:text-zinc-400">{cell.date.getDate()}</div>
      <div className="text-[11px] font-semibold leading-tight">{cell.pnl === null ? "—" : fmtMoney(cell.pnl)}</div>
      <div className="text-[10px] text-zinc-500">{tradesLabel}</div>
    </>
  );
}

export default function PnlCalendar({ days, loading, startISO, endISO }: PnlCalendarProps) {
  const months = useMemo(() => buildMonths(days, startISO, endISO), [days, startISO, endISO]);

  const [selectedMonthIndex, setSelectedMonthIndex] = useState<number | null>(null);
  const lastMonthIndex = Math.max(months.length - 1, 0);
  const activeMonthIndex = selectedMonthIndex === null ? lastMonthIndex : Math.min(selectedMonthIndex, lastMonthIndex);

  const rangeLabel = useMemo(() => {
    const startDate = normalizeDate(startISO);
    const endDate = normalizeDate(endISO);

    if (!startDate || !endDate) return null;

    const fmt = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
    return `${fmt.format(startDate)} – ${fmt.format(endDate)}`;
  }, [startISO, endISO]);

  return (
    <div className="rounded-2xl border border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950/30 p-4">
      <div className="flex items-start justify-between gap-2 text-sm text-zinc-900 dark:text-zinc-100">
        <div>
          <div className="font-semibold">PNL calendar</div>
          <div className="text-xs text-zinc-600 dark:text-zinc-400">Daily net results across the selected date range.</div>
        </div>
        {rangeLabel ? <div className="text-xs text-zinc-500">Range: {rangeLabel}</div> : null}
      </div>

      {loading ? (
        <div className="py-6 text-sm text-zinc-700 dark:text-zinc-300">Loading...</div>
      ) : !months.length ? (
        <div className="py-6 text-sm text-zinc-700 dark:text-zinc-300">No day data found for this range.</div>
      ) : (
        <div className="mt-3 space-y-4">
          {months.length > 1 ? (
            <div className="flex items-center justify-end gap-2 text-xs text-zinc-700 dark:text-zinc-300">
              <button
                type="button"
                className="rounded-lg border border-zinc-800 px-2 py-1 text-[11px] transition hover:border-zinc-700 hover:text-zinc-900 dark:text-zinc-100 disabled:cursor-not-allowed disabled:border-zinc-900 disabled:text-zinc-600"
                onClick={() => setSelectedMonthIndex(Math.max(activeMonthIndex - 1, 0))}
                disabled={activeMonthIndex === 0}
              >
                Previous
              </button>
              <div className="text-[11px] text-zinc-600 dark:text-zinc-400">{months[activeMonthIndex]?.label}</div>
              <button
                type="button"
                className="rounded-lg border border-zinc-800 px-2 py-1 text-[11px] transition hover:border-zinc-700 hover:text-zinc-900 dark:text-zinc-100 disabled:cursor-not-allowed disabled:border-zinc-900 disabled:text-zinc-600"
                onClick={() => setSelectedMonthIndex(Math.min(activeMonthIndex + 1, lastMonthIndex))}
                disabled={activeMonthIndex === months.length - 1}
              >
                Next
              </button>
            </div>
          ) : null}

          {months[activeMonthIndex] ? (
            <div className="space-y-2" key={months[activeMonthIndex].key}>
              <div className="flex items-center justify-between text-xs text-zinc-700 dark:text-zinc-300">
                <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">{months[activeMonthIndex].label}</div>
                <div className="text-[11px] text-zinc-600 dark:text-zinc-400">
                  Net {fmtMoney(months[activeMonthIndex].netPnl)} · Trades {months[activeMonthIndex].trades}
                </div>
              </div>
              <div className="grid grid-cols-7 text-[10px] uppercase tracking-wide text-zinc-500">
                {DAY_LABELS.map((label) => (
                  <div key={`${months[activeMonthIndex].key}-${label}`} className="text-center">
                    {label}
                  </div>
                ))}
              </div>
              <div className="grid grid-cols-7 gap-1 text-xs">
                {months[activeMonthIndex].cells.map((cell) => (
                  <div key={cell.key} className={cellStyles(cell)}>
                    {cellContent(cell)}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
