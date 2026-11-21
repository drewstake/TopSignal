import { useEffect, useRef, useState, type FormEvent } from "react";
import { Button } from "../../components/ui/Button";
import { ApiError, requestJson } from "../../lib/api";

type Signal = { id: number; at: string; contract: string; imbalance: "bid-heavy" | "ask-heavy"; rule: string };
type SignalPage = { account_id: number; signals: Signal[]; day: string | null; previous_day: string | null;
  next_day: string | null; next_before: number | null };
type Exit = { at: string; price: number; reason: string; gross: number; fees: number; net: number };
type Outcome = { account_id: number; id: number; entry: number | null; side: string; quantity: number;
  exits: Record<string, Exit | undefined>; coverage: string | null };
type Settings = { stop_a: string; target_a: string; stop_b: string; target_b: string;
  quantity: number; direction: "following" | "contrarian"; timed: boolean };
const defaults: Settings = { stop_a: "25", target_a: "25", stop_b: "15", target_b: "30",
  quantity: 1, direction: "following", timed: false };
const et = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit",
  second: "2-digit", hourCycle: "h23" });
const dollars = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const side = (signal: Signal, direction: Settings["direction"]) =>
  (signal.imbalance === "bid-heavy") === (direction === "following") ? "Long" : "Short";

export function RecordedSignalReplay({ accountId }: { accountId: number }) {
  const [day, setDay] = useState("");
  return <section className="topbot-panel space-y-3" aria-labelledby="recorded-replay-title">
    <h2 id="recorded-replay-title" className="text-base font-semibold">Recorded live signal replay</h2>
    <details className="method-details"><summary>Details</summary>
      <p>Each signal is modeled independently using the recorded order book. Targets require one tick of trade-through. Stops execute after 250 ms with one tick of slippage.</p>
    </details>
    <SignalDay key={`${accountId}/${day}`} accountId={accountId} day={day} onDay={setDay} />
  </section>;
}

function SignalDay({ accountId, day, onDay }: { accountId: number; day: string; onDay: (day: string) => void }) {
  const [page, setPage] = useState<SignalPage | null>(null);
  const [before, setBefore] = useState<number | null>(null);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [pageError, setPageError] = useState(false);
  const [replayError, setReplayError] = useState("");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [settings, setSettings] = useState(defaults);
  const [sideFilter, setSideFilter] = useState("All");
  const [daytime, setDaytime] = useState(false);
  const [running, setRunning] = useState(false);
  const [run, setRun] = useState<{ settings: Settings; count: number; rows: Outcome[] } | null>(null);
  const active = useRef<AbortController | null>(null);
  const path = `/api/accounts/${accountId}/recorded-signals`;
  // Pin the resolved day when paging from the default "latest" view.
  const queryDay = day || (before === null ? null : page?.day);
  useEffect(() => {
    const controller = new AbortController();
    requestJson<SignalPage>(path, { query: { day: queryDay, before }, signal: controller.signal }).then(data => {
      if (controller.signal.aborted) return;
      if (data.account_id !== accountId || (queryDay && queryDay !== data.day)) throw new Error("Scope mismatch");
      setPage(old => before === null ? data : { ...data, signals: [...(old?.signals ?? []), ...data.signals] });
    }).catch(() => { if (!controller.signal.aborted) setPageError(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [accountId, path, queryDay, before, revision]);
  useEffect(() => () => active.current?.abort(), []);

  const shown = (page?.signals ?? []).filter(s => {
    const clock = et.format(new Date(s.at));
    return (!daytime || (clock >= "09:30" && clock < "16:00")) &&
      (sideFilter === "All" || side(s, settings.direction) === sideFilter);
  });
  const picked = shown.filter(s => selected.has(s.id));
  const allShown = shown.length > 0 && picked.length === shown.length;

  async function compare(event: FormEvent) {
    event.preventDefault();
    if (!picked.length || active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setReplayError(""); setRunning(true);
    setRun({ settings: { ...settings }, count: picked.length, rows: [] });
    try {
      // Keep reads sequential so a page of selections cannot fan out into concurrent archive scans.
      for (const signal of picked) {
        const row = await requestJson<Outcome>(`${path}/${signal.id}/replay`, { query: settings, signal: controller.signal });
        if (controller.signal.aborted) return;
        if (row.account_id !== accountId || row.id !== signal.id) throw new Error("Scope mismatch");
        setRun(old => old && { ...old, rows: [...old.rows, row] });
      }
    } catch (e) {
      if (!controller.signal.aborted) setReplayError(e instanceof ApiError && e.status === 429
        ? "Another replay is reading the capture. Try again shortly." : "Replay interrupted. Completed signals are shown below.");
    } finally {
      if (!controller.signal.aborted) setRunning(false);
      active.current = null;
    }
  }

  return <>
    <div className="topbot-comparison-filters">
      <Button size="sm" variant="ghost" disabled={!page?.previous_day} onClick={() => page?.previous_day && onDay(page.previous_day)}>Previous recorded day</Button>
      <label>Capture date (New York) <input type="date" value={day || page?.day || ""} onChange={e => onDay(e.target.value)} /></label>
      <Button size="sm" variant="ghost" disabled={!page?.next_day} onClick={() => page?.next_day && onDay(page.next_day)}>Next recorded day</Button>
    </div>
    {page && (!page.previous_day || !page.next_day) && <p className="text-xs text-app-muted">{!page.previous_day && "No earlier recorded day. "}{!page.next_day && "No later recorded day."}</p>}
    {loading && <p role="status">Loading recorded signals…</p>}
    {pageError && <p role="alert">Recorded signals could not be loaded. <Button size="sm" variant="ghost"
      onClick={() => { setLoading(true); setPageError(false); setRevision(v => v+1); }}>Retry</Button></p>}
    {page && !page.signals.length && <p>No live worker signals recorded{page.day ? " on this date" : " for this account"}.</p>}
    {!!page?.signals.length && <form onSubmit={compare}>
      <fieldset disabled={running} className="min-w-0 space-y-3">
        <div className="topbot-comparison-filters">
          <label>Replay direction <select value={settings.direction} onChange={e => setSettings({ ...settings, direction: e.target.value as Settings["direction"] })}>
            <option value="following">Follow imbalance</option><option value="contrarian">Fade imbalance</option></select></label>
          <label>Contracts <select value={settings.quantity} onChange={e => setSettings({ ...settings, quantity: Number(e.target.value) })}>
            <option>1</option><option>2</option></select></label>
          <label>Show trades <select value={sideFilter} onChange={e => setSideFilter(e.target.value)}><option>All</option><option>Long</option><option>Short</option></select></label>
          <label><input type="checkbox" checked={daytime} onChange={e => setDaytime(e.target.checked)} /> Daytime only (09:30–16:00 ET)</label>
        </div>
        <div className="topbot-stat-table max-h-64 overflow-auto"><table>
          <thead><tr><th><input type="checkbox" aria-label="Select all shown signals" checked={allShown}
            onChange={() => setSelected(new Set(allShown ? [] : shown.map(s => s.id)))} /></th>
            <th>Signal</th><th>Qualified (ET)</th><th>Contract</th><th>Imbalance</th><th>Replay side</th></tr></thead>
          <tbody>{shown.map(s => <tr key={s.id}><td><input aria-label={`Select signal ${s.id}`} type="checkbox" checked={selected.has(s.id)}
            onChange={e => { const next = new Set(selected); if (e.target.checked) next.add(s.id); else next.delete(s.id); setSelected(next); }} /></td>
            <th>#{s.id}</th><td>{et.format(new Date(s.at))}</td><td>{s.contract.split(".").slice(-2).join(".")}</td>
            <td>{s.imbalance}</td><td>{side(s, settings.direction)}</td></tr>)}</tbody>
        </table></div>
        {!shown.length && <p>No signals match these filters.</p>}
        {page.next_before !== null && <Button size="sm" variant="ghost" disabled={loading || pageError} onClick={() => {
          setLoading(true); setBefore(page.next_before);
        }}>Load more signals</Button>}
        <div className="flex flex-wrap gap-4">{(["a", "b"] as const).map(key => <fieldset key={key} className="rounded-lg border border-app-border px-3 pb-2">
          <legend className="px-1 text-sm font-semibold">Bracket {key.toUpperCase()}</legend>
          <div className="topbot-comparison-filters">{(["stop", "target"] as const).map(kind => {
            const field = `${kind}_${key}` as const;
            return <label key={field}>{kind === "stop" ? "Stop" : "Target"} (pts) <input type="number" className="w-20"
              required min=".25" max="10000" step=".25" value={settings[field]} onChange={e => setSettings({ ...settings, [field]: e.target.value })} /></label>;
          })}</div></fieldset>)}</div>
        <label className="block text-sm"><input type="checkbox" checked={settings.timed} onChange={e => setSettings({ ...settings, timed: e.target.checked })} /> Include 30-second, 2-minute and 5-minute exits</label>
        <Button type="submit" disabled={running || !picked.length}>{running ? "Replaying…" : `Compare brackets (${picked.length})`}</Button>
      </fieldset>
      {!picked.length && <p className="text-sm text-app-muted">Select a signal to compare brackets.</p>}
      <p className="topbot-footnote">Replay uses $2 per MNQ point and $1.22 round-trip fees per contract. Gaps leave open exits unresolved.</p>
    </form>}
    {replayError && <p role="alert">{replayError}</p>}
    {run && <ReplayResults settings={run.settings} rows={run.rows} count={run.count} />}
  </>;
}

function ReplayResults({ settings, rows, count }: { settings: Settings; rows: Outcome[]; count: number }) {
  const paired = rows.filter(r => r.exits.a && r.exits.b);
  const total = (key: string) => paired.length ? dollars.format(paired.reduce((sum, row) => sum + row.exits[key]!.net, 0)) : "—";
  const columns = [["a", `A · ${settings.stop_a} stop / ${settings.target_a} target`], ["b", `B · ${settings.stop_b} stop / ${settings.target_b} target`]];
  if (settings.timed) columns.push(["30", "30 sec"], ["120", "2 min"], ["300", "5 min"]);
  return <section aria-label="Hypothetical replay results" className="space-y-2">
    <h3 className="font-semibold">Hypothetical results after fees</h3>
    <p role="status">{rows.length} of {count} signals processed · {settings.quantity} MNQ · {settings.direction === "following" ? "Follow" : "Fade"} imbalance</p>
    <p className="topbot-muted">Net on {paired.length} signals with both exits resolved · A {total("a")} · B {total("b")}</p>
    <div className="topbot-stat-table"><table><thead><tr><th>Signal</th><th>Side / entry</th>{columns.map(([k, label]) => <th key={k}>{label}</th>)}</tr></thead>
      <tbody>{rows.map(r => <tr key={r.id}><th>#{r.id}</th><td>{r.side} · {r.entry ?? "—"}</td>
        {columns.map(([k]) => { const exit = r.exits[k]; return <td key={k}>{exit
          ? `${dollars.format(exit.net)} · ${exit.reason} @ ${exit.price}`
          : `Unresolved · ${r.coverage ?? "No quote in exit window"}`}</td>; })}</tr>)}</tbody></table></div>
  </section>;
}
