import { useMemo, useState } from "react";

import { Button } from "../../components/ui/Button";
import { Select } from "../../components/ui/Select";
import { cn } from "../../components/ui/cn";
import type { BotBacktestResult } from "../../lib/types";
import {
  analyzeTrades, buildTradeAnalysis, formatHold, tradesToCsv,
  type AnalyzedTrade, type TradeOutcome, type TradeSideFilter, type TradeSummary,
} from "./backtestAnalytics";

const moneyFormatter = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const numberFormatter = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const timeFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", month: "short", day: "numeric", year: "2-digit", hour: "2-digit", minute: "2-digit",
});
const money = (value: number | null) => value === null ? "—" : moneyFormatter.format(value);
const number = (value: number | null) => value === null ? "—" : numberFormatter.format(value);
const percent = (value: number | null) => value === null ? "—" : `${number(value)}%`;
const tone = (value: number) => value > 0 ? "text-app-positive" : value < 0 ? "text-app-negative" : "text-app-text-soft";
const cell = "whitespace-nowrap px-3 py-2.5 text-right font-mono text-xs";
const head = "whitespace-nowrap px-3 py-2.5 text-right text-[10px] font-medium uppercase tracking-wide text-app-muted";

export function BacktestTradeAnalysis({ result }: { result: BotBacktestResult }) {
  const [side, setSide] = useState<TradeSideFilter>("all");
  const [timeGroup, setTimeGroup] = useState<"hour" | "weekday" | "year">("hour");
  const allTrades = useMemo(() => analyzeTrades(result.trades), [result.trades]);
  const allAnalysis = useMemo(() => buildTradeAnalysis(allTrades), [allTrades]);
  const trades = useMemo(() => side === "all" ? allTrades : allTrades.filter((trade) => trade.side === side), [allTrades, side]);
  const analysis = useMemo(() => side === "all" ? allAnalysis : buildTradeAnalysis(trades), [allAnalysis, side, trades]);
  const selectedLabel = side === "all" ? "All trades" : side === "long" ? "Long trades" : "Short trades";
  const timeRows = timeGroup === "hour" ? analysis.byHour : timeGroup === "weekday" ? analysis.byWeekday : analysis.byYear;

  function exportAnalysis() {
    downloadText(`backtest-${result.id}-${side}-analysis.json`, JSON.stringify({
      run_id: result.id, engine_version: result.engine_version, input_fingerprint: result.input_fingerprint,
      created_at: result.created_at, range: result.range, config_snapshot: result.config_snapshot,
      assumptions: result.assumptions, scope: { direction: side, sample: "full_replay", timezone: "America/New_York" },
      definitions: {
        outcome: "Winner > 0, loser < 0, breakeven = 0 net P&L after fees and modeled slippage.",
        holding_time: "Elapsed recorded entry-to-exit time, including session gaps; approximate at candle resolution. Same-candle exits can be 0 minutes.",
        percentiles: "Linear interpolation between ordered holding times; missing or reversed timestamps excluded from timing only.",
        excursions: "MAE/MFE are gross dollar excursions for the whole position, approximated from OHLC; no intrabar timing is known.",
        profit_factor: "Sum of positive net P&L / absolute sum of negative net P&L; null when no net losses exist.",
      },
      analysis, warnings: result.warnings, notes: result.notes ?? [],
      data_quality: result.data_quality ?? null,
    }, null, 2), "application/json");
  }

  return (
    <section aria-label="Backtest trade analysis" className="space-y-5 border-t border-app-border pt-5">
      <div>
        <h4 className="text-base font-semibold text-app-text">Trade analysis</h4>
        <p className="mt-1 text-xs text-app-muted">Full-replay trade details for comparing setups. Outcomes and profit factor use net P&amp;L after costs.</p>
      </div>

      <AnalysisBlock title="Longs vs shorts" description="Every closed trade in this replay, separated by direction.">
        <PerformanceTable label="Direction performance" rows={[allAnalysis.overall, ...allAnalysis.directions]} />
      </AnalysisBlock>

      <div className="flex flex-wrap items-end justify-between gap-3 rounded-xl border border-app-border bg-app-bg/30 p-4">
        <label className="space-y-1 text-xs text-app-muted">
          <span className="block">Direction for detailed analysis</span>
          <Select aria-label="Analysis direction" value={side} onChange={(event) => setSide(event.target.value as TradeSideFilter)}>
            <option value="all">All trades</option><option value="long">Long trades</option><option value="short">Short trades</option>
          </Select>
        </label>
        <div className="space-y-2">
          <p className="text-xs text-app-muted">{selectedLabel} · {number(trades.length)} trades in each export</p>
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="secondary" disabled={!trades.length} onClick={() => downloadText(`backtest-${result.id}-${side}-trades.csv`, tradesToCsv(trades), "text/csv;charset=utf-8")}>Export trades CSV</Button>
            <Button type="button" variant="secondary" onClick={exportAnalysis}>Export analysis JSON</Button>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <SmallMetric label="Total commissions" value={money(analysis.overall.commission)} />
        <SmallMetric label="Winners erased by fees" value={number(analysis.overall.feeErasedWinners)} detail="Positive gross P&L that became flat or negative net." />
        <SmallMetric label="Largest net win" value={money(analysis.overall.largestWin)} />
        <SmallMetric label="Largest net loss" value={money(analysis.overall.largestLoss)} />
      </div>

      <AnalysisBlock title="Holding time: winners vs losers" description={`${selectedLabel} · averages, typical duration (median), and long holds (90th percentile).`}>
        <div className="overflow-x-auto">
          <table aria-label="Holding time by outcome" className="w-full text-app-text-soft">
            <thead className="bg-app-bg/35"><tr>
              {["Outcome", "Trades", "Average held", "Median held", "90% held within", "Average bars", "Adverse move (MAE)", "Favorable move (MFE)"].map((label) => <th key={label} className={cn(head, label === "Outcome" && "text-left")}>{label}</th>)}
            </tr></thead>
            <tbody className="divide-y divide-app-border">
              {analysis.outcomes.map((row) => <tr key={row.label}>
                <th scope="row" className="px-3 py-2.5 text-left text-xs font-medium">{row.label}</th>
                <td className={cell}>{number(row.count)}</td><td className={cell}>{formatHold(row.averageHold)}</td>
                <td className={cell}>{formatHold(row.medianHold)}</td><td className={cell}>{formatHold(row.p90Hold)}</td>
                <td className={cell}>{number(row.averageBars)}</td><td className={cell}>{money(row.averageMae)}</td><td className={cell}>{money(row.averageMfe)}</td>
              </tr>)}
            </tbody>
          </table>
        </div>
        <p className="px-3 pt-3 text-xs leading-5 text-app-muted">
          Holding time is elapsed time between recorded fills, including overnight and closed-market gaps. Exits have candle-level timing;
          a same-candle exit can display 0m. MAE is the average worst adverse move; MFE is the average best favorable move, in gross dollars for the whole position.
          {analysis.overall.timedCount < analysis.overall.count ? ` ${analysis.overall.count - analysis.overall.timedCount} trade(s) have missing or invalid timing and are excluded from duration statistics.` : ""}
        </p>
      </AnalysisBlock>

      <AnalysisBlock title="When entries work" description={`${selectedLabel} · grouped by entry time in America/New_York, including daylight saving time.`}>
        <div className="mb-3 flex flex-wrap gap-2" role="group" aria-label="Entry time grouping">
          {(["hour", "weekday", "year"] as const).map((group) => <Button type="button" key={group} variant={timeGroup === group ? "primary" : "secondary"} aria-pressed={timeGroup === group} onClick={() => setTimeGroup(group)}>{group === "hour" ? "Entry hour (ET)" : group === "weekday" ? "Weekday" : "Year"}</Button>)}
        </div>
        <PerformanceTable label="Entry time performance" rows={timeRows} />
        <p className="px-3 pt-3 text-xs text-app-muted">Compare trade counts as well as returns. These groups describe this sample; choosing a time filter requires a new replay.</p>
      </AnalysisBlock>

      <AnalysisBlock title="How trades exit" description={`${selectedLabel} · targets, stops, opposite signals, rollover, and end-of-test exits kept separate.`}>
        <PerformanceTable label="Exit reason performance" rows={analysis.byExit} />
      </AnalysisBlock>

      <details className="rounded-xl border border-app-border bg-app-bg/25 p-4">
        <summary className="cursor-pointer text-sm font-semibold text-app-text">Results by holding time</summary>
        <p className="my-3 text-xs leading-5 text-app-muted">{selectedLabel} · completed-trade duration groups. This does not simulate a time stop: closing trades earlier would change fills and later entries.</p>
        <PerformanceTable label="Holding duration performance" rows={analysis.byDuration} />
      </details>

      <DetailedTradeLedger key={`${result.id}-${side}`} trades={trades} />
    </section>
  );
}

function AnalysisBlock({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  return <div className="min-w-0 rounded-xl border border-app-border bg-app-bg/25 p-4">
    <h5 className="text-sm font-semibold text-app-text">{title}</h5>
    <p className="mb-3 mt-1 text-xs text-app-muted">{description}</p>
    {children}
  </div>;
}

function SmallMetric({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return <div className="rounded-xl border border-app-border bg-app-bg/30 p-3">
    <p className="text-[10px] uppercase tracking-wide text-app-muted">{label}</p>
    <p className="mt-1 font-mono text-sm font-semibold text-app-text">{value}</p>
    {detail ? <p className="mt-1 text-xs text-app-muted">{detail}</p> : null}
  </div>;
}

function PerformanceTable({ rows, label }: { rows: TradeSummary[]; label: string }) {
  return <div className="overflow-x-auto">
    <table aria-label={label} className="w-full text-app-text-soft">
      <thead className="bg-app-bg/35"><tr>
        {["Group", "Trades", "W / L / Flat", "Win rate", "Net P&L", "Profit factor", "Avg net / trade", "Avg win", "Avg loss", "Avg held"].map((title) => <th key={title} className={cn(head, title === "Group" && "text-left")}>{title}</th>)}
      </tr></thead>
      <tbody className="divide-y divide-app-border">
        {rows.map((row) => <tr key={row.label}>
          <th scope="row" className="whitespace-nowrap px-3 py-2.5 text-left text-xs font-medium">{row.label}</th>
          <td className={cell}>{number(row.count)}</td><td className={cell}>{row.winners} / {row.losers} / {row.breakevens}</td>
          <td className={cell}>{percent(row.winRate)}</td><td className={cn(cell, tone(row.netPnl))}>{money(row.netPnl)}</td>
          <td className={cell}>{row.profitFactor === null ? "—" : `${number(row.profitFactor)}×`}</td>
          <td className={cn(cell, tone(row.expectancy ?? 0))}>{money(row.expectancy)}</td>
          <td className={cell}>{money(row.averageWin)}</td><td className={cell}>{money(row.averageLoss)}</td><td className={cell}>{formatHold(row.averageHold)}</td>
        </tr>)}
        {!rows.length ? <tr><td colSpan={10} className="px-3 py-5 text-center text-xs text-app-muted">No closed trades in this selection.</td></tr> : null}
      </tbody>
    </table>
  </div>;
}

const PAGE_SIZE = 100;
function DetailedTradeLedger({ trades }: { trades: AnalyzedTrade[] }) {
  const [outcome, setOutcome] = useState<"all" | TradeOutcome>("all");
  const [sort, setSort] = useState("entry");
  const [page, setPage] = useState(0);
  const filtered = useMemo(() => {
    const selected = trades.filter((trade) => outcome === "all" || trade.outcome === outcome);
    return selected.sort((a, b) => {
      if (sort === "loss") return a.net_pnl - b.net_pnl || a.id - b.id;
      if (sort === "win") return b.net_pnl - a.net_pnl || a.id - b.id;
      if (sort === "duration") return (b.holdMinutes ?? -1) - (a.holdMinutes ?? -1) || a.id - b.id;
      return Date.parse(a.entry_timestamp) - Date.parse(b.entry_timestamp) || a.id - b.id;
    });
  }, [outcome, sort, trades]);
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const activePage = Math.min(page, pages - 1);
  const start = activePage * PAGE_SIZE;

  return <div className="min-w-0 space-y-3">
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div><h5 className="text-sm font-semibold text-app-text">Trade ledger</h5><p className="mt-1 text-xs text-app-muted">Recorded fills in ET · holding time is approximate · excursions in dollars</p></div>
      <div className="flex flex-wrap gap-3">
        <label className="space-y-1 text-xs text-app-muted"><span className="block">Outcome</span><Select aria-label="Ledger outcome" value={outcome} onChange={(event) => { setOutcome(event.target.value as typeof outcome); setPage(0); }}>
          <option value="all">All outcomes</option><option value="winner">Winners</option><option value="loser">Losers</option><option value="breakeven">Breakeven</option>
        </Select></label>
        <label className="space-y-1 text-xs text-app-muted"><span className="block">Sort</span><Select aria-label="Ledger sort" value={sort} onChange={(event) => { setSort(event.target.value); setPage(0); }}>
          <option value="entry">Entry time</option><option value="loss">Largest losses</option><option value="win">Largest wins</option><option value="duration">Longest held</option>
        </Select></label>
      </div>
    </div>
    <div className="max-h-[34rem] overflow-auto rounded-xl border border-app-border">
      <table aria-label="Detailed trade ledger" className="w-full text-app-text-soft">
        <thead className="sticky top-0 bg-app-surface"><tr>{["#", "Side", "Outcome", "Signal ET", "Entry ET", "Exit ET", "Held", "Bars", "Qty", "Entry price", "Exit price", "Points", "Exit reason", "Gross", "Fees", "Net", "MAE", "MFE"].map((label) => <th key={label} className={head}>{label}</th>)}</tr></thead>
        <tbody className="divide-y divide-app-border">
          {filtered.slice(start, start + PAGE_SIZE).map((trade) => <tr key={trade.id}>
            <td className={cell}>{trade.id}</td><td className={cell}>{trade.side}</td><td className={cn(cell, tone(trade.net_pnl))}>{trade.outcome}</td>
            <td className={cell}>{timestamp(trade.signal_timestamp)}</td><td className={cell}>{timestamp(trade.entry_timestamp)}</td><td className={cell}>{timestamp(trade.exit_timestamp)}</td>
            <td className={cell}>{formatHold(trade.holdMinutes)}</td><td className={cell}>{trade.bars_held}</td><td className={cell}>{trade.quantity}</td>
            <td className={cell}>{number(trade.entry_price)}</td><td className={cell}>{number(trade.exit_price)}</td><td className={cell}>{number(trade.points)}</td>
            <td className={cell}>{trade.exit_reason.replaceAll("_", " ")}</td><td className={cell}>{money(trade.gross_pnl)}</td><td className={cell}>{money(trade.commission)}</td>
            <td className={cn(cell, tone(trade.net_pnl))}>{money(trade.net_pnl)}</td><td className={cell}>{money(Math.abs(trade.mae))}</td><td className={cell}>{money(Math.abs(trade.mfe))}</td>
          </tr>)}
          {!filtered.length ? <tr><td colSpan={18} className="px-3 py-6 text-center text-sm text-app-muted">No closed trades match this selection.</td></tr> : null}
        </tbody>
      </table>
    </div>
    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-app-muted">
      <span>{filtered.length ? `${number(start + 1)}–${number(Math.min(start + PAGE_SIZE, filtered.length))}` : "0"} of {number(filtered.length)} trades · page {activePage + 1} of {pages}</span>
      <div className="flex gap-2"><Button type="button" variant="secondary" disabled={activePage === 0} onClick={() => setPage(activePage - 1)}>Previous trades</Button><Button type="button" variant="secondary" disabled={activePage >= pages - 1} onClick={() => setPage(activePage + 1)}>Next trades</Button></div>
    </div>
  </div>;
}

function timestamp(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : timeFormatter.format(date);
}

function downloadText(filename: string, contents: string, type: string) {
  const url = URL.createObjectURL(new Blob([contents], { type }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
