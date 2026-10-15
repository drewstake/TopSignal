import { useMemo, useState, useSyncExternalStore } from 'react';
import type { SimulationRuntime } from './simulationRuntime';
import { accountSummary } from './simulationExchange';
import { PARTICIPANT_GROUPS, STEP_SECONDS, simulationTime, type Execution, type Instrument } from './simulationPreview';
import { usePanelPreference } from './simulationWorkspace';
import { dollars } from './simulationFormat';

const RECENT_TRADES = 8, RECENT_EVENTS = 2;

const subscribeHidden = () => () => {};
const hiddenSnapshot = () => null;
export function SimulationActivity({ runtime, instrument, earliest, onJump, active = true }: {
  runtime: SimulationRuntime; instrument: Instrument; earliest: number; onJump: (time: number, label: string) => void; active?: boolean;
}) {
  const snapshot = useSyncExternalStore(active ? runtime.subscribe : subscribeHidden, active ? runtime.getSnapshot : hiddenSnapshot);
  const market = snapshot?.session.market;
  const [tapeOpen, setTapeOpen] = usePanelPreference('tape');
  const [eventsOpen, setEventsOpen] = usePanelPreference('events');
  const [side, setSide] = useState('all');
  const tape = useMemo(() => {
    const recent: Execution[] = [];
    if (!market || !tapeOpen) return recent;
    for (let i = market.trades.length - 1; i >= 0 && recent.length < RECENT_TRADES; i--) {
      const trade = market.trades[i];
      if (trade.contract === instrument && (side === 'all' || trade.direction === Number(side))) recent.push(trade);
    }
    return recent;
  }, [market, instrument, side, tapeOpen]);
  if (!market) return null;
  return <div className="simulation-activity">
    <section className="simulation-activity-panel" aria-label={`${instrument} time and sales`}>
      <header><h2>Time & sales <small>{instrument}</small></h2><button aria-expanded={tapeOpen} onClick={() => setTapeOpen(!tapeOpen)}>{tapeOpen ? 'Collapse tape' : 'Show tape'}</button></header>
      {tapeOpen && <><div className="simulation-activity-controls"><label>Aggressor <select aria-label="Tape aggressor" value={side} onChange={e => setSide(e.target.value)}><option value="all">Both sides</option><option value="1">Buy</option><option value="-1">Sell</option></select></label>
        <span>Live · step {market.step}</span></div>
        <p className="simulation-chart-help">Latest {RECENT_TRADES} executions · newest first</p>
        <div className="simulation-tape"><table><thead><tr><th>Sim time</th><th>Market</th><th>Price</th><th>Qty</th><th>Aggressor</th></tr></thead><tbody>
          {tape.map(t => <tr key={t.id}><td>{simulationTime(t.time)}.{String(Math.round(t.time % 1 * 100)).padStart(2, '0')}</td><td>{t.contract}</td><td>{(t.ticks * market.config.contracts[t.contract].tick).toFixed(2)}</td><td>{t.quantity}</td><td className={t.direction === 1 ? 'simulation-up' : 'simulation-down'}>{t.direction === 1 ? 'Buy' : 'Sell'}</td></tr>)}
          {!tape.length && <tr><td colSpan={5}>No retained executions match this filter.</td></tr>}
        </tbody></table></div></>}
    </section>
    <section className="simulation-activity-panel" aria-label="Market event timeline"><header><h2>Event timeline</h2><button aria-expanded={eventsOpen} onClick={() => setEventsOpen(!eventsOpen)}>{eventsOpen ? 'Collapse events' : 'Show events'}</button></header>
      {eventsOpen && <><p className="simulation-chart-help">Latest {RECENT_EVENTS} events · newest first · select to inspect on chart</p><div className="simulation-events">{market.events.slice(-RECENT_EVENTS).reverse().map((event, i) => {
        const time = event.step * STEP_SECONDS, retained = time >= earliest;
        return <article className="simulation-event" key={`${event.step}:${event.kind}:${i}`}><span>{simulationTime(time)} · {event.kind}</span><strong>{event.name}</strong>
          <p>{event.kind === 'Override' ? 'Applied to future decisions and schedules.' : `Environment bias: ${event.direction === 1 ? 'up' : 'down'}. This is not an executed price move.`}</p>
          <button disabled={!retained} onClick={() => onJump(time, `${event.kind}: ${event.name}`)}>{retained ? 'Inspect on chart' : 'Before retained chart history'}</button></article>;
      })}{!market.events.length && <p>No events recorded yet.</p>}</div></>}
    </section>
  </div>;
}

export function SimulationIndividuals({ runtime, instrument, active = true }: { runtime: SimulationRuntime; instrument: Instrument; active?: boolean }) {
  const [open, setOpen] = useState(false);
  const snapshot = useSyncExternalStore(active && open ? runtime.subscribe : subscribeHidden, active && open ? runtime.getSnapshot : hiddenSnapshot);
  const market = snapshot?.session.market;
  const [query, setQuery] = useState(''), [kind, setKind] = useState('all'), [status, setStatus] = useState('all');
  const agents = market?.participants.filter(p => p.key === instrument && (kind === 'all' || p.kind === kind) && p.id.toLowerCase().includes(query.toLowerCase())
    && (status === 'all' || (status === 'halted' ? market.accounts[p.id].locked || market.accounts[p.id].liquidating : !!market.accounts[p.id].positions[instrument]))) ?? [];
  return <details className="simulation-individuals" open={open}><summary onClick={event => { event.preventDefault(); setOpen(!open); }}>Inspect individual participants · {instrument}</summary>
    {market && <>
    <div className="simulation-activity-controls"><input aria-label="Search participant ID" placeholder="Search participant ID" value={query} onChange={e => setQuery(e.target.value)} />
      <select aria-label="Participant group" value={kind} onChange={e => setKind(e.target.value)}><option value="all">Every group</option>{PARTICIPANT_GROUPS.map(g => <option key={g.kind} value={g.kind}>{g.label}</option>)}</select>
      <select aria-label="Participant status" value={status} onChange={e => setStatus(e.target.value)}><option value="all">Every state</option><option value="position">Open position</option><option value="halted">Halted / liquidating</option></select><span>{agents.length} participants</span></div>
    <p>Inventory is signed contracts. Unrealized P&amp;L marks positions to the last execution (opening reference before the first trade). Equity = closed balance + unrealized P&amp;L. Margin uses contract requirements; risk headroom includes worst-case non-reducing resting exposure.</p>
    <div className="simulation-individual-grid">{agents.map(p => {
      const account = market.accounts[p.id], summary = accountSummary(market, account), position = account.positions[instrument];
      const orders = account.openOrders.map(id => market.orders[id]), net = (position?.quantity ?? 0) * (position?.direction ?? 1);
      const buy = orders.filter(o => o.direction === 1 && !o.reduceOnly).reduce((n, o) => n + o.remaining, 0), sell = orders.filter(o => o.direction === -1 && !o.reduceOnly).reduce((n, o) => n + o.remaining, 0);
      const worst = Math.max(Math.abs(net + buy), Math.abs(net - sell)), used = worst * market.config.contracts[instrument].margin;
      const budget = Math.max(0, summary.equity) * account.risk;
      return <details key={p.id} className="simulation-participant"><summary><span><strong>{p.id}</strong><small>{p.kind} · {account.locked ? 'Halted' : account.liquidating ? 'Liquidating' : 'Active'}</small></span><span>{net > 0 ? '+' : ''}{net}<small>net contracts</small></span></summary>
        {p.structure && <div className="simulation-participant-detail"><p><strong>{({ 'zone-limit': 'Patient zone limit', 'zone-rejection': 'Candle-confirmed zone rejection', 'level-rejection': 'Support / resistance rejection', 'breakout-retest': 'Breakout retest' })[p.structure.mode]}</strong> · completed 1-minute candles</p>
          <dl className="simulation-person-ledger"><div><dt>Watched zone / level</dt><dd>{p.structure.watched}</dd></div>
            <div><dt>Current setup</dt><dd>{p.structure.setup ? `${p.structure.setup.side === 1 ? 'Buy' : 'Sell'} · ${p.structure.setup.source} · confirmed ${simulationTime(p.structure.setup.createdAt)}` : p.structure.breakout ? 'Breakout confirmed; retest pending' : 'Waiting for a setup'}</dd></div>
            <div><dt>Decision</dt><dd>{p.structure.reason}</dd></div>
            <div><dt>Structural history</dt><dd>{p.structure.bars.length} closed minutes · {p.structure.zones.length} zones · {p.structure.levels.length} levels</dd></div></dl></div>}
        <dl className="simulation-person-ledger">{Object.entries({ 'Realized P&L': dollars(account.realized), 'Unrealized P&L': dollars(summary.unrealized), 'Closed balance': dollars(account.balance), Equity: dollars(summary.equity), 'Position margin': dollars(summary.margin), 'Resting buy / sell': `${buy} / ${sell}`, 'Reducing orders': orders.filter(o => o.reduceOnly).reduce((n, o) => n + o.remaining, 0), 'Worst exposure / limit': `${worst} / ${account.limit} contracts`, 'Risk allowance': `${(account.risk * 100).toFixed(0)}% of equity · ${dollars(budget)}`, 'Risk headroom': dollars(budget - used), 'Entry / stop / target': `${position ? dollars(position.entry) : '—'} / ${position?.stop === undefined ? '—' : dollars(position.stop)} / ${position?.target === undefined ? '—' : dollars(position.target)}`, 'Protective state': position?.triggered ?? 'No triggered exit', 'Next decision': simulationTime(p.next), 'Scheduled parent remainder': p.parent }).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
        <p>{account.locked ? 'Engine halted this depleted account.' : account.liquidating ? 'Engine is working to reduce exposure through available liquidity.' : 'New exposure is checked against inventory limits and equity-based margin risk.'} Stops and targets submit orders into the book; an exit can remain pending when liquidity is insufficient.</p>
      </details>;
    })}</div>{!agents.length && <p>No participants match these filters.</p>}
    </>}
  </details>;
}
