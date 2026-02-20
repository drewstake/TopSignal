import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Input } from "../../components/ui/Input";
import { metricsApi } from "../../lib/api";
import type {
  BehaviorMetrics,
  DayPnlPoint,
  HourPnlPoint,
  StreakMetrics,
  SummaryMetrics,
  SymbolPnlPoint,
  TradeRecord,
} from "../../lib/types";

interface JournalEntry {
  id: string;
  date: string;
  title: string;
  body: string;
  tags: string[];
}

interface ResourceState<T> {
  data: T;
  loading: boolean;
  error: string | null;
}

const initialNotes: JournalEntry[] = [];

const emptySummary: SummaryMetrics = {
  trade_count: 0,
  net_pnl: 0,
  win_rate: 0,
  profit_factor: 0,
  expectancy: 0,
  average_win: 0,
  average_loss: 0,
  average_win_loss_ratio: 0,
  max_drawdown: 0,
  largest_losing_trade: 0,
  average_hold_minutes: 0,
  average_hold_minutes_winners: 0,
  average_hold_minutes_losers: 0,
};

const emptyStreaks: StreakMetrics = {
  current_win_streak: 0,
  current_loss_streak: 0,
  longest_win_streak: 0,
  longest_loss_streak: 0,
  pnl_after_losses: [
    { loss_streak: 1, trade_count: 0, total_pnl: 0, average_pnl: 0 },
    { loss_streak: 2, trade_count: 0, total_pnl: 0, average_pnl: 0 },
    { loss_streak: 3, trade_count: 0, total_pnl: 0, average_pnl: 0 },
  ],
};

const emptyBehavior: BehaviorMetrics = {
  trade_count: 0,
  average_position_size: 0,
  max_position_size: 0,
  rule_break_count: 0,
  rule_break_pnl: 0,
  rule_following_pnl: 0,
};

function useResource<T>(fetcher: () => Promise<T>, initialData: T): ResourceState<T> {
  const [data, setData] = useState<T>(initialData);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const next = await fetcher();
        if (isMounted) {
          setData(next);
        }
      } catch (err) {
        if (isMounted) {
          setError(err instanceof Error ? err.message : "Unknown error");
        }
      } finally {
        if (isMounted) {
          setLoading(false);
        }
      }
    }

    void load();
    return () => {
      isMounted = false;
    };
  }, [fetcher]);

  return { data, loading, error };
}

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const dateTimeFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "UTC",
});

const dateFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

const stickyHeadClass =
  "sticky top-0 z-10 border-b border-slate-800/80 bg-slate-900/95 px-3 py-3 text-xs font-medium uppercase tracking-wide text-slate-400";

function sideVariant(side: TradeRecord["side"]) {
  return side === "LONG" ? "accent" : "warning";
}

function pnlClass(value: number) {
  return value >= 0 ? "text-emerald-300" : "text-rose-300";
}

function formatPnl(value: number) {
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${currencyFormatter.format(value)}`;
}

function formatPrice(value: number) {
  return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 5 });
}

function tradeNetPnl(trade: TradeRecord) {
  return (trade.pnl ?? 0) - (trade.fees ?? 0);
}

function tradePoints(trade: TradeRecord) {
  if (trade.exit_price === null) {
    return 0;
  }
  const direction = trade.side === "LONG" ? 1 : -1;
  return (trade.exit_price - trade.entry_price) * direction;
}

function formatDate(isoDate: string) {
  return dateFormatter.format(new Date(`${isoDate}T00:00:00Z`));
}

function formatHourLabel(hour: number) {
  const display = hour.toString().padStart(2, "0");
  return `${display}:00`;
}

function formatHoldMinutes(minutes: number) {
  if (minutes < 60) {
    return `${Math.round(minutes)}m`;
  }

  const hours = Math.floor(minutes / 60);
  const remainder = Math.round(minutes % 60);
  return `${hours}h ${remainder.toString().padStart(2, "0")}m`;
}

export function DashboardPage() {
  const [notes, setNotes] = useState<JournalEntry[]>(initialNotes);
  const [noteTitle, setNoteTitle] = useState("");
  const [noteBody, setNoteBody] = useState("");

  const fetchSummary = useCallback(() => metricsApi.getSummary(), []);
  const fetchPnlByHour = useCallback(() => metricsApi.getPnlByHour(), []);
  const fetchPnlByDay = useCallback(() => metricsApi.getPnlByDay(), []);
  const fetchPnlBySymbol = useCallback(() => metricsApi.getPnlBySymbol(), []);
  const fetchStreaks = useCallback(() => metricsApi.getStreaks(), []);
  const fetchBehavior = useCallback(() => metricsApi.getBehavior(), []);
  const fetchTrades = useCallback(() => metricsApi.getTrades(100), []);

  const summary = useResource<SummaryMetrics>(fetchSummary, emptySummary);
  const pnlByHour = useResource<HourPnlPoint[]>(fetchPnlByHour, []);
  const pnlByDay = useResource<DayPnlPoint[]>(fetchPnlByDay, []);
  const pnlBySymbol = useResource<SymbolPnlPoint[]>(fetchPnlBySymbol, []);
  const streaks = useResource<StreakMetrics>(fetchStreaks, emptyStreaks);
  const behavior = useResource<BehaviorMetrics>(fetchBehavior, emptyBehavior);
  const trades = useResource<TradeRecord[]>(fetchTrades, []);

  const recentTrades = useMemo(() => {
    return [...trades.data]
      .sort((left, right) => (right.closed_at ?? "").localeCompare(left.closed_at ?? ""))
      .slice(0, 12);
  }, [trades.data]);

  const activeHourRows = useMemo(() => pnlByHour.data.filter((row) => row.trade_count > 0), [pnlByHour.data]);

  const kpiCards = useMemo(
    () => [
      {
        id: "net-pnl",
        label: "Net PnL (After Fees)",
        value: formatPnl(summary.data.net_pnl),
        detail: `${summary.data.trade_count} closed trades`,
      },
      {
        id: "win-rate",
        label: "Win Rate",
        value: `${summary.data.win_rate.toFixed(2)}%`,
        detail: `Avg win/loss ratio ${summary.data.average_win_loss_ratio.toFixed(2)}`,
      },
      {
        id: "profit-factor",
        label: "Profit Factor",
        value: summary.data.profit_factor.toFixed(2),
        detail: `Avg win ${formatPnl(summary.data.average_win)} | Avg loss ${formatPnl(-summary.data.average_loss)}`,
      },
      {
        id: "expectancy",
        label: "Expectancy / Trade",
        value: formatPnl(summary.data.expectancy),
        detail: `Largest loss ${formatPnl(summary.data.largest_losing_trade)}`,
      },
      {
        id: "max-drawdown",
        label: "Max Drawdown",
        value: formatPnl(summary.data.max_drawdown),
        detail: `Hold avg ${formatHoldMinutes(summary.data.average_hold_minutes)}`,
      },
    ],
    [summary.data]
  );

  function handleAddNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const cleanTitle = noteTitle.trim();
    const cleanBody = noteBody.trim();
    if (cleanTitle.length === 0 || cleanBody.length === 0) {
      return;
    }

    const now = new Date();
    const nextEntry: JournalEntry = {
      id: `JR-${now.getTime()}`,
      date: now.toISOString().slice(0, 10),
      title: cleanTitle,
      body: cleanBody,
      tags: ["manual", "dashboard"],
    };

    setNotes((current) => [nextEntry, ...current]);
    setNoteTitle("");
    setNoteBody("");
  }

  return (
    <div className="space-y-6 pb-10">
      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {kpiCards.map((metric) => (
          <Card key={metric.id} className="p-4">
            <p className="text-xs uppercase tracking-[0.16em] text-slate-500">{metric.label}</p>
            <p className="mt-2 text-2xl font-semibold text-slate-100">{summary.loading ? "Loading..." : metric.value}</p>
            <p className={`mt-2 text-xs ${summary.error ? "text-rose-300" : "text-slate-500"}`}>
              {summary.error ? `Error: ${summary.error}` : metric.detail}
            </p>
          </Card>
        ))}
      </section>

      <Card>
        <CardHeader>
          <CardTitle>Recent Trades</CardTitle>
          <CardDescription>Most recent closed trades pulled from your database.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-0">
          <div className="max-h-[420px] overflow-auto rounded-xl border border-slate-800/80">
            <table className="w-full min-w-[980px] border-collapse text-sm">
              <thead>
                <tr>
                  <th className={`${stickyHeadClass} text-left`}>Symbol</th>
                  <th className={`${stickyHeadClass} text-left`}>Side</th>
                  <th className={`${stickyHeadClass} text-right`}>Qty</th>
                  <th className={`${stickyHeadClass} text-right`}>Entry</th>
                  <th className={`${stickyHeadClass} text-right`}>Exit</th>
                  <th className={`${stickyHeadClass} text-right`}>Pts</th>
                  <th className={`${stickyHeadClass} text-right`}>PnL ($)</th>
                  <th className={`${stickyHeadClass} text-right`}>Closed</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {trades.loading ? (
                  <tr>
                    <td colSpan={8} className="px-3 py-6 text-center text-slate-400">
                      Loading trades...
                    </td>
                  </tr>
                ) : trades.error ? (
                  <tr>
                    <td colSpan={8} className="px-3 py-6 text-center text-rose-300">
                      {trades.error}
                    </td>
                  </tr>
                ) : recentTrades.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="px-3 py-6 text-center text-slate-400">
                      No trades available.
                    </td>
                  </tr>
                ) : (
                  recentTrades.map((trade) => {
                    const points = tradePoints(trade);
                    const netPnl = tradeNetPnl(trade);
                    return (
                      <tr key={trade.id} className="transition hover:bg-slate-900/65">
                        <td className="px-3 py-3 text-left">
                          <p className="font-medium text-slate-100">{trade.symbol}</p>
                          <p className="text-xs text-slate-500">ID {trade.id}</p>
                        </td>
                        <td className="px-3 py-3 text-left">
                          <Badge variant={sideVariant(trade.side)}>{trade.side}</Badge>
                        </td>
                        <td className="px-3 py-3 text-right text-slate-200">{trade.qty}</td>
                        <td className="px-3 py-3 text-right font-mono text-slate-200">{formatPrice(trade.entry_price)}</td>
                        <td className="px-3 py-3 text-right font-mono text-slate-200">
                          {trade.exit_price === null ? "-" : formatPrice(trade.exit_price)}
                        </td>
                        <td className={`px-3 py-3 text-right font-mono ${pnlClass(points)}`}>{points.toFixed(2)}</td>
                        <td className={`px-3 py-3 text-right font-semibold ${pnlClass(netPnl)}`}>{formatPnl(netPnl)}</td>
                        <td className="px-3 py-3 text-right text-slate-400">
                          {trade.closed_at ? dateTimeFormatter.format(new Date(trade.closed_at)) : "-"}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Symbol / Contract Performance</CardTitle>
          <CardDescription>PnL by symbol from closed trades (after fees).</CardDescription>
        </CardHeader>
        <CardContent className="space-y-0">
          <div className="max-h-[360px] overflow-auto rounded-xl border border-slate-800/80">
            <table className="w-full min-w-[900px] border-collapse text-sm">
              <thead>
                <tr>
                  <th className={`${stickyHeadClass} text-left`}>Symbol</th>
                  <th className={`${stickyHeadClass} text-right`}>Trades</th>
                  <th className={`${stickyHeadClass} text-right`}>Win Rate</th>
                  <th className={`${stickyHeadClass} text-right`}>Net PnL ($)</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {pnlBySymbol.loading ? (
                  <tr>
                    <td colSpan={4} className="px-3 py-6 text-center text-slate-400">
                      Loading symbol metrics...
                    </td>
                  </tr>
                ) : pnlBySymbol.error ? (
                  <tr>
                    <td colSpan={4} className="px-3 py-6 text-center text-rose-300">
                      {pnlBySymbol.error}
                    </td>
                  </tr>
                ) : pnlBySymbol.data.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-3 py-6 text-center text-slate-400">
                      No symbol data.
                    </td>
                  </tr>
                ) : (
                  pnlBySymbol.data.map((row) => (
                    <tr key={row.symbol} className="transition hover:bg-slate-900/65">
                      <td className="px-3 py-3 text-left font-medium text-slate-100">{row.symbol}</td>
                      <td className="px-3 py-3 text-right text-slate-200">{row.trade_count}</td>
                      <td className="px-3 py-3 text-right">
                        <Badge variant={row.win_rate >= 55 ? "positive" : "warning"}>{row.win_rate.toFixed(2)}%</Badge>
                      </td>
                      <td className={`px-3 py-3 text-right font-semibold ${pnlClass(row.pnl)}`}>{formatPnl(row.pnl)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <section className="grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>PnL by Hour of Day</CardTitle>
            <CardDescription>Grouped by trade open hour (UTC).</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="max-h-[330px] overflow-auto rounded-xl border border-slate-800/80 bg-slate-900/45">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr>
                    <th className={`${stickyHeadClass} text-left`}>Hour</th>
                    <th className={`${stickyHeadClass} text-right`}>Trades</th>
                    <th className={`${stickyHeadClass} text-right`}>PnL</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {pnlByHour.loading ? (
                    <tr>
                      <td colSpan={3} className="px-3 py-6 text-center text-slate-400">
                        Loading hourly PnL...
                      </td>
                    </tr>
                  ) : pnlByHour.error ? (
                    <tr>
                      <td colSpan={3} className="px-3 py-6 text-center text-rose-300">
                        {pnlByHour.error}
                      </td>
                    </tr>
                  ) : activeHourRows.length === 0 ? (
                    <tr>
                      <td colSpan={3} className="px-3 py-6 text-center text-slate-400">
                        No hourly trade data.
                      </td>
                    </tr>
                  ) : (
                    activeHourRows.map((row) => (
                      <tr key={row.hour} className="transition hover:bg-slate-900/65">
                        <td className="px-3 py-3 text-left text-slate-200">{formatHourLabel(row.hour)}</td>
                        <td className="px-3 py-3 text-right text-slate-200">{row.trade_count}</td>
                        <td className={`px-3 py-3 text-right font-semibold ${pnlClass(row.pnl)}`}>{formatPnl(row.pnl)}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>PnL by Day of Week</CardTitle>
            <CardDescription>Grouped by trade open day.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="max-h-[330px] overflow-auto rounded-xl border border-slate-800/80 bg-slate-900/45">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr>
                    <th className={`${stickyHeadClass} text-left`}>Day</th>
                    <th className={`${stickyHeadClass} text-right`}>Trades</th>
                    <th className={`${stickyHeadClass} text-right`}>PnL</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {pnlByDay.loading ? (
                    <tr>
                      <td colSpan={3} className="px-3 py-6 text-center text-slate-400">
                        Loading day-of-week PnL...
                      </td>
                    </tr>
                  ) : pnlByDay.error ? (
                    <tr>
                      <td colSpan={3} className="px-3 py-6 text-center text-rose-300">
                        {pnlByDay.error}
                      </td>
                    </tr>
                  ) : (
                    pnlByDay.data.map((row) => (
                      <tr key={row.day_of_week} className="transition hover:bg-slate-900/65">
                        <td className="px-3 py-3 text-left text-slate-200">{row.day_label}</td>
                        <td className="px-3 py-3 text-right text-slate-200">{row.trade_count}</td>
                        <td className={`px-3 py-3 text-right font-semibold ${pnlClass(row.pnl)}`}>{formatPnl(row.pnl)}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Behavior and Streaks</CardTitle>
            <CardDescription>Discipline, streak behavior, and post-loss outcomes.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {behavior.loading || streaks.loading || summary.loading ? (
              <p className="text-sm text-slate-400">Loading behavior metrics...</p>
            ) : behavior.error || streaks.error ? (
              <p className="text-sm text-rose-300">{behavior.error ?? streaks.error}</p>
            ) : (
              <>
                <div className="rounded-xl border border-slate-800/80 bg-slate-900/45 p-3">
                  <div className="flex items-center justify-between">
                    <p className="text-sm font-medium text-slate-100">Current Streak</p>
                    <Badge variant={streaks.data.current_win_streak > 0 ? "positive" : "warning"}>
                      {streaks.data.current_win_streak > 0
                        ? `${streaks.data.current_win_streak} win`
                        : `${streaks.data.current_loss_streak} loss`}
                    </Badge>
                  </div>
                  <p className="mt-2 text-xs text-slate-400">
                    Longest win: {streaks.data.longest_win_streak} | Longest loss: {streaks.data.longest_loss_streak}
                  </p>
                </div>

                <div className="rounded-xl border border-slate-800/80 bg-slate-900/45 p-3 text-xs text-slate-300">
                  <p className="font-medium text-slate-100">PnL After Loss Streaks</p>
                  <div className="mt-2 space-y-1">
                    {streaks.data.pnl_after_losses.map((bucket) => (
                      <div key={bucket.loss_streak} className="flex items-center justify-between gap-3">
                        <span>After {bucket.loss_streak === 3 ? "3+" : bucket.loss_streak} losses</span>
                        <span className={pnlClass(bucket.average_pnl)}>
                          {formatPnl(bucket.average_pnl)} avg ({bucket.trade_count} trades)
                        </span>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="rounded-xl border border-slate-800/80 bg-slate-900/45 p-3 text-xs text-slate-300">
                  <p className="font-medium text-slate-100">Position Size</p>
                  <p className="mt-2">Avg size: {behavior.data.average_position_size.toFixed(2)}</p>
                  <p>Max size: {behavior.data.max_position_size.toFixed(2)}</p>
                  <p>Largest losing trade: {formatPnl(summary.data.largest_losing_trade)}</p>
                </div>

                <div className="rounded-xl border border-slate-800/80 bg-slate-900/45 p-3 text-xs text-slate-300">
                  <p className="font-medium text-slate-100">Rule Breaks</p>
                  <p className="mt-2">Rule-break count: {behavior.data.rule_break_count}</p>
                  <p className={pnlClass(behavior.data.rule_break_pnl)}>Rule-break PnL: {formatPnl(behavior.data.rule_break_pnl)}</p>
                  <p className={pnlClass(behavior.data.rule_following_pnl)}>
                    Rule-following PnL: {formatPnl(behavior.data.rule_following_pnl)}
                  </p>
                  <p className="mt-2 text-slate-400">
                    Hold time (all / wins / losses): {formatHoldMinutes(summary.data.average_hold_minutes)} /{" "}
                    {formatHoldMinutes(summary.data.average_hold_minutes_winners)} /{" "}
                    {formatHoldMinutes(summary.data.average_hold_minutes_losers)}
                  </p>
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>Journal</CardTitle>
            <CardDescription>Execution notes and session debriefs in one timeline.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="max-h-[320px] space-y-3 overflow-auto pr-1">
              {notes.length === 0 ? (
                <p className="text-sm text-slate-400">No notes yet.</p>
              ) : (
                notes.map((entry) => (
                  <article key={entry.id} className="rounded-xl border border-slate-800/80 bg-slate-900/45 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-sm font-medium text-slate-100">{entry.title}</p>
                      <span className="text-xs text-slate-500">{formatDate(entry.date)}</span>
                    </div>
                    <p className="mt-2 text-sm text-slate-300">{entry.body}</p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {entry.tags.map((tag) => (
                        <Badge key={`${entry.id}-${tag}`} variant="neutral">
                          {tag}
                        </Badge>
                      ))}
                    </div>
                  </article>
                ))
              )}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Add Note</CardTitle>
            <CardDescription>Quick capture for post-trade context and follow-up actions.</CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-3" onSubmit={handleAddNote}>
              <Input
                value={noteTitle}
                onChange={(event) => setNoteTitle(event.target.value)}
                placeholder="Session title"
                aria-label="Session title"
              />
              <textarea
                value={noteBody}
                onChange={(event) => setNoteBody(event.target.value)}
                placeholder="Write what happened, what worked, and what to improve."
                aria-label="Session note"
                className="min-h-36 w-full rounded-xl border border-slate-700 bg-slate-900/70 p-3 text-sm text-slate-100 outline-none transition focus:border-cyan-400/70 focus:ring-2 focus:ring-cyan-500/30"
              />
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs text-slate-500">Notes are stored in this local dashboard session.</p>
                <Button type="submit" size="sm">
                  Add Note
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}