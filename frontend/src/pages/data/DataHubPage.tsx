import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useOutletContext } from "react-router-dom";
import type { AppShellOutletContext } from "../../app/AppShell";
import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { requestJson } from "../../lib/api";
import { useDemoMode } from "../../lib/demoMode";
import type { DataInventory, MarketContext, MarketEvent, MarketEvents, Observations, DecisionResearch, PublicMarketStatus } from "./dataTypes";
import { DecisionDetails } from "./DecisionDetails";

type Section = "coverage" | "events" | "flow" | "research";
type ReadKey = "inventory" | "context" | "events" | "flow" | "research" | "public";
const time = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const day = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", year: "numeric" });
function stamp(value: string | null | undefined) { return value && Number.isFinite(Date.parse(value)) ? time.format(new Date(value)) : "—"; }
function amount(value: number | null | undefined) { return value == null || !Number.isFinite(value) ? "—" : number.format(value); }
function label(value: string) { return value.replaceAll("_", " "); }
function safeLink(value: string | null) { try { const url = new URL(value ?? ""); return url.protocol === "https:" ? url.href : undefined; } catch { return undefined; } }
function eventWhen(event: MarketEvent) {
  if (event.time_precision !== "date") return stamp(event.scheduled_at ?? event.published_at);
  const start = event.scheduled_date ? Date.parse(`${event.scheduled_date}T12:00:00Z`) : NaN;
  if (!Number.isFinite(start)) return "Date only";
  const first = day.format(start);
  const end = event.scheduled_end_at ? Date.parse(event.scheduled_end_at) - 1 : NaN;
  const last = Number.isFinite(end) ? day.format(end) : first;
  return `${first}${last === first ? "" : ` – ${last}`} · date only`;
}

function Status({ value }: { value: string }) {
  const good = ["fresh", "connected", "available", "updated", "trade"].includes(value);
  return <span className={`inline-flex rounded-full border px-2 py-0.5 text-xs capitalize ${good ? "border-app-positive/30 bg-app-positive/10 text-app-positive" : "border-app-border bg-app-bg text-app-muted"}`}>{label(value)}</span>;
}
function Metric({ title, value, detail }: { title: string; value: ReactNode; detail?: string }) {
  return <div className="rounded-xl border border-app-border bg-app-bg/50 p-4"><p className="text-xs text-app-muted">{title}</p><p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>{detail && <p className="mt-1 text-xs text-app-muted">{detail}</p>}</div>;
}
function GridTable({ headers, children }: { headers: string[]; children: ReactNode }) {
  return <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b border-app-border text-xs text-app-muted">{headers.map(h => <th key={h} className="whitespace-nowrap px-3 py-3 font-medium">{h}</th>)}</tr></thead><tbody className="divide-y divide-app-border">{children}</tbody></table></div>;
}
const cell = "px-3 py-3 align-top";

export function DataHubPage() {
  const { selectedAccountId } = useOutletContext<AppShellOutletContext>();
  const { enabled: demo } = useDemoMode();
  const [section, setSection] = useState<Section>("coverage");
  const [version, setVersion] = useState(0);
  const [inventory, setInventory] = useState<DataInventory | null>(null);
  const [contextResult, setContextResult] = useState<{ live: boolean; data: MarketContext } | null>(null);
  const [publicMarkets, setPublicMarkets] = useState<PublicMarketStatus | null>(null);
  const [events, setEvents] = useState<MarketEvents | null>(null);
  const [flowResult, setFlowResult] = useState<{ contract: string; data: Observations } | null>(null);
  const [research, setResearch] = useState<{ account: number; data: DecisionResearch } | null>(null);
  const [errors, setErrors] = useState<Partial<Record<ReadKey, string>>>({});
  const [pending, setPending] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [symbol, setSymbol] = useState("all");
  const [live, setLive] = useState(false);
  const [flowContract, setFlowContract] = useState("");
  const [recordedContracts, setRecordedContracts] = useState<string[]>([]);
  const operation = useRef(0);
  const mounted = useRef(true);
  const activeAccount = useRef(selectedAccountId);
  activeAccount.current = selectedAccountId;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  useEffect(() => {
    if (demo) return;
    const controller = new AbortController();
    const signal = controller.signal;
    const read = <T,>(key: ReadKey, path: string, apply: (data: T) => void, query?: Record<string, string | number | boolean>) => {
      void requestJson<T>(path, { signal, query }).then(data => {
        if (signal.aborted) return;
        apply(data); setErrors(old => ({ ...old, [key]: undefined }));
      }).catch((error: unknown) => {
        if (!signal.aborted) setErrors(old => ({ ...old, [key]: error instanceof Error ? error.message : "Unable to load data" }));
      });
    };
    read("inventory", "/api/market-data/inventory", setInventory);
    read<MarketContext>("context", "/api/market-data/context", data => setContextResult({ live, data }), { live });
    read("public", "/api/market-data/public-status", setPublicMarkets);
    read("events", "/api/market-events", setEvents, { end: new Date(Date.now() + 45 * 86400000).toISOString() });
    read<Observations>("flow", "/api/market-observations/status", data => {
      setFlowResult({ contract: flowContract, data });
      if (!flowContract) setRecordedContracts(data.contracts ?? []);
    }, flowContract ? { contract_id: flowContract } : undefined);
    if (selectedAccountId !== null) read<DecisionResearch>("research", "/api/decision-research", data => setResearch({ account: selectedAccountId, data }), { account_id: selectedAccountId, limit: 100 });
    return () => controller.abort();
  }, [selectedAccountId, version, live, demo, flowContract]);

  async function run<T>(name: string, path: string, body: unknown, describe: (result: T) => string) {
    if (pending || demo) return;
    const token = ++operation.current;
    const account = selectedAccountId;
    setPending(name); setMessage(null);
    try {
      const result = await requestJson<T>(path, { method: "POST", body });
      if (!mounted.current || token !== operation.current) return;
      if (account === activeAccount.current) setMessage(describe(result));
      setVersion(v => v + 1);
    } catch (error) {
      if (mounted.current && token === operation.current && account === activeAccount.current) setMessage(error instanceof Error ? error.message : "The operation could not finish.");
    } finally { if (mounted.current && token === operation.current) setPending(null); }
  }
  const currentResearch = research?.account === selectedAccountId ? research.data : null;
  const context = contextResult?.live === live ? contextResult.data : null;
  const flow = flowResult?.contract === flowContract ? flowResult.data : null;
  const streams = inventory?.streams.filter(s => symbol === "all" || s.root_symbol === symbol) ?? [];
  const roots = [...new Set(inventory?.streams.map(s => s.root_symbol) ?? [])].sort();
  const archivedMinutes = inventory?.archive.series.filter(s => s.timeframe === "1m").reduce((sum, s) => sum + s.rows, 0);
  const enabled = !pending && !demo;

  return <div className="space-y-5 pb-10 text-app-text">
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div><p className="text-xs font-medium uppercase tracking-widest text-app-muted">Market intelligence</p><h1 className="mt-1 text-3xl font-semibold tracking-tight">Data hub</h1><p className="mt-2 max-w-3xl text-sm text-app-muted">See what you have, collect missing context, and connect decisions to outcomes. Times are Eastern.</p></div>
      <Button variant="secondary" disabled={!enabled} onClick={() => setVersion(v => v + 1)}>Reload status</Button>
    </div>
    {demo && <p role="status" className="rounded-xl border border-app-border p-4 text-sm text-app-muted">Data collection is unavailable in Demo Mode. Leave Demo Mode to view your stored market data.</p>}
    {message && <p role="status" className="rounded-xl border border-app-border bg-app-surface p-4 text-sm">{message}</p>}
    {pending && <p role="status" className="text-sm text-app-muted">{pending}…</p>}
    {Object.entries(errors).filter(([, error]) => error).map(([key, error]) => <p role="alert" key={key} className="text-sm text-app-negative">{label(key)}: {error}</p>)}
    {!demo && <>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric title="Stored price & reference rows" value={amount(inventory?.database_rows)} detail="Includes multiple timeframes and daily references" />
        <Metric title="Archive · continuous 1-minute bars" value={amount(archivedMinutes)} detail={inventory?.archive.status === "available" ? "Databento · separate historical source" : "Archive status not available"} />
        <Metric title="Recorded market observations" value={amount(flow?.event_count)} detail={flow ? `${label(flow.capture_mode)} · ${flow.retention_days}-day retention` : "Waiting for recorder status"} />
        <Metric title="Macro event coverage" value={events ? <Status value={events.risk.level} /> : "—"} detail={events?.risk.coverage_trusted ? "Calendar coverage verified for the risk window" : "Partial or unavailable coverage remains explicit"} />
      </div>
      <nav aria-label="Data sections" className="flex flex-wrap gap-2">{([['coverage', 'History & sources'], ['events', 'Calendar & news'], ['flow', 'Quotes & flow'], ['research', 'Decision research']] as const).map(([value, title]) => <button key={value} type="button" aria-pressed={section === value} onClick={() => setSection(value)} className={`rounded-lg border px-4 py-2 text-sm ${section === value ? "border-app-accent bg-app-accent/10 text-app-text" : "border-app-border text-app-muted"}`}>{title}</button>)}</nav>
      {section === "coverage" && <Card><CardHeader><div className="flex flex-wrap items-center justify-between gap-3"><div><CardTitle>Related markets</CardTitle><CardDescription>Stored futures prices and daily reference observations. Changes compare consecutive observations from the same source.</CardDescription></div><label className="flex items-center gap-2 text-xs text-app-muted">Futures data mode<select aria-label="Provider data mode" className="rounded-lg border border-app-border bg-app-bg p-2 text-app-text" value={String(live)} onChange={e => setLive(e.target.value === "true")}><option value="false">Simulated</option><option value="true">Live</option></select></label></div></CardHeader><CardContent>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{context?.items.map(item => <div key={item.symbol} className="rounded-xl border border-app-border p-3"><div className="flex justify-between gap-2"><span className="font-semibold">{item.symbol}</span><Status value={item.status} /></div><p className="mt-3 text-xl tabular-nums">{amount(item.close)}{item.symbol === "US10Y" && item.close != null ? "%" : ""} <span className="text-sm text-app-muted">{item.change_bps != null ? `${item.change_bps >= 0 ? "+" : ""}${amount(item.change_bps)} bps` : item.change_pct == null ? "" : `${item.change_pct >= 0 ? "+" : ""}${amount(item.change_pct)}%`}</span></p><p className="mt-1 text-xs text-app-muted">{item.observation_date ? `Daily reference · ${item.observation_date}` : `${item.timeframe ?? "No stored source"} · ${stamp(item.candle_timestamp)}`}</p>{item.source && <p className="mt-1 text-xs text-app-muted">{label(item.source)}</p>}</div>)}</div>
        {context?.note && <details className="mt-3 text-xs text-app-muted"><summary className="cursor-pointer">Source timing and calculation notes</summary><p className="mt-2">{context.note}</p></details>}
        <div className="mt-4 flex flex-wrap items-center gap-3"><Button disabled={!enabled} onClick={() => void run<{items: {symbol: string; status: string; inserted_rows: number}[]}>("Collecting recent candles", "/api/market-data/refresh", { symbols: ["MNQ", "MES", "NQ", "ES"], days: 3, live }, r => r.items.map(i => `${i.symbol}: ${label(i.status)} (${amount(i.inserted_rows)} new bars)`).join(" · "))}>Collect recent candles</Button><span className="text-xs text-app-muted">Last 3 days · existing ProjectX connection</span></div>
        <div className="mt-5 border-t border-app-border pt-4">
          <div className="flex flex-wrap items-center gap-3"><Button variant="secondary" disabled={!enabled || !publicMarkets?.sources.some(source => source.enabled)} onClick={() => void run<{items: {symbol:string; status:string; inserted_rows:number; detail:string}[]}>("Collecting public references", "/api/market-data/refresh-public", { symbols: publicMarkets?.sources.filter(source => source.enabled).map(source => source.symbol) ?? [], days: 365 }, r => r.items.map(item => `${item.symbol}: ${label(item.status)} (${amount(item.inserted_rows)} new observations). ${item.detail}`).join(" · "))}>Collect public references</Button><span className="text-xs text-app-muted">Daily context · up to 1 year · enabled sources only</span></div>
          <div className="mt-3 grid gap-3 md:grid-cols-2">{publicMarkets?.sources.map(source => <div key={source.source} className="rounded-lg border border-app-border p-3"><div className="flex items-center justify-between gap-2"><a className="text-sm font-medium underline decoration-app-border underline-offset-4" href={safeLink(source.source_url)} target="_blank" rel="noreferrer">{source.label}</a><Status value={source.status} /></div><p className="mt-2 text-xs text-app-muted">{amount(source.stored_rows)} observations · Latest date: {source.latest_observation_date ?? "—"}</p><p className="mt-1 text-xs text-app-muted">{source.data_notice}</p></div>)}</div>
        </div>
      </CardContent></Card>}
      {section === "coverage" && <>
        <Card><CardHeader><CardTitle>Application history</CardTitle><CardDescription>First and last timestamps describe stored bounds; they do not prove complete coverage.</CardDescription></CardHeader><CardContent>
          <label className="mb-3 flex items-center gap-2 text-sm">Instrument<select aria-label="History instrument" className="rounded-lg border border-app-border bg-app-bg p-2" value={symbol} onChange={e => setSymbol(e.target.value)}><option value="all">All instruments</option>{roots.map(root => <option key={root}>{root}</option>)}</select></label>
          <GridTable headers={["Contract", "Timeframe", "Source / mode", "Closed / total", "First candle", "Last candle", "Last fetched", "Recent missing minutes"]}>{streams.map(s => <tr key={`${s.contract_id}-${s.unit}-${s.unit_number}-${s.live}-${s.source}`}><td className={cell}>{s.symbol ?? s.contract_id}<p className="mt-1 text-xs text-app-muted">{s.contract_id}</p></td><td className={cell}>{s.unit_number} {s.unit}</td><td className={cell}>{s.source} {s.source === "projectx" ? s.live ? "/ live" : "/ simulated" : "/ daily reference"}</td><td className={cell}>{amount(s.complete_rows)} / {amount(s.rows)}</td><td className={`${cell} whitespace-nowrap`}>{stamp(s.first_timestamp)}</td><td className={`${cell} whitespace-nowrap`}>{stamp(s.last_timestamp)}</td><td className={`${cell} whitespace-nowrap`}>{stamp(s.last_fetched_at)}</td><td className={cell} title={s.recent_gap_check?.note}>{s.recent_gap_check ? `${amount(s.recent_gap_check.missing_open_minutes)} / ${amount(s.recent_gap_check.expected_open_minutes)} scheduled` : "—"}</td></tr>)}</GridTable>
          {!streams.length && <p className="py-6 text-sm text-app-muted">No application candles in this selection.</p>}
        </CardContent></Card>
        <div className="grid gap-5 xl:grid-cols-2"><Card><CardHeader><CardTitle>Historical archive</CardTitle><CardDescription>{inventory?.archive.note ?? "Loading archive inventory…"}</CardDescription></CardHeader><CardContent><GridTable headers={["Instrument", "Timeframe", "Bars", "First", "End (exclusive)"]}>{inventory?.archive.series.map(s => <tr key={`${s.symbol}-${s.timeframe}`}><td className={cell}>{s.symbol}</td><td className={cell}>{s.timeframe}</td><td className={cell}>{amount(s.rows)}</td><td className={cell}>{stamp(s.first_timestamp)}</td><td className={cell}>{stamp(s.end_exclusive)}</td></tr>)}</GridTable><p className="mt-3 text-xs text-app-muted">Schemas: {Object.entries(inventory?.archive.schemas ?? {}).map(([key, rows]) => `${label(key)} ${amount(rows)}`).join(" · ") || "None"}</p></CardContent></Card>
        <Card><CardHeader><CardTitle>Additional captured history</CardTitle><CardDescription>{inventory?.local_capture.note ?? "Checking local capture…"}</CardDescription></CardHeader><CardContent><p className="text-2xl font-semibold">{amount(inventory?.local_capture.rows)} <span className="text-sm font-normal text-app-muted">source minute bars</span></p><p className="mt-2 text-sm text-app-muted">{stamp(inventory?.local_capture.first_timestamp)} → {stamp(inventory?.local_capture.last_timestamp)}</p><p className="mt-2 text-sm">{amount(inventory?.local_capture.matching_database_rows)} matching bars already stored</p><p className="mt-3 text-xs text-app-muted">{inventory?.local_capture.research_exposure}</p><Button className="mt-4" disabled={!enabled || inventory?.local_capture.status !== "available"} onClick={() => void run<{inserted_rows:number; unchanged_rows:number; conflicting_rows:number}>("Importing verified captured history", "/api/market-data/import-local-history", undefined, r => `History import: ${amount(r.inserted_rows)} added, ${amount(r.unchanged_rows)} already present, ${amount(r.conflicting_rows)} conflicts preserved.`)}>Import captured history</Button></CardContent></Card></div>
        <Card><CardHeader><CardTitle>Feed availability</CardTitle><CardDescription>Available code and connected data are tracked separately.</CardDescription></CardHeader><CardContent><div className="grid gap-3 md:grid-cols-2">{inventory?.feeds.map(feed => <div key={feed.key} className="rounded-xl border border-app-border p-4"><div className="flex justify-between gap-2"><h3 className="text-sm font-medium">{feed.label}</h3><Status value={feed.status} /></div><p className="mt-2 text-xs text-app-muted">{feed.detail}</p></div>)}</div></CardContent></Card>
      </>}
      {section === "events" && <>
        <Card><CardHeader><div className="flex flex-wrap items-center justify-between gap-3"><div><CardTitle>Calendar & news</CardTitle><CardDescription>Recent publications and the next 45 days. Date-only events have no assumed announcement time.</CardDescription></div><Button disabled={!enabled} onClick={() => void run("Refreshing official sources", "/api/market-events/refresh", {}, () => "Source refresh finished. Review individual source status below.")}>Refresh sources</Button></div></CardHeader><CardContent>
          {events && <p className="mb-4 rounded-lg bg-app-bg/60 p-3 text-sm"><strong>Event risk: {events.risk.level}.</strong> {events.risk.reason}</p>}
          <div className="mb-5 grid gap-3 md:grid-cols-2">{events?.sources.map(source => <div key={source.source} className="rounded-xl border border-app-border p-3"><div className="flex items-center justify-between gap-2"><h3 className="text-sm font-medium">{source.label}</h3><Status value={source.status} /></div><p className="mt-2 text-xs text-app-muted">{source.coverage_scope} · {amount(source.event_count)} events</p><p className="mt-1 text-xs text-app-muted">Last success: {stamp(source.last_success_at)}</p>{source.error_code && <p className="mt-1 text-xs text-app-muted">{label(source.error_code)}</p>}<p className="mt-1 text-xs text-app-muted">Actuals: {source.actuals_available ? "available" : "unavailable"} · Consensus: {source.consensus_available ? "available" : "unavailable"}</p></div>)}</div>
          <GridTable headers={["When (ET)", "Event / headline", "Status", "Actual", "Consensus", "Previous", "First observed"]}>{events?.events.map(event => <tr key={`${event.source}-${event.id}`}><td className={`${cell} min-w-36`}>{eventWhen(event)}</td><td className={`${cell} min-w-64 max-w-xl`}>{safeLink(event.url) ? <a className="font-medium text-app-text underline decoration-app-border underline-offset-4 hover:text-app-accent" href={safeLink(event.url)} target="_blank" rel="noreferrer">{event.title}</a> : event.title}<p className="mt-1 text-xs text-app-muted">{label(event.source)} · {event.importance}</p></td><td className={cell}><Status value={event.state} /></td><td className={cell}>{event.actual ?? "—"}</td><td className={cell}>{event.forecast ?? "—"}</td><td className={cell}>{event.previous ?? "—"}{event.revised && <p className="text-xs text-app-muted">Before revision: {event.revised}</p>}</td><td className={cell}>{stamp(event.first_seen_at)}</td></tr>)}</GridTable>
          {!events?.events.length && <p className="py-6 text-sm text-app-muted">No stored events in this window. Refresh sources to collect available publications and schedules.</p>}
        </CardContent></Card>
      </>}
      {section === "flow" && <Card><CardHeader><CardTitle>Recorded quotes & trade flow</CardTitle><CardDescription>Real observations collected through the order-book connection. Open the Bot order book during market hours to collect updates.</CardDescription></CardHeader><CardContent>
        <label className="mb-4 flex items-center gap-2 text-sm">Recorded contract<select aria-label="Recorded contract" value={flowContract} onChange={event => setFlowContract(event.target.value)} className="rounded-lg border border-app-border bg-app-bg p-2"><option value="">All contracts</option>{[...new Set([...recordedContracts, ...(flow?.contracts ?? []), ...(flowContract ? [flowContract] : [])])].map(contract => <option key={contract}>{contract}</option>)}</select></label>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"><Metric title="Last observation" value={<span className="text-base">{stamp(flow?.last_received_at)}</span>} /><Metric title="Latest spread (price points)" value={amount(flow?.spread.latest)} /><Metric title="Recorded trade prints" value={amount(flow?.profile.trade_count)} /><Metric title="Classified trade delta" value={amount(flow?.profile.delta)} detail="Unavailable if aggressor classification is not established" /></div>
        <div className="mt-4 flex flex-wrap gap-3 text-xs text-app-muted">{Object.entries(flow?.counts ?? {}).map(([key, count]) => <span key={key}>{label(key)}: {amount(count)}</span>)}<span>Queued: {amount(flow?.queued)}</span><span>Dropped: {amount(flow?.dropped)}</span><span>Write errors: {amount(flow?.write_errors)}</span></div>
        {flow?.warnings.map(w => <p key={w} className="mt-3 text-sm text-app-muted">{w}</p>)}
        <p className="mt-4 text-xs text-app-muted">{flow?.profile.basis}</p>
        {(flow?.profile.levels.length ?? 0) > 0 && <div className="mt-4 max-h-96 overflow-auto"><GridTable headers={["Price", "Executed volume"]}>{flow?.profile.levels.map(level => <tr key={level.price}><td className={cell}>{amount(level.price)}</td><td className={cell}>{amount(level.volume)}</td></tr>)}</GridTable></div>}
        <Link className="mt-5 inline-block text-sm text-app-accent underline" to={`/bot${selectedAccountId ? `?account=${selectedAccountId}` : ""}`}>Open order book</Link>
      </CardContent></Card>}
      {section === "research" && <Card><CardHeader><div className="flex flex-wrap items-center justify-between gap-3"><div><CardTitle>Decision research</CardTitle><CardDescription>New decisions preserve their original context. Heuristic scores are not calibrated win probabilities.</CardDescription></div><Button disabled={!enabled || selectedAccountId === null} onClick={() => void run("Evaluating observed outcomes", `/api/decision-research/evaluate?account_id=${selectedAccountId}`, undefined, () => "Available outcomes evaluated using stored closed minute candles.")}>Evaluate outcomes</Button></div></CardHeader><CardContent>
        {selectedAccountId === null ? <p className="text-sm text-app-muted">Select an account to view its decision records.</p> : <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"><Metric title="Recorded decisions" value={amount(currentResearch?.summary.total)} /><Metric title="Labeled outcomes" value={amount(currentResearch?.summary.labeled)} /><Metric title="Pending outcomes" value={amount(currentResearch?.summary.pending)} /><Metric title="Ambiguous outcomes" value={amount(currentResearch?.summary.ambiguous)} /></div>
          <GridTable headers={["Observed (ET)", "Contract", "Action", "Score", "Plan: entry / stop / target", "Outcome"]}>{currentResearch?.items.map(d => <tr key={d.id}><td className={cell}>{stamp(d.observed_at)}</td><td className={cell}>{d.contract_id}</td><td className={cell}>{d.action}<p className="max-w-sm text-xs text-app-muted">{d.reason}</p></td><td className={cell}>{amount(d.score)}</td><td className={cell}>{amount(d.entry_price)} / {amount(d.stop_loss)} / {amount(d.take_profit)}</td><td className={cell}><Status value={d.outcome} /><div className="mt-2"><DecisionDetails key={`${selectedAccountId}-${d.id}`} id={d.id} accountId={selectedAccountId} /></div></td></tr>)}</GridTable>
          {!currentResearch?.items.length && <p className="py-6 text-sm text-app-muted">No captured decisions for this account yet. Records are created as the bot evaluates setups; old decisions are not assigned invented historical context.</p>}
          {Boolean(currentResearch?.score_buckets?.length) && <div className="mt-5"><h3 className="font-medium">Outcomes by original score</h3><p className="mt-1 text-xs text-app-muted">Hypothetical target/stop sequence among resolved cases, not live returns. Other outcomes remain counted separately.</p><GridTable headers={["Score", "Target first", "Stop first", "Other", "Target-first rate"]}>{currentResearch?.score_buckets?.map(bucket => <tr key={bucket.minimum_score}><td className={cell}>{bucket.minimum_score}–{bucket.maximum_score}</td><td className={cell}>{amount(bucket.target)}</td><td className={cell}>{amount(bucket.stop)}</td><td className={cell}>{amount(bucket.other)}</td><td className={cell}>{bucket.target_first_rate === null ? "—" : `${amount(bucket.target_first_rate * 100)}% (n=${bucket.resolved_barrier_count})`}</td></tr>)}</GridTable></div>}
          <h3 className="mt-5 font-medium">Execution observations</h3><p className="mt-2 text-sm">Matched orders: {amount(currentResearch?.execution.matched_orders)} / {amount(currentResearch?.execution.order_attempts)} · Fill records: {amount(currentResearch?.execution.matched_fill_count)}</p><p className="mt-2 text-sm">Average signed decision-to-fill price difference: {amount(currentResearch?.execution.mean_signed_price_difference)} points</p>{currentResearch?.execution.limitations.map(note => <p className="mt-2 text-xs text-app-muted" key={note}>{note}</p>)}
        </>}
      </CardContent></Card>}
    </>}
  </div>;
}
