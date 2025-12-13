import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { hasSessionToken } from "../../lib/session";
import { getActiveAccountId } from "../../lib/activeAccount";
import { fmtMoney } from "../../lib/format";
import { searchTrades } from "../../api/trade";
import { loadTradesAllAccounts } from "../data/loadTradesAllAccounts";
import { computeDashboardFromTrades } from "../data/computeDashboard";
import type { DashboardComputed } from "../data/computeDashboard";
import type { DayPoint } from "../../types/metrics";
import EquityCurveChart from "../components/charts/EquityCurveChart";

type Mode = "active" | "all";

function fmtPct(x: number) {
  if (!Number.isFinite(x)) return "0.0%";
  return `${(x * 100).toFixed(1)}%`;
}

function fmtPF(x: number) {
  if (x === Infinity) return "∞";
  if (!Number.isFinite(x)) return "0.00";
  return x.toFixed(2);
}

function fmtDays(x: number) {
  if (!Number.isFinite(x)) return "0.0";
  return `${x.toFixed(1)}d`;
}

function fmtHours(x: number) {
  if (!Number.isFinite(x)) return "0.0";
  return `${x.toFixed(1)}h`;
}

export default function DashboardPage() {
  const connected = hasSessionToken();

  const [mode, setMode] = useState<Mode>("active");
  const [daysBack, setDaysBack] = useState<number>(30);
  const [effectiveDaysBack, setEffectiveDaysBack] = useState<number>(30);

  // only used for "all accounts" mode
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

    // If nothing was returned for a short window, retry with a wider lookback so
    // older accounts still show historical trades without manual tweaking.
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

        const { trades: activeTrades, daysUsed } = await fetchActiveTradesWithFallback(id, forceRefresh);
        setComputed(computeDashboardFromTrades(activeTrades));
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

  const daySummary = useMemo(() => {
    const days = computed?.days || [];
    const activeDays = days.length;
    const greenDays = days.filter((d) => d.netPnl > 0).length;
    const redDays = days.filter((d) => d.netPnl < 0).length;
    const flatDays = activeDays - greenDays - redDays;

    const bestDaySeed: DayPoint = {
      date: "",
      grossPnl: 0,
      fees: 0,
      netPnl: Number.NEGATIVE_INFINITY,
      trades: 0,
      contracts: 0,
      buys: 0,
      sells: 0,
    };

    const worstDaySeed: DayPoint = {
      ...bestDaySeed,
      netPnl: Number.POSITIVE_INFINITY,
    };

    const bestDay = days.reduce((best, d) => (d.netPnl > best.netPnl ? d : best), bestDaySeed);
    const worstDay = days.reduce((worst, d) => (d.netPnl < worst.netPnl ? d : worst), worstDaySeed);

    return {
      activeDays,
      greenDays,
      redDays,
      flatDays,
      bestDay: activeDays ? bestDay : null,
      worstDay: activeDays ? worstDay : null,
    };
  }, [computed]);

  return (
    <div className="grid grid-cols-1 gap-3">
      <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-lg font-semibold text-zinc-100">Dashboard</div>
            <div className="mt-1 text-sm text-zinc-400">
              Note: this page is accurate for PnL, fees, win rate, day stats, and drawdown from daily equity.
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-1 rounded-xl border border-zinc-800 bg-zinc-950/40 p-1 text-sm">
              <button
                onClick={() => setMode("active")}
                className={
                  "rounded-lg px-3 py-1.5 " +
                  (mode === "active" ? "bg-zinc-800 text-zinc-100" : "text-zinc-300")
                }
              >
                Active account
              </button>
              <button
                onClick={() => setMode("all")}
                className={
                  "rounded-lg px-3 py-1.5 " +
                  (mode === "all" ? "bg-zinc-800 text-zinc-100" : "text-zinc-300")
                }
              >
                All accounts
              </button>
            </div>

            <div className="flex items-center gap-2 rounded-xl border border-zinc-800 bg-zinc-950/40 px-3 py-2 text-sm text-zinc-200">
              <div className="text-zinc-400">Days</div>
              <input
                type="number"
                min={1}
                max={365}
                value={daysBack}
                onChange={(e) => setDaysBack(Number(e.target.value))}
                className="w-20 rounded-lg border border-zinc-800 bg-zinc-950/40 px-2 py-1 text-zinc-100"
              />
            </div>

            <button
              onClick={() => load(true)}
              className="rounded-xl border border-zinc-800 bg-zinc-950/40 px-3 py-2 text-sm text-zinc-200"
            >
              Refresh
            </button>
          </div>
        </div>

        {!connected ? (
          <div className="mt-4 rounded-xl border border-zinc-800 bg-zinc-950/30 px-3 py-2 text-sm text-zinc-200">
            You are not connected. Go to{" "}
            <Link to="/settings" className="underline">
              Settings
            </Link>{" "}
            and connect first.
          </div>
        ) : null}

        {mode === "active" ? (
          <div className="mt-4 rounded-xl border border-zinc-800 bg-zinc-950/30 px-3 py-2 text-sm text-zinc-200">
            Active account ID: <span className="text-zinc-100">{activeAccountId ?? "None"}</span>. Set it on{" "}
            <Link to="/accounts" className="underline">
              Accounts
            </Link>
            .
          </div>
        ) : (
          <div className="mt-4 flex flex-wrap items-center gap-4 rounded-xl border border-zinc-800 bg-zinc-950/30 px-3 py-2 text-sm text-zinc-200">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={onlyActiveAccounts}
                onChange={(e) => setOnlyActiveAccounts(e.target.checked)}
                className="h-4 w-4 accent-zinc-200"
              />
              Only active accounts
            </label>

            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={includeInvisibleAccounts}
                onChange={(e) => setIncludeInvisibleAccounts(e.target.checked)}
                className="h-4 w-4 accent-zinc-200"
              />
              Include invisible accounts (needed for most inactive accounts)
            </label>
          </div>
        )}

        {error ? (
          <div className="mt-4 rounded-xl border border-zinc-800 bg-zinc-950/30 px-3 py-2 text-sm text-zinc-200">
            {error}
          </div>
        ) : null}

        <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Net PnL</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtMoney(totals?.netPnl ?? 0)}</div>
            <div className="mt-1 text-xs text-zinc-500">Range: last {effectiveDaysBack} day(s)</div>
          </div>

          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Fees</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtMoney(totals?.fees ?? 0)}</div>
            <div className="mt-1 text-xs text-zinc-500">Gross: {fmtMoney(totals?.grossPnl ?? 0)}</div>
          </div>

          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Win rate</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtPct(totals?.winRate ?? 0)}</div>
            <div className="mt-1 text-xs text-zinc-500">
              W {totals?.wins ?? 0} / L {totals?.losses ?? 0} / BE {totals?.breakeven ?? 0}
            </div>
          </div>

          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Max drawdown</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtMoney(totals?.maxDrawdown ?? 0)}</div>
            <div className="mt-1 text-xs text-zinc-500">From daily equity</div>
          </div>
        </div>

        <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Profit factor</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtPF(totals?.profitFactor ?? 0)}</div>
            <div className="mt-1 text-xs text-zinc-500">
              Avg win {fmtMoney(totals?.avgWin ?? 0)} | Avg loss {fmtMoney(totals?.avgLoss ?? 0)}
            </div>
          </div>

          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Trades</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">{totals?.realizedTrades ?? 0}</div>
            <div className="mt-1 text-xs text-zinc-500">
              Half-turns {totals?.halfTurns ?? 0} | Executions {totals?.totalExecutions ?? 0}
            </div>
          </div>

          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Day win rate</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtPct(totals?.dayWinRate ?? 0)}</div>
            <div className="mt-1 text-xs text-zinc-500">
              Green {daySummary.greenDays} | Red {daySummary.redDays} | Flat {daySummary.flatDays}
            </div>
          </div>

          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Avg trades/day</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">
              {(totals?.avgTradesPerDay ?? 0).toFixed(2)}
            </div>
            <div className="mt-1 text-xs text-zinc-500">Active days {daySummary.activeDays}</div>
          </div>
        </div>

        <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Expectancy / trade</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtMoney(totals?.expectancyPerTrade ?? 0)}</div>
            <div className="mt-1 text-xs text-zinc-500">Tail risk (avg worst 5%): {fmtMoney(totals?.tailRiskAvg ?? 0)}</div>
          </div>

          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Risk & drawdown</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtMoney(totals?.maxIntradayDrawdown ?? 0)}</div>
            <div className="mt-1 text-xs text-zinc-500">Avg DD {fmtMoney(totals?.avgDrawdown ?? 0)} | Max length {fmtDays(totals?.maxDrawdownLengthDays ?? 0)}</div>
          </div>

          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Recovery</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtDays(totals?.avgTimeToRecoveryDays ?? 0)}</div>
            <div className="mt-1 text-xs text-zinc-500">Avg length {fmtDays(totals?.avgDrawdownLengthDays ?? 0)}</div>
          </div>

          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Efficiency</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtMoney(totals?.profitPerHour ?? 0)} / hr</div>
            <div className="mt-1 text-xs text-zinc-500">Per day {fmtMoney(totals?.profitPerDay ?? 0)}</div>
          </div>
        </div>

        <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Consistency</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtPct(totals?.consistencyByWeek ?? 0)}</div>
            <div className="mt-1 text-xs text-zinc-500">Week finish green %</div>
          </div>

          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Streaks</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">W {totals?.maxConsecutiveWins ?? 0} / L {totals?.maxConsecutiveLosses ?? 0}</div>
            <div className="mt-1 text-xs text-zinc-500">
              Avg losing streak {totals?.avgLosingStreak !== undefined ? totals.avgLosingStreak.toFixed(1) : "0"}
            </div>
          </div>

          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Duration</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtHours((totals?.avgTradeDurationMs ?? 0) / 1000 / 60 / 60)}</div>
            <div className="mt-1 text-xs text-zinc-500">Avg win {fmtHours((totals?.avgWinDurationMs ?? 0) / 1000 / 60 / 60)} | Avg loss {fmtHours((totals?.avgLossDurationMs ?? 0) / 1000 / 60 / 60)}</div>
          </div>

          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-xs text-zinc-400">Time to recovery</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">{fmtDays(totals?.avgTimeToRecoveryDays ?? 0)}</div>
            <div className="mt-1 text-xs text-zinc-500">Max DD length {fmtDays(totals?.maxDrawdownLengthDays ?? 0)}</div>
          </div>
        </div>

        <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-sm font-semibold text-zinc-100">Time-of-day performance</div>
            <div className="mt-2 divide-y divide-zinc-800 text-sm text-zinc-200">
              {(totals?.timeBlocks || []).map((b) => (
                <div key={b.label} className="flex items-center justify-between py-2">
                  <div>{b.label}</div>
                  <div className="text-right">
                    <div>{fmtMoney(b.netPnl)}</div>
                    <div className="text-xs text-zinc-500">Trades {b.trades}</div>
                  </div>
                </div>
              ))}
              {!totals?.timeBlocks?.length ? (
                <div className="py-2 text-zinc-400">No realized trades in range.</div>
              ) : null}
            </div>
          </div>

          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
            <div className="text-sm font-semibold text-zinc-100">Instrument breakdown</div>
            <div className="mt-2 divide-y divide-zinc-800 text-sm text-zinc-200">
              {(totals?.instruments || []).map((i) => (
                <div key={i.contractId} className="flex items-center justify-between py-2">
                  <div className="text-xs text-zinc-400">{i.contractId}</div>
                  <div className="text-right">
                    <div>{fmtMoney(i.netPnl)}</div>
                    <div className="text-xs text-zinc-500">Trades {i.trades}</div>
                  </div>
                </div>
              ))}
              {!totals?.instruments?.length ? (
                <div className="py-2 text-zinc-400">No realized trades in range.</div>
              ) : null}
            </div>
          </div>
        </div>

        <div className="mt-3 rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
          <div className="mb-2 flex items-center justify-between text-sm text-zinc-100">
            <div>
              <div className="font-semibold">Equity curve</div>
              <div className="text-xs text-zinc-400">Cumulative net PnL with daily PnL overlay</div>
            </div>
            <div className="text-xs text-zinc-400">Range: last {effectiveDaysBack} day(s)</div>
          </div>

          {loading ? (
            <div className="py-6 text-sm text-zinc-300">Loading...</div>
          ) : !computed?.equity?.length ? (
            <div className="py-6 text-sm text-zinc-300">No equity data found for this range.</div>
          ) : (
            <EquityCurveChart data={computed.equity} />
          )}
        </div>

        <div className="mt-4 overflow-hidden rounded-2xl border border-zinc-800">
          <div className="grid grid-cols-12 bg-zinc-950/40 px-4 py-2 text-xs text-zinc-400">
            <div className="col-span-3">Date</div>
            <div className="col-span-3">Net</div>
            <div className="col-span-2">Trades</div>
            <div className="col-span-2">Contracts</div>
            <div className="col-span-2">Fees</div>
          </div>

          <div className="divide-y divide-zinc-800 bg-zinc-950/20">
            {loading ? (
              <div className="px-4 py-4 text-sm text-zinc-300">Loading...</div>
            ) : !computed || computed.days.length === 0 ? (
              <div className="px-4 py-4 text-sm text-zinc-300">No day data found for this range.</div>
            ) : (
              computed.days
                .slice()
                .reverse()
                .slice(0, 25)
                .map((d) => (
                  <div key={d.date} className="grid grid-cols-12 items-center px-4 py-3 text-sm text-zinc-200">
                    <div className="col-span-3 text-zinc-300">{d.date}</div>
                    <div className="col-span-3">{fmtMoney(d.netPnl)}</div>
                    <div className="col-span-2">{d.trades}</div>
                    <div className="col-span-2">{d.contracts}</div>
                    <div className="col-span-2">{fmtMoney(d.fees)}</div>
                  </div>
                ))
            )}
          </div>
        </div>

        <div className="mt-4 rounded-xl border border-zinc-800 bg-zinc-950/30 px-3 py-2 text-xs text-zinc-400">
          {mode === "active" ? (
            <>
              Using: POST <span className="text-zinc-200">/api/Trade/search</span> (active account only)
            </>
          ) : (
            <>
              Using: POST <span className="text-zinc-200">/api/Account/search</span> + POST{" "}
              <span className="text-zinc-200">/api/Trade/search</span> (all accounts aggregation)
            </>
          )}
        </div>
      </div>
    </div>
  );
}
