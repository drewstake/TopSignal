import { useEffect, useRef, useState, useSyncExternalStore, type FormEvent } from 'react';
import { SimulationChart } from '../SimulationChart';
import { INSTRUMENTS, POPULATION, SPECS, TIMEFRAMES, protection, round, simulationTime, type Direction, type Instrument, type Order, type OrderRequest, type Position, type Timeframe } from './model';
import { freshSeed, getFundedRuntime, type View } from './runtime';
import '../SimulationPage.css';
import './TradingPage.css';

const money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format;
const price = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format;
const pnlClass = (value: number) => value < 0 ? 'gf-negative' : value > 0 ? 'gf-positive' : '';
type Panel = 'Positions' | 'Working orders' | 'Fills' | 'Trade history' | 'Order history';

export function TradingPage() {
  const runtime = getFundedRuntime(), view = useSyncExternalStore(runtime.subscribe, runtime.getSnapshot);
  const dispatch = runtime.dispatch, market = view.session.market;
  const [symbol, setSymbol] = useState<Instrument>('BX'), [timeframe, setTimeframe] = useState<Timeframe>('1m');
  const [type, setType] = useState<OrderRequest['type']>('Market'), [quantity, setQuantity] = useState('1'), [entry, setEntry] = useState('');
  const [stopOn, setStopOn] = useState(true), [targetOn, setTargetOn] = useState(true), [stop, setStop] = useState('500'), [target, setTarget] = useState('1500');
  const [panel, setPanel] = useState<Panel>('Positions'), [localError, setLocalError] = useState('');
  const [expanded, setExpanded] = useState(false);
  const workspace = useRef<HTMLDivElement>(null), expandButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!expanded) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    expandButton.current?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setExpanded(false); expandButton.current?.focus(); }
      if (event.key !== 'Tab') return;
      const nodes = [...(workspace.current?.querySelectorAll<HTMLElement>('button:not(:disabled), select, input:not(:disabled), summary, [tabindex="0"]') ?? [])].filter(node => node.getClientRects().length);
      const first = nodes[0], last = nodes.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    window.addEventListener('keydown', key);
    return () => { document.body.style.overflow = previous; window.removeEventListener('keydown', key); };
  }, [expanded]);
  const spec = SPECS[symbol], book = market?.books[symbol], levels = view.books[symbol];
  const last = book ? (book.last ?? book.reference) * spec.tick : spec.last;
  const disabled = view.warming || view.restarting || !view.account;
  const effective = (value: string) => { try { const p = protection(spec, Number(quantity), Number(value)); return `${p.ticks} ticks · ${money(p.dollars)} total`; } catch { return 'Enter a valid amount and quantity'; } };
  function submit(direction: Direction) {
    setLocalError(''); const qty = Number(quantity);
    if (!Number.isSafeInteger(qty) || qty < 1) { setLocalError('Enter a positive whole number of contracts.'); return; }
    try { if (stopOn) protection(spec, qty, Number(stop)); if (targetOn) protection(spec, qty, Number(target)); } catch (error) { setLocalError((error as Error).message); return; }
    if (type !== 'Market' && (!entry || !Number.isFinite(Number(entry)) || Number(entry) <= 0)) { setLocalError('Enter a positive entry price.'); return; }
    const normalized = type === 'Market' ? undefined : round(Number(entry) / spec.tick) * spec.tick;
    if (normalized !== undefined) setEntry(normalized.toFixed(2));
    dispatch({ type: 'order', request: { contract: symbol, direction, quantity: qty, type, price: normalized, stopDollars: stopOn ? Number(stop) : undefined, targetDollars: targetOn ? Number(target) : undefined } });
  }
  const account = view.account, totals = view.totals;
  return <main className="gf-terminal simulation-page">
    <header className="gf-header">
      <a className="gf-brand" href="/" aria-label="TopSignal home"><span className="gf-logo">↗</span>TopSignal</a>
      <span className="gf-environment"><i />SIMULATED EXCHANGE</span>
      <div className="gf-header-right"><span>390 participants · 3 markets</span><a href="/dashboard">Journal & accounts ↗</a></div>
    </header>
    <section className="gf-account" aria-label="Trading account">
      <div><span>Balance</span><strong>{money(totals?.balance ?? 10000)}</strong></div>
      <div><span>Equity</span><strong>{money(totals?.equity ?? 10000)}</strong></div>
      <div><span>Realized P&L</span><strong className={pnlClass(totals?.realized ?? 0)}>{money(totals?.realized ?? 0)}</strong></div>
      <div><span>Unrealized P&L</span><strong className={pnlClass(totals?.unrealized ?? 0)}>{money(totals?.unrealized ?? 0)}</strong></div>
      <div className="gf-capacity"><span>Contract capacity</span><strong>{view.capacity?.available ?? 10}<small> / {view.capacity?.limit ?? 10} available</small></strong><span>{view.capacity?.reserved ?? 0} reserved across all markets</span></div>
      <button className="gf-flatten" disabled={disabled} onClick={() => dispatch({ type: 'flatten' })}>Flatten all</button>
    </section>
    <nav className="gf-markets" aria-label="Active markets">
      {INSTRUMENTS.map(key => { const b = market?.books[key], current = b ? (b.last ?? b.reference) * SPECS[key].tick : SPECS[key].last, change = b?.open ? current - b.open * SPECS[key].tick : 0;
        return <button key={key} aria-label={`${key} ${SPECS[key].name}`} aria-pressed={key === symbol} onClick={() => { setSymbol(key); setEntry(''); }}><span><b>{key}</b><small>{SPECS[key].name}</small></span><strong>{view.warming ? 'Opening…' : price(current)}</strong><small className={pnlClass(change)}>{change >= 0 ? '+' : ''}{change.toFixed(2)}</small></button>;
      })}
      <div className="gf-regime"><span>Market regime</span><strong>{view.regime || 'Initializing'}</strong></div>
    </nav>
    {view.warming && <div className="gf-warmup" role="status"><strong>Opening the exchange</strong><span>Running the auction and 7,200 real simulation steps · {Math.floor(view.warmupProgress * 100)}%</span><progress value={view.warmupProgress} max={1} /></div>}
    <div ref={workspace} className={`gf-workspace${expanded ? ' is-expanded' : ''}`} role={expanded ? 'dialog' : undefined} aria-modal={expanded ? true : undefined} aria-label={expanded ? 'Expanded market workspace' : undefined}>
      <section className="gf-chart-panel" aria-label={`${symbol} market chart`}>
        <div className="gf-chart-heading"><div><h1>{spec.name} <span>{symbol}</span></h1><span className="gf-subtle">{money(spec.tick * spec.multiplier)} / tick · {spec.tick} tick size · ${spec.multiplier} multiplier</span></div><strong>{view.warming ? 'Opening…' : price(last)}</strong></div>
        <div className="gf-chart-controls"><div className="gf-timeframes" aria-label="Chart timeframe">{TIMEFRAMES.map(([tf]) => <button key={tf} aria-pressed={tf === timeframe} onClick={() => setTimeframe(tf)}>{tf}</button>)}</div><button ref={expandButton} aria-label={expanded ? 'Exit expanded chart' : 'Expand chart'} aria-pressed={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? 'Exit' : 'Expand'}</button></div>
        <div className="gf-chart-slot">{market && !view.warming ? <SimulationChart key={`${symbol}-${timeframe}-${view.generation}`} runtime={runtime} symbol={symbol} tickSize={spec.tick} timeframe={timeframe} seconds={TIMEFRAMES.find(([tf]) => tf === timeframe)![1]} height={390} fitContainer /> : <div className="gf-chart-placeholder">Building history from matched trades…</div>}</div>
        <div className="gf-market-status"><span className={market && market.time < view.newsUntil ? 'gf-news-active' : ''}>{market && market.time < view.newsUntil ? view.news : 'No active synthetic news'}</span><span>Volume {book?.volume.toLocaleString() ?? '—'}</span></div>
      </section>
      <aside className="gf-depth-panel" aria-label="Order book and tape">
        <div className="gf-panel-title"><h2>Order book</h2><span>5 levels</span></div>
        <div className="gf-depth-head"><span>Price</span><span>Contracts</span></div>
        <div className="gf-depth-side" aria-label="Asks">{levels.asks.length ? [...levels.asks].reverse().map(l => <div key={l.ticks} className="gf-level gf-ask" style={{ '--depth': `${Math.min(100, l.quantity / Math.max(...levels.asks.map(l => l.quantity)) * 100)}%` } as React.CSSProperties}><span>{price(l.ticks * spec.tick)}</span><b>{l.quantity}</b></div>) : <p>No resting asks</p>}</div>
        <div className="gf-spread"><strong>{price(last)}</strong><span>{levels.asks[0] && levels.bids[0] ? `${levels.asks[0].ticks - levels.bids[0].ticks} tick spread` : 'One side unavailable'}</span></div>
        <div className="gf-depth-side" aria-label="Bids">{levels.bids.length ? levels.bids.map(l => <div key={l.ticks} className="gf-level gf-bid" style={{ '--depth': `${Math.min(100, l.quantity / Math.max(...levels.bids.map(l => l.quantity)) * 100)}%` } as React.CSSProperties}><span>{price(l.ticks * spec.tick)}</span><b>{l.quantity}</b></div>) : <p>No resting bids</p>}</div>
        <div className="gf-panel-title gf-tape-title"><h2>Recent trades</h2><span>Actual fills</span></div>
        <div className="gf-tape" aria-label="Recent executed trades">{market?.trades.filter(t => t.contract === symbol).slice(-7).reverse().map(t => <div key={t.id}><time>{simulationTime(t.time)}</time><span className={t.direction === 1 ? 'gf-positive' : 'gf-negative'}>{price(t.ticks * spec.tick)}</span><b>{t.quantity}</b></div>)}</div>
      </aside>
      <aside className="gf-ticket" aria-label="Trade ticket">
        <div className="gf-panel-title"><h2>Place a trade</h2><span>Simulated money</span></div>
        <form onSubmit={e => e.preventDefault()}>
          <label>Order type<select value={type} onChange={e => { setType(e.target.value as OrderRequest['type']); if (!entry) setEntry(last.toFixed(2)); }}><option>Market</option><option>Limit</option><option>Stop</option></select></label>
          <label>Contracts<input aria-label="Contracts" type="number" min="1" step="1" inputMode="numeric" value={quantity} onChange={e => setQuantity(e.target.value)} /></label>
          {type !== 'Market' && <label>{type} entry price<input aria-label={`${type} entry price`} type="number" min={spec.tick} step={spec.tick} value={entry} onChange={e => setEntry(e.target.value)} onBlur={() => { if (entry && Number.isFinite(Number(entry))) setEntry((round(Number(entry) / spec.tick) * spec.tick).toFixed(2)); }} /></label>}
          <div className="gf-trade-buttons"><button type="button" className="gf-buy" disabled={disabled || account?.locked} onClick={() => submit(1)}>Buy / Long<small>Buy capacity {view.capacity?.directions[symbol][1] ?? 10}</small></button><button type="button" className="gf-sell" disabled={disabled || account?.locked} onClick={() => submit(-1)}>Sell / Short<small>Sell capacity {view.capacity?.directions[symbol][-1] ?? 10}</small></button></div>
          <fieldset><legend>Position protection <span>Total dollars</span></legend>
            <label className="gf-check"><input type="checkbox" checked={stopOn} onChange={e => setStopOn(e.target.checked)} />Stop loss</label>
            <input aria-label="Stop loss dollars" type="number" min="0.01" max="1000000000" step="0.01" disabled={!stopOn} value={stop} onChange={e => setStop(e.target.value)} /><small>{stopOn ? effective(stop) : 'Stop loss disabled'}</small>
            <label className="gf-check"><input type="checkbox" checked={targetOn} onChange={e => setTargetOn(e.target.checked)} />Take profit</label>
            <input aria-label="Take profit dollars" type="number" min="0.01" max="1000000000" step="0.01" disabled={!targetOn} value={target} onChange={e => setTarget(e.target.value)} /><small>{targetOn ? effective(target) : 'Take profit disabled'}</small>
          </fieldset>

          <p className="gf-ticket-note">{type === 'Market' ? 'Immediate execution. Any unfilled quantity is cancelled.' : type === 'Limit' ? 'Fills at your limit or better. Unfilled quantity rests in the book.' : 'Reserves capacity off-book. A traded price triggers immediate market execution.'}</p>
        </form>
      </aside>
    </div>
    <div className={`gf-notice${view.error || localError ? ' is-error' : ''}`} role={view.error || localError ? 'alert' : 'status'}>{localError || view.error || (account?.locked ? 'Account depleted. New exposure is disabled; eligible liquidation continues through the book.' : view.notice || 'Ready to trade.')}</div>
    {view.receipt && <div className="gf-receipt" aria-label="Latest order confirmation"><span>Order #{view.receipt.id}</span><strong>{view.receipt.status}</strong><span>Filled {view.receipt.filled}</span><span>Remaining {view.receipt.remaining}</span><span>Cancelled {view.receipt.cancelled}</span><span>Average {view.receipt.filled ? price(view.receipt.notional / view.receipt.filled) : '—'}</span><span>Slippage {view.receipt.filled ? price((view.receipt.notional / view.receipt.filled - view.receipt.reference * SPECS[view.receipt.contract].tick) * view.receipt.direction) : '—'}</span></div>}
    <section className="gf-activity" aria-label="Trading activity">
      <nav aria-label="Account panels">{(['Positions', 'Working orders', 'Fills', 'Trade history', 'Order history'] as Panel[]).map(p => <button key={p} aria-pressed={panel === p} onClick={() => setPanel(p)}>{p}{p === 'Positions' ? ` (${Object.keys(account?.positions ?? {}).length})` : p === 'Working orders' ? ` (${view.orders.length})` : ''}</button>)}</nav>
      <div className="gf-table-scroll">{panel === 'Positions' && <table><thead><tr><th>Market</th><th>Position</th><th>Avg entry</th><th>Last</th><th>Unrealized P&L</th><th>Protection</th><th>Action</th></tr></thead><tbody>{INSTRUMENTS.map(key => { const p = account?.positions[key]; if (!p || !market) return null; const last = (market.books[key].last ?? market.books[key].reference) * SPECS[key].tick, pnl = (last - p.entry) * p.direction * p.quantity * SPECS[key].multiplier;
        return <tr key={key}><th>{key}</th><td className={p.direction === 1 ? 'gf-positive' : 'gf-negative'}>{p.direction === 1 ? 'Long' : 'Short'} {p.quantity}</td><td>{price(p.entry)}</td><td>{price(last)}</td><td className={pnlClass(pnl)}>{money(pnl)}</td><td><PositionProtection key={`${key}-${p.id}`} symbol={key} position={p} disabled={disabled} /></td><td><button disabled={disabled} onClick={() => dispatch({ type: 'close', key })}>Close position</button>{p.triggered && <small className="gf-pending-exit">{p.triggered} · {p.quantity} remaining</small>}</td></tr>;
      })}{!Object.keys(account?.positions ?? {}).length && <tr><td colSpan={7} className="gf-empty">No open positions. Choose a market and place your first trade.</td></tr>}</tbody></table>}
      {(panel === 'Working orders' || panel === 'Order history') && <OrdersTable view={view} orders={panel === 'Working orders' ? view.orders : view.history} working={panel === 'Working orders'} />}
      {panel === 'Fills' && <table><thead><tr><th>Time</th><th>Order</th><th>Market</th><th>Side</th><th>Contracts</th><th>Price</th><th>Slippage / contract</th></tr></thead><tbody>{account?.fills.slice(0, 100).map(f => <tr key={`${f.trade}-${f.order}`}><td>{simulationTime(f.time)}</td><td>#{f.order}</td><th>{f.contract}</th><td>{f.direction === 1 ? 'Buy' : 'Sell'}</td><td>{f.quantity}</td><td>{price(f.price)}</td><td>{price(f.slippage)}</td></tr>)}{!account?.fills.length && <tr><td colSpan={7} className="gf-empty">Your executions will appear here.</td></tr>}</tbody></table>}
      {panel === 'Trade history' && <table><thead><tr><th>Time</th><th>Market</th><th>Position</th><th>Closed</th><th>Entry</th><th>Exit</th><th>Realized P&L</th><th>Reason</th></tr></thead><tbody>{account?.history.map((t, i) => <tr key={`${t.trade}-${i}`}><td>{simulationTime(t.time)}</td><th>{t.contract}</th><td>{t.direction === 1 ? 'Long' : 'Short'}</td><td>{t.quantity}</td><td>{price(t.entry)}</td><td>{price(t.exit)}</td><td className={pnlClass(t.pnl)}>{money(t.pnl)}</td><td>{t.reason}</td></tr>)}{!account?.history.length && <tr><td colSpan={8} className="gf-empty">Closed quantities and realized results will appear here.</td></tr>}</tbody></table>}
      </div>
    </section>
    <footer className="gf-footer"><div className="gf-playback"><button disabled={view.warming || view.restarting} onClick={() => dispatch({ type: 'play', playing: !view.playing })}>{view.playing ? 'Ⅱ Pause' : '▶ Play'}</button><button disabled={view.warming || view.playing || view.restarting} onClick={() => dispatch({ type: 'step' })}>Step 0.25s</button><span aria-label="Simulation elapsed time">{simulationTime(market?.time ?? 0)}</span><span>18&#215; &#183; 72 steps/s</span></div><div className="gf-restarts"><span>Seed {view.seed}</span><button disabled={disabled} onClick={() => dispatch({ type: 'restart', seed: view.seed })}>Restart same seed</button><button disabled={disabled} onClick={() => dispatch({ type: 'restart', seed: freshSeed() })}>Restart new seed</button></div></footer>
    <div className="gf-session-note"><span>Local simulated session · no fees · reload starts a fresh account from the saved seed</span><span>{view.restarting ? 'Restart waiting for closing liquidity' : view.backlog > 18 ? `${view.backlog.toFixed(1)} simulated seconds pending` : 'Chart, book and account use the same exchange'}</span></div>
    <details className="gf-population"><summary>Participant funding & simulation details</summary><p>130 bots per market. Starting capital is per bot; every order reserves finite inventory and funding.</p><div>{Object.entries(POPULATION).map(([kind, [count, capital]]) => <span key={kind}><b>{kind}</b> {count} × {money(capital)}</span>)}</div><p>Shared regimes and synthetic news influence quotes. Only matched trades move prices. Restarts close positions first, then repeat the opening auction and full warmup. Seed and inputs determine replay; this port is not a verified published-game snapshot.</p></details>
  </main>;
}
function OrdersTable({ orders, working, view }: { orders: Order[]; working: boolean; view: View }) {
  const runtime = getFundedRuntime();
  return <table><thead><tr><th>Order</th><th>Market / side</th><th>Type</th><th>Price</th><th>Filled / remaining / cancelled</th><th>Average / slippage</th><th>Status / reason</th>{working && <th>Action</th>}</tr></thead><tbody>{orders.map(o => <tr key={o.id}><td>#{o.id}</td><th>{o.contract} · {o.direction === 1 ? 'Buy' : 'Sell'}</th><td>{o.protection ?? o.type}{o.reduceOnly && <small>Reduce only</small>}</td><td>{o.price !== undefined ? price(o.price * SPECS[o.contract].tick) : 'Market'}</td><td>{o.filled} / {o.remaining} / {o.cancelled}</td><td>{o.filled ? `${price(o.notional / o.filled)} / ${price((o.notional / o.filled - o.reference * SPECS[o.contract].tick) * o.direction)}` : '—'}</td><td>{o.status}<small>{o.reason}</small></td>{working && <td><button disabled={view.restarting} onClick={() => runtime.dispatch({ type: 'cancel', id: o.id })}>Cancel #{o.id}</button></td>}</tr>)}{!orders.length && <tr><td colSpan={8} className="gf-empty">{working ? 'No working orders.' : 'Completed and cancelled orders will appear here.'}</td></tr>}</tbody></table>;
}
function PositionProtection({ symbol, position: p, disabled }: { symbol: Instrument; position: Position; disabled: boolean }) {
  const [editing, setEditing] = useState(false), [field, setField] = useState<'stop' | 'target'>('stop'), [amount, setAmount] = useState('500');
  const runtime = getFundedRuntime();
  const submit = (e: FormEvent) => { e.preventDefault(); runtime.dispatch({ type: 'protection', key: symbol, position: p.id, field, dollars: Number(amount) }); setEditing(false); };
  return <div className="gf-position-protection"><span>SL {p.stop !== undefined ? price(p.stop) : 'Off'} · TP {p.target !== undefined ? price(p.target) : 'Off'}</span><button disabled={disabled || !!p.triggered} onClick={() => setEditing(!editing)}>Edit protection</button>{editing && <form onSubmit={submit}><select aria-label={`${symbol} protection field`} value={field} onChange={e => setField(e.target.value as 'stop' | 'target')}><option value="stop">Stop loss</option><option value="target">Take profit</option></select><input aria-label={`${symbol} protection total dollars`} type="number" min="0.01" max="1000000000" step="0.01" value={amount} onChange={e => setAmount(e.target.value)} /><button type="submit">Apply</button><button type="button" onClick={() => { runtime.dispatch({ type: 'protection', key: symbol, position: p.id, field, dollars: null }); setEditing(false); }}>Disable</button></form>}</div>;
}
