import { useEffect, useMemo, useState } from "react";
import { hasSessionToken } from "../../lib/session";
import { getActiveAccountId } from "../../lib/activeAccount";
import { searchTrades } from "../../api/trade";
import { loadTradesAllAccounts } from "../data/loadTradesAllAccounts";
import { computeDashboardFromTrades } from "../data/computeDashboard";
import type { DashboardComputed } from "../data/computeDashboard";
import DashboardHeader from "../components/DashboardHeader";
import SummaryCards, { type DaySummary } from "../components/SummaryCards";
import PerformanceBreakdowns from "../components/PerformanceBreakdowns";
import RecentDaysTable from "../components/RecentDaysTable";
import PnlCalendar from "../components/PnlCalendar";
import ApiUsageNote from "../components/ApiUsageNote";
import MarketDataTicker from "../../market/MarketDataTicker";

type Mode = "active" | "all";

type TimeAnalysis = {
  timeData: { label: string; trades: number; netPnl: number }[];
  dayData: { label: string; netPnl: number; trades: number }[];
  busiestTime: { label: string; trades: number; netPnl: number } | null;
  bestTime: { label: string; trades: number; netPnl: number } | null;
  bestDay: { label: string; netPnl: number; trades: number } | null;
};

export default function DashboardPage() {
  const connected = hasSessionToken();

  const [mode, setMode] = useState<Mode>("active");
  const [daysBack, setDaysBack] = useState<number>(30);
  const [effectiveDaysBack, setEffectiveDaysBack] = useState<number>(30);

  const [onlyActiveAccounts, setOnlyActiveAccounts] = useState<boolean>(false);
  const [includeInvisibleAccounts, setIncludeInvisibleAccounts] = useState<boolean>(true);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [computed, setComputed] = useState<DashboardComputed | null>(null);

  const activeAccountId = getActiveAccountId();

  const range = useMemo(() => {
    const safeDays = Math.max(1, Math.min(365, Number(daysBack) || 30));
    const end = new Date();
    const start = new Date(Date.now() - safeDays * 24 * 60 * 60 * 1000);
    return { startISO: start.toISOString(), endISO: end.toISOString(), safeDays };
  }, [daysBack]);

  async function fetchActiveTradesWithFallback(accountId: number, forceRefresh: boolean) {
    const rangeKey = `${accountId}:${range.safeDays}:${range.startISO.slice(0, 10)}:${range.endISO.slice(0, 10)}`;

    const initial = await searchTrades({
      accountId,
      startTimestamp: range.startISO,
      endTimestamp: range.endISO,
      cacheTtlMs: 2 * 60 * 1000,
      forceRefresh,
      cacheKey: `active:${rangeKey}:initial`,
    });

    if (!initial.success || initial.errorCode !== 0) {
      throw new Error(initial.errorMessage || `Trade/search failed (errorCode ${initial.errorCode}).`);
    }

    const trades = initial.trades || [];
    if (trades.length || range.safeDays >= 365) {
      return { trades, daysUsed: range.safeDays };
    }

    const fallbackWindowsDays = [365, 365 * 3];
    for (const days of fallbackWindowsDays) {
      if (days <= range.safeDays) continue;

      const extendedStart = new Date(Date.parse(range.endISO) - days * 24 * 60 * 60 * 1000).toISOString();
      const extended = await searchTrades({
        accountId,
        startTimestamp: extendedStart,
        endTimestamp: range.endISO,
        cacheTtlMs: 2 * 60 * 1000,
        forceRefresh,
        cacheKey: `active:${rangeKey}:extended:${days}`,
      });

      if (!extended.success || extended.errorCode !== 0) {
        throw new Error(extended.errorMessage || `Trade/search failed (errorCode ${extended.errorCode}).`);
      }

      const extendedTrades = extended.trades || [];
      if (extendedTrades.length) {
        return { trades: extendedTrades, daysUsed: days };
      }
    }

    return { trades: [], daysUsed: Math.max(range.safeDays, ...fallbackWindowsDays) };
  }

  async function load(forceRefresh = false) {
    setError(null);
    setComputed(null);

    if (!connected) {
      setError("You are not connected. Go to Settings and connect first.");
      return;
    }

    setLoading(true);
    try {
      if (mode === "active") {
        const id = getActiveAccountId();
        if (!id) throw new Error("Pick an active account on the Accounts page first.");

        const { trades, daysUsed } = await fetchActiveTradesWithFallback(id, forceRefresh);
        setComputed(computeDashboardFromTrades(trades));
        setEffectiveDaysBack(daysUsed);

        if (daysUsed !== range.safeDays) {
          setDaysBack(daysUsed);
        }
      } else {
        const agg = await loadTradesAllAccounts({
          startTimestamp: range.startISO,
          endTimestamp: range.endISO,
          onlyActiveAccounts,
          includeInvisibleAccounts,
          daysPerChunk: 30,
          concurrency: 2,
          forceRefresh,
        });

        setComputed(computeDashboardFromTrades(agg.allTrades || []));
        setEffectiveDaysBack(range.safeDays);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to load dashboard.";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, range.startISO, range.endISO, onlyActiveAccounts, includeInvisibleAccounts]);

  const totals = computed?.totals;
  const equity = computed?.equity ?? [];

  const timeAnalysis: TimeAnalysis = useMemo(() => {
    const TZ = "America/New_York";
    const timeFormatter = new Intl.DateTimeFormat("en-US", {
      timeZone: TZ,
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });

    const roundTrips = computed?.roundTrips ?? [];
    const byMinute = new Map<number, { netPnl: number; trades: number }>();
    const byDay = new Map<string, { netPnl: number; trades: number }>();

    for (const rt of roundTrips) {
      const entry = new Date(rt.entryTime);
      const parts = timeFormatter.formatToParts(entry);
      const hour = Number(parts.find((p) => p.type === "hour")?.value ?? 0);
      const minute = Number(parts.find((p) => p.type === "minute")?.value ?? 0);
      const minuteKey = hour * 60 + minute;
      const dow = entry.toLocaleDateString("en-US", { weekday: "short", timeZone: TZ });

      const minuteRow = byMinute.get(minuteKey) || { netPnl: 0, trades: 0 };
      minuteRow.netPnl += rt.netPnl;
      minuteRow.trades += 1;
      byMinute.set(minuteKey, minuteRow);

      const dayRow = byDay.get(dow) || { netPnl: 0, trades: 0 };
      dayRow.netPnl += rt.netPnl;
      dayRow.trades += 1;
      byDay.set(dow, dayRow);
    }

    const timeData = [...byMinute.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([minuteKey, v]) => {
        const hour = Math.floor(minuteKey / 60);
        const minute = minuteKey % 60;

        return {
          label: `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`,
          trades: v.trades,
          netPnl: v.netPnl,
        };
      });

    const dayOrder = ["Mon", "Tue", "Wed", "Thu", "Fri"];
    const dayData = dayOrder.map((label) => ({
      label,
      netPnl: byDay.get(label)?.netPnl ?? 0,
      trades: byDay.get(label)?.trades ?? 0,
    }));

    const busiestTime = timeData.reduce<{ label: string; trades: number; netPnl: number } | null>(
      (best, row) => (row.trades > (best?.trades ?? 0) ? row : best),
      null
    );

    const bestTime = timeData.reduce<{ label: string; trades: number; netPnl: number } | null>(
      (best, row) => (row.netPnl > (best?.netPnl ?? Number.NEGATIVE_INFINITY) ? row : best),
      null
    );

    const bestDay = dayData.reduce<{ label: string; netPnl: number; trades: number } | null>((best, row) => {
      if (row.trades === 0) return best;
      if (!best) return row;
      return row.netPnl > best.netPnl ? row : best;
    }, null);

    return { timeData, dayData, busiestTime, bestTime, bestDay };
  }, [computed]);

  const daySummary: DaySummary = useMemo(() => {
    const days = computed?.days || [];
    const activeDays = days.length;
    const greenDays = days.filter((d) => d.netPnl > 0).length;
    const redDays = days.filter((d) => d.netPnl < 0).length;
    const flatDays = activeDays - greenDays - redDays;

    const bestDaySeed = {
      date: "",
      netPnl: Number.NEGATIVE_INFINITY,
    };

    const worstDaySeed = {
      date: "",
      netPnl: Number.POSITIVE_INFINITY,
    };

    const bestDay = days.reduce((best, d) => (d.netPnl > best.netPnl ? d : best), bestDaySeed);
    const worstDay = days.reduce((worst, d) => (d.netPnl < worst.netPnl ? d : worst), worstDaySeed);

    return {
      activeDays,
      greenDays,
      redDays,
      flatDays,
      bestDay: activeDays ? { date: bestDay.date, netPnl: bestDay.netPnl } : null,
      worstDay: activeDays ? { date: worstDay.date, netPnl: worstDay.netPnl } : null,
    };
  }, [computed]);

  return (
    <div className="grid grid-cols-1 gap-3">
      <DashboardHeader
        mode={mode}
        setMode={setMode}
        daysBack={daysBack}
        setDaysBack={setDaysBack}
        onRefresh={() => load(true)}
        connected={connected}
        activeAccountId={activeAccountId}
        onlyActiveAccounts={onlyActiveAccounts}
        setOnlyActiveAccounts={setOnlyActiveAccounts}
        includeInvisibleAccounts={includeInvisibleAccounts}
        setIncludeInvisibleAccounts={setIncludeInvisibleAccounts}
        error={error}
      />

      <MarketDataTicker />

      <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5 space-y-4">
        <SummaryCards totals={totals} daySummary={daySummary} effectiveDaysBack={effectiveDaysBack} />
        <PerformanceBreakdowns
          loading={loading}
          timeAnalysis={timeAnalysis}
          totals={totals}
          equity={equity}
          effectiveDaysBack={effectiveDaysBack}
        />
        <PnlCalendar
          loading={loading}
          days={computed?.days ?? []}
          startISO={range.startISO}
          endISO={range.endISO}
        />
        <RecentDaysTable loading={loading} computed={computed} />
        <ApiUsageNote mode={mode} />
      </div>
    </div>
  );
}
