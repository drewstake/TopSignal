import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Button } from "../../components/ui/Button";
import { requestJson } from "../../lib/api";
import { DatabentoChart } from "./DatabentoChart";
import { TopBotBacktestPanel } from "./TopBotBacktestPanel";
import type { ChartWindow, Timeframe, HistoryRange } from "./historyChartData";
type Query = { timeframe: Timeframe; start?: string; end?: string };
const day = (value: string) => value.slice(0, 10);
const formatDay = (value: string) => new Date(value).toLocaleDateString("en-US", { timeZone: "UTC", month: "short", day: "numeric", year: "numeric" });
const endOfDay = (value: string) => new Date(new Date(`${value}T00:00:00Z`).getTime() + 86400000).toISOString();
const inputClass = "h-10 rounded-xl border border-app-border bg-app-bg px-3 text-sm text-app-text focus:outline-none focus:ring-2 focus:ring-app-accent";

export function BacktestingPage() {
  const [query, setQuery] = useState<Query>({ timeframe: "5m" });
  const [data, setData] = useState<ChartWindow | null>(null);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [initialView, setInitialView] = useState<"candles" | "overview">("candles");
  const [initialAnchor, setInitialAnchor] = useState<number | undefined>();
  const viewport = useRef<HistoryRange | null>(null);
  const rememberViewport = useCallback((range: HistoryRange) => { viewport.current = range; }, []);

  const load = (next: Query, view: "candles" | "overview" = "candles", preserveDate = false) => {
    setInitialAnchor(preserveDate && viewport.current ? (viewport.current.from + viewport.current.to) / 2 : undefined);
    setInitialView(view);
    setLoading(true);
    setError(null);
    setQuery(next);
  };

  useEffect(() => {
    const controller = new AbortController();
    requestJson<ChartWindow>("/api/market-data/databento/candles", { query: { ...query, overview: true, limit: 1000 }, signal: controller.signal }).then((result) => {
      if (controller.signal.aborted) return;
      setData(result);
      setStart(day(result.start));
      setEnd(day(new Date(new Date(result.end).getTime() - 1).toISOString()));
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      const message = reason instanceof Error ? reason.message : "";
      setError(message.includes("databento_chart_history_missing")
        ? "The local MNQ history or selected timeframe is not installed on this backend."
        : message.includes("databento_chart_cache_unavailable")
          ? "The local Databento cache could not be read. Check the cache files before retrying."
          : "Could not load historical candles. Check that the backend is running and try again.");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [query]);

  const apply = (event: FormEvent) => {
    event.preventDefault();
    if (!start || !end || start > end) {
      setError("Choose a start date on or before the end date.");
      return;
    }
    load({ timeframe: query.timeframe, start: `${start}T00:00:00Z`, end: endOfDay(end) });
  };

  return <section className="space-y-5" aria-labelledby="backtesting-title">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><p className="text-xs font-semibold uppercase tracking-[0.18em] text-app-accent">Historical research</p><h1 id="backtesting-title" className="mt-1 text-2xl font-semibold text-app-text">Backtesting</h1><p className="mt-2 text-sm text-app-muted">Explore MNQ price action with Databento historical candles.</p></div>
      <a href="#topbot-backtest" className="rounded-full border border-app-accent/30 bg-app-accent/10 px-3 py-1.5 text-xs font-medium text-app-accent">Run TopBot ↓</a>
    </div>

    <div className="grid gap-3 sm:grid-cols-3">
      {[["Instrument", "MNQ", "Micro E-mini Nasdaq-100 futures"],
        ["Available history", data ? `${formatDay(data.coverage_start)} – ${formatDay(data.coverage_end)}` : "—", "Local Databento coverage"],
        ["Candles in range", data ? data.total.toLocaleString() : "—", data ? `${data.timeframe} bars · All available to explore` : "Closed historical bars"]].map(([label, value, detail]) =>
        <div key={label} className="rounded-2xl border border-app-border bg-app-surface/50 p-4"><p className="text-xs text-app-muted">{label}</p><p className="mt-2 text-base font-semibold tabular-nums text-app-text">{value}</p><p className="mt-1 text-xs text-app-muted">{detail}</p></div>)}
    </div>

    <form onSubmit={apply} className="flex flex-wrap items-end gap-3 rounded-2xl border border-app-border bg-app-surface/50 p-4">
      <fieldset disabled={loading} className="mr-auto"><legend className="mb-2 text-xs text-app-muted">Timeframe</legend><div className="flex gap-1">
        {(["1m", "5m", "4h", "1d"] as const).map((timeframe) => <Button key={timeframe} size="md" variant={query.timeframe === timeframe ? "primary" : "ghost"} aria-pressed={query.timeframe === timeframe} onClick={() => load({ ...query, timeframe }, "candles", true)}>{timeframe}</Button>)}
      </div></fieldset>
      <label className="grid gap-2 text-xs text-app-muted">From (UTC)<input aria-label="From date" type="date" required min={data ? day(data.coverage_start) : "2010-01-01"} max={data ? day(data.coverage_end) : "2100-12-30"} value={start} onChange={(event) => setStart(event.target.value)} className={inputClass} /></label>
      <label className="grid gap-2 text-xs text-app-muted">Through (UTC)<input aria-label="Through date" type="date" required min={data ? day(data.coverage_start) : "2010-01-01"} max={data ? day(data.coverage_end) : "2100-12-30"} value={end} onChange={(event) => setEnd(event.target.value)} className={inputClass} /></label>
      <Button type="submit" disabled={loading || !start || !end}>{loading ? "Loading…" : "Load candles"}</Button>
      <Button variant="secondary" disabled={loading} onClick={() => load({ timeframe: query.timeframe }, "overview")}>All history</Button>
    </form>

    {error && <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-app-negative/40 bg-app-negative/10 p-4 text-sm text-app-text"><span>{error}</span><Button variant="ghost" size="sm" onClick={() => load({ ...query })}>Retry</Button></div>}
    {loading ? <div role="status" className="flex h-[520px] items-center justify-center rounded-2xl border border-app-border bg-app-surface/50 text-sm text-app-muted">Loading Databento candles…</div>
      : !error && data && <>
        {data.candles.length ? <DatabentoChart key={`${data.timeframe}:${data.start}:${data.end}`} history={data} initialView={initialView} initialAnchor={initialAnchor} onViewportChange={rememberViewport} />
          : <div role="status" className="flex h-80 items-center justify-center rounded-2xl border border-app-border bg-app-surface/50 px-6 text-center text-sm text-app-muted">No closed candles in this range. Try another date or timeframe; futures markets close for weekends and holidays.</div>}
        {data.candles.length > 0 && <p className="text-xs text-app-muted">Range: {formatDay(data.start)} – {formatDay(new Date(new Date(data.end).getTime() - 1).toISOString())}. Zoom in anywhere for individual {data.timeframe} candles. Prices follow the selected futures delivery; contract rolls can create gaps.</p>}
      </>}
    {data && <TopBotBacktestPanel coverageEnd={data.coverage_end} chartStart={data.start} chartEnd={data.end}/>}
  </section>;
}
