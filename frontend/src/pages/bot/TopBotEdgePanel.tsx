import { useState } from "react";
import { performance, type EdgeReport, type Inclusion, type ReportState, type SignalStudy } from "./imbalanceReport";

const money = (v: number | null) => v == null ? "—" : v.toLocaleString("en-US", { style: "currency", currency: "USD" });
const pct = (v: number | null) => v == null ? "—" : (v*100).toFixed(1)+"%";
const fmt = (v: number | null | undefined) => v == null ? "—" : v.toFixed(2);
const time = (v: string) => new Date(v).toLocaleString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit" })+" ET";
const ticks = (a: number | null, b: number | null, side: string) =>
  a == null || b == null ? "—" : fmt((b-a)*(side === "BUY" ? 4 : -4));

export function TopBotEdgePanel({ state, now }: { state: ReportState; now: number }) {
  return <section className="topbot-panel" aria-label="Bot profitability">
    <h2 className="text-base font-semibold">Bot profitability</h2>
    {state.message && <p role="status">{state.message}</p>}
    {state.data ? <PerformanceView data={state.data} now={now} /> : !state.message && <p>Checking closed trades and signal observations…</p>}
  </section>;
}

function PerformanceView({ data, now }: { data: EdgeReport; now: number }) {
  const [mode, setMode] = useState("fixed");
  const [version, setVersion] = useState("");
  const trades = data.actual.filter(t => (!mode || t.mode === mode) && (!version || t.version === version));
  const stats = performance(trades);
  const versions = [...new Set(data.actual.filter(t => !mode || t.mode === mode).map(t => t.version))];
  const delta = stats.rate != null && stats.breakeven != null ? (stats.rate-stats.breakeven)*100 : null;
  return <>
    <div className="topbot-comparison-filters">
      <label>Exit plan <select value={mode} onChange={e => { setMode(e.target.value); setVersion(""); }}>
        <option value="fixed">Fixed bracket</option><option value="trailing">Trailing stop</option><option value="">All saved plans</option></select></label>
      <label>Saved strategy version <select value={version} onChange={e => setVersion(e.target.value)}>
        <option value="">All versions</option>{versions.map(v => <option key={v} value={v}>{v}</option>)}</select></label>
    </div>
    <p className="topbot-muted">{stats.count} closed practice trades · {stats.sessions} trading sessions · {stats.win} wins / {stats.loss} losses / {stats.flats} flat
      {" · "}Updated {time(data.as_of)}{now-Date.parse(data.as_of)>90_000 && " · Stale report"}{data.truncated && " · Latest 5,000 attempts only"}</p>
    <dl className="topbot-levels topbot-stat-summary">
      <div><dt>Average net / trade</dt><dd>{money(stats.mean)}</dd></div>
      <div><dt>Win / break-even rate</dt><dd>{pct(stats.rate)} / {pct(stats.breakeven)}</dd></div>
      <div><dt>Gap to break-even</dt><dd>{delta == null ? "—" : (delta>0 ? "+" : "")+delta.toFixed(1)+" pp"}</dd></div>
      <div><dt>Net P&amp;L</dt><dd>{money(stats.net)}</dd></div>
      <div><dt>Gross / fees &amp; commissions</dt><dd>{money(stats.gross)} / {money(stats.costs)}</dd></div>
      <div><dt>Closed-equity max drawdown</dt><dd>{money(stats.drawdown)}</dd></div>
    </dl>
    <p className="topbot-footnote">Break-even uses realized average wins and losses after costs. Both rates exclude flat trades. Drawdown measures peak to trough on closed trades.</p>
    <details><summary>Qualification quote, pre-submit quote and entry fill</summary>
      <div className="topbot-stat-table"><table><thead><tr><th>Entry (ET)</th><th>Side</th><th>Qualification</th><th>Pre-submit</th><th>Fill</th><th>Delay (ticks)</th><th>Fill slippage (ticks)</th></tr></thead>
        <tbody>{trades.slice(0,30).map(t => <tr key={t.id}><td>{time(t.entry)}</td><td>{t.side}</td><td>{fmt(t.qualify)}</td><td>{fmt(t.submit)}</td><td>{fmt(t.fill)}</td>
          <td>{ticks(t.qualify,t.submit,t.side)}</td><td>{ticks(t.submit,t.fill,t.side)}</td></tr>)}</tbody></table></div>
      <p className="topbot-footnote">Ask for a buy, bid for a sell. Positive ticks are adverse. Actual fills already include slippage. A dash means the relevant quote or fill is unavailable.</p>
    </details>
  </>;
}

export function InclusionPanel({ rows, version, mode, start, end }: { rows?: Inclusion[]; version: string; mode: string; start: string; end: string }) {
  const cohort = rows?.filter(r => (!version || r.version === version) && (!mode || r.mode === mode) && (!start || r.date >= start) && (!end || r.date <= end));
  const reasons = new Map<string,number>();
  for (const r of cohort ?? []) for (const reason of r.reasons) reasons.set(reason,(reasons.get(reason) ?? 0)+1);
  const main = [...reasons].sort((a,b) => b[1]-a[1] || a[0].localeCompare(b[0]))[0];
  return <div className="topbot-baseline" aria-label="Exit study coverage by actual outcome">
    <h4>Which actual trades enter the exit comparison?</h4>
    {cohort == null ? <p>Outcome coverage unavailable.</p> : <>
      <div className="topbot-stat-table"><table><thead><tr><th>Broker outcome</th><th>Usable / enrolled</th><th>Usable</th><th>Excluded</th><th>Pending</th></tr></thead>
        <tbody>{["win","loss","flat","unknown"].map(k => {
          const group=cohort.filter(r => r.outcome === k), usable=group.filter(r => r.status==="usable").length;
          if (!group.length && (k==="flat" || k==="unknown")) return null;
          return <tr key={k}><th>{{win:"Winners",loss:"Losers",flat:"Flat",unknown:"Unresolved"}[k]}</th><td>{usable} / {group.length}</td>
            <td>{pct(group.length ? usable/group.length : null)}</td><td>{group.filter(r => r.status==="excluded").length}</td><td>{group.filter(r => r.status==="pending").length}</td></tr>;
        })}</tbody></table></div>
      <p className="topbot-footnote">{main ? "Most common exclusion: "+main[0].replaceAll("_"," ")+" ("+main[1]+" entries). " : ""}
        A trade is usable when all exit alternatives resolve. Unequal winner and loser coverage can skew the target comparison.</p>
    </>}
  </div>;
}

export function SignalPanel({ data, now }: { data: EdgeReport; now: number }) {
  const [source,setSource]=useState("Worker");
  const selected: SignalStudy | undefined=data.studies.find(s=>s.source===source);
  return <section className="topbot-panel" aria-label="Imbalance signal study"><h2 className="text-base font-semibold">Imbalance signal study</h2>
    <p className="topbot-muted">This study tracks contrarian signals: bid-heavy shorts and ask-heavy longs.</p>
    <div className="topbot-comparison-filters"><label>Observation stream <select value={source} onChange={e=>setSource(e.target.value)}>
      <option>Worker</option><option>Independent</option></select></label></div>
    {selected && <>
      <p>{selected.total} distinct qualifications · {selected.matched} matched {selected.matched===1 ? "control" : "controls"}{source==="Worker" && " · "+selected.filled+" linked fills"}
        {" · "}{selected.gaps} recorded coverage interruptions</p>
      <p className="topbot-muted">{data.contract} · Study registered {time(data.started)} · Last observation {selected.through ? time(selected.through) : "unavailable"}
        {selected.through && now-Date.parse(selected.through)>90_000 && " · Delayed or feed idle"}</p>
      <div className="topbot-stat-table"><table><thead><tr><th>Horizon</th><th>All observed / mean pts</th><th>Complete pairs</th><th>Signal pts</th><th>Baseline pts</th><th>Difference pts</th></tr></thead>
        <tbody>{selected.horizons.map(h=><tr key={h.seconds}><th>{h.seconds===30 ? "30 sec" : h.seconds/60+" min"}</th>
          <td>{h.valid} / {fmt(h.all_mean)}</td><td>{h.pairs}</td><td>{fmt(h.signal)}</td><td>{fmt(h.baseline)}</td><td>{fmt(h.difference)}</td></tr>)}</tbody></table></div>
      <p className="topbot-footnote">Buy at the ask and mark at the bid, or sell at the bid and mark at the ask. Values are points before fees. Controls use the same observed contract, rule, New York hour, spread and preceding 60-second move (2-point bands). Each control is below threshold, at least five minutes earlier and used once. The difference averages complete pairs only.</p>
      <details><summary>Recent qualifying episodes, skipped signals and coverage</summary>
        <p className="topbot-footnote">{source==="Worker" ? "Recorded worker qualifications include signals between evaluations. Reasons use linked decisions, orders or recorded open positions." : "Continuous market observations run with the bot disarmed. Independent arrivals can differ from the worker; the two samples are shown separately."}</p>
        <div className="topbot-stat-table"><table><thead><tr><th>Qualified (ET)</th><th>Side</th><th>Bid / ask</th><th>Disposition</th><th>Control</th><th>30s / 2m / 5m (pts)</th></tr></thead>
          <tbody>{selected.recent.map(r=><tr key={r.id}><td>{time(r.at)}</td><td>{r.side===1 ? "Buy" : "Sell"}</td><td>{fmt(r.bid)} / {fmt(r.ask)}</td><td>{r.disposition}{r.reason && " · "+r.reason}</td>
          <td>{r.control==null ? "Unmatched" : "#"+r.control}</td><td>{[30,120,300].map(h=>r.moves[h] ? r.moves[h].reason || fmt(r.moves[h].points) : "Pending").join(" / ")}</td></tr>)}</tbody></table></div>
      </details>
    </>}
  </section>;
}
